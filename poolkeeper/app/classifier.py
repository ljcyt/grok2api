from __future__ import annotations

from typing import Any, Dict


def is_schedulable_account(account: Dict[str, Any]) -> bool:
    """Approximate healthy/schedulable Build account from G2A admin list payload."""
    if not account.get("enabled", True):
        return False
    if str(account.get("authStatus") or "") == "reauthRequired":
        return False
    if account.get("cooldownUntil"):
        return False
    provider = str(account.get("provider") or "")
    if provider and provider != "grok_build":
        return False
    quota = account.get("quota") or {}
    status = str(quota.get("status") or "active")
    if status in ("waitingReset", "probing"):
        return False
    # remaining unknown is ok; exhausted-looking free window still may be usable
    return True


def count_healthy(accounts: list[Dict[str, Any]]) -> int:
    return sum(1 for a in accounts if is_schedulable_account(a))


def classify_bucket(classification: str) -> str:
    c = (classification or "").lower()
    if c == "alive":
        return "alive"
    if c == "quota_limited":
        return "quota"
    if c in ("soft_alive",):
        return "soft"
    if c in ("network_error",):
        return "network"
    if c in ("suspect_dead", "confirmed_dead"):
        return "dead"
    if c == "skipped":
        return "skipped"
    return "error"
