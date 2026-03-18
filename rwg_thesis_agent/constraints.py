from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Dict, List, Tuple

from .state_tracker import get_value, set_value


DAY_NAMES = {1: "週一", 2: "週二", 3: "週三", 4: "週四", 5: "週五", 6: "週六", 0: "週日"}


def is_valid_hhmm(hhmm: str) -> bool:
    s = str(hhmm).zfill(4)
    if len(s) != 4 or not s.isdigit():
        return False
    hh = int(s[:2])
    mm = int(s[2:])
    return 0 <= hh <= 23 and 0 <= mm <= 59



def hhmm_to_minutes(hhmm: str) -> int:
    s = str(hhmm).zfill(4)
    return int(s[:2]) * 60 + int(s[2:])



def minutes_to_hhmm(minutes: int) -> str:
    minutes = max(0, int(minutes))
    hh = minutes // 60
    mm = minutes % 60
    return f"{hh:02d}{mm:02d}"



def validate_hours_json(bh: Any) -> Tuple[bool, str]:
    if not isinstance(bh, list) or not bh:
        return False, "hours_json 必須是非空 list"
    for i, p in enumerate(bh):
        if not isinstance(p, dict):
            return False, f"hours_json[{i}] 必須是 object"
        o = p.get("open")
        c = p.get("close")
        if not isinstance(o, dict) or not isinstance(c, dict):
            return False, f"hours_json[{i}] 缺少 open/close"
        if o.get("day") not in range(7) or c.get("day") not in range(7):
            return False, f"hours_json[{i}] day 必須介於 0~6"
        if not is_valid_hhmm(str(o.get("time", ""))) or not is_valid_hhmm(str(c.get("time", ""))):
            return False, f"hours_json[{i}] time 必須是合法 HHMM"
    return True, "ok"



def validate_table_inventory(res: Any) -> Tuple[bool, str]:
    if not isinstance(res, list) or not res:
        return False, "table_inventory 必須是非空 list"
    for i, r in enumerate(res):
        if not isinstance(r, dict):
            return False, f"table_inventory[{i}] 必須是 object"
        if int(r.get("party_size", 0)) <= 0 or int(r.get("spots_total", -1)) < 0:
            return False, f"table_inventory[{i}] 格式不正確"
    return True, "ok"



def capacity_hint(table_inventory: List[Dict[str, int]]) -> int:
    return max(1, sum(int(x["party_size"]) * int(x["spots_total"]) for x in table_inventory))



