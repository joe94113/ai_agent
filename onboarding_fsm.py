import json
import re
import requests
from typing import Dict, Any, Optional, Tuple, List

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "llama3.1:8b-instruct-q4_K_M"

# =========================
# Validators (沿用/補強你原本的)
# =========================

HHMM_RE = re.compile(r"^\d{4}$")

def validate_business_hours_json(bh: Any) -> Tuple[bool, str]:
    if not isinstance(bh, list) or len(bh) == 0:
        return False, "business_hours_json 必須是非空 list"
    for i, p in enumerate(bh):
        if not isinstance(p, dict):
            return False, f"business_hours_json[{i}] 必須是 object"
        if "open" not in p or "close" not in p:
            return False, f"business_hours_json[{i}] 必須包含 open/close"
        o = p["open"]; c = p["close"]
        if not isinstance(o, dict) or not isinstance(c, dict):
            return False, f"business_hours_json[{i}].open/close 必須是 object"
        if "day" not in o or "time" not in o or "day" not in c or "time" not in c:
            return False, f"business_hours_json[{i}] open/close 必須包含 day/time"
        if not (isinstance(o["day"], int) and 0 <= o["day"] <= 6):
            return False, f"business_hours_json[{i}].open.day 必須 0~6"
        if not (isinstance(c["day"], int) and 0 <= c["day"] <= 6):
            return False, f"business_hours_json[{i}].close.day 必須 0~6"
        ot = str(o["time"]); ct = str(c["time"])
        if not HHMM_RE.match(ot):
            return False, f"business_hours_json[{i}].open.time 必須是 4 位 HHMM"
        if not HHMM_RE.match(ct):
            return False, f"business_hours_json[{i}].close.time 必須是 4 位 HHMM"
    return True, "ok"

def validate_resources(res: Any) -> Tuple[bool, str]:
    if not isinstance(res, list) or len(res) == 0:
        return False, "resources 必須是非空 list"
    for i, r in enumerate(res):
        if not isinstance(r, dict):
            return False, f"resources[{i}] 必須是 object"
        if "party_size" not in r or "spots_total" not in r:
            return False, f"resources[{i}] 必須包含 party_size/spots_total"
        if not isinstance(r["party_size"], int) or r["party_size"] <= 0:
            return False, f"resources[{i}].party_size 必須是正整數"
        if not isinstance(r["spots_total"], int) or r["spots_total"] < 0:
            return False, f"resources[{i}].spots_total 必須是整數且 >=0"
    return True, "ok"

def validate_strategy(s: Any) -> Tuple[bool, str]:
    if not isinstance(s, dict):
        return False, "strategy 必須是 object"
    need = ["goal_type","online_role","peak_periods","peak_strategy","no_show_tolerance","can_merge_tables","max_party_size"]
    for k in need:
        if k not in s:
            return False, f"strategy 缺少 {k}"

    if s["goal_type"] not in ["fill_seats","control_queue","keep_walkin"]:
        return False, "strategy.goal_type 不合法"
    if s["online_role"] not in ["primary","assistant","minimal"]:
        return False, "strategy.online_role 不合法"
    if s["peak_strategy"] not in ["online_first","walkin_first","no_online"]:
        return False, "strategy.peak_strategy 不合法"
    if s["no_show_tolerance"] not in ["low","medium","high"]:
        return False, "strategy.no_show_tolerance 不合法"
    if not isinstance(s["can_merge_tables"], bool):
        return False, "strategy.can_merge_tables 必須是 boolean"
    if not isinstance(s["max_party_size"], int) or s["max_party_size"] <= 0:
        return False, "strategy.max_party_size 必須是正整數"

    if not isinstance(s["peak_periods"], list):
        return False, "strategy.peak_periods 必須是 list"
    allowed = {"weekday_lunch","weekday_dinner","weekend_brunch","weekend_dinner"}
    for x in s["peak_periods"]:
        if x not in allowed:
            return False, f"strategy.peak_periods 出現不允許的值：{x}"

    return True, "ok"

