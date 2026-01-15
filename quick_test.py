# quick_test.py
import io
import json
import os
import re
from contextlib import redirect_stdout
from unittest.mock import patch
from typing import Any, Dict, List, Tuple, Optional
from typing import Union

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

def auto_answer(question_block: str) -> str:
    q = (question_block or "").replace(" ", "")

    # ✅ 1) 先處理「確認題」（一定要放最前面）
    if "這樣對嗎" in q and ("A." in q and "B." in q):
        return "A"

    # ✅ 2) 再處理營業時間輸入題
    if "營業時間" in q:
        return "每天 08:00-17:00"

    if "線上訂位建議" in q and "直接採用" in q and "我想調整" in q:
        return "A"  # 大部分 case：讓測試能收斂、跑完

    # ✅ Step11：修改提示（要回「一句調整內容」，不能回 A/B）
    if "你想怎麼調整" in q:
        return "忙的時候每 30 分鐘最多 2 組線上訂位"

    # 店名
    if "店名" in q:
        return "自動測試店"

    # 桌型
    if ("桌型" in q) or ("幾張" in q) or ("人桌" in q):
        return "4人桌2張 6人桌1張"

    # 用餐時間 A/B/C
    if "用餐" in q and ("A." in q or "B." in q or "C." in q):
        return "B"

    # 併桌
    if "併起來" in q and ("A." in q and "B." in q):
        return "A"

    # 最大人數
    if "最多" in q and ("幾個人" in q or "幾人" in q):
        return "8人"

    # 線上訂位角色
    if "扮演什麼角色" in q and ("A." in q or "B." in q or "C." in q):
        return "B"

    # 最忙時段
    if "最容易忙起來" in q and ("A." in q or "B." in q or "C." in q or "D." in q):
        return "C"

    # 忙時線上佔比
    if "佔多少位置" in q and ("A." in q or "B." in q or "C." in q):
        return "B"

    # 忙時策略
    if "比較希望怎麼做" in q and ("A." in q or "B." in q or "C." in q):
        return "A"

    # no-show
    if "沒來" in q and ("A." in q or "B." in q or "C." in q):
        return "B"

    # Step11 接受/修改
    if "直接採用" in q and "我想調整" in q:
        return "A"

    # 最後保底：選項題就 A
    if "A." in q and "B." in q:
        return "A"

    return "A"

def classify_step(q: str) -> str:
    t = (q or "").replace(" ", "")

    if "請問店名" in t or "店名是什麼" in t:
        return "store_name"
    if "桌型" in t or "人桌" in t:
        return "resources"
    if "用餐" in t and ("多久" in t or "大約" in t):
        return "duration"
    if "整理一下營業時間" in t and "這樣對嗎" in t:
        return "hours_confirm"
    if "營業時間" in t:
        return "hours"
    if "併起來" in t:
        return "merge_tables"
    if "最多" in t and ("幾個人" in t or "一起用餐" in t):
        return "max_party_size"
    if "扮演什麼角色" in t:
        return "online_role"
    if "最容易忙起來" in t:
        return "peak_period"
    if "佔多少位置" in t:
        return "peak_ratio"
    if "比較希望怎麼做" in t:
        return "peak_strategy"
    if "沒來" in t or "放鳥" in t:
        return "no_show"

    # ✅ Step11 兩種問題
    if "線上訂位建議" in t and "直接採用" in t and "我想調整" in t:
        return "step11_confirm"
    if "你想怎麼調整" in t:
        return "step11_modify"

    return "unknown"

# -----------------------------
# 跑一個測試案例（腳本化 input）+ 產生 interleaved log
# -----------------------------
InputPlan = Union[List[str], Dict[str, List[str]]]

