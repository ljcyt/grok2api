from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# allow `python -m app.main` from poolkeeper/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audit import dump_json, run_id_now, setup_logging
from app.classifier import classify_bucket, count_healthy
from app.clients.grok2api import Grok2APIClient
from app.clients.register8787 import Register8787Client
from app.config import Config
from app.scheduler import file_lock, sleep_interval
from app.state import StateStore
from app.waterline import plan_replenish

log = setup_logging()


def _account_id(account: Dict[str, Any]) -> str:
    return str(account.get("id") or account.get("accountId") or "").strip()


def select_probe_candidates(
    accounts: List[Dict[str, Any]],
    store: StateStore,
    *,
    limit: int,
    suspect_recheck_minutes: int,
) -> List[str]:
    now = time.time()
    suspect: List[str] = []
    stale: List[str] = []
    attention: List[str] = []
    normal: List[str] = []

    for acc in accounts:
        aid = _account_id(acc)
        if not aid:
            continue
        st = store.get_probe_state(aid) or {}
        last = st.get("last_result")
        last_at = float(st.get("last_probe_at") or 0)
        if last == "suspect_dead":
            if now - last_at >= suspect_recheck_minutes * 60:
                suspect.append(aid)
            continue
        if str(acc.get("authStatus") or "") == "reauthRequired" or not acc.get(
            "enabled", True
        ):
            attention.append(aid)
            continue
        if last_at == 0 or now - last_at > 6 * 3600:
            stale.append(aid)
        else:
            normal.append(aid)

    ordered = suspect + stale + attention + normal
    # de-dup preserve order
    seen = set()
    out: List[str] = []
    for aid in ordered:
        if aid in seen:
            continue
        seen.add(aid)
        out.append(aid)
        if len(out) >= limit:
            break
    return out


