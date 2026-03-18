from __future__ import annotations

from typing import Any, Dict, Optional

from .constraints import finalize_conditional_slots, update_constraints
from .state_tracker import get_value


FSM_ORDER = [
    "table_inventory",
    "service_duration_sec",
    "booking_hours_mode",
    "online_booking_hours_json",
    "can_merge_tables",
    "max_party_size",
    "service_scheduling_rules",
    "default_policy",
    "time_block_overrides",
    "no_show_tolerance",
    "popularity",
    "seating_sections",
    "merchant_terms",
]


class BaselineFSMAgent:
    name = "fixed_fsm"

    def choose_next_slot(self, state: Dict[str, Any]) -> Optional[str]:
        finalize_conditional_slots(state)
        update_constraints(state)

        for slot in FSM_ORDER:
            if get_value(state, slot) is not None:
                continue
            if slot == "online_booking_hours_json" and get_value(state, "booking_hours_mode") != "custom":
                continue
            if slot == "max_party_size" and get_value(state, "can_merge_tables") is False:
                continue
            return slot
        return None
