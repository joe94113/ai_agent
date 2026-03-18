import copy
import json
import math
import random
import re
import statistics
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "llama3.1:8b-instruct-q4_K_M"
DEFAULT_TIMEZONE = "Asia/Taipei"
DEFAULT_SERVICE_ID = "reservation"
DEFAULT_SLOT_MINUTES = 30
DAY_NAMES = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"]
HHMM_RE = re.compile(r"^\d{4}$")
SIMPLIFY_TRIGGERS = {"聽不懂", "不用了", "隨便", "你幫我決定"}

# =========================================================
# Preloaded merchant context (comes from sales/CRM/Laravel)
# =========================================================
# Important: the agent does NOT ask for merchant basics again.
# It only configures reservation settings on top of this context.
# Merchant terms / policy text / booking rules are NOT preloaded here;
# they are optional settings collected inside the Google reservation settings UI.
PRELOADED_MERCHANT_CONTEXT: Dict[str, Any] = {
    "store_id": 123,
    "merchant_id": "merchant-demo-001",
    "store_name": "示範餐廳",
    "category": "restaurant",
    "timezone": DEFAULT_TIMEZONE,
    "telephone": "+886-2-1234-5678",
    "website_url": "https://example.com",
    "address": {
        "country": "TW",
        "region": "Taipei City",
        "locality": "Da'an District",
        "street_address": "仁愛路四段 100 號",
        "postal_code": "106",
    },
    "geo": {
        "latitude": 25.033,
        "longitude": 121.565,
    },
    # Google-compatible day mapping: 0=週日, 1=週一, ..., 6=週六
    "business_hours_json": [
        {"open": {"day": 1, "time": "1100"}, "close": {"day": 1, "time": "1400"}},
        {"open": {"day": 1, "time": "1700"}, "close": {"day": 1, "time": "2100"}},
        {"open": {"day": 2, "time": "1100"}, "close": {"day": 2, "time": "1400"}},
        {"open": {"day": 2, "time": "1700"}, "close": {"day": 2, "time": "2100"}},
        {"open": {"day": 3, "time": "1100"}, "close": {"day": 3, "time": "1400"}},
        {"open": {"day": 3, "time": "1700"}, "close": {"day": 3, "time": "2100"}},
        {"open": {"day": 4, "time": "1100"}, "close": {"day": 4, "time": "1400"}},
        {"open": {"day": 4, "time": "1700"}, "close": {"day": 4, "time": "2100"}},
        {"open": {"day": 5, "time": "1100"}, "close": {"day": 5, "time": "1400"}},
        {"open": {"day": 5, "time": "1700"}, "close": {"day": 5, "time": "2200"}},
        {"open": {"day": 6, "time": "1100"}, "close": {"day": 6, "time": "2200"}},
        {"open": {"day": 0, "time": "1100"}, "close": {"day": 0, "time": "2200"}},
    ],
}


# =========================================================
# State
# =========================================================
def default_state(merchant_context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "merchant_context": copy.deepcopy(merchant_context),  # read-only source
        "reservation_settings": {
            "table_inventory": [],
            "service_duration_sec": None,
            "booking_hours_mode": "same_as_business_hours_minus_duration",
            "online_booking_hours_json": [],
            "can_merge_tables": None,
            "max_party_size": None,
            "seating_sections": [],  # [{room_id, room_name, room_description?}]
            # Optional merchant terms shown on Google booking page.
            # This is NOT part of the preloaded merchant context.
            # It can be authored on the spot by the merchant in settings UI.
            "merchant_terms": {
                "enabled": False,
                "text": None,
                "url": None,
                "source": "agent_input",
            },
            "service_scheduling_rules": {
                "min_advance_booking_sec": None,
                "min_advance_online_canceling_sec": None,
            },
            "policy": {
                "online_role": None,
                "goal_type": None,
                "peak_periods": [],
                "peak_strategy": None,
                "peak_online_quota_ratio": None,
                "no_show_tolerance": None,
                "popularity": None,
            },
        },
        "derived": {},
        "meta": {
            "schema_version": "rwg-settings-ui-v2-internal-json",
            "agent_version": "settings-only-cli-v2-internal-json",
            "generated_at": None,
            "excluded_partner_wide_features": [
                "special_request_box",
            ],
        },
    }


# =========================================================
# Generic helpers
# =========================================================
def deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v


