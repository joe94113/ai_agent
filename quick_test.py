import io
import json
import re
from contextlib import redirect_stdout
from unittest.mock import patch
import os

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

    # 支援：十、十一、二十、二十三
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

def parse_resources(text: str):
    out = []
    for m in RES_PAIR_RE.finditer(text):
        ps = cn_to_int(m.group(1))
        st = cn_to_int(m.group(2))
        if ps > 0 and st >= 0:
            out.append({"party_size": ps, "spots_total": st})

    # 合併同 party_size（避免重複）
    merged = {}
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

def extract_time_range(text: str):
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


def parse_business_hours_json(text: str):
    rng = extract_time_range(text)
    if not rng:
        return []
    start, end = rng

    # 決定開店日
    # day: 0=週一 ... 6=週日
    days = None
    if ("每天" in text) or ("每日" in text):
        days = list(range(7))
    elif ("週一到週六" in text) or ("週一～週六" in text) or ("週一至週六" in text):
        days = list(range(6))
    elif ("週一到週五" in text) or ("週一～週五" in text) or ("週一至週五" in text):
        days = list(range(5))
    else:
        # 測試用 fallback：沒說就當每天
        days = list(range(7))

    # 公休處理
    if ("週日" in text or "星期日" in text) and ("公休" in text or "休" in text):
        days = [d for d in days if d != 6]

    out = []
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
# 跑一個測試案例（腳本化 input）
# -----------------------------
def run_case(name: str, inputs: list[str], use_real_llm: bool = False):
    it = iter(inputs)

    def scripted_input(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration:
            raise RuntimeError(f"[{name}] 測試輸入不夠用，FSM 又多問了一題。請補 inputs。")

    buf = io.StringIO()
    with redirect_stdout(buf), patch("builtins.input", side_effect=scripted_input):
        if use_real_llm:
            agent.main()
        else:
            with patch.object(agent, "llm_extract", side_effect=fake_llm_extract):
                agent.main()

    out = buf.getvalue()

    # 记录问题、输入和AI的回答
    log_data = []
    question_and_answers = []

    for line in out.splitlines():
        if "🤖 Agent：" in line:
            log_data.append(f"問題:\n{line}")
            question_and_answers.append(f"問題:\n{line}")
        elif "你：" in line:
            log_data.append(f"輸入:\n{line}")
            question_and_answers.append(f"輸入:\n{line}")

    # 抓 FINAL_JSON（你的 main 會 print: FINAL_JSON: {...}）
    final = None
    for line in reversed(out.splitlines()):
        if "FINAL_JSON:" in line:
            json_str = line.split("FINAL_JSON:", 1)[1].strip()
            final = json.loads(json_str)
            break

    if final is None:
        print(f"\n❌ [{name}] 找不到 FINAL_JSON，完整輸出如下：\n{out}")
        raise AssertionError("FINAL_JSON missing")

    ok, reason = agent.validate_final_json(final)
    if not ok:
        print(f"\n❌ [{name}] FINAL_JSON validator 失敗：{reason}\n輸出如下：\n{out}")
        raise AssertionError(reason)

    # 你也可以加上簡單統計
    turns = out.count("🤖 Agent：")
    print(f"✅ [{name}] PASS | turns={turns} | store_name={final.get('store_name')} | capacity_hint={final.get('capacity_hint')}")

    # 將結果寫入檔案
    with open(f"test_results_{name}.txt", "w", encoding="utf-8") as f:
        f.write(f"測試案例: {name}\n")
        f.write(f"回應總回合數: {turns}\n")
        f.write(f"store_name: {final.get('store_name')}\n")
        f.write(f"capacity_hint: {final.get('capacity_hint')}\n")
        f.write("\n### 詳細問答過程:\n")
        
        # 輸出問題和使用者回答
        for log in log_data:
            f.write(f"{log}\n")
        
        # 輸出問題和回答的詳細過程
        f.write("\n### 問題和回答紀錄:\n")
        for qa in question_and_answers:
            f.write(f"{qa}\n")
    
    return final, out


def main():
    # 你可以在這裡新增更多案例
    TESTS = {
        "happy_daily_open": [
            "123簡餐",
            "四人桌五個 六人桌四個 八人桌一個",
            "A",  # Step 3
            "每天 08:00-17:00",
            "A",  # Step 4 confirm
            "A",  # Step 5 merge tables
            "12人",  # Step 5-2 max party
            "A",  # Step 6 online role
            "C",  # Step 7 peak
            "C",  # Step 8 quota
            "C",  # Step 9 peak strategy
            "C",  # Step 10 no-show tolerance
            "A",  # Step 11 accept recommendation
        ],
        "random_answer_case_1": [
            "123簡餐",
            "4人桌5個 6人桌2個",
            "1+1",  # 隨便回答
            "A",  # Step 3
            "每天 08:00-17:00",
            "A",  # Step 4 confirm
            "A",  # Step 5 merge tables
            "12人",  # Step 5-2 max party
            "A",  # Step 6 online role
            "C",  # Step 7 peak
            "C",  # Step 8 quota
            "C",  # Step 9 peak strategy
            "C",  # Step 10 no-show tolerance
            "A",  # Step 11 accept recommendation
        ],
        "random_answer_case_2": [
            "123簡餐",
            "4人桌5個 6人桌2個",
            "藍色好嗎？",  # 亂回答
            "A",  # Step 3
            "每天 08:00-17:00",
            "A",  # Step 4 confirm
            "A",  # Step 5 merge tables
            "12人",  # Step 5-2 max party
            "A",  # Step 6 online role
            "C",  # Step 7 peak
            "C",  # Step 8 quota
            "C",  # Step 9 peak strategy
            "C",  # Step 10 no-show tolerance
            "A",  # Step 11 accept recommendation
        ],
    }

    for name, inputs in TESTS.items():
        run_case(name, inputs, use_real_llm=False)

    print("\n🎉 All tests passed.")


if __name__ == "__main__":
    main()
