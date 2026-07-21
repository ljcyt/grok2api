from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS account_probe_state (
                    account_id TEXT PRIMARY KEY,
                    provider TEXT,
                    email_hash TEXT,
                    last_result TEXT,
                    last_status_code INTEGER,
                    last_probe_at REAL,
                    consecutive_dead INTEGER DEFAULT 0,
                    consecutive_network_fail INTEGER DEFAULT 0,
                    confirmed_dead_at REAL,
                    last_detail TEXT,
                    disabled_at REAL,
                    updated_at REAL,
                    debt_score REAL DEFAULT 0,
                    hard_streak INTEGER DEFAULT 0,
                    demotion_class TEXT DEFAULT 'none',
                    half_open_successes INTEGER DEFAULT 0,
                    baseline_priority INTEGER,
                    demoted_at REAL,
                    half_open_since REAL,
                    cooldown_step INTEGER DEFAULT 0,
                    target_priority INTEGER,
                    bot_flagged INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS maintenance_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at REAL,
                    finished_at REAL,
                    pool_total INTEGER,
                    probed INTEGER,
                    alive INTEGER,
                    quota_limited INTEGER,
                    soft_failed INTEGER,
                    network_failed INTEGER,
                    confirmed_dead INTEGER,
                    disabled INTEGER,
                    deleted INTEGER,
                    inventory_imported INTEGER,
                    register_started INTEGER,
                    register_target INTEGER,
                    healthy_before INTEGER,
                    healthy_after INTEGER,
                    status TEXT,
                    summary_json TEXT
                );
                CREATE TABLE IF NOT EXISTS maintenance_actions (
                    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    account_id TEXT,
                    action TEXT,
                    reason TEXT,
                    dry_run INTEGER,
                    result TEXT,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            self._migrate_demotion_columns(conn)

    def _migrate_demotion_columns(self, conn: sqlite3.Connection) -> None:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(account_probe_state)").fetchall()
        }
        additions = {
            "debt_score": "REAL DEFAULT 0",
            "hard_streak": "INTEGER DEFAULT 0",
            "demotion_class": "TEXT DEFAULT 'none'",
            "half_open_successes": "INTEGER DEFAULT 0",
            "baseline_priority": "INTEGER",
            "demoted_at": "REAL",
            "half_open_since": "REAL",
            "cooldown_step": "INTEGER DEFAULT 0",
            "target_priority": "INTEGER",
            "bot_flagged": "INTEGER DEFAULT 0",
        }
        for name, decl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE account_probe_state ADD COLUMN {name} {decl}")

    def get_probe_state(self, account_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM account_probe_state WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_probe_result(
        self,
        account_id: str,
        *,
        provider: str,
        classification: str,
        status_code: int,
        detail: str,
        confirmed_required: int = 2,
    ) -> Dict[str, Any]:
        now = time.time()
        prev = self.get_probe_state(account_id) or {}
        consecutive_dead = int(prev.get("consecutive_dead") or 0)
        consecutive_network = int(prev.get("consecutive_network_fail") or 0)
        confirmed_at = prev.get("confirmed_dead_at")

        if classification in ("suspect_dead", "confirmed_dead"):
            consecutive_dead += 1
            consecutive_network = 0
        elif classification == "network_error":
            consecutive_network += 1
        else:
            consecutive_dead = 0
            consecutive_network = 0
            confirmed_at = None

        final_class = classification
        if consecutive_dead >= confirmed_required and classification in (
            "suspect_dead",
            "confirmed_dead",
        ):
            final_class = "confirmed_dead"
            if not confirmed_at:
                confirmed_at = now

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO account_probe_state(
                    account_id, provider, last_result, last_status_code, last_probe_at,
                    consecutive_dead, consecutive_network_fail, confirmed_dead_at,
                    last_detail, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    provider=excluded.provider,
                    last_result=excluded.last_result,
                    last_status_code=excluded.last_status_code,
                    last_probe_at=excluded.last_probe_at,
                    consecutive_dead=excluded.consecutive_dead,
                    consecutive_network_fail=excluded.consecutive_network_fail,
                    confirmed_dead_at=excluded.confirmed_dead_at,
                    last_detail=excluded.last_detail,
                    updated_at=excluded.updated_at
                """,
                (
                    account_id,
                    provider,
                    final_class,
                    status_code,
                    now,
                    consecutive_dead,
                    consecutive_network,
                    confirmed_at,
                    detail,
                    now,
                ),
            )
        state = self.get_probe_state(account_id) or {}
        state["classification"] = final_class
        return state

    def save_demotion_state(self, account_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        allowed = {
            "debt_score",
            "hard_streak",
            "demotion_class",
            "half_open_successes",
            "baseline_priority",
            "demoted_at",
            "half_open_since",
            "cooldown_step",
            "target_priority",
            "bot_flagged",
            "updated_at",
        }
        cols = []
        vals: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            cols.append(f"{key} = ?")
            if key == "bot_flagged":
                vals.append(1 if value else 0)
            else:
                vals.append(value)
        if not cols:
            return
        vals.append(account_id)
        with self._connect() as conn:
            # ensure row exists
            conn.execute(
                "INSERT OR IGNORE INTO account_probe_state(account_id) VALUES (?)",
                (account_id,),
            )
            conn.execute(
                f"UPDATE account_probe_state SET {', '.join(cols)} WHERE account_id = ?",
                vals,
            )

    def list_demoted(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM account_probe_state
                WHERE demotion_class IN ('soft', 'hard', 'half_open')
                ORDER BY COALESCE(demoted_at, 0) ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_disabled(self, account_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE account_probe_state SET disabled_at = ?, updated_at = ? WHERE account_id = ?",
                (time.time(), time.time(), account_id),
            )

    def list_confirmed_dead(self, grace_hours: int = 0) -> List[Dict[str, Any]]:
        now = time.time()
        min_ts = now - grace_hours * 3600 if grace_hours > 0 else now + 1
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM account_probe_state
                WHERE last_result = 'confirmed_dead'
                  AND confirmed_dead_at IS NOT NULL
                  AND confirmed_dead_at <= ?
                ORDER BY confirmed_dead_at ASC
                """,
                (min_ts if grace_hours > 0 else now,),
            ).fetchall()
            # when grace_hours=0: return all confirmed
            if grace_hours <= 0:
                rows = conn.execute(
                    "SELECT * FROM account_probe_state WHERE last_result = 'confirmed_dead' ORDER BY confirmed_dead_at ASC"
                ).fetchall()
            return [dict(r) for r in rows]

    def start_run(self, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO maintenance_runs(run_id, started_at, status) VALUES (?, ?, ?)",
                (run_id, time.time(), "running"),
            )

    def finish_run(self, run_id: str, stats: Dict[str, Any], status: str = "ok") -> None:
        import json

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE maintenance_runs SET
                    finished_at=?, pool_total=?, probed=?, alive=?, quota_limited=?,
                    soft_failed=?, network_failed=?, confirmed_dead=?, disabled=?,
                    deleted=?, inventory_imported=?, register_started=?, register_target=?,
                    healthy_before=?, healthy_after=?, status=?, summary_json=?
                WHERE run_id=?
                """,
                (
                    time.time(),
                    int(stats.get("pool_total") or 0),
                    int(stats.get("probed") or 0),
                    int(stats.get("alive") or 0),
                    int(stats.get("quota_limited") or 0),
                    int(stats.get("soft_failed") or 0),
                    int(stats.get("network_failed") or 0),
                    int(stats.get("confirmed_dead") or 0),
                    int(stats.get("disabled") or 0),
                    int(stats.get("deleted") or 0),
                    int(stats.get("inventory_imported") or 0),
                    int(stats.get("register_started") or 0),
                    int(stats.get("register_target") or 0),
                    int(stats.get("healthy_before") or 0),
                    int(stats.get("healthy_after") or 0),
                    status,
                    json.dumps(stats, ensure_ascii=False),
                    run_id,
                ),
            )

    def log_action(
        self,
        run_id: str,
        account_id: str,
        action: str,
        reason: str,
        *,
        dry_run: bool,
        result: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO maintenance_actions(run_id, account_id, action, reason, dry_run, result, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, account_id, action, reason, 1 if dry_run else 0, result, time.time()),
            )

    def get_meta(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
