from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

# 核心 slot 定義：讓 agent 不只是收資料，而是有明確狀態可追蹤
SLOT_SPECS: Dict[str, Dict[str, Any]] = {
    "table_inventory": {"required": True, "group": "core"},
    "service_duration_sec": {"required": True, "group": "core"},
    "booking_hours_mode": {"required": True, "group": "core"},
    "online_booking_hours_json": {"required": False, "group": "core"},  # conditional
    "can_merge_tables": {"required": True, "group": "core"},
    "max_party_size": {"required": False, "group": "core"},  # conditional
    "service_scheduling_rules": {"required": True, "group": "core"},
    "default_policy": {"required": True, "group": "policy"},
    "time_block_overrides": {"required": False, "group": "policy"},
    "no_show_tolerance": {"required": False, "group": "simulation"},
    "popularity": {"required": False, "group": "simulation"},
    "seating_sections": {"required": False, "group": "optional"},
    "merchant_terms": {"required": False, "group": "optional"},
}


DEFAULT_MERCHANT_CONTEXT: Dict[str, Any] = {
    "store_id": 123,
    "merchant_id": "merchant-demo-001",
    "store_name": "示範餐廳",
    "category": "restaurant",
    "timezone": "Asia/Taipei",
    "telephone": "+886-2-1234-5678",
    "website_url": "https://example.com",
    "address": {
        "country": "TW",
        "region": "Taipei City",
        "locality": "Da'an District",
        "street_address": "仁愛路四段 100 號",
        "postal_code": "106",
    },
    "geo": {"latitude": 25.033, "longitude": 121.565},
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


def create_state(merchant_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    merchant = deepcopy(merchant_context or DEFAULT_MERCHANT_CONTEXT)
    return {
        "merchant_context": merchant,
        "slots": {
            name: {
                "value": None,
                "required": spec["required"],
                "group": spec["group"],
                "confirmed": False,
                "confidence": 0.0,
                "asked": 0,
            }
            for name, spec in SLOT_SPECS.items()
        },
        "conflicts": [],
        "warnings": [],
        "history": [],
    }


def get_value(state: Dict[str, Any], slot_name: str, default: Any = None) -> Any:
    return state["slots"].get(slot_name, {}).get("value", default)


def set_value(state: Dict[str, Any], slot_name: str, value: Any, confidence: float = 1.0) -> None:
    state["slots"][slot_name]["value"] = value
    state["slots"][slot_name]["confidence"] = float(confidence)
    state["slots"][slot_name]["confirmed"] = value is not None


def increment_asked(state: Dict[str, Any], slot_name: str) -> None:
    state["slots"][slot_name]["asked"] += 1


def add_history(state: Dict[str, Any], slot_name: str, question: str, answer: str, parsed_value: Any) -> None:
    state["history"].append(
        {
            "slot": slot_name,
            "question": question,
            "answer": answer,
            "parsed_value": deepcopy(parsed_value),
        }
    )


def unresolved_slots(state: Dict[str, Any]) -> List[str]:
    return [name for name, meta in state["slots"].items() if meta["value"] is None]


def core_slots_ready(state: Dict[str, Any]) -> bool:
    required_core = [
        "table_inventory",
        "service_duration_sec",
        "booking_hours_mode",
        "can_merge_tables",
        "service_scheduling_rules",
        "default_policy",
    ]
    return all(get_value(state, slot) is not None for slot in required_core)


def export_slot_values(state: Dict[str, Any]) -> Dict[str, Any]:
    return {k: deepcopy(v["value"]) for k, v in state["slots"].items()}
