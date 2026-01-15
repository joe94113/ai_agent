# quick_test.py
import io
import json
import os
import re
from contextlib import redirect_stdout
from unittest.mock import patch
from typing import Any, Dict, List, Tuple, Optional

import onboarding_fsm as agent  # ← 改成你的檔名（不要加 .py）


# -----------------------------
# 簡易中文字數字轉 int（夠測試用）
# -----------------------------
CN_MAP = {
    "零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9
}

def cn_to_int(s: str) -> int:
    s = s.strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)

    # 十、十一、二十、二十三
    if s == "十":
        return 10
    if "十" in s:
        left, right = s.split("十", 1)
        tens = CN_MAP.get(left, 1) if left else 1
        ones = CN_MAP.get(right, 0) if right else 0
        return tens * 10 + ones

    return CN_MAP.get(s, 0)


# -----------------------------
# 解析桌型：四人桌五個 / 4人桌5張 / 8人桌1個
# -----------------------------
RES_PAIR_RE = re.compile(
    r"([0-9一二兩三四五六七八九十]+)\s*人?\s*桌?\s*([0-9一二兩三四五六七八九十]+)\s*(?:張|個|桌|位)?"
)

def parse_resources(text: str) -> List[Dict[str, int]]:
    out: List[Dict[str, int]] = []
    for m in RES_PAIR_RE.finditer(text):
        ps = cn_to_int(m.group(1))
        st = cn_to_int(m.group(2))
        if ps > 0 and st >= 0:
            out.append({"party_size": ps, "spots_total": st})

    # 合併同 party_size
    merged: Dict[int, int] = {}
    for r in out:
        merged.setdefault(r["party_size"], 0)
        merged[r["party_size"]] += r["spots_total"]

    return [{"party_size": ps, "spots_total": merged[ps]} for ps in sorted(merged.keys())]


# -----------------------------
# 解析時間：08:00-17:00 / 8點到17點 / 早八晚五
# -----------------------------
TIME_RANGE_RE1 = re.compile(r"(\d{1,2})[:：](\d{2})\s*(?:-|–|~|到|至)\s*(\d{1,2})[:：](\d{2})")
TIME_RANGE_RE2 = re.compile(r"(\d{1,2})\s*(?:點|時)\s*(?:-|–|~|到|至)\s*(\d{1,2})\s*(?:點|時)")
TIME_RANGE_RE3 = re.compile(r"早([0-9一二兩三四五六七八九十]+).*(?:晚|到晚上)([0-9一二兩三四五六七八九十]+)")

def to_hhmm(h: int, m: int) -> str:
    h = max(0, min(23, int(h)))
    m = max(0, min(59, int(m)))
    return f"{h:02d}{m:02d}"

def extract_time_range(text: str) -> Optional[Tuple[str, str]]:
    t = text.strip()

    m = TIME_RANGE_RE1.search(t)
    if m:
        sh, sm, eh, em = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        return to_hhmm(sh, sm), to_hhmm(eh, em)

    m = TIME_RANGE_RE2.search(t)
    if m:
        sh, eh = int(m.group(1)), int(m.group(2))
        return to_hhmm(sh, 0), to_hhmm(eh, 0)

    m = TIME_RANGE_RE3.search(t)
    if m:
        sh = cn_to_int(m.group(1))
        eh = cn_to_int(m.group(2))
        return to_hhmm(sh, 0), to_hhmm(eh, 0)

    return None


def parse_business_hours_json(text: str) -> List[Dict[str, Any]]:
    rng = extract_time_range(text)
    if not rng:
        return []
    start, end = rng

    # day: 0=週一 ... 6=週日
    if ("每天" in text) or ("每日" in text):
        days = list(range(7))
    elif ("週一到週六" in text) or ("週一～週六" in text) or ("週一至週六" in text):
        days = list(range(6))
    elif ("週一到週五" in text) or ("週一～週五" in text) or ("週一至週五" in text):
        days = list(range(5))
    else:
        # 測試用 fallback：沒說就當每天
        days = list(range(7))

    # 公休
    if ("週日" in text or "星期日" in text) and ("公休" in text or "休" in text):
        days = [d for d in days if d != 6]

    out: List[Dict[str, Any]] = []
    for d in days:
        out.append({"open": {"day": d, "time": start}, "close": {"day": d, "time": end}})
    return out


