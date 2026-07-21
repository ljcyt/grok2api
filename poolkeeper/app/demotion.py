"""Soft/hard demotion + half-open recovery (from cpa-grok-panel).

Priority-only scheduling: higher Grok2API priority is preferred (ORDER BY priority DESC).
Soft/hard demotion write lower priorities so bad accounts are selected less often.
Does not auto-delete; confirmed_dead cleanup stays separate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


CLASS_NONE = "none"
CLASS_SOFT = "soft"
CLASS_HARD = "hard"
CLASS_HALF_OPEN = "half_open"

# Default cooldown ladder (hours), same spirit as panel 6h→12h→24h
DEFAULT_COOLDOWN_HOURS = (6, 12, 24)


@dataclass
class DemotionPolicy:
    enabled: bool = True
    soft_enabled: bool = True
    half_open_enabled: bool = True
    skip_bots: bool = True

    soft_priority: int = 0  # G2A default is 1; soft goes below baseline
    hard_priority: int = -100
    soft_debt_threshold: float = 2.0
    hard_debt_threshold: float = 4.5
    debt_fail_401: float = 1.5
    debt_fail_429: float = 0.5
    debt_success_decay: float = 1.0
    count_429: bool = False
    hard_streak_threshold: int = 3
    half_open_success_threshold: int = 2
    cooldown_hours: tuple[int, ...] = DEFAULT_COOLDOWN_HOURS

    @classmethod
    def from_config(cls, cfg: Any) -> "DemotionPolicy":
        hours = getattr(cfg, "demotion_cooldown_hours", None) or DEFAULT_COOLDOWN_HOURS
        if isinstance(hours, str):
            hours = tuple(int(x.strip()) for x in hours.split(",") if x.strip())
        return cls(
            enabled=bool(getattr(cfg, "demotion_enabled", True)),
            soft_enabled=bool(getattr(cfg, "demotion_soft_enabled", True)),
            half_open_enabled=bool(getattr(cfg, "demotion_half_open_enabled", True)),
            skip_bots=bool(getattr(cfg, "demotion_skip_bots", True)),
            soft_priority=int(getattr(cfg, "demotion_soft_priority", 0)),
            hard_priority=int(getattr(cfg, "demotion_hard_priority", -100)),
            soft_debt_threshold=float(getattr(cfg, "demotion_soft_debt_threshold", 2.0)),
            hard_debt_threshold=float(getattr(cfg, "demotion_hard_debt_threshold", 4.5)),
            debt_fail_401=float(getattr(cfg, "demotion_debt_fail_401", 1.5)),
            debt_fail_429=float(getattr(cfg, "demotion_debt_fail_429", 0.5)),
            debt_success_decay=float(getattr(cfg, "demotion_debt_success_decay", 1.0)),
            count_429=bool(getattr(cfg, "demotion_count_429", False)),
            hard_streak_threshold=int(getattr(cfg, "demotion_hard_streak_threshold", 3)),
            half_open_success_threshold=int(
                getattr(cfg, "demotion_half_open_success_threshold", 2)
            ),
            cooldown_hours=tuple(hours) if hours else DEFAULT_COOLDOWN_HOURS,
        )


@dataclass
class DemotionDecision:
    class_name: str = CLASS_NONE
    target_priority: Optional[int] = None
    write_priority: bool = False
    enter_half_open: bool = False
    restore_baseline: bool = False
    reason: str = ""
    debt_score: float = 0.0
    hard_streak: int = 0
    half_open_successes: int = 0
    baseline_priority: Optional[int] = None
    cooldown_hours: int = 0


def _is_attributed_fail(classification: str, status_code: int, count_429: bool) -> bool:
    c = (classification or "").lower()
    if c in ("suspect_dead", "confirmed_dead"):
        return True
    if status_code in (400, 401, 403):
        return True
    if count_429 and status_code == 429:
        return True
    return False


def _is_success(classification: str) -> bool:
    return (classification or "").lower() in ("alive", "quota_limited", "soft_alive")


def apply_probe_evidence(
    *,
    prev: Dict[str, Any],
    classification: str,
    status_code: int,
    current_priority: int,
    bot_flagged: bool,
    policy: DemotionPolicy,
    now: float,
) -> tuple[Dict[str, Any], DemotionDecision]:
    """Update local demotion fields from one probe result; return (new_state, decision)."""
    state = dict(prev or {})
    debt = float(state.get("debt_score") or 0.0)
    streak = int(state.get("hard_streak") or 0)
    class_name = str(state.get("demotion_class") or CLASS_NONE)
    half_ok = int(state.get("half_open_successes") or 0)
    baseline = state.get("baseline_priority")
    if baseline is not None:
        baseline = int(baseline)
    cooldown_step = int(state.get("cooldown_step") or 0)
    demoted_at = float(state.get("demoted_at") or 0)
    half_open_since = float(state.get("half_open_since") or 0)

    decision = DemotionDecision(
        class_name=class_name,
        debt_score=debt,
        hard_streak=streak,
        half_open_successes=half_ok,
        baseline_priority=baseline,
    )

    if not policy.enabled:
        return state, decision

    attributed = _is_attributed_fail(classification, status_code, policy.count_429)
    success = _is_success(classification)

    # --- update evidence ---
    if attributed:
        if status_code == 429 and policy.count_429:
            debt += policy.debt_fail_429
        else:
            debt += policy.debt_fail_401
        streak += 1
        state["last_evidence_at"] = now
        state["last_failure_code"] = str(status_code or classification)
    elif success:
        debt = max(0.0, debt - policy.debt_success_decay)
        streak = 0
        if class_name == CLASS_HALF_OPEN:
            half_ok += 1
    else:
        # network / soft / unknown: clear hard streak only, keep debt
        streak = 0

    # --- decide class transitions ---
    want_hard = streak >= policy.hard_streak_threshold or debt >= policy.hard_debt_threshold
    want_soft = (
        policy.soft_enabled
        and debt >= policy.soft_debt_threshold
        and not want_hard
        and class_name not in (CLASS_HARD, CLASS_HALF_OPEN)
    )

    if class_name == CLASS_HALF_OPEN:
        if attributed:
            # fail closed → hard
            if baseline is None:
                baseline = current_priority
            class_name = CLASS_HARD
            decision.write_priority = True
            decision.target_priority = policy.hard_priority
            decision.reason = "half_open_fail_to_hard"
            demoted_at = now
            half_ok = 0
            half_open_since = 0
            # escalate cooldown step
            cooldown_step = min(cooldown_step + 1, len(policy.cooldown_hours) - 1)
        elif success and half_ok >= policy.half_open_success_threshold:
            class_name = CLASS_NONE
            decision.restore_baseline = True
            decision.write_priority = True
            decision.target_priority = baseline if baseline is not None else 1
            decision.reason = "half_open_restored"
            debt = 0.0
            streak = 0
            half_ok = 0
            half_open_since = 0
            demoted_at = 0
            cooldown_step = 0
            baseline = None
    elif want_hard:
        if baseline is None and class_name == CLASS_NONE:
            baseline = current_priority
        elif baseline is None:
            baseline = current_priority
        if class_name != CLASS_HARD:
            class_name = CLASS_HARD
            decision.write_priority = True
            decision.target_priority = policy.hard_priority
            decision.reason = "hard_demotion"
            demoted_at = now
            half_ok = 0
            half_open_since = 0
    elif want_soft:
        if baseline is None:
            baseline = current_priority
        if class_name != CLASS_SOFT:
            class_name = CLASS_SOFT
            decision.write_priority = True
            decision.target_priority = policy.soft_priority
            decision.reason = "soft_demotion"
            demoted_at = now

    # bot: skip automatic half-open / restore writes later
    decision.class_name = class_name
    decision.debt_score = debt
    decision.hard_streak = streak
    decision.half_open_successes = half_ok
    decision.baseline_priority = baseline
    if demoted_at:
        hours = policy.cooldown_hours[
            min(cooldown_step, len(policy.cooldown_hours) - 1)
        ]
        decision.cooldown_hours = hours

    state.update(
        {
            "debt_score": debt,
            "hard_streak": streak,
            "demotion_class": class_name,
            "half_open_successes": half_ok,
            "baseline_priority": baseline,
            "demoted_at": demoted_at or None,
            "half_open_since": half_open_since or None,
            "cooldown_step": cooldown_step,
            "target_priority": decision.target_priority,
            "bot_flagged": bool(bot_flagged),
            "updated_at": now,
        }
    )
    return state, decision


def maybe_enter_half_open(
    state: Dict[str, Any],
    *,
    policy: DemotionPolicy,
    now: float,
    bot_flagged: bool,
) -> tuple[Dict[str, Any], DemotionDecision]:
    """If hard/soft demotion cooled down, enter half-open observation priority."""
    decision = DemotionDecision(
        class_name=str(state.get("demotion_class") or CLASS_NONE),
        debt_score=float(state.get("debt_score") or 0),
        hard_streak=int(state.get("hard_streak") or 0),
        half_open_successes=int(state.get("half_open_successes") or 0),
        baseline_priority=state.get("baseline_priority"),
    )
    if not policy.enabled or not policy.half_open_enabled:
        return state, decision
    class_name = decision.class_name
    if class_name not in (CLASS_HARD, CLASS_SOFT):
        return state, decision
    if policy.skip_bots and bot_flagged:
        decision.reason = "skip_bot"
        return state, decision
    demoted_at = float(state.get("demoted_at") or 0)
    if demoted_at <= 0:
        return state, decision
    step = int(state.get("cooldown_step") or 0)
    hours = policy.cooldown_hours[min(step, len(policy.cooldown_hours) - 1)]
    if now - demoted_at < hours * 3600:
        decision.reason = "cooldown_pending"
        decision.cooldown_hours = hours
        return state, decision

    state = dict(state)
    state["demotion_class"] = CLASS_HALF_OPEN
    state["half_open_since"] = now
    state["half_open_successes"] = 0
    state["target_priority"] = policy.soft_priority
    decision.class_name = CLASS_HALF_OPEN
    decision.write_priority = True
    decision.enter_half_open = True
    decision.target_priority = policy.soft_priority
    decision.reason = "enter_half_open"
    decision.cooldown_hours = hours
    return state, decision
