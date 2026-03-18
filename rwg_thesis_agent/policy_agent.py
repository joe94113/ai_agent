from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .constraints import feed_readiness, finalize_conditional_slots, update_constraints
from .state_tracker import SLOT_SPECS, core_slots_ready, get_value


class DynamicPolicyAgent:
    name = "dynamic_policy"

    def choose_next_slot(self, state: Dict[str, Any]) -> Optional[str]:
        finalize_conditional_slots(state)
        update_constraints(state)

        candidates = self._candidate_slots(state)
        if not candidates:
            return None

        if feed_readiness(state):
            # feed 已可生成時，只再追問「真正會改變可執行規則」的 override。
            # 其餘 simulation / optional 欄位可先用預設值，讓 agent 以較少輪數完成設定。
            candidates = [c for c in candidates if c == "time_block_overrides"]
            if not candidates:
                return None

        ranked: List[Tuple[str, float]] = [(slot, self.question_score(slot, state)) for slot in candidates]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[0][0] if ranked else None

    def _candidate_slots(self, state: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        for slot in SLOT_SPECS.keys():
            if get_value(state, slot) is not None:
                continue
            if slot == "online_booking_hours_json" and get_value(state, "booking_hours_mode") != "custom":
                continue
            if slot == "max_party_size" and get_value(state, "can_merge_tables") is False:
                continue
            out.append(slot)
        return out

    def question_score(self, slot: str, state: Dict[str, Any]) -> float:
        group = SLOT_SPECS[slot]["group"]
        score = 0.0
        asked = state["slots"][slot]["asked"]
        confidence = state["slots"][slot]["confidence"]

        if group == "core":
            score += 10.0
        elif group == "policy":
            score += 6.0
        elif group == "simulation":
            score += 3.0
        else:
            score += 1.0

        if asked > 0:
            score -= 1.5 * asked
        if confidence < 0.6:
            score += 1.0

        # 在核心資料未齊前，先不要問太抽象的策略題
        if not core_slots_ready(state) and group != "core":
            score -= 4.0

        # 時段 override 是你論文的一個可執行亮點，核心完成後提高優先度
        if slot == "time_block_overrides" and core_slots_ready(state):
            score += 3.0

        # 如果不可併桌，max_party_size 可直接推導，不需要問
        if slot == "max_party_size" and get_value(state, "can_merge_tables") is False:
            score -= 100.0

        # 商家條款與座位區是 optional，優先度刻意降低
        if slot in {"merchant_terms", "seating_sections"}:
            score -= 2.5

        # no_show / popularity 只為 simulation，用於 recommendation，不應高於可執行規則
        if slot in {"no_show_tolerance", "popularity"}:
            score -= 1.0

        return score
