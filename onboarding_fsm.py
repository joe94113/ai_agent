import json
import re
import math
import requests
from typing import Dict, Any, Optional, Tuple, List

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "llama3.1:8b-instruct-q4_K_M"

# =========================
# Simulation Engine (Thesis Core Contribution)
# =========================

class RestaurantSimulator:
    def __init__(self, capacity, duration_min, online_ratio, no_show_prob):
        self.capacity = capacity
        self.duration_min = duration_min
        self.online_quota = int(capacity * online_ratio)
        self.walkin_quota = capacity - self.online_quota
        self.no_show_prob = no_show_prob
        
    def run_one_evening(self):
        """
        模擬一個忙碌晚上的營運 (例如 4 小時)
        假設：需求通常大於供給 (因為是 Peak Hour)
        """
        # 1. 產生需求 (Demand Generation)
        # 假設該時段潛在客流量是座位數的 1.5 倍 ~ 2.5 倍 (Poisson 分佈概念簡化)
        potential_online_demand = int(random.uniform(0.8, 1.5) * self.capacity)
        potential_walkin_demand = int(random.uniform(0.5, 1.2) * self.capacity)

        # 2. 處理線上訂位 (Online Booking Process)
        # 訂位成功數 = min(需求, 配額)
        booked_seats = min(potential_online_demand, self.online_quota)
        
        # 3. 處理 No-Show (Stochastic Event)
        actual_arrivals_online = 0
        for _ in range(booked_seats):
            if random.random() > self.no_show_prob:
                actual_arrivals_online += 1
        
        # 4. 處理現場客 (Walk-in Process)
        # 現場客可以用：原本保留給現場的 + 線上 No-show 空出來的
        available_for_walkin = self.capacity - actual_arrivals_online
        walkin_seated = min(potential_walkin_demand, available_for_walkin)
        
        # 5. 計算結果 metrics
        total_seated = actual_arrivals_online + walkin_seated
        utilization = total_seated / self.capacity
        
        # 拒絕掉的客人 (Lost Opportunity)
        rejected_online = max(0, potential_online_demand - self.online_quota)
        rejected_walkin = max(0, potential_walkin_demand - available_for_walkin)
        total_rejected = rejected_online + rejected_walkin
        
        return {
            "utilization": utilization,
            "lost_customers": total_rejected,
            "empty_seats": self.capacity - total_seated
        }

def run_simulation_report(state: Dict[str, Any], runs=100) -> str:
    """
    執行 N 次模擬並產生分析報告
    """
    # 從 state 提取參數
    cap = state.get("capacity_hint", 20)
    dur = state.get("duration_sec", 5400) // 60
    
    # 解析策略參數
    strat = state.get("strategy", {})
    ratio = float(strat.get("peak_online_quota_ratio", 0.5))
    peak_strat = strat.get("peak_strategy", "online_first")
    
    # 根據 peak_strategy 微調
    if peak_strat == "no_online":
        ratio = 0.0
    elif peak_strat == "walkin_first" and ratio > 0.3:
        ratio = 0.3 # 強制降低
        
    # 解析 No-show 機率
    ns_tol = strat.get("no_show_tolerance", "medium")
    ns_prob = {"low": 0.05, "medium": 0.15, "high": 0.30}.get(ns_tol, 0.15)

    sim = RestaurantSimulator(cap, dur, ratio, ns_prob)
    
    # 執行蒙地卡羅模擬
    results = [sim.run_one_evening() for _ in range(runs)]
    
    # 統計分析
    avg_util = statistics.mean([r["utilization"] for r in results])
    avg_lost = statistics.mean([r["lost_customers"] for r in results])
    avg_empty = statistics.mean([r["empty_seats"] for r in results])
    
    # 產生建議文本 (Rule-based Advisory)
    advice = ""
    if avg_util < 0.7:
        advice = "⚠️ 警告：座位利用率偏低，建議「增加線上訂位配額」以鎖定客源。"
    elif avg_lost > cap * 0.5:
        advice = "⚠️ 警告：拒絕客人數過多，代表需求外溢。建議啟用「候補名單」功能或嚴格限制用餐時間。"
    elif ns_prob > 0.2 and ratio > 0.6:
        advice = "⚠️ 風險：您的 No-show 容忍度高且線上佔比高，可能導致因 No-show 產生大量臨時空桌，建議降低線上比例。"
    else:
        advice = "✅ 分析：目前的配置在模擬中表現平衡，能有效兼顧翻桌率與客源。"

    report = (
        f"\n📊 【AI 營運模擬報告 (基於 {runs} 次蒙地卡羅模擬)】\n"
        f"------------------------------------------------\n"
        f"• 預估平均座位利用率：{avg_util:.1%}\n"
        f"• 預估每晚流失客數　：約 {int(avg_lost)} 人 (因客滿或配額不足)\n"
        f"• 預估每晚閒置空位　：約 {int(avg_empty)} 位 (因 No-show 或配額限制)\n"
        f"------------------------------------------------\n"
        f"💡 {advice}\n"
    )
    return report

