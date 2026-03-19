from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from .constraints import DAY_NAMES, compute_peak_policy, feed_readiness, update_constraints
from .simulation import run_simulation_report
from .state_tracker import export_slot_values, get_value

TZ_UTC8 = timezone(timedelta(hours=8))


def hhmm_to_colon(hhmm: str) -> str:
    s = str(hhmm).zfill(4)
    return f"{s[:2]}:{s[2:]}"



def summarize_hours(bh: List[Dict[str, Any]]) -> str:
    day_map: Dict[int, List[str]] = {d: [] for d in range(7)}
    for p in bh:
        od = int(p["open"]["day"])
        cd = int(p["close"]["day"])
        ot = hhmm_to_colon(p["open"]["time"])
        ct = hhmm_to_colon(p["close"]["time"])
        if od == cd:
            day_map[od].append(f"{ot}–{ct}")
        else:
            day_map[od].append(f"{ot}–隔天{ct}")

    order = [1, 2, 3, 4, 5, 6, 0]
    signatures = []
    for d in order:
        sig = "、".join(day_map[d]) if day_map[d] else "CLOSED"
        signatures.append(sig)

    parts = []
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and signatures[j + 1] == signatures[i]:
            j += 1
        start_day = DAY_NAMES[order[i]]
        end_day = DAY_NAMES[order[j]]
        label = start_day if i == j else f"{start_day}～{end_day}"
        if signatures[i] == "CLOSED":
            parts.append(f"{label} 公休")
        else:
            parts.append(f"{label} {signatures[i]}")
        i = j + 1
    return "；".join(parts)



def summarize_table_inventory(inv: List[Dict[str, Any]]) -> str:
    if not inv:
        return "（無）"
    return "、".join(f"{int(x['party_size'])} 人桌 {int(x['spots_total'])} 張" for x in sorted(inv, key=lambda x: int(x["party_size"])))



def summarize_sections(sections: List[Dict[str, str]]) -> str:
    if not sections:
        return "預設座位區"
    return "、".join(sec["room_name"] for sec in sections)



def build_reservation_settings(state: Dict[str, Any]) -> Dict[str, Any]:
    slots = export_slot_values(state)
    return {
        "table_inventory": slots["table_inventory"],
        "service_duration_sec": slots["service_duration_sec"],
        "booking_hours_mode": slots["booking_hours_mode"],
        "online_booking_hours_json": slots["online_booking_hours_json"],
        "can_merge_tables": slots["can_merge_tables"],
        "max_party_size": slots["max_party_size"],
        "seating_sections": slots["seating_sections"] or [],
        "merchant_terms": slots["merchant_terms"] or {"enabled": False, "text": None, "url": None, "source": "agent_input"},
        "service_scheduling_rules": slots["service_scheduling_rules"],
        "reservation_policy": {
            "default": slots["default_policy"],
            "time_block_overrides": slots["time_block_overrides"] or [],
            "risk_preferences": {"no_show_tolerance": slots["no_show_tolerance"] or "medium"},
            "simulation_inputs": {"popularity": slots["popularity"] or "medium"},
        },
    }



def build_laravel_visual_payload(state: Dict[str, Any], simulation_report: Dict[str, Any]) -> Dict[str, Any]:
    peak_policy = compute_peak_policy(state)
    merchant = state["merchant_context"]
    settings = build_reservation_settings(state)
    return {
        "merchant_card": {
            "store_id": merchant["store_id"],
            "merchant_id": merchant["merchant_id"],
            "store_name": merchant["store_name"],
            "telephone": merchant["telephone"],
            "website_url": merchant["website_url"],
            "business_hours_summary": summarize_hours(merchant["business_hours_json"]),
            "read_only": True,
        },
        "settings_form": {
            "table_inventory_summary": summarize_table_inventory(settings["table_inventory"]),
            "service_duration_sec": settings["service_duration_sec"],
            "online_booking_hours_summary": summarize_hours(settings["online_booking_hours_json"]),
            "can_merge_tables": settings["can_merge_tables"],
            "max_party_size": settings["max_party_size"],
            "seating_sections_summary": summarize_sections(settings["seating_sections"]),
            "merchant_terms": settings["merchant_terms"],
            "service_scheduling_rules": settings["service_scheduling_rules"],
            "reservation_policy": settings["reservation_policy"],
        },
        "derived": {
            "peak_policy": peak_policy,
            "warnings": list(state.get("warnings", [])),
            "simulation_report": simulation_report,
        },
        "excluded_partner_wide_features": ["special_request_box"],
    }



def build_daily_feed_job_input(state: Dict[str, Any], simulation_report: Dict[str, Any]) -> Dict[str, Any]:
    merchant = state["merchant_context"]
    settings = build_reservation_settings(state)
    peak_policy = compute_peak_policy(state)
    ready_for_solver = feed_readiness(state)
    return {
        "merchant_id": merchant["merchant_id"],
        "store_id": merchant["store_id"],
        "service_id": "reservation",
        "timezone": merchant["timezone"],
        "business_hours_json": merchant["business_hours_json"],
        "service_scheduling_rules": settings["service_scheduling_rules"],
        "merchant_terms": settings["merchant_terms"],
        "reservation_policy": settings["reservation_policy"],
        "inventory_solver_input": {
            "solver_owner": "laravel",
            "table_inventory": settings["table_inventory"],
            "online_booking_hours_json": settings["online_booking_hours_json"],
            "service_duration_sec": settings["service_duration_sec"],
            "slot_minutes": peak_policy["slot_minutes"],
            "can_merge_tables": settings["can_merge_tables"],
            "max_party_size": settings["max_party_size"],
            "party_size_range": {"min": 1, "max": settings["max_party_size"]},
            "seating_sections": settings["seating_sections"],
            "reservation_policy": settings["reservation_policy"],
            "solver_contract": {
                "generate_slot_level_availability_in": "laravel",
                "emit_party_sizes_from_1_to_max_party_size": True,
                "use_business_rules_not_raw_capacity_sum": True,
                "recompute_after_booking_or_cancellation": True,
            },
        },
        "advisory": {
            "peak_policy": peak_policy,
            "warnings": list(state.get("warnings", [])),
            "simulation_report": simulation_report,
        },
        "feed_generation": {
            "availability_days": 30,
            "processing_instruction": "PROCESS_AS_COMPLETE",
            "full_inventory": True,
            "availability_source": "laravel_inventory_solver",
        },
        "readiness": {
            "ready_for_laravel_solver": ready_for_solver,
            "ready_for_google_feed": False,
            "reason": "Google Availability feed should be assembled after Laravel computes slot-level spots_open/spots_total.",
        },
    }



def build_internal_output(state: Dict[str, Any]) -> Dict[str, Any]:
    update_constraints(state)
    simulation_report = run_simulation_report(state, runs=200)
    output = {
        "merchant_context": state["merchant_context"],
        "reservation_settings": build_reservation_settings(state),
        "laravel_visual_payload": build_laravel_visual_payload(state, simulation_report),
        "daily_feed_job_input": build_daily_feed_job_input(state, simulation_report),
        "meta": {
            "schema_version": "rwg-thesis-agent-v2-laravel-solver",
            "generated_at": datetime.now(TZ_UTC8).isoformat(),
            "agent_runtime": "python-package",
            "conflicts": list(state.get("conflicts", [])),
            "warnings": list(state.get("warnings", [])),
        },
    }

    # 保證 JSON round-trip 後仍合法，避免輸出壞掉
    serialized = json.dumps(output, ensure_ascii=False)
    return json.loads(serialized)
