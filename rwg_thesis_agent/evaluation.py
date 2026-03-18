from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Type

from .baseline_fsm import BaselineFSMAgent
from .builders import build_internal_output
from .constraints import feed_readiness, update_constraints
from .extractors import extract_slot_value
from .policy_agent import DynamicPolicyAgent
from .prompt_handlers import ask_text
from .state_tracker import create_state, increment_asked, add_history


@dataclass
class Scenario:
    name: str
    description: str
    answers: Dict[str, str]


SCENARIOS: List[Scenario] = [
    Scenario(
        name="balanced_basic",
        description="一般平衡型店家，沒有條款與座位分區。",
        answers={
            "table_inventory": "2人桌4張、4人桌3張、6人桌1張",
            "service_duration_sec": "60",
            "booking_hours_mode": "same",
            "can_merge_tables": "yes",
            "max_party_size": "10",
            "service_scheduling_rules": "booking=7200 cancel=86400",
            "default_policy": "balanced 0.5",
            "time_block_overrides": "none",
            "no_show_tolerance": "medium",
            "popularity": "medium",
            "seating_sections": "none",
            "merchant_terms": "none",
        },
    ),
    Scenario(
        name="weekend_walkin_only",
        description="假日晚餐很忙，該時段不開線上。",
        answers={
            "table_inventory": "2人桌4張、4人桌3張、6人桌1張",
            "service_duration_sec": "60",
            "booking_hours_mode": "same",
            "can_merge_tables": "yes",
            "max_party_size": "12",
            "service_scheduling_rules": "booking=7200 cancel=86400",
            "default_policy": "online_first 0.5",
            "time_block_overrides": "weekend_dinner=no_online",
            "no_show_tolerance": "medium",
            "popularity": "high",
            "seating_sections": "none",
            "merchant_terms": "none",
        },
    ),
    Scenario(
        name="no_merge_small_party",
        description="不允許併桌，且不想多問 optional 欄位。",
        answers={
            "table_inventory": "2人桌5張、4人桌2張",
            "service_duration_sec": "90",
            "booking_hours_mode": "same",
            "can_merge_tables": "no",
            "service_scheduling_rules": "booking=3600 cancel=0",
            "default_policy": "walkin_first 0.2",
            "time_block_overrides": "none",
            "no_show_tolerance": "low",
            "popularity": "low",
            "seating_sections": "none",
            "merchant_terms": "none",
        },
    ),
]


AGENT_TYPES: Dict[str, Type] = {
    "fsm": BaselineFSMAgent,
    "policy": DynamicPolicyAgent,
}



def run_scenario(agent_key: str, scenario: Scenario) -> Dict[str, Any]:
    state = create_state()
    agent = AGENT_TYPES[agent_key]()

    turns = 0
    asked_slots: List[str] = []

    while True:
        update_constraints(state)
        slot = agent.choose_next_slot(state)
        if slot is None:
            break

        question = ask_text(slot, state)
        increment_asked(state, slot)
        answer = scenario.answers.get(slot, "none")
        parsed_value, confidence, message = extract_slot_value(slot, answer, state)
        turns += 1
        asked_slots.append(slot)

        if parsed_value is not None:
            state["slots"][slot]["value"] = parsed_value
            state["slots"][slot]["confidence"] = confidence
            state["slots"][slot]["confirmed"] = True
        add_history(state, slot, question, answer, parsed_value)

        # 防禦：避免 parser 失敗造成無限迴圈
        if parsed_value is None:
            state["slots"][slot]["value"] = None
            break

    update_constraints(state)
    output = build_internal_output(state)
    return {
        "scenario": scenario.name,
        "description": scenario.description,
        "agent": agent_key,
        "turns": turns,
        "asked_slots": asked_slots,
        "conflict_count": len(state.get("conflicts", [])),
        "warning_count": len(state.get("warnings", [])),
        "feed_ready": feed_readiness(state),
        "final_output": output,
    }



def run_benchmark() -> Dict[str, Any]:
    results = []
    for scenario in SCENARIOS:
        for agent_key in ("fsm", "policy"):
            results.append(run_scenario(agent_key, scenario))

    summary: Dict[str, Dict[str, Any]] = {}
    for agent_key in ("fsm", "policy"):
        subset = [r for r in results if r["agent"] == agent_key]
        summary[agent_key] = {
            "avg_turns": round(sum(r["turns"] for r in subset) / len(subset), 2),
            "avg_conflicts": round(sum(r["conflict_count"] for r in subset) / len(subset), 2),
            "feed_ready_rate": round(sum(1 for r in subset if r["feed_ready"]) / len(subset), 2),
        }

    return {"results": results, "summary": summary}



def benchmark_as_json() -> str:
    return json.dumps(run_benchmark(), ensure_ascii=False, indent=2)
