from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from .state_tracker import get_value

ALLOWED_PERIODS = {"weekday_lunch", "weekday_dinner", "weekend_brunch", "weekend_dinner"}
HHMM_RE = re.compile(r"^\d{4}$")


def ask_text(slot_name: str, state: Dict[str, Any]) -> str:
    business_hours = state["merchant_context"]["business_hours_json"]
    examples = {
        "table_inventory": "請輸入桌型，例如：2人桌4張、4人桌3張、6人桌1張。",
        "service_duration_sec": "平均用餐時間多久？可輸入 60 / 90 / 120 分鐘。",
        "booking_hours_mode": "線上可訂時段要怎麼設定？輸入 same（沿用營業時間扣掉用餐時間）或 custom（自訂）。",
        "online_booking_hours_json": "請輸入自訂線上可訂時段 JSON list，格式與 business_hours_json 相同。",
        "can_merge_tables": "大團體是否可以併桌？輸入 yes 或 no。",
        "max_party_size": "最多可接待幾人？請輸入數字，例如 8 / 10 / 12。",
        "service_scheduling_rules": "請輸入最晚可訂與最晚可線上取消，例如：booking=7200 cancel=86400（秒）。",
        "default_policy": "預設線上策略為何？例如：online_first 0.5、walkin_first 0.2、balanced 0.5、no_online。",
        "time_block_overrides": "是否有忙時特殊規則？例如：weekday_lunch=online_first,0.8; weekend_dinner=no_online。若沒有可輸入 none。",
        "no_show_tolerance": "對 no-show 的容忍度？輸入 low / medium / high。",
        "popularity": "店家熱門程度？輸入 low / medium / high。",
        "seating_sections": "是否有座位區？例如：bar, patio, private_room。沒有可輸入 none。",
        "merchant_terms": "商家條款可輸入 none，或用 text:條款內容 | url:https://...",
    }
    if slot_name == "online_booking_hours_json":
        return examples[slot_name] + f"\n目前預載營業時間共有 {len(business_hours)} 段，可直接貼 JSON。"
    return examples[slot_name]



def parse_slot(slot_name: str, text: str, state: Dict[str, Any]) -> Tuple[Any, float, str]:
    parsers = {
        "table_inventory": parse_table_inventory,
        "service_duration_sec": parse_duration,
        "booking_hours_mode": parse_booking_hours_mode,
        "online_booking_hours_json": parse_hours_json,
        "can_merge_tables": parse_yes_no,
        "max_party_size": parse_positive_int,
        "service_scheduling_rules": parse_scheduling_rules,
        "default_policy": parse_default_policy,
        "time_block_overrides": parse_time_block_overrides,
        "no_show_tolerance": parse_enum_low_medium_high,
        "popularity": parse_enum_low_medium_high,
        "seating_sections": parse_seating_sections,
        "merchant_terms": parse_merchant_terms,
    }
    parser = parsers[slot_name]
    value = parser(text)
    if value is None:
        return None, 0.0, f"無法解析 {slot_name}"
    return value, 1.0, "ok"



def parse_table_inventory(text: str) -> List[Dict[str, int]] | None:
    text = text.strip()
    if text.startswith("["):
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                return [
                    {"party_size": int(x["party_size"]), "spots_total": int(x["spots_total"])}
                    for x in obj
                ]
        except Exception:
            return None
    matches = re.findall(r"(\d+)\s*人桌\s*(\d+)\s*張", text)
    if not matches:
        return None
    return [{"party_size": int(ps), "spots_total": int(cnt)} for ps, cnt in matches]



def parse_duration(text: str) -> int | None:
    m = re.search(r"(\d+)", text)
    if not m:
        return None
    minutes = int(m.group(1))
    if minutes not in (60, 90, 120):
        return None
    return minutes * 60



def parse_booking_hours_mode(text: str) -> str | None:
    t = text.strip().lower()
    if t in {"same", "auto", "same_as_business_hours_minus_duration"}:
        return "same_as_business_hours_minus_duration"
    if t in {"custom", "manual"}:
        return "custom"
    return None



def parse_hours_json(text: str) -> List[Dict[str, Any]] | None:
    text = text.strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "online_booking_hours_json" in obj:
                obj = obj["online_booking_hours_json"]
            if isinstance(obj, list):
                return obj
        except Exception:
            return None
    if text.startswith("["):
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                return obj
        except Exception:
            return None
    return None



def parse_yes_no(text: str) -> bool | None:
    t = text.strip().lower()
    if t in {"yes", "y", "true", "可以", "可", "能", "1"}:
        return True
    if t in {"no", "n", "false", "不行", "不能", "否", "0"}:
        return False
    return None



def parse_positive_int(text: str) -> int | None:
    m = re.search(r"(\d+)", text)
    if not m:
        return None
    n = int(m.group(1))
    return n if n > 0 else None