def validate_final_json(final: Any) -> Tuple[bool, str]:
    if not isinstance(final, dict):
        return False, "FINAL_JSON 必須是 object"
    for k in ["store_id","store_name","capacity_hint","resources","duration_sec","business_hours_json","strategy"]:
        if k not in final:
            return False, f"缺少欄位 {k}"

    if final["store_id"] is not None and not isinstance(final["store_id"], int):
        return False, "store_id 必須是 null 或 int"

    if not isinstance(final["store_name"], str) or not final["store_name"].strip():
        return False, "store_name 必須是非空字串"

    if not isinstance(final["capacity_hint"], int) or final["capacity_hint"] <= 0:
        return False, "capacity_hint 必須是正整數"

    ok, msg = validate_resources(final["resources"])
    if not ok:
        return False, msg

    if not isinstance(final["duration_sec"], int) or final["duration_sec"] <= 0:
        return False, "duration_sec 必須是正整數（秒）"

    ok, msg = validate_business_hours_json(final["business_hours_json"])
    if not ok:
        return False, msg

    ok, msg = validate_strategy(final["strategy"])
    if not ok:
        return False, msg

    return True, "ok"

def merge_patch(state: Dict[str, Any], patch: Dict[str, Any]) -> None:
    for k, v in patch.items():
        if k == "strategy" and isinstance(v, dict):
            state.setdefault("strategy", {})
            if isinstance(state["strategy"], dict):
                state["strategy"].update(v)
            else:
                state["strategy"] = v
        else:
            state[k] = v

def capacity_hint_from_resources(resources: List[Dict[str, int]]) -> int:
    # 「總座位數」當 capacity_hint：sum(party_size * spots_total)
    return max(1, sum(int(r["party_size"]) * int(r["spots_total"]) for r in resources))

# =========================
# LLM Extractor (只抽取，不聊天，不問問題)
# =========================

EXTRACTOR_SYSTEM = r"""
你是一個「資料抽取器」，只負責把使用者回答轉成 JSON patch。
你必須只輸出一段 JSON object（不要文字、不要解釋、不要換行前後多餘內容）。
不可包含 Markdown code block。
若資訊不足或無法判斷，輸出空物件 {}。

重要規則：
- 輸出必須是合法 JSON（key 用雙引號）。
- 只輸出本步驟需要的欄位。
"""

def call_ollama(messages: List[Dict[str, str]]) -> str:
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "top_p": 0.9
        }
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["message"]["content"]

def parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip().strip("`").strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None