def slugify(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or f"id-{int(time.time())}"


def normalize_choice(text: str) -> str:
    t = (text or "").strip().lower()
    return t.replace("選項", "").replace(" ", "")


def is_simplify_trigger(text: str) -> bool:
    return (text or "").strip() in SIMPLIFY_TRIGGERS


def valid_hhmm(hhmm: Any) -> bool:
    s = str(hhmm).zfill(4)
    if not HHMM_RE.match(s):
        return False
    hh = int(s[:2])
    mm = int(s[2:])
    return 0 <= hh <= 23 and 0 <= mm <= 59


def hhmm_to_colon(hhmm: str) -> str:
    s = str(hhmm).zfill(4)
    return f"{s[:2]}:{s[2:]}"


def hhmm_to_minutes(hhmm: str) -> int:
    s = str(hhmm).zfill(4)
    return int(s[:2]) * 60 + int(s[2:])


def minutes_to_hhmm(minutes: int) -> str:
    minutes = max(0, int(minutes))
    return f"{minutes // 60:02d}{minutes % 60:02d}"


def summarize_business_hours(bh: List[Dict[str, Any]]) -> str:
    day_map: Dict[int, List[Tuple[int, str, int, str]]] = {d: [] for d in range(7)}
    for p in bh:
        o = p.get("open", {})
        c = p.get("close", {})
        od = int(o.get("day", 0))
        cd = int(c.get("day", 0))
        ot = str(o.get("time", "0000")).zfill(4)
        ct = str(c.get("time", "0000")).zfill(4)
        day_map[od].append((od, ot, cd, ct))

    def interval_text(od: int, ot: str, cd: int, ct: str) -> str:
        if od == cd:
            return f"{hhmm_to_colon(ot)}–{hhmm_to_colon(ct)}"
        return f"{hhmm_to_colon(ot)}–隔天{hhmm_to_colon(ct)}"

    # show Monday->Sunday for humans
    display_order = [1, 2, 3, 4, 5, 6, 0]
    sigs: List[str] = []
    for d in display_order:
        intervals = sorted(day_map.get(d, []), key=lambda x: x[1])
        if not intervals:
            sigs.append("CLOSED")
        else:
            sigs.append("、".join(interval_text(*it) for it in intervals))

    parts: List[str] = []
    i = 0
    while i < 7:
        sig = sigs[i]
        j = i
        while j + 1 < 7 and sigs[j + 1] == sig:
            j += 1
        start_name = DAY_NAMES[display_order[i]]
        end_name = DAY_NAMES[display_order[j]]
        day_label = start_name if i == j else f"{start_name}～{end_name}"
        if sig == "CLOSED":
            parts.append(f"{day_label} 公休")
        else:
            parts.append(f"{day_label} {sig}")
        i = j + 1
    return "；".join(parts)


def summarize_tables(tables: List[Dict[str, Any]]) -> str:
    if not tables:
        return "（無）"
    items = []
    for t in sorted(tables, key=lambda x: int(x.get("party_size", 0))):
        items.append(f"{int(t['party_size'])} 人桌 {int(t['spots_total'])} 張")
    return "、".join(items)


def summarize_sections(sections: List[Dict[str, Any]]) -> str:
    if not sections:
        return "預設座位區"
    return "、".join(s["room_name"] for s in sections)


# =========================================================
# Validators
# =========================================================
def validate_business_hours_json(bh: Any) -> Tuple[bool, str]:
    if not isinstance(bh, list) or not bh:
        return False, "business_hours_json 必須是非空 list"
    for i, p in enumerate(bh):
        if not isinstance(p, dict):
            return False, f"business_hours_json[{i}] 必須是 object"
        if "open" not in p or "close" not in p:
            return False, f"business_hours_json[{i}] 必須包含 open/close"
        o = p["open"]
        c = p["close"]
        if not isinstance(o, dict) or not isinstance(c, dict):
            return False, f"business_hours_json[{i}] open/close 必須是 object"
        if any(k not in o for k in ["day", "time"]) or any(k not in c for k in ["day", "time"]):
            return False, f"business_hours_json[{i}] open/close 必須包含 day/time"
        try:
            od = int(o["day"])
            cd = int(c["day"])
        except Exception:
            return False, f"business_hours_json[{i}] day 必須可轉成整數"
        if od == 7:
            od = 0
        if cd == 7:
            cd = 0
        if not (0 <= od <= 6 and 0 <= cd <= 6):
            return False, f"business_hours_json[{i}] day 必須 0~6"
        if not valid_hhmm(o["time"]) or not valid_hhmm(c["time"]):
            return False, f"business_hours_json[{i}] time 必須是有效 HHMM"
        o["day"] = od
        c["day"] = cd
        o["time"] = str(o["time"]).zfill(4)
        c["time"] = str(c["time"]).zfill(4)
    return True, "ok"


def validate_table_inventory(tables: Any) -> Tuple[bool, str]:
    if not isinstance(tables, list) or not tables:
        return False, "table_inventory 必須是非空 list"
    for i, t in enumerate(tables):
        if not isinstance(t, dict):
            return False, f"table_inventory[{i}] 必須是 object"
        if "party_size" not in t or "spots_total" not in t:
            return False, f"table_inventory[{i}] 缺少 party_size / spots_total"
        try:
            ps = int(t["party_size"])
            st = int(t["spots_total"])
        except Exception:
            return False, f"table_inventory[{i}] party_size / spots_total 必須是整數"
        if ps <= 0 or st < 0:
            return False, f"table_inventory[{i}] 數值不合法"
        t["party_size"] = ps
        t["spots_total"] = st
    return True, "ok"


def validate_sections(sections: Any) -> Tuple[bool, str]:
    if sections in (None, []):
        return True, "ok"
    if not isinstance(sections, list):
        return False, "seating_sections 必須是 list"
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            return False, f"seating_sections[{i}] 必須是 object"
        name = str(s.get("room_name") or "").strip()
        if not name:
            return False, f"seating_sections[{i}].room_name 必須是非空字串"
        room_id = str(s.get("room_id") or slugify(name))
        s["room_id"] = room_id
        s["room_name"] = name
        if s.get("room_description") is not None:
            s["room_description"] = str(s["room_description"]).strip() or None
    return True, "ok"


def validate_settings_ready(state: Dict[str, Any]) -> Tuple[bool, str]:
    s = state["reservation_settings"]
    ok, msg = validate_table_inventory(s.get("table_inventory"))
    if not ok:
        return False, msg
    if not isinstance(s.get("service_duration_sec"), int) or s["service_duration_sec"] <= 0:
        return False, "service_duration_sec 必須是正整數"
    if not isinstance(s.get("can_merge_tables"), bool):
        return False, "can_merge_tables 必須是 boolean"
    if not isinstance(s.get("max_party_size"), int) or s["max_party_size"] <= 0:
        return False, "max_party_size 必須是正整數"
    ok, msg = validate_business_hours_json(s.get("online_booking_hours_json"))
    if not ok:
        return False, f"online_booking_hours_json 無效：{msg}"
    ok, msg = validate_sections(s.get("seating_sections"))
    if not ok:
        return False, msg
    policy = s["policy"]
    if policy.get("online_role") not in {"primary", "assistant", "minimal"}:
        return False, "online_role 尚未完成"
    if policy.get("peak_strategy") not in {"online_first", "walkin_first", "no_online"}:
        return False, "peak_strategy 尚未完成"
    if policy.get("no_show_tolerance") not in {"low", "medium", "high"}:
        return False, "no_show_tolerance 尚未完成"
    if not isinstance(policy.get("peak_periods"), list) or not policy["peak_periods"]:
        return False, "peak_periods 尚未完成"
    if policy.get("peak_online_quota_ratio") not in {0.0, 0.2, 0.5, 0.8}:
        return False, "peak_online_quota_ratio 尚未完成"
    if policy.get("popularity") not in {"low", "medium", "high"}:
        return False, "popularity 尚未完成"
    if s["service_scheduling_rules"].get("min_advance_booking_sec") is None:
        return False, "min_advance_booking_sec 尚未完成"
    if s["service_scheduling_rules"].get("min_advance_online_canceling_sec") is None:
        return False, "min_advance_online_canceling_sec 尚未完成"
    if s["merchant_terms"].get("enabled"):
        if not str(s["merchant_terms"].get("text") or "").strip() and not str(s["merchant_terms"].get("url") or "").strip():
            return False, "merchant_terms 啟用後至少需有 text 或 url"
    return True, "ok"


# =========================================================
# Domain derivations
# =========================================================
def capacity_hint_from_tables(tables: List[Dict[str, int]]) -> int:
    return max(1, sum(int(t["party_size"]) * int(t["spots_total"]) for t in tables))


def compute_booking_hours_json(base_hours: List[Dict[str, Any]], duration_sec: int) -> List[Dict[str, Any]]:
    dur_min = max(0, int(duration_sec) // 60)
    out: List[Dict[str, Any]] = []
    for p in base_hours:
        o = p["open"]
        c = p["close"]
        od = int(o["day"])
        cd = int(c["day"])
        ot = str(o["time"]).zfill(4)
        ct = str(c["time"]).zfill(4)
        if od != cd:
            out.append({"open": {"day": od, "time": ot}, "close": {"day": cd, "time": ct}})
            continue
        last_start = max(hhmm_to_minutes(ot), hhmm_to_minutes(ct) - dur_min)
        out.append({
            "open": {"day": od, "time": ot},
            "close": {"day": od, "time": minutes_to_hhmm(last_start)},
        })
    return out


def typical_party_size_from_tables(tables: List[Dict[str, Any]]) -> int:
    items = []
    total_weight = 0
    for t in tables:
        ps = int(t["party_size"])
        w = int(t["spots_total"])
        if w <= 0:
            continue
        items.append((ps, w))
        total_weight += w
    if not items:
        return 2
    items.sort(key=lambda x: x[0])
    threshold = (total_weight + 1) / 2
    acc = 0
    for ps, w in items:
        acc += w
        if acc >= threshold:
            return ps
    return items[-1][0]


def compute_peak_online_policy(state: Dict[str, Any], slot_minutes: int = DEFAULT_SLOT_MINUTES) -> Dict[str, int]:
    settings = state["reservation_settings"]
    tables = settings["table_inventory"]
    cap = capacity_hint_from_tables(tables)
    duration_sec = settings["service_duration_sec"]
    policy = settings["policy"]
    ratio = float(policy["peak_online_quota_ratio"])
    peak_strategy = policy["peak_strategy"]
    no_show_tolerance = policy["no_show_tolerance"]
    goal_type = policy["goal_type"]

    slot_minutes = max(10, min(int(slot_minutes), 120))
    typical_ps = typical_party_size_from_tables(tables)
    duration_slots = max(1, math.ceil((duration_sec / 60) / slot_minutes))

    if peak_strategy == "no_online":
        ratio = 0.0

    base = int(math.floor(cap * ratio))
    goal_factor = {"fill_seats": 1.05, "control_queue": 1.00, "keep_walkin": 0.80}.get(goal_type, 1.0)
    ns_factor = {"low": 0.90, "medium": 1.00, "high": 1.05}.get(no_show_tolerance, 1.0)
    seat_budget = max(0, min(cap, int(math.floor(base * goal_factor * ns_factor))))
    denom = max(1, typical_ps * duration_slots)
    party_limit = seat_budget // denom
    if seat_budget > 0 and party_limit == 0:
        party_limit = 1

    return {
        "capacity_hint": cap,
        "slot_minutes": slot_minutes,
        "duration_slots": duration_slots,
        "typical_party_size": typical_ps,
        "peak_online_seat_budget": seat_budget,
        "peak_online_party_limit_per_slot": party_limit,
    }


def apply_goal_type(state: Dict[str, Any]) -> None:
    p = state["reservation_settings"]["policy"]
    role = p.get("online_role")
    p["goal_type"] = {
        "primary": "fill_seats",
        "assistant": "control_queue",
        "minimal": "keep_walkin",
    }.get(role, "control_queue")


def derive_warnings(state: Dict[str, Any]) -> List[str]:
    s = state["reservation_settings"]
    p = s["policy"]
    warnings: List[str] = []
    largest_table = max(int(t["party_size"]) for t in s["table_inventory"])
    if s["can_merge_tables"] is False and s["max_party_size"] > largest_table:
        warnings.append("不可併桌時，max_party_size 不應大於最大單桌尺寸。")
    if p["peak_strategy"] == "no_online" and p["peak_online_quota_ratio"] != 0.0:
        warnings.append("peak_strategy 設為 no_online 時，線上配額比例應視為 0%。")
    if p["no_show_tolerance"] == "high" and p["peak_online_quota_ratio"] == 0.8:
        warnings.append("高 no-show 容忍度搭配 80% 線上配額，容易出現臨時空桌。")
    if s["merchant_terms"]["enabled"] and s["merchant_terms"].get("url"):
        url = str(s["merchant_terms"]["url"])
        if not (url.startswith("http://") or url.startswith("https://")):
            warnings.append("merchant_terms.url 建議使用 http:// 或 https:// 開頭。")
    return warnings


# =========================================================
# Simple simulator (kept heuristic, but proposal-bound)
# =========================================================
class RestaurantSimulator:
    def __init__(self, capacity: int, online_ratio: float, no_show_prob: float, popularity_multiplier: float):
        self.capacity = capacity
        self.online_quota = int(capacity * online_ratio)
        self.no_show_prob = no_show_prob
        self.pop_mult = popularity_multiplier

    def run_one_evening(self) -> Dict[str, float]:
        base_demand = random.uniform(0.8, 1.2) * self.capacity
        total_potential_demand = int(base_demand * self.pop_mult)
        potential_online_demand = int(total_potential_demand * 0.5)
        potential_walkin_demand = total_potential_demand - potential_online_demand

        booked_seats = min(potential_online_demand, self.online_quota)
        actual_online = 0
        for _ in range(booked_seats):
            if random.random() > self.no_show_prob:
                actual_online += 1

        available_for_walkin = self.capacity - actual_online
        walkin_seated = min(potential_walkin_demand, available_for_walkin)

        total_seated = actual_online + walkin_seated
        utilization = total_seated / max(1, self.capacity)
        rejected_online = max(0, potential_online_demand - self.online_quota)
        rejected_walkin = max(0, potential_walkin_demand - available_for_walkin)
        return {
            "utilization": utilization,
            "lost_customers": rejected_online + rejected_walkin,
            "empty_seats": self.capacity - total_seated,
        }


def run_simulation_report(state: Dict[str, Any], runs: int = 200) -> Dict[str, Any]:
    settings = state["reservation_settings"]
    policy = settings["policy"]
    cap = state["derived"]["peak_policy"]["capacity_hint"]
    ratio = float(policy["peak_online_quota_ratio"])
    peak_strategy = policy["peak_strategy"]
    if peak_strategy == "no_online":
        ratio = 0.0
    elif peak_strategy == "walkin_first" and ratio > 0.3:
        ratio = 0.3

    ns_prob = {"low": 0.05, "medium": 0.15, "high": 0.30}[policy["no_show_tolerance"]]
    pop_mult = {"low": 0.6, "medium": 1.2, "high": 2.0}[policy["popularity"]]
    sim = RestaurantSimulator(cap, ratio, ns_prob, pop_mult)
    results = [sim.run_one_evening() for _ in range(runs)]
    avg_util = statistics.mean([r["utilization"] for r in results])
    avg_lost = statistics.mean([r["lost_customers"] for r in results])
    avg_empty = statistics.mean([r["empty_seats"] for r in results])

    if avg_util < 0.7:
        advice = "座位利用率偏低，建議增加線上訂位可見性或放寬線上配額。"
    elif avg_lost > cap * 0.5:
        advice = "流失客數偏高，建議提高可接待量、增加候補或縮短尖峰用餐時長。"
    elif ns_prob > 0.2 and ratio > 0.6:
        advice = "No-show 風險與線上比例都偏高，建議降低尖峰線上比例。"
    else:
        advice = "目前設定在模擬中相對平衡。"

    return {
        "runs": runs,
        "avg_utilization": round(avg_util, 4),
        "avg_lost_customers": round(avg_lost, 2),
        "avg_empty_seats": round(avg_empty, 2),
        "advice": advice,
    }


# =========================================================
# LLM extractor (settings-only)
# =========================================================
EXTRACTOR_SYSTEM = r"""
你是一個「Google 預訂設定資料抽取器」，只負責把使用者回答轉成 JSON patch。
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
        "options": {"temperature": 0.2, "top_p": 0.9},
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def extract_first_json_object_str(text: str) -> Optional[str]:
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
                    return s[start:i + 1]
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
    schema_guide = {
        "table_inventory": r'輸出：{"reservation_settings":{"table_inventory":[{"party_size":2,"spots_total":4},{"party_size":4,"spots_total":3}]}}',
        "custom_booking_hours": r'''輸出：{"reservation_settings":{"online_booking_hours_json":[
{"open":{"day":1,"time":"1100"},"close":{"day":1,"time":"1300"}},
{"open":{"day":5,"time":"1700"},"close":{"day":5,"time":"2100"}}
]}}
0=週日, 1=週一, ..., 6=週六；time 必須是 4 位 HHMM 字串。''',
        "seating_sections": r'''輸出：{"reservation_settings":{"seating_sections":[
{"room_id":"patio","room_name":"戶外區"},
{"room_id":"bar","room_name":"吧台"}
]}}；若沒有分區，輸出空物件 {}''',
        "merchant_terms": r'''輸出：{"reservation_settings":{"merchant_terms":{"enabled":true,"text":"用餐時間以現場安排為準","url":"https://example.com/terms"}}}
若使用者表示沒有，輸出 {"reservation_settings":{"merchant_terms":{"enabled":false}}}
可只有文字、只有網址、或兩者都有。''',
    }
    guide = schema_guide.get(step_name, "輸出：{}")
    user_prompt = f"""
【步驟】{step_name}
【輸出格式】{guide}
【使用者回答】{user_text}
【已知商家與設定摘要】{json.dumps(state, ensure_ascii=False)}
請只輸出 JSON object。
""".strip()
    messages = [
        {"role": "system", "content": EXTRACTOR_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]
    raw = call_ollama(messages)
    obj = parse_json_object(raw)
    return obj if obj is not None else {}


# =========================================================
# Previews for Laravel UI and daily feed job
# =========================================================
def localized_text(value: str, locale: str = "zh-Hant") -> Dict[str, Any]:
    return {
        "value": value,
        "localized_value": [{"locale": locale, "value": value}],
    }


def build_preview_availability(state: Dict[str, Any], preview_days: int = 2, max_slots: int = 24) -> List[Dict[str, Any]]:
    mc = state["merchant_context"]
    s = state["reservation_settings"]
    tables = sorted(s["table_inventory"], key=lambda x: int(x["party_size"]))
    bh = s["online_booking_hours_json"]
    tz = ZoneInfo(mc.get("timezone") or DEFAULT_TIMEZONE)
    now_local = datetime.now(tz)
    preview: List[Dict[str, Any]] = []
    slot_minutes = DEFAULT_SLOT_MINUTES

    for offset in range(preview_days):
        d = now_local.date() + timedelta(days=offset)
        google_day = (d.weekday() + 1) % 7  # Python Mon=0 -> Google Sun=0
        todays_periods = [p for p in bh if int(p["open"]["day"]) == google_day]
        for period in todays_periods:
            start_min = hhmm_to_minutes(period["open"]["time"])
            end_min = hhmm_to_minutes(period["close"]["time"])
            cur = start_min
            while cur <= end_min:
                for t in tables:
                    slot_dt = datetime(d.year, d.month, d.day, cur // 60, cur % 60, tzinfo=tz)
                    preview.append({
                        "merchant_id": mc["merchant_id"],
                        "service_id": DEFAULT_SERVICE_ID,
                        "start_sec": int(slot_dt.timestamp()),
                        "duration_sec": s["service_duration_sec"],
                        "spots_total": int(t["spots_total"]),
                        "spots_open": int(t["spots_total"]),
                        "resources": {"party_size": int(t["party_size"])}
                    })
                    if len(preview) >= max_slots:
                        return preview
                cur += slot_minutes
    return preview


def build_google_feed_preview(state: Dict[str, Any]) -> Dict[str, Any]:
    """Debug only. Laravel can assemble official Google feeds later."""
    mc = state["merchant_context"]
    s = state["reservation_settings"]
    merchant = {
        "merchant_id": mc["merchant_id"],
        "name": mc["store_name"],
        "category": mc.get("category", "restaurant"),
        "telephone": mc.get("telephone"),
        "url": mc.get("website_url"),
        "geo": {
            "latitude": mc.get("geo", {}).get("latitude"),
            "longitude": mc.get("geo", {}).get("longitude"),
            "address": mc.get("address", {}),
        },
    }
    if s["merchant_terms"]["enabled"]:
        merchant["terms"] = {
            k: v for k, v in {
                "text": s["merchant_terms"].get("text"),
                "url": s["merchant_terms"].get("url"),
            }.items() if v
        }

    service = {
        "merchant_id": mc["merchant_id"],
        "service_id": DEFAULT_SERVICE_ID,
        "localized_service_name": localized_text("Reservation"),
        "scheduling_rules": {
            "min_advance_booking": s["service_scheduling_rules"]["min_advance_booking_sec"],
            "min_advance_online_canceling": s["service_scheduling_rules"]["min_advance_online_canceling_sec"],
            "admission_policy": "TIME_STRICT",
        }
    }
    return {
        "merchant_feed_preview": {"merchant": [merchant]},
        "services_feed_preview": {"service": [service]},
        "availability_feed_preview": {"availability": build_preview_availability(state)},
    }


def build_daily_feed_job_input(state: Dict[str, Any]) -> Dict[str, Any]:
    mc = state["merchant_context"]
    s = state["reservation_settings"]
    return {
        "merchant_id": mc["merchant_id"],
        "store_id": mc.get("store_id"),
        "service_id": DEFAULT_SERVICE_ID,
        "timezone": mc.get("timezone", DEFAULT_TIMEZONE),
        "business_hours_json": mc.get("business_hours_json", []),
        "online_booking_hours_json": s["online_booking_hours_json"],
        "table_inventory": s["table_inventory"],
        "service_duration_sec": s["service_duration_sec"],
        "can_merge_tables": s["can_merge_tables"],
        "max_party_size": s["max_party_size"],
        "seating_sections": s["seating_sections"],
        "merchant_terms": s["merchant_terms"],
        "service_scheduling_rules": s["service_scheduling_rules"],
        "policy": s["policy"],
        "derived": state["derived"],
        "feed_generation": {
            "availability_days": 30,
            "processing_instruction": "PROCESS_AS_COMPLETE",
            "full_inventory": True,
        },
    }


def build_laravel_visual_payload(state: Dict[str, Any]) -> Dict[str, Any]:
    mc = state["merchant_context"]
    s = state["reservation_settings"]
    return {
        "merchant_card": {
            "store_id": mc.get("store_id"),
            "merchant_id": mc["merchant_id"],
            "store_name": mc["store_name"],
            "telephone": mc.get("telephone"),
            "website_url": mc.get("website_url"),
            "business_hours_summary": summarize_business_hours(mc.get("business_hours_json", [])),
            "read_only": True,
        },
        "settings_form": {
            "table_inventory_summary": summarize_tables(s["table_inventory"]),
            "service_duration_sec": s["service_duration_sec"],
            "online_booking_hours_summary": summarize_business_hours(s["online_booking_hours_json"]),
            "can_merge_tables": s["can_merge_tables"],
            "max_party_size": s["max_party_size"],
            "seating_sections_summary": summarize_sections(s["seating_sections"]),
            "merchant_terms": s["merchant_terms"],  # optional, agent-authored setting
            "service_scheduling_rules": s["service_scheduling_rules"],
            "policy": s["policy"],
        },
        "derived": state["derived"],
        "warnings": derive_warnings(state),
        "simulation": state["derived"].get("simulation_report"),
        "excluded_partner_wide_features": state["meta"]["excluded_partner_wide_features"],
    }


def build_internal_payload(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "merchant_context": state["merchant_context"],
        "reservation_settings": state["reservation_settings"],
        "laravel_visual_payload": build_laravel_visual_payload(state),
        "daily_feed_job_input": build_daily_feed_job_input(state),
        "meta": state["meta"],
    }


def validate_internal_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload 必須是 object"
    required_top = ["merchant_context", "reservation_settings", "laravel_visual_payload", "daily_feed_job_input", "meta"]
    for key in required_top:
        if key not in payload:
            return False, f"缺少頂層欄位 {key}"

    mc = payload["merchant_context"]
    if not isinstance(mc, dict):
        return False, "merchant_context 必須是 object"
    for key in ["store_id", "merchant_id", "store_name", "timezone", "business_hours_json"]:
        if key not in mc:
            return False, f"merchant_context 缺少 {key}"
    ok, msg = validate_business_hours_json(mc.get("business_hours_json"))
    if not ok:
        return False, f"merchant_context.business_hours_json 不合法：{msg}"

    rs = payload["reservation_settings"]
    if not isinstance(rs, dict):
        return False, "reservation_settings 必須是 object"
    ok, msg = validate_settings_ready({
        "merchant_context": mc,
        "reservation_settings": rs,
        "derived": payload.get("daily_feed_job_input", {}).get("derived", {}),
        "meta": payload.get("meta", {}),
    })
    if not ok:
        return False, f"reservation_settings 不合法：{msg}"

    dj = payload["daily_feed_job_input"]
    if not isinstance(dj, dict):
        return False, "daily_feed_job_input 必須是 object"
    for key in ["merchant_id", "store_id", "service_id", "timezone", "business_hours_json", "online_booking_hours_json", "table_inventory", "service_duration_sec", "policy", "feed_generation"]:
        if key not in dj:
            return False, f"daily_feed_job_input 缺少 {key}"
    ok, msg = validate_business_hours_json(dj.get("business_hours_json"))
    if not ok:
        return False, f"daily_feed_job_input.business_hours_json 不合法：{msg}"
    ok, msg = validate_business_hours_json(dj.get("online_booking_hours_json"))
    if not ok:
        return False, f"daily_feed_job_input.online_booking_hours_json 不合法：{msg}"
    ok, msg = validate_table_inventory(dj.get("table_inventory"))
    if not ok:
        return False, f"daily_feed_job_input.table_inventory 不合法：{msg}"

    if not isinstance(payload["laravel_visual_payload"], dict):
        return False, "laravel_visual_payload 必須是 object"

    meta = payload["meta"]
    if not isinstance(meta, dict):
        return False, "meta 必須是 object"
    for key in ["schema_version", "agent_version", "generated_at"]:
        if key not in meta:
            return False, f"meta 缺少 {key}"

    return True, "ok"


def ensure_json_roundtrip(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return json.loads(raw)


# =========================================================
# Orchestrator (settings UI only)
# =========================================================
def apply_simple_defaults(state: Dict[str, Any]) -> None:
    deep_merge(state["reservation_settings"], {
        "can_merge_tables": True,
        "max_party_size": 8,
        "service_scheduling_rules": {
            "min_advance_booking_sec": 3600,
            "min_advance_online_canceling_sec": 3600,
        },
        "policy": {
            "online_role": "assistant",
            "peak_periods": ["weekend_dinner"],
            "peak_strategy": "online_first",
            "peak_online_quota_ratio": 0.5,
            "no_show_tolerance": "medium",
            "popularity": "medium",
        },
    })
    apply_goal_type(state)


def finalize_state(state: Dict[str, Any]) -> None:
    s = state["reservation_settings"]
    if s["booking_hours_mode"] == "same_as_business_hours_minus_duration":
        s["online_booking_hours_json"] = compute_booking_hours_json(
            state["merchant_context"]["business_hours_json"],
            s["service_duration_sec"],
        )
    apply_goal_type(state)
    state["derived"]["peak_policy"] = compute_peak_online_policy(state)
    state["derived"]["warnings"] = derive_warnings(state)
    state["derived"]["simulation_report"] = run_simulation_report(state)
    state["meta"]["generated_at"] = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat()


def print_merchant_context(state: Dict[str, Any]) -> None:
    mc = state["merchant_context"]
    print("商家基本資料已預先載入，agent 只設定 Google 預訂相關規則。\n")
    print("已載入商家：")
    print(f"- 店名：{mc['store_name']}")
    print(f"- Merchant ID：{mc['merchant_id']}")
    print(f"- 營業時間：{summarize_business_hours(mc['business_hours_json'])}")
    print(f"- 電話：{mc.get('telephone')}")
    print("- 以上資料為唯讀，不再重問。\n")


def main() -> None:
    state = default_state(PRELOADED_MERCHANT_CONTEXT)
    print_merchant_context(state)

    # 1) table inventory
    while True:
        print("🤖 Agent：\n請設定店內可訂位桌型，例如：2人桌4張、4人桌3張、6人桌1張。")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        patch = llm_extract("table_inventory", user_in, state)
        tables = patch.get("reservation_settings", {}).get("table_inventory")
        ok, msg = validate_table_inventory(tables)
        if ok:
            state["reservation_settings"]["table_inventory"] = tables
            break
        print(f"🤖 Agent：\n我需要像『2人桌4張、4人桌3張』這樣的資訊，再說一次好嗎？（{msg}）\n")

    # 2) duration
    while True:
        print("\n🤖 Agent：\n一般一組客人平均用餐多久？\nA. 1 小時\nB. 1.5 小時\nC. 2 小時")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        c = normalize_choice(user_in)
        if c in ("a", "1", "60", "60分鐘", "一小時", "1小時"):
            state["reservation_settings"]["service_duration_sec"] = 3600
            break
        if c in ("b", "1.5", "90", "90分鐘", "一個半小時", "1個半小時"):
            state["reservation_settings"]["service_duration_sec"] = 5400
            break
        if c in ("c", "2", "120", "120分鐘", "兩小時", "2小時"):
            state["reservation_settings"]["service_duration_sec"] = 7200
            break
        print("🤖 Agent：\n請選 A / B / C。\n")

    # 3) booking hours mode
    while True:
        print("\n🤖 Agent：\n線上可訂『入座時間』要怎麼開？\nA. 直接依營業時間自動推算（結束時間會扣掉平均用餐時長）\nB. 我要自己設定線上可訂時間")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        c = normalize_choice(user_in)
        if c in ("a", "自動", "照營業時間"):
            state["reservation_settings"]["booking_hours_mode"] = "same_as_business_hours_minus_duration"
            state["reservation_settings"]["online_booking_hours_json"] = compute_booking_hours_json(
                state["merchant_context"]["business_hours_json"],
                state["reservation_settings"]["service_duration_sec"],
            )
            break
        if c in ("b", "自己", "自訂", "客製"):
            state["reservation_settings"]["booking_hours_mode"] = "custom"
            while True:
                print("\n🤖 Agent：\n請描述線上可訂時間，例如：週一到週五 11:00-13:00、17:00-20:00；六日 11:00-21:00")
                txt = input("\n你：").strip()
                if txt.lower() in ("exit", "quit"):
                    return
                patch = llm_extract("custom_booking_hours", txt, state)
                bh = patch.get("reservation_settings", {}).get("online_booking_hours_json")
                ok, msg = validate_business_hours_json(bh)
                if ok:
                    state["reservation_settings"]["online_booking_hours_json"] = bh
                    print(f"🤖 Agent：\n我整理成：{summarize_business_hours(bh)}")
                    break
                print(f"🤖 Agent：\n我沒有成功整理出合法時間格式，請再描述一次。（{msg}）")
            break
        print("🤖 Agent：\n請選 A / B。\n")

    # 4) merge tables
    while True:
        print("\n🤖 Agent：\n多人訂位時可以併桌嗎？\nA. 可以\nB. 不行")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        if is_simplify_trigger(user_in):
            apply_simple_defaults(state)
            break
        c = normalize_choice(user_in)
        if c in ("a", "可以", "yes", "y"):
            state["reservation_settings"]["can_merge_tables"] = True
            break
        if c in ("b", "不行", "no", "n"):
            state["reservation_settings"]["can_merge_tables"] = False
            largest_table = max(int(t["party_size"]) for t in state["reservation_settings"]["table_inventory"])
            state["reservation_settings"]["max_party_size"] = largest_table
            break
        print("🤖 Agent：\n請選 A / B。\n")

    # 5) max party size
    if state["reservation_settings"]["can_merge_tables"] is True and state["reservation_settings"]["max_party_size"] is None:
        while True:
            print("\n🤖 Agent：\n最多允許幾人線上訂位？例如 8、10、12")
            user_in = input("\n你：").strip()
            if user_in.lower() in ("exit", "quit"):
                return
            m = re.search(r"(\d+)", user_in)
            if m:
                n = int(m.group(1))
                if n > 0:
                    state["reservation_settings"]["max_party_size"] = n
                    break
            print("🤖 Agent：\n我需要一個正整數人數。\n")

    # 6) seating sections (optional)
    while True:
        print("\n🤖 Agent：\n店內有需要讓 Google 顯示的分區嗎？例如 吧台、戶外區、包廂。\n沒有的話請輸入：無")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        if normalize_choice(user_in) in ("無", "沒有", "none", "no"):
            state["reservation_settings"]["seating_sections"] = []
            break
        patch = llm_extract("seating_sections", user_in, state)
        sections = patch.get("reservation_settings", {}).get("seating_sections")
        ok, msg = validate_sections(sections)
        if ok:
            state["reservation_settings"]["seating_sections"] = sections or []
            break
        print(f"🤖 Agent：\n我需要分區名稱清單，例如『吧台、戶外區』。（{msg}）\n")

    # 7) merchant terms (optional)
    while True:
        print("\n🤖 Agent：\n要不要在 Google 預訂頁面顯示商家備註/條款？\nA. 不用\nB. 要，我來輸入")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        c = normalize_choice(user_in)
        if c in ("a", "不用", "不需要", "no"):
            state["reservation_settings"]["merchant_terms"] = {"enabled": False, "text": None, "url": None, "source": "agent_input"}
            break
        if c in ("b", "要", "需要", "yes"):
            print("\n🤖 Agent：\n請直接輸入條款說明，若有網址也一起附上。例：『訂位保留 10 分鐘，https://example.com/terms』")
            txt = input("\n你：").strip()
            if txt.lower() in ("exit", "quit"):
                return
            patch = llm_extract("merchant_terms", txt, state)
            mt = patch.get("reservation_settings", {}).get("merchant_terms")
            if isinstance(mt, dict):
                state["reservation_settings"]["merchant_terms"] = {
                    "enabled": bool(mt.get("enabled", True)),
                    "text": mt.get("text"),
                    "url": mt.get("url"),
                    "source": "agent_input",
                }
                break
        print("🤖 Agent：\n請選 A / B。\n")

    # 8) service-level scheduling rules
    while True:
        print("\n🤖 Agent：\n最晚要在用餐開始前多久完成線上訂位？\nA. 30 分鐘\nB. 1 小時\nC. 2 小時")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        c = normalize_choice(user_in)
        if c in ("a", "30", "30分鐘"):
            state["reservation_settings"]["service_scheduling_rules"]["min_advance_booking_sec"] = 1800
            break
        if c in ("b", "60", "1小時", "一小時"):
            state["reservation_settings"]["service_scheduling_rules"]["min_advance_booking_sec"] = 3600
            break
        if c in ("c", "120", "2小時", "兩小時"):
            state["reservation_settings"]["service_scheduling_rules"]["min_advance_booking_sec"] = 7200
            break
        print("🤖 Agent：\n請選 A / B / C。\n")

    while True:
        print("\n🤖 Agent：\n最晚要在用餐開始前多久完成線上取消？\nA. 隨時可取消\nB. 1 小時前\nC. 24 小時前")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        c = normalize_choice(user_in)
        if c in ("a", "隨時", "0"):
            state["reservation_settings"]["service_scheduling_rules"]["min_advance_online_canceling_sec"] = 0
            break
        if c in ("b", "1小時", "一小時", "60"):
            state["reservation_settings"]["service_scheduling_rules"]["min_advance_online_canceling_sec"] = 3600
            break
        if c in ("c", "24小時", "一天", "1440"):
            state["reservation_settings"]["service_scheduling_rules"]["min_advance_online_canceling_sec"] = 86400
            break
        print("🤖 Agent：\n請選 A / B / C。\n")

    # 9) online role
    while True:
        print("\n🤖 Agent：\n線上訂位在店裡扮演什麼角色？\nA. 主要方式\nB. 輔助工具\nC. 少量開放")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        if is_simplify_trigger(user_in):
            apply_simple_defaults(state)
            break
        c = normalize_choice(user_in)
        if c in ("a", "主要"):
            state["reservation_settings"]["policy"]["online_role"] = "primary"
            break
        if c in ("b", "輔助"):
            state["reservation_settings"]["policy"]["online_role"] = "assistant"
            break
        if c in ("c", "少量"):
            state["reservation_settings"]["policy"]["online_role"] = "minimal"
            break
        print("🤖 Agent：\n請選 A / B / C。\n")
    apply_goal_type(state)

    # 10) peak periods
    while True:
        print("\n🤖 Agent：\n你覺得最忙的是哪一段？\nA. 平日中午\nB. 平日晚餐\nC. 假日中午\nD. 假日晚餐")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        c = normalize_choice(user_in)
        if c in ("a", "平日中午"):
            state["reservation_settings"]["policy"]["peak_periods"] = ["weekday_lunch"]
            break
        if c in ("b", "平日晚餐"):
            state["reservation_settings"]["policy"]["peak_periods"] = ["weekday_dinner"]
            break
        if c in ("c", "假日中午"):
            state["reservation_settings"]["policy"]["peak_periods"] = ["weekend_brunch"]
            break
        if c in ("d", "假日晚餐"):
            state["reservation_settings"]["policy"]["peak_periods"] = ["weekend_dinner"]
            break
        print("🤖 Agent：\n請選 A / B / C / D。\n")

    # 11) popularity
    while True:
        print("\n🤖 Agent：\n最忙時段現場排隊狀況？\nA. 幾乎不用等\nB. 稍微等一下（約 1–3 組）\nC. 大排長龍")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        c = normalize_choice(user_in)
        if c in ("a", "不用等"):
            state["reservation_settings"]["policy"]["popularity"] = "low"
            break
        if c in ("b", "稍微", "1-3"):
            state["reservation_settings"]["policy"]["popularity"] = "medium"
            break
        if c in ("c", "大排長龍", "半小時"):
            state["reservation_settings"]["policy"]["popularity"] = "high"
            break
        print("🤖 Agent：\n請選 A / B / C。\n")

    # 12) peak online quota ratio
    while True:
        print("\n🤖 Agent：\n尖峰時段線上訂位大概要佔多少位置？\nA. 80%\nB. 50%\nC. 20%\nD. 不開線上")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        c = normalize_choice(user_in)
        if c in ("a", "80", "80%"):
            state["reservation_settings"]["policy"]["peak_online_quota_ratio"] = 0.8
            break
        if c in ("b", "50", "50%"):
            state["reservation_settings"]["policy"]["peak_online_quota_ratio"] = 0.5
            break
        if c in ("c", "20", "20%"):
            state["reservation_settings"]["policy"]["peak_online_quota_ratio"] = 0.2
            break
        if c in ("d", "不開"):
            state["reservation_settings"]["policy"]["peak_online_quota_ratio"] = 0.0
            state["reservation_settings"]["policy"]["peak_strategy"] = "no_online"
            break
        print("🤖 Agent：\n請選 A / B / C / D。\n")

    # 13) peak strategy
    if state["reservation_settings"]["policy"]["peak_strategy"] is None:
        while True:
            print("\n🤖 Agent：\n尖峰時段比較想怎麼做？\nA. 優先給線上\nB. 留較多給現場\nC. 尖峰不開線上")
            user_in = input("\n你：").strip()
            if user_in.lower() in ("exit", "quit"):
                return
            c = normalize_choice(user_in)
            if c in ("a", "優先線上"):
                state["reservation_settings"]["policy"]["peak_strategy"] = "online_first"
                break
            if c in ("b", "現場"):
                state["reservation_settings"]["policy"]["peak_strategy"] = "walkin_first"
                break
            if c in ("c", "不開"):
                state["reservation_settings"]["policy"]["peak_strategy"] = "no_online"
                state["reservation_settings"]["policy"]["peak_online_quota_ratio"] = 0.0
                break
            print("🤖 Agent：\n請選 A / B / C。\n")

    # 14) no-show tolerance
    while True:
        print("\n🤖 Agent：\n如果 10 組線上訂位有 1–2 組沒來，你可以接受嗎？\nA. 不太能接受\nB. 勉強可以\nC. 可以接受")
        user_in = input("\n你：").strip()
        if user_in.lower() in ("exit", "quit"):
            return
        c = normalize_choice(user_in)
        if c in ("a", "不太能", "不能"):
            state["reservation_settings"]["policy"]["no_show_tolerance"] = "low"
            break
        if c in ("b", "勉強"):
            state["reservation_settings"]["policy"]["no_show_tolerance"] = "medium"
            break
        if c in ("c", "可以"):
            state["reservation_settings"]["policy"]["no_show_tolerance"] = "high"
            break
        print("🤖 Agent：\n請選 A / B / C。\n")

    finalize_state(state)
    ok, msg = validate_settings_ready(state)
    if not ok:
        print("\n❌ 設定尚未完整：", msg)
        return

    result = build_internal_payload(state)
    ok, msg = validate_internal_payload(result)
    if not ok:
        print("\n❌ 內部 JSON 結構驗證失敗：", msg)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    result = ensure_json_roundtrip(result)

    print("\n✅ [Laravel 可視化設定草稿]")
    print(json.dumps(result["laravel_visual_payload"], ensure_ascii=False, indent=2))
    print("\n✅ [每日 Feed Job 輸入]\n")
    print(json.dumps(result["daily_feed_job_input"], ensure_ascii=False, indent=2))
    print("\n✅ [完整內部輸出 JSON]\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