def run_case(
    name: str,
    inputs: InputPlan,
    use_real_llm: bool = False,
    log_dir: str = "test_logs",
    max_turns: int = 120,
    allow_autofill: bool = True,
):
    os.makedirs(log_dir, exist_ok=True)

    turns: List[Dict[str, Any]] = []
    buf = io.StringIO()
    last_len = 0
    input_calls = 0

    # ✅ 每個 step 被問到第幾次（重問時會取下一個答案）
    step_counts: Dict[str, int] = {}

    # ✅ list 模式才需要 iterator
    it = iter(inputs) if isinstance(inputs, list) else None

    # ✅ 計數：是否真的打到 Ollama（requests.post 被呼叫幾次）
    llm_calls = {"n": 0}
    real_post = agent.requests.post

    def wrapped_post(url, *args, **kwargs):
        llm_calls["n"] += 1
        return real_post(url, *args, **kwargs)

    def pick_from_plan(step: str) -> Tuple[str, bool]:
        """
        回傳 (answer, auto_used)
        - dict 模式：依 step 取答案；同 step 重問會依序取下一個；用完就沿用最後一個
        - list 模式：照順序取；用完才 auto
        """
        auto_used = False

        # ✅ dict(step-plan) 模式：真實 LLM 強烈建議用這個
        if isinstance(inputs, dict):
            seq = inputs.get(step)
            if seq is None:
                seq = inputs.get("default", [])

            if isinstance(seq, list) and len(seq) > 0:
                k = step_counts.get(step, 0)
                step_counts[step] = k + 1
                ans = seq[k] if k < len(seq) else seq[-1]
                return str(ans), False

            # 沒提供就 auto
            return "", True

        # ✅ list 模式
        assert it is not None
        try:
            ans = next(it)
            return str(ans), False
        except StopIteration:
            return "", True

    def scripted_input(prompt: str = "") -> str:
        nonlocal last_len, input_calls
        input_calls += 1
        if input_calls > max_turns:
            raise RuntimeError(f"[{name}] 超過 max_turns={max_turns}，疑似 LLM 一直重問/卡住。")

        so_far = buf.getvalue()
        delta = so_far[last_len:]
        last_len = len(so_far)

        q = extract_last_agent_block(delta) or "🤖 Agent：<未捕捉到輸出>"
        step = classify_step(q)

        a, auto_used = pick_from_plan(step)

        # ✅ auto 時用你的 auto_answer 產答案（要能收斂 Step11）
        if auto_used:
            if not allow_autofill:
                raise RuntimeError(f"[{name}] 測試輸入不夠用 / step-plan 未覆蓋：step={step}")
            a = auto_answer(q)

        turns.append({
            "step": step,
            "auto": auto_used,
            "q": q,
            "a": a,
        })
        return a

    err = None
    out = ""
    try:
        with redirect_stdout(buf), patch("builtins.input", side_effect=scripted_input):
            if use_real_llm:
                with patch.object(agent.requests, "post", side_effect=wrapped_post):
                    agent.main()
            else:
                with patch.object(agent, "llm_extract", side_effect=fake_llm_extract):
                    agent.main()
    except Exception as e:
        err = e
    finally:
        out = buf.getvalue()

        auto_cnt = sum(1 for t in turns if t.get("auto"))
        log_name = f"{name}.txt" if err is None else f"FAIL_{name}.txt"
        log_path = os.path.join(log_dir, log_name)

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"測試案例: {name}\n")
            f.write(f"use_real_llm: {use_real_llm}\n")
            f.write(f"llm_http_calls: {llm_calls['n']}\n")
            f.write(f"turns: {len(turns)}\n")
            f.write(f"auto_fills: {auto_cnt}\n")
            if err is not None:
                f.write(f"STATUS: FAIL\nERROR: {repr(err)}\n")
            else:
                f.write("STATUS: PASS\n")

            f.write("\n====================\n### Interleaved Transcript\n====================\n")
            for i, t in enumerate(turns, 1):
                f.write(f"\n--- Turn {i} ---\n")
                if t.get("auto"):
                    f.write("[AUTO-FILL]\n")
                f.write(f"[step={t.get('step')}]\n")
                f.write((t.get("q") or "").rstrip() + "\n")
                f.write("\n輸入:\n")
                f.write(str(t.get("a", "")) + "\n")

            f.write("\n====================\n### RAW STDOUT\n====================\n")
            f.write(out)

    if err is not None:
        raise err

    if use_real_llm and llm_calls["n"] == 0:
        raise AssertionError(f"[{name}] use_real_llm=True 但 llm_http_calls=0，代表沒有打到 Ollama。")

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

    log_path = os.path.join(log_dir, f"{name}.txt")
    auto_cnt = sum(1 for t in turns if t.get("auto"))

    print(
        f"✅ [{name}] PASS | turns={len(turns)} | auto={auto_cnt} | "
        f"llm_http_calls={llm_calls['n']} | store_name={final.get('store_name')} | "
        f"capacity_hint={final.get('capacity_hint')} | log={log_path}"
    )
    return final, out, log_path