def llm_extract(step_name: str, user_text: str, state: Dict[str, Any]) -> Dict[str, Any]:
    # 把需求講清楚：每一步要輸出的 schema
    # 注意：這裡是給模型看的，不會展示給商家
    schema_guide = {
        "store_name": r'輸出：{"store_name": "<非空字串>"}',
        "resources": r'輸出：{"resources":[{"party_size":4,"spots_total":5},{"party_size":6,"spots_total":2}]}  party_size/spots_total 都是整數',
        "duration_sec": r'輸出：{"duration_sec":3600} 或 5400 或 7200（秒）',
        "business_hours_json": r'''輸出：{"business_hours_json":[
  {"open":{"day":0,"time":"0800"},"close":{"day":0,"time":"1700"}},
  ...
]}
day: 0=週一, 1=週二, 2=週三, 3=週四, 4=週五, 5=週六, 6=週日
time: 必須是 4 位 HHMM 字串，例如 "0830"
若使用者說「每天 08:00-17:00」，就輸出 day 0~6 各一筆
若使用者說「週一到週六 08:00-17:00，週日公休」，就輸出 day 0~5 各一筆''',
        "merge_tables": r'輸出：{"strategy":{"can_merge_tables":true}} 或 false',
        "max_party_size": r'輸出：{"strategy":{"max_party_size":8}}（整數）',
        "online_role": r'輸出：{"strategy":{"online_role":"primary"}} 或 "assistant" 或 "minimal"',
        "peak_periods": r'輸出：{"strategy":{"peak_periods":["weekend_brunch"]}} 允許值：weekday_lunch,weekday_dinner,weekend_brunch,weekend_dinner',
        "peak_online_quota_ratio": r'輸出：{"strategy":{"peak_online_quota_ratio":0.5}}（0.8/0.5/0.2 其一）',
        "peak_strategy": r'輸出：{"strategy":{"peak_strategy":"online_first"}} 或 "walkin_first" 或 "no_online"',
        "no_show_tolerance": r'輸出：{"strategy":{"no_show_tolerance":"medium"}} 或 low/high',
        "recommendation_patch": r'''
                你可以輸出以下欄位（可只輸出其中一部分，沒提到的不要輸出）：
                {
                "booking_hours_json":[
                    {"open":{"day":0,"time":"0800"},"close":{"day":0,"time":"1600"}},
                    ...
                ],
                "strategy":{
                    "peak_strategy":"online_first" 或 "walkin_first" 或 "no_online",
                    "peak_online_quota_ratio": 0.8 或 0.5 或 0.2 或 0.0,
                    "peak_online_resources":[
                    {"party_size":4,"spots_total":2},
                    {"party_size":6,"spots_total":1}
                    ]
                }
                }

                規則：
                - booking_hours_json 格式與 business_hours_json 相同（day 0~6；time 為 4 位 HHMM 字串）
                - peak_online_resources 的 party_size 必須出現在總 resources 裡
                - peak_online_resources.spots_total 代表「線上可訂桌數」，必須介於 0 ~ 該桌型總桌數
                '''
    }

    guide = schema_guide.get(step_name, "輸出：{}")

    user_prompt = f"""
【步驟】{step_name}
【輸出格式】{guide}
【使用者回答】{user_text}
【已知狀態摘要】{json.dumps(state, ensure_ascii=False)}
請只輸出 JSON object。
""".strip()

    messages = [
        {"role": "system", "content": EXTRACTOR_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    raw = call_ollama(messages)
    obj = parse_json_object(raw)
    return obj if obj is not None else {}

# =========================
# FSM Orchestrator（由程式控制問答流程）
# =========================

def normalize_choice(text: str) -> str:
    t = text.strip().lower()
    # 支援使用者輸入 a / A / 選項A / 直接文字
    t = t.replace("選項", "").replace(" ", "")
    return t

DAY_NAMES = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

def hhmm_to_colon(hhmm: str) -> str:
    s = str(hhmm).zfill(4)
    return f"{s[:2]}:{s[2:]}"

def summarize_business_hours(bh: List[Dict[str, Any]]) -> str:
    """
    將 business_hours_json 轉為商家容易確認的字串，例如：
    週一～週六 08:00–17:00；週日 公休
    支援同一天多段時段（例如午晚餐），會顯示：11:00–14:00、17:00–21:00
    """
    # day -> list of (open_day, open_time, close_day, close_time)
    day_map: Dict[int, List[Tuple[int, str, int, str]]] = {d: [] for d in range(7)}

    for p in bh:
        o = p.get("open", {})
        c = p.get("close", {})
        od = int(o.get("day"))
        cd = int(c.get("day"))
        ot = str(o.get("time")).zfill(4)
        ct = str(c.get("time")).zfill(4)
        day_map[od].append((od, ot, cd, ct))

    def interval_text(od: int, ot: str, cd: int, ct: str) -> str:
        ot2 = hhmm_to_colon(ot)
        ct2 = hhmm_to_colon(ct)
        if od == cd:
            return f"{ot2}–{ct2}"
        # 跨日（少見，但保底）
        return f"{ot2}–隔天{ct2}"

    # 每一天做一個「signature」，用來把連續相同營業時間的日子合併成區間
    sigs: List[str] = []
    for d in range(7):
        intervals = day_map.get(d, [])
        if not intervals:
            sigs.append("CLOSED")
            continue
        # 依開始時間排序
        intervals = sorted(intervals, key=lambda x: x[1])
        sig = "、".join(interval_text(*it) for it in intervals)
        sigs.append(sig)

    # 合併連續相同 signature 的日子
    parts: List[str] = []
    i = 0
    while i < 7:
        sig = sigs[i]
        j = i
        while j + 1 < 7 and sigs[j + 1] == sig:
            j += 1

        day_label = DAY_NAMES[i] if i == j else f"{DAY_NAMES[i]}～{DAY_NAMES[j]}"
        if sig == "CLOSED":
            parts.append(f"{day_label} 公休")
        else:
            parts.append(f"{day_label} {sig}")

        i = j + 1

    return "；".join(parts)

def hhmm_to_minutes(hhmm: str) -> int:
    s = str(hhmm).zfill(4)
    return int(s[:2]) * 60 + int(s[2:])

def minutes_to_hhmm(minutes: int) -> str:
    minutes = max(0, int(minutes))
    hh = minutes // 60
    mm = minutes % 60
    return f"{hh:02d}{mm:02d}"

def summarize_resources(res: List[Dict[str, Any]]) -> str:
    # res: [{"party_size":4,"spots_total":2}, ...]
    if not res:
        return "（無）"
    items = []
    for r in sorted(res, key=lambda x: int(x.get("party_size", 0))):
        ps = int(r["party_size"])
        st = int(r["spots_total"])
        items.append(f"{ps} 人桌 {st} 張")
    return "、".join(items)

def compute_booking_hours_json(business_hours_json: List[Dict[str, Any]], duration_sec: int) -> List[Dict[str, Any]]:
    """
    建議線上訂位可訂入座時段：
    - 以營業時段為基礎
    - 最後可訂入座時間 = close_time - 用餐時長
    """
    dur_min = max(0, int(duration_sec) // 60)
    out: List[Dict[str, Any]] = []

    for p in business_hours_json:
        o = p["open"]; c = p["close"]
        od = int(o["day"]); cd = int(c["day"])
        ot = str(o["time"]).zfill(4)
        ct = str(c["time"]).zfill(4)

        # 跨日先保底照原時段（少見）
        if od != cd:
            out.append({"open": {"day": od, "time": ot}, "close": {"day": cd, "time": ct}})
            continue

        otm = hhmm_to_minutes(ot)
        ctm = hhmm_to_minutes(ct)
        last_start = ctm - dur_min

        # 保底：如果扣完小於等於開始，代表時段太短；就不扣（至少不會變成奇怪的 08:00–08:00）
        if last_start <= otm:
            last_start = ctm

        out.append({"open": {"day": od, "time": ot}, "close": {"day": od, "time": minutes_to_hhmm(last_start)}})

    return out

def compute_peak_online_resources(total_resources: List[Dict[str, Any]], ratio: float, peak_strategy: str) -> List[Dict[str, Any]]:
    """
    建議最忙時段線上開放桌數（按桌型）：
    - no_online: 全部 0
    - 其他：按 ratio 分配，每種桌型至少 1 張（如果 ratio>0 且該桌型總張數>0）
    """
    if peak_strategy == "no_online":
        return [{"party_size": int(r["party_size"]), "spots_total": 0} for r in total_resources]

    ratio = float(ratio)
    out: List[Dict[str, Any]] = []
    for r in total_resources:
        ps = int(r["party_size"])
        tot = int(r["spots_total"])
        if tot <= 0:
            out.append({"party_size": ps, "spots_total": 0})
            continue

        n = int(round(tot * ratio))
        if ratio > 0 and n == 0:
            n = 1
        n = max(0, min(n, tot))
        out.append({"party_size": ps, "spots_total": n})

    return out

def clamp_online_resources(online_res: List[Dict[str, Any]], total_res: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    把線上桌數限制在 [0, 該桌型總桌數]，並只保留存在的桌型
    """
    tot_map = {int(r["party_size"]): int(r["spots_total"]) for r in total_res}
    out_map = {ps: 0 for ps in tot_map}

    for r in online_res:
        try:
            ps = int(r["party_size"])
            st = int(r["spots_total"])
        except Exception:
            continue
        if ps not in tot_map:
            continue
        st = max(0, min(st, tot_map[ps]))
        out_map[ps] = st

    return [{"party_size": ps, "spots_total": out_map[ps]} for ps in sorted(out_map.keys())]

def merge_online_resources(base: List[Dict[str, Any]], patch: List[Dict[str, Any]], total_res: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    允許使用者只改某些桌型，其餘沿用原建議
    """
    base_map = {int(r["party_size"]): int(r["spots_total"]) for r in base}
    for r in patch:
        try:
            ps = int(r["party_size"])
            st = int(r["spots_total"])
        except Exception:
            continue
        base_map[ps] = st

    merged = [{"party_size": ps, "spots_total": base_map.get(ps, 0)} for ps in sorted({int(r["party_size"]) for r in total_res})]
    return clamp_online_resources(merged, total_res)

def main():
    # 初始 state：store_id 可為 None，符合 validator
    state: Dict[str, Any] = {
        "store_id": None,
        "strategy": {
            # Google Reserve 前提固定值（如果你後端需要，也可放這裡）
            # allow_same_day: false, advance_days_min: 1 (不在 validator schema，先不放)
        }
    }

    print("✅ Onboarding FSM Agent 已啟動（輸入 exit 離開）\n")

    # Step 1：店名（必須第一句只問這個）
    while True:
        print("🤖 Agent：\n請問店名是什麼？")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return
        patch = llm_extract("store_name", user_in, state)
        if "store_name" in patch and isinstance(patch["store_name"], str) and patch["store_name"].strip():
            merge_patch(state, {"store_name": patch["store_name"].strip()})
            break
        print("🤖 Agent：\n我沒有聽清楚店名，可以再說一次嗎？\n")

    # Step 2：桌型 resources
    while True:
        print("\n🤖 Agent：\n店裡大概有哪些桌型呢？例如：2 人桌幾張、4 人桌幾張，可以一次告訴我。")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return
        patch = llm_extract("resources", user_in, state)
        res = patch.get("resources")
        ok, msg = validate_resources(res)
        if ok:
            merge_patch(state, {"resources": res})
            break
        print(f"🤖 Agent：\n我需要的是像「4人桌5張、6人桌2張」這樣的資訊，可以再講一次嗎？（{msg}）\n")

    # Step 3：用餐時間 duration_sec（用選項固定）
    while True:
        print("\n🤖 Agent：\n一般來說，一組客人用餐大約多久？\nA. 一小時左右\nB. 一個半小時\nC. 兩小時左右")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return

        c = normalize_choice(user_in)
        # 優先用規則處理（更穩），LLM 當備援
        if c in ("a", "1", "60", "60分鐘", "一小時", "1小時"):
            merge_patch(state, {"duration_sec": 3600})
            break
        if c in ("b", "1.5", "90", "90分鐘", "一個半小時", "1個半小時"):
            merge_patch(state, {"duration_sec": 5400})
            break
        if c in ("c", "2", "120", "120分鐘", "兩小時", "2小時"):
            merge_patch(state, {"duration_sec": 7200})
            break

        patch = llm_extract("duration_sec", user_in, state)
        dur = patch.get("duration_sec")
        if isinstance(dur, int) and dur in (3600, 5400, 7200):
            merge_patch(state, {"duration_sec": dur})
            break

        print("🤖 Agent：\n我這邊只需要 A / B / C 三選一即可～再選一次：\n")

    # Step 4：營業時間 business_hours_json
    while True:
        print("\n🤖 Agent：\n你們平常的營業時間大概是什麼時候？例如：每天早上八點到晚上五點。")
        user_in = input("\n你：").strip()
        ...
        patch = llm_extract("business_hours_json", user_in, state)
        bh = patch.get("business_hours_json")
        ok, msg = validate_business_hours_json(bh)
        if ok:
            summary = summarize_business_hours(bh)
            print(f"\n🤖 Agent：\n我整理一下營業時間：{summary}\n這樣對嗎？\nA. 對\nB. 不對，需要修改")
            ans = input("\n你：").strip()
            c = normalize_choice(ans)
            if c in ("a", "對", "是", "yes", "y"):
                merge_patch(state, {"business_hours_json": bh})
                break
            else:
                print("\n🤖 Agent：\n好的，那你再說一次營業時間，我重新整理。")
                continue
        print(f"🤖 Agent：\n我需要清楚的「幾點到幾點」以及是否有公休日，例如：週一到週六 08:00–17:00，週日公休。\n（{msg}）\n")

    # Step 5：併桌 can_merge_tables（策略內）
    while True:
        print("\n🤖 Agent：\n如果人數比較多，現場可以把桌子併起來使用嗎？\nA. 可以\nB. 不行")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return

        c = normalize_choice(user_in)
        if c in ("a", "可以", "可", "yes", "y"):
            merge_patch(state, {"strategy": {"can_merge_tables": True}})
            break
        if c in ("b", "不行", "否", "no", "n"):
            merge_patch(state, {"strategy": {"can_merge_tables": False}})
            # 若不能併桌，max_party_size 就取桌型最大 party_size
            max_size = max(int(r["party_size"]) for r in state["resources"])
            merge_patch(state, {"strategy": {"max_party_size": max_size}})
            break

        patch = llm_extract("merge_tables", user_in, state)
        s = patch.get("strategy", {})
        if isinstance(s, dict) and isinstance(s.get("can_merge_tables"), bool):
            merge_patch(state, {"strategy": {"can_merge_tables": s["can_merge_tables"]}})
            if s["can_merge_tables"] is False:
                max_size = max(int(r["party_size"]) for r in state["resources"])
                merge_patch(state, {"strategy": {"max_party_size": max_size}})
            break

        print("🤖 Agent：\n我只需要選 A 或 B 就好～再選一次：\n")

    # Step 5-2：最大接待人數 max_party_size（若可以併桌才問）
    if state["strategy"].get("can_merge_tables") is True:
        while True:
            print("\n🤖 Agent：\n最多大概可以接到幾個人一起用餐？例如 8 人、10 人、12 人。")
            user_in = input("\n你：").strip()
            if user_in.lower() in ("exit", "quit"):
                print("Bye")
                return
            # 先用規則抓數字
            m = re.search(r"(\d+)", user_in)
            if m:
                n = int(m.group(1))
                if n > 0:
                    merge_patch(state, {"strategy": {"max_party_size": n}})
                    break
            patch = llm_extract("max_party_size", user_in, state)
            s = patch.get("strategy", {})
            if isinstance(s, dict) and isinstance(s.get("max_party_size"), int) and s["max_party_size"] > 0:
                merge_patch(state, {"strategy": {"max_party_size": s["max_party_size"]}})
                break
            print("🤖 Agent：\n我需要一個人數（例如 8 / 10 / 12），再說一次好嗎？\n")

    # Step 6：線上訂位角色 online_role
    while True:
        print("\n🤖 Agent：\n你希望線上訂位在店裡扮演什麼角色？\nA. 主要方式（希望大多數客人先訂位）\nB. 輔助工具（只想避免忙的時候太亂）\nC. 少量開放（主要還是現場）")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return

        c = normalize_choice(user_in)
        if c in ("a", "主要", "主力"):
            merge_patch(state, {"strategy": {"online_role": "primary"}})
            break
        if c in ("b", "輔助", "工具"):
            merge_patch(state, {"strategy": {"online_role": "assistant"}})
            break
        if c in ("c", "少量", "現場"):
            merge_patch(state, {"strategy": {"online_role": "minimal"}})
            break

        patch = llm_extract("online_role", user_in, state)
        s = patch.get("strategy", {})
        if isinstance(s, dict) and s.get("online_role") in ("primary", "assistant", "minimal"):
            merge_patch(state, {"strategy": {"online_role": s["online_role"]}})
            break

        print("🤖 Agent：\n我只需要選 A / B / C 其中一個～再選一次：\n")

    # Step 7：最忙時段 peak_periods
    while True:
        print("\n🤖 Agent：\n你覺得店裡最容易忙起來的是哪一段？\nA. 平日中午\nB. 平日晚餐\nC. 假日中午\nD. 假日晚餐\nE. 不太確定（交給系統）")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return

        c = normalize_choice(user_in)
        if c in ("a", "平日中午"):
            merge_patch(state, {"strategy": {"peak_periods": ["weekday_lunch"]}})
            break
        if c in ("b", "平日晚餐"):
            merge_patch(state, {"strategy": {"peak_periods": ["weekday_dinner"]}})
            break
        if c in ("c", "假日中午"):
            merge_patch(state, {"strategy": {"peak_periods": ["weekend_brunch"]}})
            break
        if c in ("d", "假日晚餐"):
            merge_patch(state, {"strategy": {"peak_periods": ["weekend_dinner"]}})
            break
        if c in ("e", "不確定", "交給系統", "隨便"):
            merge_patch(state, {"strategy": {"peak_periods": ["weekend_dinner"]}})
            break

        patch = llm_extract("peak_periods", user_in, state)
        s = patch.get("strategy", {})
        if isinstance(s, dict) and isinstance(s.get("peak_periods"), list):
            # validate allowed values
            allowed = {"weekday_lunch","weekday_dinner","weekend_brunch","weekend_dinner"}
            if all(x in allowed for x in s["peak_periods"]) and len(s["peak_periods"]) > 0:
                merge_patch(state, {"strategy": {"peak_periods": s["peak_periods"]}})
                break

        print("🤖 Agent：\n我只需要選 A / B / C / D / E 其中一個～再選一次：\n")

    # Step 8：忙時線上配額 peak_online_quota_ratio
    while True:
        print("\n🤖 Agent：\n在最忙的時段，你希望線上訂位大概佔多少位置？\nA. 大部分（約 80%）\nB. 一半左右（約 50%）\nC. 少量即可（約 20%）")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return

        c = normalize_choice(user_in)
        if c in ("a", "80", "80%", "大部分"):
            merge_patch(state, {"strategy": {"peak_online_quota_ratio": 0.8}})
            break
        if c in ("b", "50", "50%", "一半"):
            merge_patch(state, {"strategy": {"peak_online_quota_ratio": 0.5}})
            break
        if c in ("c", "20", "20%", "少量"):
            merge_patch(state, {"strategy": {"peak_online_quota_ratio": 0.2}})
            break

        patch = llm_extract("peak_online_quota_ratio", user_in, state)
        s = patch.get("strategy", {})
        if isinstance(s, dict) and s.get("peak_online_quota_ratio") in (0.8, 0.5, 0.2):
            merge_patch(state, {"strategy": {"peak_online_quota_ratio": s["peak_online_quota_ratio"]}})
            break

        print("🤖 Agent：\n我只需要選 A / B / C 其中一個～再選一次：\n")

    # Step 9：忙時策略 peak_strategy
    while True:
        print("\n🤖 Agent：\n在最忙的時候，你比較希望怎麼做？\nA. 先讓線上訂位進來，比較好控制\nB. 留比較多位置給現場客\nC. 忙的時候就不開線上訂位")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return

        c = normalize_choice(user_in)
        if c in ("a", "先讓線上", "好控制"):
            merge_patch(state, {"strategy": {"peak_strategy": "online_first"}})
            break
        if c in ("b", "留給現場", "現場客"):
            merge_patch(state, {"strategy": {"peak_strategy": "walkin_first"}})
            break
        if c in ("c", "不開", "關掉", "no"):
            merge_patch(state, {"strategy": {"peak_strategy": "no_online"}})
            break

        patch = llm_extract("peak_strategy", user_in, state)
        s = patch.get("strategy", {})
        if isinstance(s, dict) and s.get("peak_strategy") in ("online_first", "walkin_first", "no_online"):
            merge_patch(state, {"strategy": {"peak_strategy": s["peak_strategy"]}})
            break

        print("🤖 Agent：\n我只需要選 A / B / C 其中一個～再選一次：\n")

    # Step 10：no-show 容忍 no_show_tolerance
    while True:
        print("\n🤖 Agent：\n如果 10 組線上訂位，有 1～2 組沒來，你可以接受嗎？\nA. 不太能接受\nB. 勉強可以\nC. 可以接受")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return

        c = normalize_choice(user_in)
        if c in ("a", "不太能", "不能"):
            merge_patch(state, {"strategy": {"no_show_tolerance": "low"}})
            break
        if c in ("b", "勉強", "還行"):
            merge_patch(state, {"strategy": {"no_show_tolerance": "medium"}})
            break
        if c in ("c", "可以", "能接受"):
            merge_patch(state, {"strategy": {"no_show_tolerance": "high"}})
            break

        patch = llm_extract("no_show_tolerance", user_in, state)
        s = patch.get("strategy", {})
        if isinstance(s, dict) and s.get("no_show_tolerance") in ("low", "medium", "high"):
            merge_patch(state, {"strategy": {"no_show_tolerance": s["no_show_tolerance"]}})
            break

        print("🤖 Agent：\n我只需要選 A / B / C 其中一個～再選一次：\n")

        # =========================
    # Step 11：AI 建議 → A 接受 / B 修改
    # 建議內容：booking_hours_json（可訂入座時間）＋ peak_online_resources（忙時線上桌數）
    # =========================

    # 先用「可控的規則」算一份保底建議（一定合法）
    fallback_booking = compute_booking_hours_json(state["business_hours_json"], state["duration_sec"])
    ratio = float(state["strategy"].get("peak_online_quota_ratio", 0.5))
    peak_strategy_local = state["strategy"]["peak_strategy"]
    fallback_peak_online = compute_peak_online_resources(state["resources"], ratio, peak_strategy_local)

    # 再嘗試用 AI 產出建議 patch（不合法就用 fallback）
    ai_patch = llm_extract(
        "recommendation_patch",
        "請根據已知資訊提出「線上訂位開放時間」與「最忙時段線上可訂桌數」的建議。",
        state
    )

    # 取 booking_hours_json
    booking_hours = ai_patch.get("booking_hours_json", fallback_booking)
    ok_bh, _ = validate_business_hours_json(booking_hours)
    if not ok_bh:
        booking_hours = fallback_booking

    # 取 peak_strategy / ratio（可選）
    ai_strategy = ai_patch.get("strategy", {}) if isinstance(ai_patch.get("strategy"), dict) else {}
    if ai_strategy.get("peak_strategy") in ("online_first", "walkin_first", "no_online"):
        peak_strategy_local = ai_strategy["peak_strategy"]
    if ai_strategy.get("peak_online_quota_ratio") in (0.8, 0.5, 0.2, 0.0):
        ratio = float(ai_strategy["peak_online_quota_ratio"])

    # 取 peak_online_resources
    peak_online = ai_strategy.get("peak_online_resources", fallback_peak_online)
    ok_res, _ = validate_resources(peak_online)
    if not ok_res:
        peak_online = compute_peak_online_resources(state["resources"], ratio, peak_strategy_local)
    peak_online = clamp_online_resources(peak_online, state["resources"])

    # 若策略是 no_online，保守地把忙時線上桌數歸零（一致性）
    if peak_strategy_local == "no_online":
        peak_online = compute_peak_online_resources(state["resources"], ratio, "no_online")
        ratio = 0.0

    # 進入「顯示建議 → 接受/修改」迴圈
    while True:
        print("\n🤖 Agent：\n我根據你剛剛提供的資訊，整理了一個線上訂位建議，給你快速確認：")
        print(f"1) 線上訂位開放時間（最後可訂入座時間）：{summarize_business_hours(booking_hours)}")

        if peak_strategy_local == "no_online":
            print("2) 在你最忙的時段：建議不開放線上訂位（全部留給現場）。")
        else:
            print(f"2) 在你最忙的時段：建議線上可訂桌數約為：{summarize_resources(peak_online)}")
            print(f"   （線上佔比參考：{int(ratio*100)}%）")

        print("\nA. 直接採用這個建議\nB. 我想調整")
        ans = input("\n你：").strip()
        c = normalize_choice(ans)

        if c in ("a", "ok", "對", "接受", "好", "yes", "y"):
            # 確認採用：寫回 state
            merge_patch(state, {
                "booking_hours_json": booking_hours,
                "strategy": {
                    "peak_strategy": peak_strategy_local,
                    "peak_online_quota_ratio": ratio,
                    "peak_online_resources": peak_online
                }
            })
            break

        # 修改模式
        print("\n🤖 Agent：\n沒問題～你想怎麼調整？你可以直接說：\n"
              "- 例如「線上訂位時間改成每天 09:00–16:00」\n"
              "- 或「忙時 4 人桌 2 張、6 人桌 1 張」\n"
              "- 或「忙的時候也要開線上訂位」/「忙的時候不開」\n"
              "- 也可以一起說")
        mod = input("\n你：").strip()
        if mod.lower() in ("exit", "quit"):
            print("Bye")
            return

        mod_patch = llm_extract(
            "recommendation_patch",
            mod,
            {
                **state,
                "booking_hours_json": booking_hours,
                "strategy": {**state["strategy"], "peak_online_resources": peak_online}
            }
        )

        # 套用 booking_hours_json（若有）
        if "booking_hours_json" in mod_patch:
            bh2 = mod_patch["booking_hours_json"]
            ok_bh2, msg_bh2 = validate_business_hours_json(bh2)
            if ok_bh2:
                booking_hours = bh2
            else:
                print(f"🤖 Agent：\n我沒有讀懂你要改的「時間」（{msg_bh2}），這部分先不改。")

        # 套用 strategy（若有）
        s2 = mod_patch.get("strategy", {}) if isinstance(mod_patch.get("strategy"), dict) else {}

        if s2.get("peak_strategy") in ("online_first", "walkin_first", "no_online"):
            peak_strategy_local = s2["peak_strategy"]
            if peak_strategy_local == "no_online":
                ratio = 0.0
                peak_online = compute_peak_online_resources(state["resources"], ratio, "no_online")

        if s2.get("peak_online_quota_ratio") in (0.8, 0.5, 0.2, 0.0):
            ratio = float(s2["peak_online_quota_ratio"])
            if peak_strategy_local == "no_online":
                ratio = 0.0

        if "peak_online_resources" in s2:
            pr = s2["peak_online_resources"]
            ok_pr, msg_pr = validate_resources(pr)
            if ok_pr:
                peak_online = merge_online_resources(peak_online, pr, state["resources"])
            else:
                print(f"🤖 Agent：\n我沒有讀懂你要改的「桌數」（{msg_pr}），這部分先不改。")

        # 若 peak_strategy 不是 no_online，但使用者改到全 0，也允許；反之若 no_online 卻 >0，這裡保守歸零
        if peak_strategy_local == "no_online":
            peak_online = compute_peak_online_resources(state["resources"], ratio, "no_online")
            ratio = 0.0

    # 由程式推導 goal_type（避免模型亂填）
    online_role = state["strategy"]["online_role"]
    if online_role == "primary":
        goal_type = "fill_seats"
    elif online_role == "assistant":
        goal_type = "control_queue"
    else:
        goal_type = "keep_walkin"
    merge_patch(state, {"strategy": {"goal_type": goal_type}})

    # 確保 can_merge_tables/max_party_size 一定存在（保險）
    state["strategy"].setdefault("can_merge_tables", True)
    state["strategy"].setdefault("max_party_size", 8)

    # capacity_hint
    cap = capacity_hint_from_resources(state["resources"])
    merge_patch(state, {"capacity_hint": cap})

    # 組 FINAL_JSON（完全符合你的 validator schema）
    final = {
        "store_id": state.get("store_id", None),
        "store_name": state["store_name"],
        "capacity_hint": state["capacity_hint"],
        "resources": state["resources"],
        "duration_sec": state["duration_sec"],
        "business_hours_json": state["business_hours_json"],
        "strategy": state["strategy"],
    }

    ok, reason = validate_final_json(final)
    if not ok:
        print("\n❌ FINAL_JSON 本地驗證失敗（代表抽取或規則有 bug，需要修）")
        print("原因：", reason)
        print(json.dumps(final, ensure_ascii=False, indent=2))
        return

    print("\n✅ FINAL_JSON 驗證通過（可直接送後端）")
    print("FINAL_JSON:", json.dumps(final, ensure_ascii=False))

if __name__ == "__main__":
    main()