def parse_scheduling_rules(text: str) -> Dict[str, int] | None:
    text = text.strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            return {
                "min_advance_booking_sec": int(obj["min_advance_booking_sec"]),
                "min_advance_online_canceling_sec": int(obj["min_advance_online_canceling_sec"]),
            }
        except Exception:
            return None
    booking = re.search(r"booking\s*=\s*(\d+)", text, re.I)
    cancel = re.search(r"cancel\s*=\s*(\d+)", text, re.I)
    if booking and cancel:
        return {
            "min_advance_booking_sec": int(booking.group(1)),
            "min_advance_online_canceling_sec": int(cancel.group(1)),
        }
    nums = re.findall(r"(\d+)", text)
    if len(nums) >= 2:
        return {
            "min_advance_booking_sec": int(nums[0]),
            "min_advance_online_canceling_sec": int(nums[1]),
        }
    return None



def parse_default_policy(text: str) -> Dict[str, Any] | None:
    t = text.strip().lower()
    if t == "no_online":
        return {"online_enabled": False, "online_quota_ratio": 0.0, "channel_priority": "walkin_only"}
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            return {
                "online_enabled": bool(obj.get("online_enabled", True)),
                "online_quota_ratio": float(obj.get("online_quota_ratio", 0.5)),
                "channel_priority": str(obj.get("channel_priority", "balanced")),
            }
        except Exception:
            return None
    m = re.match(r"(online_first|walkin_first|balanced)\s*(0(?:\.\d+)?|1(?:\.0+)?)?", t)
    if m:
        priority = m.group(1)
        ratio = float(m.group(2) or (0.5 if priority != "walkin_first" else 0.2))
        return {"online_enabled": True, "online_quota_ratio": ratio, "channel_priority": priority}
    return None



def parse_time_block_overrides(text: str) -> List[Dict[str, Any]] | None:
    t = text.strip().lower()
    if t in {"none", "skip", "無", "沒有"}:
        return []
    if t.startswith("["):
        try:
            obj = json.loads(t)
            return obj if isinstance(obj, list) else None
        except Exception:
            return None
    result: List[Dict[str, Any]] = []
    parts = [p.strip() for p in text.split(";") if p.strip()]
    for part in parts:
        if "=" not in part:
            return None
        period_raw, rule_raw = [x.strip().lower() for x in part.split("=", 1)]
        periods = [p.strip() for p in period_raw.split(",") if p.strip()]
        if not periods or any(p not in ALLOWED_PERIODS for p in periods):
            return None

        if rule_raw == "no_online":
            result.append(
                {
                    "periods": periods,
                    "online_enabled": False,
                    "channel_priority": "walkin_only",
                }
            )
            continue

        m = re.match(r"(online_first|walkin_first|balanced)\s*,\s*(0(?:\.\d+)?|1(?:\.0+)?)", rule_raw)
        if not m:
            return None
        result.append(
            {
                "periods": periods,
                "online_enabled": True,
                "channel_priority": m.group(1),
                "online_quota_ratio": float(m.group(2)),
            }
        )
    return result



def parse_enum_low_medium_high(text: str) -> str | None:
    t = text.strip().lower()
    if t in {"low", "medium", "high"}:
        return t
    mapping = {"低": "low", "中": "medium", "高": "high"}
    return mapping.get(t)



def parse_seating_sections(text: str) -> List[Dict[str, str]] | None:
    t = text.strip()
    if not t:
        return None
    if t.lower() in {"none", "skip", "無", "沒有"}:
        return []
    items = [x.strip() for x in t.split(",") if x.strip()]
    if not items:
        return None
    return [{"room_id": slugify(x), "room_name": x} for x in items]



def parse_merchant_terms(text: str) -> Dict[str, Any] | None:
    t = text.strip()
    if not t:
        return None
    if t.lower() in {"none", "skip", "無", "沒有"}:
        return {"enabled": False, "text": None, "url": None, "source": "agent_input"}
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            return {
                "enabled": bool(obj.get("enabled", True)),
                "text": obj.get("text"),
                "url": obj.get("url"),
                "source": "agent_input",
            }
        except Exception:
            return None

    text_part = None
    url_part = None
    for piece in [p.strip() for p in t.split("|")]:
        if piece.lower().startswith("text:"):
            text_part = piece[5:].strip() or None
        elif piece.lower().startswith("url:"):
            url_part = piece[4:].strip() or None
    if text_part is None and url_part is None:
        return None
    return {"enabled": True, "text": text_part, "url": url_part, "source": "agent_input"}



def slugify(text: str) -> str:
    s = re.sub(r"\s+", "_", text.strip().lower())
    s = re.sub(r"[^a-z0-9_\-]", "", s)
    return s or "section"
