from __future__ import annotations

import random
import statistics
from typing import Any, Dict

from .constraints import compute_peak_policy
from .state_tracker import get_value


class RestaurantSimulator:
    def __init__(self, capacity: int, online_ratio: float, no_show_prob: float, popularity_multiplier: float):
        self.capacity = capacity
        self.online_quota = int(capacity * online_ratio)
        self.no_show_prob = no_show_prob
        self.popularity_multiplier = popularity_multiplier

    def run_one_evening(self) -> Dict[str, float]:
        base_demand = random.uniform(0.8, 1.2) * self.capacity
        total_potential_demand = int(base_demand * self.popularity_multiplier)
        potential_online = int(total_potential_demand * 0.5)
        potential_walkin = total_potential_demand - potential_online

        booked_online = min(potential_online, self.online_quota)
        actual_online = sum(1 for _ in range(booked_online) if random.random() > self.no_show_prob)
        available_for_walkin = max(0, self.capacity - actual_online)
        actual_walkin = min(potential_walkin, available_for_walkin)

        total_seated = actual_online + actual_walkin
        rejected = max(0, potential_online - self.online_quota) + max(0, potential_walkin - available_for_walkin)
        return {
            "utilization": total_seated / self.capacity if self.capacity else 0.0,
            "lost_customers": rejected,
            "empty_seats": self.capacity - total_seated,
        }



def run_simulation_report(state: Dict[str, Any], runs: int = 200) -> Dict[str, Any]:
    peak_policy = compute_peak_policy(state)
    default_policy = get_value(state, "default_policy") or {}
    overrides = get_value(state, "time_block_overrides") or []
    effective = overrides[0] if overrides else default_policy

    if effective.get("online_enabled") is False:
        ratio = 0.0
    else:
        ratio = float(effective.get("online_quota_ratio", default_policy.get("online_quota_ratio", 0.5)))

    no_show_tol = get_value(state, "no_show_tolerance") or "medium"
    no_show_prob = {"low": 0.05, "medium": 0.15, "high": 0.30}.get(no_show_tol, 0.15)

    popularity = get_value(state, "popularity") or "medium"
    pop_mult = {"low": 0.7, "medium": 1.2, "high": 2.0}.get(popularity, 1.2)

    sim = RestaurantSimulator(
        capacity=peak_policy["capacity_hint"] or 20,
        online_ratio=ratio,
        no_show_prob=no_show_prob,
        popularity_multiplier=pop_mult,
    )

    results = [sim.run_one_evening() for _ in range(runs)]
    avg_util = statistics.mean(r["utilization"] for r in results)
    avg_lost = statistics.mean(r["lost_customers"] for r in results)
    avg_empty = statistics.mean(r["empty_seats"] for r in results)

    if avg_util < 0.7:
        advice = "座位利用率偏低，建議增加線上訂位可見性或放寬線上配額。"
    elif avg_lost > max(1, peak_policy["capacity_hint"] * 0.5):
        advice = "流失客人偏多，可考慮啟用候補名單或在忙時降低線上配額。"
    elif no_show_prob > 0.2 and ratio > 0.6:
        advice = "no-show 風險偏高且線上比例高，建議降低忙時線上比例。"
    else:
        advice = "目前設定在模擬中相對平衡。"

    return {
        "runs": runs,
        "avg_utilization": round(avg_util, 4),
        "avg_lost_customers": round(avg_lost, 2),
        "avg_empty_seats": round(avg_empty, 2),
        "advice": advice,
    }