def derive_online_booking_hours(business_hours_json: List[Dict[str, Any]], duration_sec: int) -> List[Dict[str, Any]]:
    dur_min = max(0, int(duration_sec) // 60)
    out = []
    for period in business_hours_json:
        o = period["open"]
        c = period["close"]
        od = int(o["day"])
        cd = int(c["day"])
        ot = str(o["time"]).zfill(4)
        ct = str(c["time"]).zfill(4)
        if od != cd:
            out.append(deepcopy(period))
            continue
        start = hhmm_to_minutes(ot)
        end = hhmm_to_minutes(ct)
        last_start = max(start, end - dur_min)
        out.append({"open": {"day": od, "time": ot}, "close": {"day": od, "time": minutes_to_hhmm(last_start)}})
    return out



def finalize_conditional_slots(state: Dict[str, Any]) -> None:
    table_inv = get_value(state, "table_inventory")
    if table_inv:
        ok, _ = validate_table_inventory(table_inv)
        if not ok:
            return

    mode = get_value(state, "booking_hours_mode")
    duration = get_value(state, "service_duration_sec")
    if mode == "same_as_business_hours_minus_duration" and duration and get_value(state, "online_booking_hours_json") is None:
        derived = derive_online_booking_hours(state["merchant_context"]["business_hours_json"], duration)
        set_value(state, "online_booking_hours_json", derived, confidence=1.0)

    can_merge = get_value(state, "can_merge_tables")
    if can_merge is False and get_value(state, "max_party_size") is None and table_inv:
        inferred = max(int(x["party_size"]) for x in table_inv)
        set_value(state, "max_party_size", inferred, confidence=1.0)




def update_constraints(state: Dict[str, Any]) -> Dict[str, Any]:
    finalize_conditional_slots(state)
    conflicts: List[str] = []
    warnings: List[str] = []

    table_inv = get_value(state, "table_inventory")
    if table_inv is not None:
        ok, msg = validate_table_inventory(table_inv)
        if not ok:
            conflicts.append(msg)

    business_hours = state["merchant_context"]["business_hours_json"]
    ok, msg = validate_hours_json(business_hours)
    if not ok:
        conflicts.append(f"merchant_context.business_hours_json 錯誤：{msg}")

    online_hours = get_value(state, "online_booking_hours_json")
    if online_hours is not None:
        ok, msg = validate_hours_json(online_hours)
        if not ok:
            conflicts.append(f"online_booking_hours_json 錯誤：{msg}")
        elif not online_hours_within_business_hours(online_hours, business_hours):
            conflicts.append("線上可訂時段超出營業時間")

    can_merge = get_value(state, "can_merge_tables")
    max_party = get_value(state, "max_party_size")
    if table_inv:
        max_table = max(int(x["party_size"]) for x in table_inv)
        if can_merge is False and max_party and int(max_party) > max_table:
            conflicts.append("不可併桌時，max_party_size 不可大於最大單桌尺寸")
        if can_merge is True and max_party and int(max_party) > max_table:
            warnings.append("max_party_size 大於最大單桌尺寸，表示此店依賴併桌")

    default_policy = get_value(state, "default_policy")
    if default_policy:
        enabled = bool(default_policy.get("online_enabled", True))
        ratio = float(default_policy.get("online_quota_ratio", 0.0))
        if not enabled and ratio > 0:
            conflicts.append("default_policy 設為不開線上時，online_quota_ratio 應為 0")

    overrides = get_value(state, "time_block_overrides") or []
    seen_periods = set()
    for ov in overrides:
        for p in ov.get("periods", []):
            if p in seen_periods:
                warnings.append(f"period {p} 出現重複 override，後端需定義覆蓋順序")
            seen_periods.add(p)
        if ov.get("online_enabled") is False and ov.get("online_quota_ratio", 0) not in (0, 0.0, None):
            conflicts.append("override 設為 no_online 時，不應再帶 online_quota_ratio")

    state["conflicts"] = conflicts
    state["warnings"] = warnings
    return {"conflicts": conflicts, "warnings": warnings}



def online_hours_within_business_hours(online: List[Dict[str, Any]], business: List[Dict[str, Any]]) -> bool:
    business_map: Dict[Tuple[int, str], str] = {}
    for p in business:
        key = (int(p["open"]["day"]), str(p["open"]["time"]).zfill(4))
        business_map[key] = str(p["close"]["time"]).zfill(4)

    # 這裡做簡化：要求每段線上時段必須以同一天同開始時間對應到某個營業時段，且 close 不得晚於 business close
    for p in online:
        key = (int(p["open"]["day"]), str(p["open"]["time"]).zfill(4))
        close = str(p["close"]["time"]).zfill(4)
        if key not in business_map:
            return False
        if hhmm_to_minutes(close) > hhmm_to_minutes(business_map[key]):
            return False
    return True



def feed_readiness(state: Dict[str, Any]) -> bool:
    required = [
        "table_inventory",
        "service_duration_sec",
        "booking_hours_mode",
        "online_booking_hours_json",
        "can_merge_tables",
        "max_party_size",
        "service_scheduling_rules",
        "default_policy",
    ]
    return all(get_value(state, x) is not None for x in required) and not state.get("conflicts")



def typical_party_size(table_inventory: List[Dict[str, int]]) -> int:
    weighted = []
    total = 0
    for row in table_inventory:
        ps = int(row["party_size"])
        wt = int(row["spots_total"])
        if wt > 0:
            weighted.append((ps, wt))
            total += wt
    weighted.sort(key=lambda x: x[0])
    cum = 0
    for ps, wt in weighted:
        cum += wt
        if cum >= (total + 1) / 2:
            return ps
    return weighted[-1][0] if weighted else 2



def compute_peak_policy(state: Dict[str, Any]) -> Dict[str, Any]:
    table_inv = get_value(state, "table_inventory") or []
    cap = capacity_hint(table_inv) if table_inv else 0
    duration_sec = get_value(state, "service_duration_sec") or 3600
    default_policy = get_value(state, "default_policy") or {
        "online_enabled": True,
        "online_quota_ratio": 0.5,
        "channel_priority": "balanced",
    }
    overrides = get_value(state, "time_block_overrides") or []
    peak = overrides[0] if overrides else default_policy
    ratio = 0.0 if not peak.get("online_enabled", True) else float(peak.get("online_quota_ratio", default_policy.get("online_quota_ratio", 0.5)))
    slot_minutes = 30
    duration_slots = max(1, math.ceil((duration_sec / 60) / slot_minutes))
    typical_ps = typical_party_size(table_inv) if table_inv else 2
    seat_budget = min(cap, max(0, math.floor(cap * ratio)))
    party_limit = max(0, seat_budget // max(1, typical_ps * duration_slots))
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
