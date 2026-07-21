from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    grok2api_base_url: str = "http://127.0.0.1:8000"
    grok2api_admin_user: str = "admin"
    grok2api_admin_password: str = ""
    register_base_url: str = "http://127.0.0.1:8787"
    register_web_token: str = ""
    # public tunnel for register (documentation / optional remote use)
    register_public_url: str = "https://grok2.081488.xyz"

    interval_minutes: int = 30
    lock_file: str = "data/poolkeeper.lock"
    state_db: str = "data/poolkeeper.db"

    probe_model: str = "grok-4.5"
    probe_concurrency: int = 5
    probe_timeout_seconds: int = 20
    probe_max_accounts_per_round: int = 100
    probe_confirmed_dead_required: int = 2
    probe_suspect_recheck_minutes: int = 15
    probe_dry_run: bool = True

    cleanup_mode: str = "disable"  # report_only | disable | delete
    cleanup_max_actions_per_round: int = 20
    cleanup_action_interval_seconds: float = 2.0
    cleanup_hard_delete_grace_hours: int = 48

    waterline_low: int = 100
    waterline_target: int = 150
    waterline_emergency: int = 30
    waterline_required_model: str = "grok-4.5"

    replenish_enabled: bool = True
    inventory_first: bool = True
    max_import_per_round: int = 50
    max_register_per_round: int = 100
    replenish_cooldown_minutes: int = 60
    failure_cooldown_minutes: int = 180
    pause_when_existing_job_active: bool = True
    minimum_register_success_rate: float = 0.05

    max_delete_ratio_per_round: float = 0.02
    stop_on_network_failure_ratio: float = 0.30
    stop_on_soft_failure_ratio: float = 0.50

    # soft/hard demotion (cpa-grok-panel style); G2A uses higher priority first
    demotion_enabled: bool = True
    demotion_soft_enabled: bool = True
    demotion_half_open_enabled: bool = True
    demotion_skip_bots: bool = True
    demotion_soft_priority: int = 0
    demotion_hard_priority: int = -100
    demotion_soft_debt_threshold: float = 2.0
    demotion_hard_debt_threshold: float = 4.5
    demotion_debt_fail_401: float = 1.5
    demotion_debt_fail_429: float = 0.5
    demotion_debt_success_decay: float = 1.0
    demotion_count_429: bool = False
    demotion_hard_streak_threshold: int = 3
    demotion_half_open_success_threshold: int = 2
    demotion_cooldown_hours: tuple = (6, 12, 24)
    demotion_max_writes_per_round: int = 50

    request_timeout_seconds: float = 30.0
    once: bool = False
    metrics_port: int = 9108

    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        cfg = cls()
        file_path = Path(path or _env("POOLKEEPER_CONFIG", "config.yaml"))
        data: dict[str, Any] = {}
        if file_path.is_file():
            loaded = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data = loaded

        g2a = data.get("grok2api") or {}
        reg = data.get("register8787") or {}
        sch = data.get("scheduler") or {}
        probe = data.get("probe") or {}
        cleanup = data.get("cleanup") or {}
        water = data.get("waterline") or {}
        replenish = data.get("replenish") or {}
        safety = data.get("safety") or {}
        demotion = data.get("demotion") or {}

        cfg.grok2api_base_url = _env("G2A_BASE_URL", str(g2a.get("base_url") or cfg.grok2api_base_url))
        cfg.grok2api_admin_user = _env("G2A_ADMIN_USER", str(g2a.get("admin_user") or cfg.grok2api_admin_user))
        cfg.grok2api_admin_password = _env(
            "G2A_ADMIN_PASSWORD", str(g2a.get("admin_password") or cfg.grok2api_admin_password)
        )
        cfg.register_base_url = _env(
            "REGISTER_BASE_URL", str(reg.get("base_url") or cfg.register_base_url)
        )
        cfg.register_web_token = _env(
            "REGISTER_WEB_TOKEN", str(reg.get("web_token") or cfg.register_web_token)
        )
        cfg.register_public_url = _env(
            "REGISTER_PUBLIC_URL", str(reg.get("public_url") or cfg.register_public_url)
        )

        cfg.interval_minutes = _env_int(
            "POOLKEEPER_INTERVAL_MINUTES", int(sch.get("interval_minutes") or cfg.interval_minutes)
        )
        cfg.lock_file = str(sch.get("lock_file") or cfg.lock_file)
        cfg.state_db = str(data.get("state_db") or cfg.state_db)

        cfg.probe_model = str(probe.get("model") or cfg.probe_model)
        cfg.probe_concurrency = int(probe.get("concurrency") or cfg.probe_concurrency)
        cfg.probe_timeout_seconds = int(probe.get("timeout_seconds") or cfg.probe_timeout_seconds)
        cfg.probe_max_accounts_per_round = int(
            probe.get("max_accounts_per_round") or cfg.probe_max_accounts_per_round
        )
        cfg.probe_confirmed_dead_required = int(
            probe.get("confirmed_dead_required") or cfg.probe_confirmed_dead_required
        )
        cfg.probe_suspect_recheck_minutes = int(
            probe.get("suspect_recheck_minutes") or cfg.probe_suspect_recheck_minutes
        )
        if "dry_run" in probe:
            cfg.probe_dry_run = bool(probe.get("dry_run"))
        cfg.probe_dry_run = _env_bool("POOLKEEPER_DRY_RUN", cfg.probe_dry_run)

        cfg.cleanup_mode = str(cleanup.get("mode") or cfg.cleanup_mode)
        cfg.cleanup_max_actions_per_round = int(
            cleanup.get("max_actions_per_round") or cfg.cleanup_max_actions_per_round
        )
        cfg.cleanup_action_interval_seconds = float(
            cleanup.get("action_interval_seconds") or cfg.cleanup_action_interval_seconds
        )
        cfg.cleanup_hard_delete_grace_hours = int(
            cleanup.get("hard_delete_grace_hours") or cfg.cleanup_hard_delete_grace_hours
        )

        cfg.waterline_low = int(water.get("low") or cfg.waterline_low)
        cfg.waterline_target = int(water.get("target") or cfg.waterline_target)
        cfg.waterline_emergency = int(water.get("emergency") or cfg.waterline_emergency)
        cfg.waterline_required_model = str(
            water.get("required_model") or cfg.waterline_required_model
        )

        if "enabled" in replenish:
            cfg.replenish_enabled = bool(replenish.get("enabled"))
        cfg.replenish_enabled = _env_bool("POOLKEEPER_REPLENISH", cfg.replenish_enabled)
        if "inventory_first" in replenish:
            cfg.inventory_first = bool(replenish.get("inventory_first"))
        cfg.max_import_per_round = int(
            replenish.get("max_import_per_round") or cfg.max_import_per_round
        )
        cfg.max_register_per_round = int(
            replenish.get("max_register_per_round") or cfg.max_register_per_round
        )
        cfg.replenish_cooldown_minutes = int(
            replenish.get("cooldown_minutes") or cfg.replenish_cooldown_minutes
        )
        cfg.failure_cooldown_minutes = int(
            replenish.get("failure_cooldown_minutes") or cfg.failure_cooldown_minutes
        )
        if "pause_when_existing_job_active" in replenish:
            cfg.pause_when_existing_job_active = bool(replenish.get("pause_when_existing_job_active"))
        cfg.minimum_register_success_rate = float(
            replenish.get("minimum_success_rate")
            or safety.get("minimum_register_success_rate")
            or cfg.minimum_register_success_rate
        )

        cfg.max_delete_ratio_per_round = float(
            safety.get("max_delete_ratio_per_round") or cfg.max_delete_ratio_per_round
        )
        cfg.stop_on_network_failure_ratio = float(
            safety.get("stop_on_network_failure_ratio") or cfg.stop_on_network_failure_ratio
        )
        cfg.stop_on_soft_failure_ratio = float(
            safety.get("stop_on_soft_failure_ratio") or cfg.stop_on_soft_failure_ratio
        )

        cfg.request_timeout_seconds = float(
            g2a.get("request_timeout_seconds") or cfg.request_timeout_seconds
        )
        if demotion:
            if "enabled" in demotion:
                cfg.demotion_enabled = bool(demotion.get("enabled"))
            if "soft_enabled" in demotion:
                cfg.demotion_soft_enabled = bool(demotion.get("soft_enabled"))
            if "half_open_enabled" in demotion:
                cfg.demotion_half_open_enabled = bool(demotion.get("half_open_enabled"))
            if "skip_bots" in demotion:
                cfg.demotion_skip_bots = bool(demotion.get("skip_bots"))
            cfg.demotion_soft_priority = int(
                demotion.get("soft_priority", cfg.demotion_soft_priority)
            )
            cfg.demotion_hard_priority = int(
                demotion.get("hard_priority", cfg.demotion_hard_priority)
            )
            cfg.demotion_soft_debt_threshold = float(
                demotion.get("soft_debt_threshold", cfg.demotion_soft_debt_threshold)
            )
            cfg.demotion_hard_debt_threshold = float(
                demotion.get("hard_debt_threshold", cfg.demotion_hard_debt_threshold)
            )
            cfg.demotion_debt_fail_401 = float(
                demotion.get("debt_fail_401", cfg.demotion_debt_fail_401)
            )
            cfg.demotion_debt_fail_429 = float(
                demotion.get("debt_fail_429", cfg.demotion_debt_fail_429)
            )
            cfg.demotion_debt_success_decay = float(
                demotion.get("debt_success_decay", cfg.demotion_debt_success_decay)
            )
            if "count_429" in demotion:
                cfg.demotion_count_429 = bool(demotion.get("count_429"))
            cfg.demotion_hard_streak_threshold = int(
                demotion.get("hard_streak_threshold", cfg.demotion_hard_streak_threshold)
            )
            cfg.demotion_half_open_success_threshold = int(
                demotion.get(
                    "half_open_success_threshold",
                    cfg.demotion_half_open_success_threshold,
                )
            )
            hours = demotion.get("cooldown_hours")
            if isinstance(hours, (list, tuple)) and hours:
                cfg.demotion_cooldown_hours = tuple(int(x) for x in hours)
            cfg.demotion_max_writes_per_round = int(
                demotion.get("max_writes_per_round", cfg.demotion_max_writes_per_round)
            )
        cfg.demotion_enabled = _env_bool("POOLKEEPER_DEMOTION", cfg.demotion_enabled)
        cfg.once = _env_bool("POOLKEEPER_ONCE", False)
        cfg.metrics_port = _env_int("POOLKEEPER_METRICS_PORT", cfg.metrics_port)
        cfg.extra = data
        return cfg