def run_once(cfg: Config) -> Dict[str, Any]:
    run_id = run_id_now()
    store = StateStore(cfg.state_db)
    store.start_run(run_id)
    stats: Dict[str, Any] = {
        "run_id": run_id,
        "pool_total": 0,
        "probed": 0,
        "alive": 0,
        "quota_limited": 0,
        "soft_failed": 0,
        "network_failed": 0,
        "confirmed_dead": 0,
        "disabled": 0,
        "deleted": 0,
        "inventory_imported": 0,
        "register_started": 0,
        "register_target": 0,
        "healthy_before": 0,
        "healthy_after": 0,
        "dry_run": cfg.probe_dry_run,
    }

    g2a = Grok2APIClient(
        cfg.grok2api_base_url,
        cfg.grok2api_admin_user,
        cfg.grok2api_admin_password,
        timeout=cfg.request_timeout_seconds,
    )
    reg = Register8787Client(
        cfg.register_base_url,
        cfg.register_web_token,
        timeout=cfg.request_timeout_seconds,
    )

    if not g2a.health():
        log.error("grok2api healthz failed")
        store.finish_run(run_id, stats, status="g2a_unhealthy")
        return stats
    if cfg.register_web_token and not reg.health():
        log.warning("8787 health failed (continue without replenish)")

    try:
        summary = g2a.summary()
        log.info("g2a summary: %s", dump_json(summary if isinstance(summary, dict) else {}))
    except Exception as exc:
        log.warning("summary failed: %s", exc)

    accounts = g2a.list_accounts(provider="grok_build")
    stats["pool_total"] = len(accounts)
    healthy = count_healthy(accounts)
    stats["healthy_before"] = healthy
    log.info("pool_total=%s healthy=%s", len(accounts), healthy)

    # --- probe ---
    candidates = select_probe_candidates(
        accounts,
        store,
        limit=cfg.probe_max_accounts_per_round,
        suspect_recheck_minutes=cfg.probe_suspect_recheck_minutes,
    )
    probe_results: List[Dict[str, Any]] = []
    # Probe in chunks so a single HTTP call cannot stall the whole round.
    chunk_size = max(1, min(20, cfg.probe_max_accounts_per_round))
    if candidates:
        for offset in range(0, len(candidates), chunk_size):
            chunk = candidates[offset : offset + chunk_size]
            try:
                part = g2a.probe_build(
                    chunk,
                    model=cfg.probe_model,
                    timeout_seconds=cfg.probe_timeout_seconds,
                    concurrency=cfg.probe_concurrency,
                )
                probe_results.extend(part)
                log.info(
                    "probe chunk %s-%s/%s results=%s",
                    offset + 1,
                    offset + len(chunk),
                    len(candidates),
                    len(part),
                )
            except Exception as exc:
                log.error("probe chunk failed: %s", exc)

    network_fails = 0
    soft_fails = 0
    for row in probe_results:
        aid = str(row.get("account_id") or "")
        classification = str(row.get("classification") or "error")
        status_code = int(row.get("status_code") or 0)
        reason = str(row.get("reason") or "")
        state = store.upsert_probe_result(
            aid,
            provider="grok_build",
            classification=classification,
            status_code=status_code,
            detail=reason,
            confirmed_required=cfg.probe_confirmed_dead_required,
        )
        final = str(state.get("classification") or classification)
        bucket = classify_bucket(final)
        stats["probed"] += 1
        if bucket == "alive":
            stats["alive"] += 1
        elif bucket == "quota":
            stats["quota_limited"] += 1
        elif bucket == "soft":
            stats["soft_failed"] += 1
            soft_fails += 1
        elif bucket == "network":
            stats["network_failed"] += 1
            network_fails += 1
        elif bucket == "dead":
            if final == "confirmed_dead":
                stats["confirmed_dead"] += 1

    probed_n = max(1, stats["probed"])
    if stats["probed"] and network_fails / probed_n >= cfg.stop_on_network_failure_ratio:
        log.warning("network failure ratio high — skip cleanup this round")
        cleanup_ok = False
    elif stats["probed"] and soft_fails / probed_n >= cfg.stop_on_soft_failure_ratio:
        log.warning("soft failure ratio high — skip cleanup this round")
        cleanup_ok = False
    else:
        cleanup_ok = True

    # --- cleanup ---
    if cleanup_ok and cfg.cleanup_mode != "report_only":
        dead = store.list_confirmed_dead(grace_hours=0)
        max_actions = min(
            cfg.cleanup_max_actions_per_round,
            max(1, int(len(accounts) * cfg.max_delete_ratio_per_round)),
        )
        actions = 0
        for item in dead:
            if actions >= max_actions:
                break
            aid = str(item.get("account_id") or "")
            if not aid:
                continue
            if cfg.probe_dry_run or cfg.cleanup_mode == "report_only":
                store.log_action(
                    run_id, aid, cfg.cleanup_mode, "confirmed_dead", dry_run=True, result="dry_run"
                )
                actions += 1
                continue
            try:
                if cfg.cleanup_mode == "disable":
                    g2a.set_enabled(aid, False)
                    store.mark_disabled(aid)
                    store.log_action(
                        run_id, aid, "disable", "confirmed_dead", dry_run=False, result="ok"
                    )
                    stats["disabled"] += 1
                elif cfg.cleanup_mode == "delete":
                    # only hard-delete after grace if previously disabled
                    disabled_at = float(item.get("disabled_at") or 0)
                    grace = cfg.cleanup_hard_delete_grace_hours * 3600
                    if disabled_at and time.time() - disabled_at >= grace:
                        g2a.delete_account(aid)
                        store.log_action(
                            run_id, aid, "delete", "confirmed_dead", dry_run=False, result="ok"
                        )
                        stats["deleted"] += 1
                    else:
                        g2a.set_enabled(aid, False)
                        store.mark_disabled(aid)
                        store.log_action(
                            run_id,
                            aid,
                            "disable",
                            "pre_delete_grace",
                            dry_run=False,
                            result="ok",
                        )
                        stats["disabled"] += 1
                actions += 1
                time.sleep(cfg.cleanup_action_interval_seconds)
            except Exception as exc:
                store.log_action(
                    run_id, aid, cfg.cleanup_mode, str(exc)[:120], dry_run=False, result="error"
                )
                log.error("cleanup %s failed: %s", aid, exc)

    # re-count healthy after cleanup
    try:
        accounts_after = g2a.list_accounts(provider="grok_build")
        healthy_after = count_healthy(accounts_after)
    except Exception:
        accounts_after = accounts
        healthy_after = healthy
    stats["healthy_after"] = healthy_after

    # --- replenish ---
    if cfg.replenish_enabled and cfg.register_web_token:
        rate_raw = store.get_meta("register_success_rate")
        try:
            success_rate = float(rate_raw) if rate_raw else 0.2
        except ValueError:
            success_rate = 0.2
        plan = plan_replenish(
            healthy_after,
            low=cfg.waterline_low,
            target=cfg.waterline_target,
            emergency=cfg.waterline_emergency,
            max_per_round=cfg.max_register_per_round,
            success_rate=success_rate,
        )
        log.info(
            "waterline action=%s healthy=%s deficit=%s planned=%s",
            plan.action,
            plan.healthy,
            plan.deficit,
            plan.planned,
        )
        if plan.action != "none" and plan.planned > 0:
            # cooldown
            last_reg = float(store.get_meta("last_register_at") or 0)
            if time.time() - last_reg < cfg.replenish_cooldown_minutes * 60:
                log.info("replenish cooldown active — skip")
            else:
                try:
                    active = reg.active_register()
                except Exception as exc:
                    active = None
                    log.warning("8787 status failed: %s", exc)
                if active and cfg.pause_when_existing_job_active:
                    log.info("existing register job active — skip new register")
                else:
                    # inventory first: probe+push local auth
                    if cfg.inventory_first and not cfg.probe_dry_run:
                        try:
                            push = reg.start_g2a_push(probe_first=True, concurrency=4)
                            log.info("inventory g2a push started: %s", dump_json(push))
                            stats["inventory_imported"] = 1
                            time.sleep(2)
                            # re-evaluate healthy
                            accounts_after = g2a.list_accounts(provider="grok_build")
                            healthy_after = count_healthy(accounts_after)
                            stats["healthy_after"] = healthy_after
                            plan = plan_replenish(
                                healthy_after,
                                low=cfg.waterline_low,
                                target=cfg.waterline_target,
                                emergency=cfg.waterline_emergency,
                                max_per_round=cfg.max_register_per_round,
                                success_rate=success_rate,
                            )
                        except Exception as exc:
                            log.warning("inventory push failed: %s", exc)

                    if plan.planned > 0:
                        stats["register_target"] = plan.planned
                        if cfg.probe_dry_run:
                            log.info("DRY_RUN register target=%s", plan.planned)
                        else:
                            try:
                                job = reg.start_register(plan.planned, threads=3)
                                store.set_meta("last_register_at", str(time.time()))
                                stats["register_started"] = 1
                                log.info("register started: %s", dump_json(job))
                            except Exception as exc:
                                log.error("register failed: %s", exc)
                                store.set_meta(
                                    "last_register_fail_at", str(time.time())
                                )

    store.finish_run(run_id, stats, status="ok")
    log.info("round done: %s", dump_json(stats))
    return stats


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    os.chdir(ROOT)
    _load_dotenv(ROOT / ".env")
    cfg = Config.load(os.environ.get("POOLKEEPER_CONFIG", "config.yaml"))
    log.info(
        "poolkeeper start dry_run=%s g2a=%s reg=%s public=%s",
        cfg.probe_dry_run,
        cfg.grok2api_base_url,
        cfg.register_base_url,
        cfg.register_public_url,
    )
    if not cfg.grok2api_admin_password:
        log.error("G2A_ADMIN_PASSWORD missing")
        return 2

    while True:
        with file_lock(cfg.lock_file) as ok:
            if not ok:
                log.warning("another poolkeeper holds the lock — skip")
            else:
                try:
                    run_once(cfg)
                except Exception:
                    log.exception("run_once failed")
        if cfg.once:
            return 0
        sleep_interval(cfg.interval_minutes)


if __name__ == "__main__":
    raise SystemExit(main())