# =========================
# Validators
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
    # 總座位數 = sum(party_size * spots_total)
    return max(1, sum(int(r["party_size"]) * int(r["spots_total"]) for r in resources))


# =========================
# Human-readable summaries
# =========================

DAY_NAMES = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

def hhmm_to_colon(hhmm: str) -> str:
    s = str(hhmm).zfill(4)
    return f"{s[:2]}:{s[2:]}"

def summarize_business_hours(bh: List[Dict[str, Any]]) -> str:
    """
    例：週一～週日 08:00–17:00；週日 公休
    支援同一天多段：11:00–14:00、17:00–21:00
    """
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
        return f"{ot2}–隔天{ct2}"

    sigs: List[str] = []
    for d in range(7):
        intervals = day_map.get(d, [])
        if not intervals:
            sigs.append("CLOSED")
            continue
        intervals = sorted(intervals, key=lambda x: x[1])
        sig = "、".join(interval_text(*it) for it in intervals)
        sigs.append(sig)

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

def summarize_resources(res: List[Dict[str, Any]]) -> str:
    if not res:
        return "（無）"
    items = []
    for r in sorted(res, key=lambda x: int(x.get("party_size", 0))):
        ps = int(r["party_size"])
        st = int(r["spots_total"])
        items.append(f"{ps} 人桌 {st} 張")
    return "、".join(items)


# =========================
# Recommendation algorithm (slot-based / capacity constraint)
# =========================

def hhmm_to_minutes(hhmm: str) -> int:
    s = str(hhmm).zfill(4)
    return int(s[:2]) * 60 + int(s[2:])

def minutes_to_hhmm(minutes: int) -> str:
    minutes = max(0, int(minutes))
    hh = minutes // 60
    mm = minutes % 60
    return f"{hh:02d}{mm:02d}"

