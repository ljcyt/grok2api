from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class WaterlinePlan:
    healthy: int
    low: int
    target: int
    emergency: bool
    deficit: int
    planned: int
    action: str  # none | replenish | emergency


def plan_replenish(
    healthy: int,
    *,
    low: int,
    target: int,
    emergency: int,
    max_per_round: int,
    success_rate: float = 1.0,
) -> WaterlinePlan:
    if healthy >= low:
        return WaterlinePlan(
            healthy=healthy,
            low=low,
            target=target,
            emergency=False,
            deficit=0,
            planned=0,
            action="none",
        )
    deficit = max(0, target - healthy)
    rate = max(0.05, min(1.0, success_rate or 1.0))
    raw = int(math.ceil(deficit / rate)) if deficit else 0
    planned = min(raw, max_per_round)
    is_emergency = healthy < emergency
    return WaterlinePlan(
        healthy=healthy,
        low=low,
        target=target,
        emergency=is_emergency,
        deficit=deficit,
        planned=planned,
        action="emergency" if is_emergency else "replenish",
    )