# -----------------------------
# Mock 掉 llm_extract：避免真的打 Ollama（測 FSM 流程最快）
# -----------------------------
def fake_llm_extract(step_name: str, user_text: str, state: dict) -> dict:
    if step_name == "store_name":
        name = user_text.strip()
        return {"store_name": name} if name else {}

    if step_name == "resources":
        res = parse_resources(user_text)
        return {"resources": res} if res else {}

    if step_name == "business_hours_json":
        bh = parse_business_hours_json(user_text)
        return {"business_hours_json": bh} if bh else {}

    # Step 11 recommendation_patch：讓演算法 fallback 自己算（最快）
    if step_name == "recommendation_patch":
        return {}

    # 其他步驟：FSM 大多用規則處理，這裡回 {} 即可
    return {}


# -----------------------------
# 從「新印出的 stdout 片段」抓出「最後一段 🤖 Agent：...」
# -----------------------------
def extract_last_agent_block(delta: str) -> str:
    if not delta:
        return ""

    marker = "🤖 Agent："
    idx = delta.rfind(marker)
    if idx == -1:
        return delta.strip()

    block = delta[idx:].strip()
    return block


# -----------------------------
# 跑一個測試案例（腳本化 input）+ 產生 interleaved log
# -----------------------------
def run_case(name: str, inputs: List[str], use_real_llm: bool = False, log_dir: str = "test_logs"):
    os.makedirs(log_dir, exist_ok=True)

    it = iter(inputs)
    consumed_inputs: List[str] = []
    turns: List[Dict[str, str]] = []

    buf = io.StringIO()
    last_len = 0  # 上次 input 時 stdout 長度

    def scripted_input(prompt: str = "") -> str:
        nonlocal last_len

        # 取出「從上次 input 之後，到這次 input 之前」新印出的文字
        so_far = buf.getvalue()
        delta = so_far[last_len:]
        last_len = len(so_far)

        q = extract_last_agent_block(delta)

        try:
            a = next(it)
        except StopIteration:
            raise RuntimeError(f"[{name}] 測試輸入不夠用，FSM 又多問了一題。請補 inputs。")

        consumed_inputs.append(a)
        turns.append({"q": q, "a": a})
        return a

    # 執行 agent.main()，把 print 都導到 buf
    with redirect_stdout(buf), patch("builtins.input", side_effect=scripted_input):
        if use_real_llm:
            # ✅ 這個模式會真的跑到 Ollama（agent.llm_extract 會打 requests）
            agent.main()
        else:
            # ✅ 這個模式不會打到模型，只測 FSM/validator/流程
            with patch.object(agent, "llm_extract", side_effect=fake_llm_extract):
                agent.main()

    out = buf.getvalue()

    # 抓 FINAL_JSON
    final = None
    for line in reversed(out.splitlines()):
        if "FINAL_JSON:" in line:
            json_str = line.split("FINAL_JSON:", 1)[1].strip()
            final = json.loads(json_str)
            break

    if final is None:
        raise AssertionError(f"[{name}] 找不到 FINAL_JSON。\n\nRAW:\n{out}")

    ok, reason = agent.validate_final_json(final)
    if not ok:
        raise AssertionError(f"[{name}] FINAL_JSON validator 失敗：{reason}\n\nRAW:\n{out}")

    used_n = len(consumed_inputs)
    unused = inputs[used_n:] if used_n < len(inputs) else []

    # 寫 log 檔
    log_path = os.path.join(log_dir, f"{name}.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"測試案例: {name}\n")
        f.write(f"use_real_llm: {use_real_llm}\n")
        f.write(f"輸入被消耗數: {used_n}\n")
        if unused:
            f.write(f"⚠️ 未被使用的 inputs: {len(unused)}（代表你給太多輸入或流程提前結束）\n")

        f.write(f"store_name: {final.get('store_name')}\n")
        f.write(f"capacity_hint: {final.get('capacity_hint')}\n")
        f.write("\n====================\n")
        f.write("### Interleaved Transcript\n")
        f.write("====================\n")

        for i, t in enumerate(turns, 1):
            f.write(f"\n--- Turn {i} ---\n")
            q = (t.get("q") or "").rstrip()
            if not q:
                q = "🤖 Agent：<未捕捉到輸出>"
            f.write(q + "\n")
            f.write("\n輸入:\n")
            f.write(t.get("a", "") + "\n")

        f.write("\n====================\n")
        f.write("### RAW STDOUT\n")
        f.write("====================\n")
        f.write(out)

    # 給 console 的簡短 PASS
    print(f"✅ [{name}] PASS | turns={len(turns)} | store_name={final.get('store_name')} | capacity_hint={final.get('capacity_hint')} | log={log_path}")
    return final, out, log_path


def main():
    TESTS: Dict[str, List[str]] = {
        # 正常流程
        "happy_daily_open": [
            "123簡餐",
            "四人桌五個 六人桌四個 八人桌一個",
            "A",
            "每天 08:00-17:00",
            "A",
            "A",
            "12人",
            "A",
            "C",
            "C",
            "C",
            "C",
            "A",
        ],

        # 週日公休 + 不可併桌（會跳過 max_party_size 詢問）
        "closed_sunday_no_merge": [
            "週末小館",
            "4人桌3張 6人桌2張",
            "B",
            "週一到週六 08:00-17:00，週日公休",
            "A",
            "B",   # 不可併桌
            "B",   # online_role
            "D",   # peak
            "B",   # ratio
            "A",   # peak strategy
            "B",   # no-show
            "A",   # step11 accept
        ],

        # 桌型亂答一次再答對
        "bad_resources_then_ok": [
            "測試店",
            "1+1",                 # resources 解析不到 -> 會重問
            "4人桌2張 6人桌1張",    # ok
            "A",
            "每天 08:00-17:00",
            "A",
            "A",
            "8人",
            "B",
            "A",
            "B",
            "B",
            "B",
            "A",
        ],

        # 用餐時間亂答一次再答對
        "bad_duration_then_ok": [
            "亂答店",
            "4人桌2張",
            "我不知道",  # invalid -> 重問
            "C",         # ok
            "每天 08:00-17:00",
            "A",
            "A",
            "10",
            "C",
            "E",
            "B",
            "A",
            "C",
            "A",
        ],

        # 營業時間亂答一次再答對
        "bad_hours_then_ok": [
            "時間店",
            "4人桌2張 6人桌1張",
            "A",
            "藍色好嗎？",        # hours 解析不到 -> 重問
            "每天 08:00-17:00",  # ok
            "A",
            "A",
            "12人",
            "A",
            "C",
            "C",
            "B",
            "B",
            "A",
        ],

        # 確認營業時間選 B（要求修改）再輸入新時間
        "hours_confirm_B_then_fix": [
            "改時間店",
            "4人桌2張",
            "B",
            "每天 08:00-17:00",
            "B",  # confirm 不對 -> 會要求再說一次營業時間
            "週一到週六 09:00-18:00，週日公休",
            "A",  # confirm ok
            "A",
            "8",
            "B",
            "D",
            "A",
            "A",
            "B",
            "A",
        ],

        # Step11 走修改路徑：B -> 輸入修改文字 -> A 接受
        "step11_modify_path": [
            "修改店",
            "4人桌3張 6人桌2張",
            "A",
            "每天 08:00-17:00",
            "A",
            "A",
            "12",
            "A",
            "C",
            "B",
            "A",
            "B",
            "B",                  # Step11: 我想調整
            "忙時 4 人桌 1 張、6 人桌 1 張",  # （fake_llm 不會改，但能測流程）
            "A",                  # 接受
        ],
    }

    # 預設：跑 mock（最快、最穩）
    for name, inputs in TESTS.items():
        # run_case(name, inputs, use_real_llm=False)

    # ✅ 如果你想「確定有打到模型」，加一個 smoke test：
    # （注意：這會真的打到 Ollama，結果可能不 deterministic、也可能比較慢）
        run_case("smoke_real_llm", TESTS["happy_daily_open"], use_real_llm=True)

    print("\n🎉 All tests passed. Logs are under ./test_logs/")


if __name__ == "__main__":
    main()