def compute_booking_hours_json(business_hours_json: List[Dict[str, Any]], duration_sec: int) -> List[Dict[str, Any]]:
    """
    線上可訂入座時間 = 營業時段內，最後可訂入座時間 close - duration
    """
    dur_min = max(0, int(duration_sec) // 60)
    out: List[Dict[str, Any]] = []

    for p in business_hours_json:
        o = p["open"]; c = p["close"]
        od = int(o["day"]); cd = int(c["day"])
        ot = str(o["time"]).zfill(4)
        ct = str(c["time"]).zfill(4)

        # 跨日（少見）先原樣
        if od != cd:
            out.append({"open": {"day": od, "time": ot}, "close": {"day": cd, "time": ct}})
            continue

        otm = hhmm_to_minutes(ot)
        ctm = hhmm_to_minutes(ct)
        last_start = ctm - dur_min
        # 若時段太短，至少讓 last_start 不小於 open（可能變成只剩一個可訂點）
        last_start = max(otm, last_start)

        out.append({"open": {"day": od, "time": ot}, "close": {"day": od, "time": minutes_to_hhmm(last_start)}})

    return out

def typical_party_size_from_resources(resources: List[Dict[str, Any]]) -> int:
    """
    用 spots_total 加權中位數推估典型人數（穩定、可解釋）
    """
    items = []
    total_w = 0
    for r in resources:
        ps = int(r["party_size"])
        w = int(r["spots_total"])
        if w <= 0:
            continue
        items.append((ps, w))
        total_w += w
    if not items:
        return max(int(r["party_size"]) for r in resources)

    items.sort(key=lambda x: x[0])
    cum = 0
    half = (total_w + 1) / 2
    for ps, w in items:
        cum += w
        if cum >= half:
            return ps
    return items[-1][0]

def compute_peak_online_policy(
    capacity_hint: int,
    resources: List[Dict[str, Any]],
    duration_sec: int,
    ratio: float,
    peak_strategy: str,
    goal_type: str,
    no_show_tolerance: str,
    slot_minutes: int = 30,
) -> Dict[str, int]:
    """
    slot-based admission control：
    - seat_budget：忙時線上座位預算（capacity * ratio，再按目標與放鳥容忍微調）
    - party_limit_per_slot：每個 slot 最多新增幾組線上訂位（粗估）
    """
    slot_minutes = int(slot_minutes)
    slot_minutes = max(10, min(slot_minutes, 120))

    typical_ps = typical_party_size_from_resources(resources)
    duration_min = duration_sec / 60.0
    k = max(1, math.ceil(duration_min / slot_minutes))  # 一組客人佔用 slot 數

    if peak_strategy == "no_online":
        return {
            "peak_slot_minutes": slot_minutes,
            "peak_online_seat_budget": 0,
            "peak_online_party_limit_per_slot": 0,
            "typical_party_size": int(typical_ps),
            "duration_slots": int(k),
        }

    base = int(math.floor(capacity_hint * float(ratio)))

    goal_factor = {"fill_seats": 1.05, "control_queue": 1.00, "keep_walkin": 0.80}.get(goal_type, 1.00)
    ns_factor = {"low": 0.90, "medium": 1.00, "high": 1.05}.get(no_show_tolerance, 1.00)

    seat_budget = int(math.floor(base * goal_factor * ns_factor))
    seat_budget = max(0, min(seat_budget, capacity_hint))

    denom = max(1, typical_ps * k)
    party_limit = seat_budget // denom
    if seat_budget > 0 and party_limit == 0:
        party_limit = 1

    return {
        "peak_slot_minutes": slot_minutes,
        "peak_online_seat_budget": int(seat_budget),
        "peak_online_party_limit_per_slot": int(party_limit),
        "typical_party_size": int(typical_ps),
        "duration_slots": int(k),
    }


# =========================
# LLM Extractor (只抽 JSON，不聊天)
# =========================

EXTRACTOR_SYSTEM = r"""
你是一個「資料抽取器」，只負責把使用者回答轉成 JSON patch。
你必須只輸出一段 JSON object（不要文字、不要解釋）。
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
        "format": "json",
        "options": {
            "temperature": 0.2,
            "top_p": 0.9
        }
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["message"]["content"]

def extract_first_json_object_str(text: str) -> Optional[str]:
    """
    從模型輸出中抓第一個完整 JSON object 字串（更耐髒輸出）
    """
    if not text:
        return None
    s = text.strip().strip("`").strip()
    start = s.find("{")
    if start < 0:
        return None

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i+1]
    return None

def parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    obj_str = extract_first_json_object_str(text)
    if not obj_str:
        return None
    try:
        obj = json.loads(obj_str)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None

def llm_extract(step_name: str, user_text: str, state: Dict[str, Any]) -> Dict[str, Any]:
    if step_name == "store_name":
        name = (user_text or "").strip()
        return {"store_name": name} if name else {}
    schema_guide = {
        "store_name": r'輸出：{"store_name": "<非空字串>"}',
        "resources": r'輸出：{"resources":[{"party_size":4,"spots_total":5},{"party_size":6,"spots_total":2}]}  party_size/spots_total 都是整數',
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
        "peak_online_quota_ratio": r'輸出：{"strategy":{"peak_online_quota_ratio":0.5}}（0.8/0.5/0.2/0.0 其一）',
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

    "peak_slot_minutes": 30,
    "peak_online_seat_budget": 20,
    "peak_online_party_limit_per_slot": 2
  }
}

規則：
- booking_hours_json 格式與 business_hours_json 相同（day 0~6；time 為 4 位 HHMM 字串）
- peak_slot_minutes / peak_online_seat_budget / peak_online_party_limit_per_slot 必須是整數（>=0）
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
# FSM Orchestrator
# =========================

SIMPLIFY_TRIGGERS = {"聽不懂", "不用了", "隨便", "你幫我決定"}

def normalize_choice(text: str) -> str:
    t = text.strip().lower()
    t = t.replace("選項", "").replace(" ", "")
    return t

def is_simplify_trigger(text: str) -> bool:
    t = text.strip()
    return t in SIMPLIFY_TRIGGERS

def apply_simplified_strategy_defaults(state: Dict[str, Any]) -> None:
    # 只針對策略（因為 resources/business hours/duration 仍必須取得才能輸出 FINAL_JSON）
    merge_patch(state, {
        "strategy": {
            "goal_type": "control_queue",
            "online_role": "assistant",
            "peak_periods": ["weekend_dinner"],
            "peak_strategy": "online_first",
            "peak_online_quota_ratio": 0.5,
            "no_show_tolerance": "medium",
            "can_merge_tables": True,
            "max_party_size": 8,
        }
    })

def clamp_int(v: Any, lo: int, hi: int) -> int:
    try:
        vv = int(v)
    except Exception:
        return lo
    return max(lo, min(vv, hi))

def main():
    state: Dict[str, Any] = {
        "store_id": None,
        "strategy": {}
    }

    print("✅ Onboarding FSM Agent 已啟動（輸入 exit 離開）\n")

    # Step 1：店名
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
        if c in ("a", "1", "60", "60分鐘", "一小時", "1小時"):
            merge_patch(state, {"duration_sec": 3600})
            break
        if c in ("b", "1.5", "90", "90分鐘", "一個半小時", "1個半小時"):
            merge_patch(state, {"duration_sec": 5400})
            break
        if c in ("c", "2", "120", "120分鐘", "兩小時", "2小時"):
            merge_patch(state, {"duration_sec": 7200})
            break

        print("🤖 Agent：\n我這邊只需要 A / B / C 三選一即可～再選一次：\n")

    # Step 4：營業時間 business_hours_json（含摘要確認）
    while True:
        print("\n🤖 Agent：\n你們平常的營業時間大概是什麼時候？例如：每天早上八點到晚上五點。")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return

        patch = llm_extract("business_hours_json", user_in, state)
        bh = patch.get("business_hours_json")
        ok, msg = validate_business_hours_json(bh)
        if ok:
            summary = summarize_business_hours(bh)
            print(f"\n🤖 Agent：\n我整理一下營業時間：{summary}\n這樣對嗎？\nA. 對\nB. 不對，需要修改")
            ans = input("\n你：").strip()
            if ans.lower() in ("exit", "quit"):
                print("Bye")
                return
            cc = normalize_choice(ans)
            if cc in ("a", "對", "是", "yes", "y"):
                merge_patch(state, {"business_hours_json": bh})
                break
            print("\n🤖 Agent：\n好的，那你再說一次營業時間，我重新整理。")
            continue

        print(f"🤖 Agent：\n我需要清楚的「幾點到幾點」以及是否有公休日，例如：週一到週六 08:00–17:00，週日公休。\n（{msg}）\n")

    # Step 5：併桌 can_merge_tables
    while True:
        print("\n🤖 Agent：\n如果人數比較多，現場可以把桌子併起來使用嗎？\nA. 可以\nB. 不行")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            print("Bye")
            return

        if is_simplify_trigger(user_in):
            print("\n🤖 Agent：\n沒問題，我先用一個安全的預設幫你把後面的策略設定好。")
            apply_simplified_strategy_defaults(state)
            break

        c = normalize_choice(user_in)
        if c in ("a", "可以", "可", "yes", "y"):
            merge_patch(state, {"strategy": {"can_merge_tables": True}})
            break
        if c in ("b", "不行", "否", "no", "n"):
            merge_patch(state, {"strategy": {"can_merge_tables": False}})
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

    # Step 5-2：最大接待人數（只有在 can_merge_tables=True 且尚未有 max_party_size 才問）
    if state["strategy"].get("can_merge_tables") is True and "max_party_size" not in state["strategy"]:
        while True:
            print("\n🤖 Agent：\n最多大概可以接到幾個人一起用餐？例如 8 人、10 人、12 人。")
            user_in = input("\n你：").strip()
            if user_in.lower() in ("exit", "quit"):
                print("Bye")
                return

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

    # 若 simplify 已經填好 strategy，可能已經有 online_role 等欄位，可直接跳過 Step 6~10
    if "online_role" not in state["strategy"]:
        # Step 6：線上訂位角色
        while True:
            print("\n🤖 Agent：\n你希望線上訂位在店裡扮演什麼角色？\nA. 主要方式（希望大多數客人先訂位）\nB. 輔助工具（只想避免忙的時候太亂）\nC. 少量開放（主要還是現場）")
            user_in = input("\n你：").strip()
            if user_in.lower() in ("exit", "quit"):
                print("Bye")
                return

            if is_simplify_trigger(user_in):
                print("\n🤖 Agent：\n沒問題，我先用一個安全的預設幫你把後面的策略設定好。")
                apply_simplified_strategy_defaults(state)
                break

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

    if "peak_periods" not in state["strategy"]:
        # Step 7：最忙時段
        while True:
            print("\n🤖 Agent：\n你覺得店裡最容易忙起來的是哪一段？\nA. 平日中午\nB. 平日晚餐\nC. 假日中午\nD. 假日晚餐\nE. 不太確定（交給系統）")
            user_in = input("\n你：").strip()
            if user_in.lower() in ("exit", "quit"):
                print("Bye")
                return

            if is_simplify_trigger(user_in):
                print("\n🤖 Agent：\n沒問題，我先用一個安全的預設幫你把後面的策略設定好。")
                apply_simplified_strategy_defaults(state)
                break

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
                allowed = {"weekday_lunch","weekday_dinner","weekend_brunch","weekend_dinner"}
                if all(x in allowed for x in s["peak_periods"]) and len(s["peak_periods"]) > 0:
                    merge_patch(state, {"strategy": {"peak_periods": s["peak_periods"]}})
                    break

            print("🤖 Agent：\n我只需要選 A / B / C / D / E 其中一個～再選一次：\n")

    if "peak_online_quota_ratio" not in state["strategy"]:
        # Step 8：忙時線上配額比例
        while True:
            print("\n🤖 Agent：\n在最忙的時段，你希望線上訂位大概佔多少位置？\nA. 大部分（約 80%）\nB. 一半左右（約 50%）\nC. 少量即可（約 20%）")
            user_in = input("\n你：").strip()
            if user_in.lower() in ("exit", "quit"):
                print("Bye")
                return

            if is_simplify_trigger(user_in):
                print("\n🤖 Agent：\n沒問題，我先用一個安全的預設幫你把後面的策略設定好。")
                apply_simplified_strategy_defaults(state)
                break

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
            if isinstance(s, dict) and s.get("peak_online_quota_ratio") in (0.8, 0.5, 0.2, 0.0):
                merge_patch(state, {"strategy": {"peak_online_quota_ratio": s["peak_online_quota_ratio"]}})
                break

            print("🤖 Agent：\n我只需要選 A / B / C 其中一個～再選一次：\n")

    if "peak_strategy" not in state["strategy"]:
        # Step 9：忙時策略
        while True:
            print("\n🤖 Agent：\n在最忙的時候，你比較希望怎麼做？\nA. 先讓線上訂位進來，比較好控制\nB. 留比較多位置給現場客\nC. 忙的時候就不開線上訂位")
            user_in = input("\n你：").strip()
            if user_in.lower() in ("exit", "quit"):
                print("Bye")
                return

            if is_simplify_trigger(user_in):
                print("\n🤖 Agent：\n沒問題，我先用一個安全的預設幫你把後面的策略設定好。")
                apply_simplified_strategy_defaults(state)
                break

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

    if "no_show_tolerance" not in state["strategy"]:
        # Step 10：no-show 容忍
        while True:
            print("\n🤖 Agent：\n如果 10 組線上訂位，有 1～2 組沒來，你可以接受嗎？\nA. 不太能接受\nB. 勉強可以\nC. 可以接受")
            user_in = input("\n你：").strip()
            if user_in.lower() in ("exit", "quit"):
                print("Bye")
                return

            if is_simplify_trigger(user_in):
                print("\n🤖 Agent：\n沒問題，我先用一個安全的預設幫你把後面的策略設定好。")
                apply_simplified_strategy_defaults(state)
                break

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

    # goal_type：由程式推導（如果 simplify 已經填了就不覆蓋）
    if "goal_type" not in state["strategy"]:
        online_role = state["strategy"]["online_role"]
        if online_role == "primary":
            goal_type = "fill_seats"
        elif online_role == "assistant":
            goal_type = "control_queue"
        else:
            goal_type = "keep_walkin"
        merge_patch(state, {"strategy": {"goal_type": goal_type}})

    # capacity_hint（Step 11 用得到）
    merge_patch(state, {"capacity_hint": capacity_hint_from_resources(state["resources"])})

    # =========================
    # Step 11：AI 建議 → A 接受 / B 修改（slot-based / capacity constraint）
    # =========================

    # 保底演算法建議（一定可算）
    booking_hours = compute_booking_hours_json(state["business_hours_json"], state["duration_sec"])
    ok_bh, _ = validate_business_hours_json(booking_hours)
    if not ok_bh:
        booking_hours = state["business_hours_json"]

    ratio = float(state["strategy"].get("peak_online_quota_ratio", 0.5))
    peak_strategy_local = state["strategy"]["peak_strategy"]
    policy = compute_peak_online_policy(
        capacity_hint=state["capacity_hint"],
        resources=state["resources"],
        duration_sec=state["duration_sec"],
        ratio=ratio,
        peak_strategy=peak_strategy_local,
        goal_type=state["strategy"]["goal_type"],
        no_show_tolerance=state["strategy"]["no_show_tolerance"],
        slot_minutes=30
    )

    # AI 先「看過」並可提出 patch（可選）
    ai_patch = llm_extract(
        "recommendation_patch",
        "請根據已知資訊提出建議（若不需要改動就輸出 {}）。",
        {**state, "booking_hours_json": booking_hours, "strategy": {**state["strategy"], **policy}}
    )

    # 套用 AI patch（有夾值保護）
    if "booking_hours_json" in ai_patch:
        bh2 = ai_patch["booking_hours_json"]
        ok2, _ = validate_business_hours_json(bh2)
        if ok2:
            booking_hours = bh2

    s2 = ai_patch.get("strategy", {}) if isinstance(ai_patch.get("strategy"), dict) else {}

    if s2.get("peak_strategy") in ("online_first", "walkin_first", "no_online"):
        peak_strategy_local = s2["peak_strategy"]
    if s2.get("peak_online_quota_ratio") in (0.8, 0.5, 0.2, 0.0):
        ratio = float(s2["peak_online_quota_ratio"])

    if "peak_slot_minutes" in s2:
        policy["peak_slot_minutes"] = clamp_int(s2["peak_slot_minutes"], 10, 120)
    if "peak_online_seat_budget" in s2:
        policy["peak_online_seat_budget"] = clamp_int(s2["peak_online_seat_budget"], 0, state["capacity_hint"])
    if "peak_online_party_limit_per_slot" in s2:
        policy["peak_online_party_limit_per_slot"] = clamp_int(s2["peak_online_party_limit_per_slot"], 0, 999)

    # 收斂一致性：若策略/比例有改，重新算 policy（更穩）
    if peak_strategy_local == "no_online":
        ratio = 0.0
    policy = compute_peak_online_policy(
        capacity_hint=state["capacity_hint"],
        resources=state["resources"],
        duration_sec=state["duration_sec"],
        ratio=ratio,
        peak_strategy=peak_strategy_local,
        goal_type=state["strategy"]["goal_type"],
        no_show_tolerance=state["strategy"]["no_show_tolerance"],
        slot_minutes=policy.get("peak_slot_minutes", 30)
    )

    # 商家確認/修改迴圈
    while True:
        # 1. 執行模擬 (這就是你的論文亮點：即時運算)
        sim_report = run_simulation_report(state, runs=100)
        
        print("\n🤖 Agent：\n我整理了一個線上訂位建議，並幫您跑了 100 次營運模擬：")
        
        # 顯示模擬數據
        print(sim_report)
        
        print(f"目前設定概要：")
        print(f"1) 線上訂位可訂入座時間：{summarize_business_hours(booking_hours)}")
        
        if peak_strategy_local == "no_online":
            print("2) 忙時策略：不開放線上訂位 (No Online)")
        else:
            print(f"2) 忙時策略：線上佔比約 {int(ratio*100)}% ({policy['peak_strategy']})")
            print(f"   (預算: {policy['peak_online_seat_budget']} 位 / 每 {policy['peak_slot_minutes']} 分鐘限 {policy['peak_online_party_limit_per_slot']} 組)")

        print("\nA. 直接採用這個建議\nB. 我想調整")
        ans = input("\n你：").strip()
        # ... (以下不用變，接原本的 input 處理邏輯)
        if ans.lower() in ("exit", "quit"):
            print("Bye")
            return
        c = normalize_choice(ans)
        
        if c in ("a", "ok", "對", "接受", "好", "yes", "y"):
            # ... (這裡接原本的確認邏輯)
            merge_patch(state, {
                "booking_hours_json": booking_hours,
                "strategy": {
                    "peak_strategy": peak_strategy_local,
                    "peak_online_quota_ratio": ratio,
                    "peak_slot_minutes": policy["peak_slot_minutes"],
                    "peak_online_seat_budget": policy["peak_online_seat_budget"],
                    "peak_online_party_limit_per_slot": policy["peak_online_party_limit_per_slot"],
                }
            })
            break

        print("\n🤖 Agent：\n沒問題～你想怎麼調整？你可以直接說：\n"
              "- 例如「線上訂位時間改成每天 09:00–16:00」\n"
              "- 或「忙的時候每 30 分鐘最多 2 組線上訂位」\n"
              "- 或「忙的時候線上最多留 15 個位子」\n"
              "- 或「忙的時候不開線上訂位」\n")
        mod = input("\n你：").strip()
        if mod.lower() in ("exit", "quit"):
            print("Bye")
            return

        mod_patch = llm_extract(
            "recommendation_patch",
            mod,
            {**state, "booking_hours_json": booking_hours, "strategy": {**state["strategy"], **policy}}
        )

        if "booking_hours_json" in mod_patch:
            bh2 = mod_patch["booking_hours_json"]
            ok2, msg2 = validate_business_hours_json(bh2)
            if ok2:
                booking_hours = bh2
            else:
                print(f"🤖 Agent：\n我沒有讀懂你要改的時間（{msg2}），這部分先不改。")

        s3 = mod_patch.get("strategy", {}) if isinstance(mod_patch.get("strategy"), dict) else {}

        if s3.get("peak_strategy") in ("online_first", "walkin_first", "no_online"):
            peak_strategy_local = s3["peak_strategy"]
        if s3.get("peak_online_quota_ratio") in (0.8, 0.5, 0.2, 0.0):
            ratio = float(s3["peak_online_quota_ratio"])

        if "peak_slot_minutes" in s3:
            policy["peak_slot_minutes"] = clamp_int(s3["peak_slot_minutes"], 10, 120)
        if "peak_online_seat_budget" in s3:
            policy["peak_online_seat_budget"] = clamp_int(s3["peak_online_seat_budget"], 0, state["capacity_hint"])
        if "peak_online_party_limit_per_slot" in s3:
            policy["peak_online_party_limit_per_slot"] = clamp_int(s3["peak_online_party_limit_per_slot"], 0, 999)

        if peak_strategy_local == "no_online":
            ratio = 0.0

        policy = compute_peak_online_policy(
            capacity_hint=state["capacity_hint"],
            resources=state["resources"],
            duration_sec=state["duration_sec"],
            ratio=ratio,
            peak_strategy=peak_strategy_local,
            goal_type=state["strategy"]["goal_type"],
            no_show_tolerance=state["strategy"]["no_show_tolerance"],
            slot_minutes=policy.get("peak_slot_minutes", 30)
        )

    # 保險：必備策略欄位存在
    state["strategy"].setdefault("can_merge_tables", True)
    state["strategy"].setdefault("max_party_size", 8)

    # 組 FINAL_JSON（必備 schema + 額外 booking_hours_json / extra strategy keys）
    final = {
        "store_id": state.get("store_id", None),
        "store_name": state["store_name"],
        "capacity_hint": state["capacity_hint"],
        "resources": state["resources"],
        "duration_sec": state["duration_sec"],
        "business_hours_json": state["business_hours_json"],
        "booking_hours_json": state.get("booking_hours_json", state["business_hours_json"]),
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