def main():
    # ✅ 真實 LLM 建議用 step-plan（依問題回覆）
    TESTS: Dict[str, Dict[str, List[str]]] = {
        "happy_daily_open": {
            "store_name": ["123簡餐"],
            "resources": ["四人桌五個 六人桌四個 八人桌一個"],
            "duration": ["A"],
            "hours": ["每天 08:00-17:00"],
            "hours_confirm": ["A"],
            "merge_tables": ["A"],
            "max_party_size": ["12人"],
            "online_role": ["A"],
            "peak_period": ["C"],
            "peak_ratio": ["C"],
            "peak_strategy": ["C"],
            "no_show": ["C"],
            "step11_confirm": ["A"],
        },

        "closed_sunday_no_merge": {
            "store_name": ["週末小館"],
            "resources": ["4人桌3張 6人桌2張"],
            "duration": ["B"],
            "hours": ["週一到週六 08:00-17:00，週日公休"],
            "hours_confirm": ["A"],
            "merge_tables": ["B"],  # 不可併桌（max_party_size 不會問）
            "online_role": ["B"],
            "peak_period": ["D"],
            "peak_ratio": ["B"],
            "peak_strategy": ["A"],
            "no_show": ["B"],
            "step11_confirm": ["A"],
        },

        "bad_resources_then_ok": {
            "store_name": ["測試店"],
            "resources": ["1+1", "4人桌2張 6人桌1張"],  # ✅ 同 step 重問會吃下一個
            "duration": ["A"],
            "hours": ["每天 08:00-17:00"],
            "hours_confirm": ["A"],
            "merge_tables": ["A"],
            "max_party_size": ["8人"],
            "online_role": ["B"],
            "peak_period": ["A"],
            "peak_ratio": ["B"],
            "peak_strategy": ["B"],
            "no_show": ["B"],
            "step11_confirm": ["A"],
        },

        "bad_duration_then_ok": {
            "store_name": ["亂答店"],
            "resources": ["4人桌2張"],
            "duration": ["我不知道", "C"],  # ✅ 重問後改答對
            "hours": ["每天 08:00-17:00"],
            "hours_confirm": ["A"],
            "merge_tables": ["A"],
            "max_party_size": ["10"],
            "online_role": ["C"],
            "peak_period": ["E"],
            "peak_ratio": ["B"],
            "peak_strategy": ["A"],
            "no_show": ["C"],
            "step11_confirm": ["A"],
        },

        "bad_hours_then_ok": {
            "store_name": ["時間店"],
            "resources": ["4人桌2張 6人桌1張"],
            "duration": ["A"],
            "hours": ["藍色好嗎？", "每天 08:00-17:00"],  # ✅ hours 抽不到會重問
            "hours_confirm": ["A"],
            "merge_tables": ["A"],
            "max_party_size": ["12人"],
            "online_role": ["A"],
            "peak_period": ["C"],
            "peak_ratio": ["C"],
            "peak_strategy": ["B"],
            "no_show": ["B"],
            "step11_confirm": ["A"],
        },

        "hours_confirm_B_then_fix": {
            "store_name": ["改時間店"],
            "resources": ["4人桌2張"],
            "duration": ["B"],
            "hours": ["每天 08:00-17:00", "週一到週六 09:00-18:00，週日公休"],
            "hours_confirm": ["B", "A"],  # ✅ 先說不對，再確認正確
            "merge_tables": ["A"],
            "max_party_size": ["8"],
            "online_role": ["B"],
            "peak_period": ["D"],
            "peak_ratio": ["A"],
            "peak_strategy": ["A"],
            "no_show": ["B"],
            "step11_confirm": ["A"],
        },

        "step11_modify_path": {
            "store_name": ["修改店"],
            "resources": ["4人桌3張 6人桌2張"],
            "duration": ["A"],
            "hours": ["每天 08:00-17:00"],
            "hours_confirm": ["A"],
            "merge_tables": ["A"],
            "max_party_size": ["12"],
            "online_role": ["A"],
            "peak_period": ["C"],
            "peak_ratio": ["B"],
            "peak_strategy": ["A"],
            "no_show": ["B"],
            # ✅ Step11：第一次選 B 進修改，第二次選 A 接受
            "step11_confirm": ["B", "A"],
            "step11_modify": ["忙時 4 人桌 1 張、6 人桌 1 張"],
        },
    }

    for name, plan in TESTS.items():
        run_case(name, plan, use_real_llm=True, allow_autofill=True, max_turns=120)

    print("\n🎉 All tests passed. Logs are under ./test_logs/")

if __name__ == "__main__":
    main()
