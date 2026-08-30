"""
SQLite persistence for cases and decision log entries. Single file, no server -- deliberately
right-sized for a ~100-case batch demo, not production (see PRD.md tech stack table).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from models import Case, DecisionLogEntry

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "revenue_risk.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    surface TEXT NOT NULL,
    amount_inr REAL NOT NULL,
    status TEXT NOT NULL,
    severity_score REAL NOT NULL,
    payload TEXT NOT NULL   -- full Case, JSON-serialized (source of truth for surface-specific detail)
);

CREATE TABLE IF NOT EXISTS decision_log (
    log_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    action_tier TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    outcome TEXT,
    amount_at_risk_inr REAL NOT NULL,
    amount_recovered_inr REAL NOT NULL,
    payload TEXT NOT NULL,  -- full DecisionLogEntry, JSON-serialized
    FOREIGN KEY (case_id) REFERENCES cases(case_id)
);

CREATE INDEX IF NOT EXISTS idx_decision_log_case_id ON decision_log(case_id);
CREATE INDEX IF NOT EXISTS idx_cases_surface ON cases(surface);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """db_path: override the default DB file -- used by run_batch_multiagent.py (2026-08-30) to
    keep the multi-agent system's batch progress in a separate file from the proven single-agent
    system's, so Gate 3's validated clean-case count is never ambiguous with the existing 61+/95.
    Every function below still works unchanged either way -- they all just take the resulting
    sqlite3.Connection, never DB_PATH directly."""
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    """Drops and recreates both tables -- used at the start of a fresh batch run so re-running
    doesn't accumulate duplicate/stale rows from a prior run."""
    conn.executescript("DROP TABLE IF EXISTS decision_log; DROP TABLE IF EXISTS cases;")
    init_db(conn)


def upsert_case(conn: sqlite3.Connection, case: Case) -> None:
    conn.execute(
        "INSERT INTO cases (case_id, surface, amount_inr, status, severity_score, payload) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(case_id) DO UPDATE SET "
        "status=excluded.status, severity_score=excluded.severity_score, payload=excluded.payload",
        (case.case_id, case.surface.value, case.amount_inr, case.status.value,
         case.severity_score, case.model_dump_json()),
    )
    conn.commit()


def insert_decision_log_entry(conn: sqlite3.Connection, entry: DecisionLogEntry) -> None:
    conn.execute(
        "INSERT INTO decision_log (log_id, case_id, timestamp, iteration, action_tier, "
        "action_taken, outcome, amount_at_risk_inr, amount_recovered_inr, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (entry.log_id, entry.case_id, entry.timestamp.isoformat(), entry.iteration,
         entry.action_tier.value, entry.action_taken.value, entry.outcome,
         entry.amount_at_risk_inr, entry.amount_recovered_inr, entry.model_dump_json()),
    )
    conn.commit()


def load_all_cases(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT payload FROM cases").fetchall()
    return [json.loads(r[0]) for r in rows]


def load_all_decision_log_entries(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT payload FROM decision_log ORDER BY timestamp").fetchall()
    return [json.loads(r[0]) for r in rows]


def get_cleanly_completed_case_ids(conn: sqlite3.Connection) -> set[str]:
    """
    Case ids whose MOST RECENT run_case_agent() attempt completed without a
    generation_error/max_iterations_exceeded artifact. Used by run_batch.py's --resume flag to skip
    cases that already have a trustworthy result instead of re-running (and re-spending quota on)
    all 95 from zero.

    Fixed 2026-08-29 (see DEVLOG.md): this used to check ALL log rows ever recorded for a case_id,
    across every past run -- so a case that failed once on an early attempt (a stale error from a
    since-superseded run) could never count as clean again, even after later fully succeeding,
    because --resume re-runs any case with an error ANYWHERE in its history, including attempts
    long since superseded by a clean one. Confirmed live on real data: INV-0017 and PMT-0030 both
    had a genuinely clean final attempt but were kept out of the clean set by leftover error rows
    from 2026-08-27 attempts. Now judges each case by its trailing run of entries since its LAST
    terminal (non-error) outcome.

    NOTE on why this doesn't try to reconstruct true run_case_agent() attempt boundaries: not every
    tool call produces a DecisionLogEntry (only terminal ones -- execute_action/escalate_to_human/
    log_decision -- do; see agent_loop.py), so a successful attempt's `iteration` field can be
    anything (3, 4, 5...) depending on how many earlier tool calls it made, and `iteration == 1`
    does NOT reliably mark a fresh attempt's start (confirmed against real batch data: a first
    version of this fix used that heuristic and it was wrong -- checked and reverted). What DOES
    matter for correctness here is only the entries AFTER the last terminal outcome: those either
    are empty (case ends cleanly) or are a trailing run of pure errors (an attempt that started
    after the last real result and never reached a new one) -- either way, checking just that
    trailing slice gives the right answer regardless of how many separate attempts got merged
    further back in history, since two merged-together failed attempts are still correctly "not
    clean" either way.
    """
    rows = conn.execute("SELECT rowid, case_id, payload FROM decision_log ORDER BY rowid").fetchall()
    entries_by_case: dict[str, list[dict]] = {}
    for _rowid, case_id, payload in rows:
        entries_by_case.setdefault(case_id, []).append(json.loads(payload))

    bad_outcomes = {"generation_error", "max_iterations_exceeded"}
    clean_ids = set()
    for case_id, entries in entries_by_case.items():
        if not entries:
            continue
        # Find the last terminal (non-error) entry; everything strictly after it is the trailing
        # slice that determines cleanliness. If the very last entry itself is terminal, the
        # trailing slice is empty -- vacuously clean.
        last_terminal_idx = None
        for i, e in enumerate(entries):
            if e.get("outcome") not in bad_outcomes:
                last_terminal_idx = i
        if last_terminal_idx is None:
            continue  # never had a single terminal outcome -- not clean
        trailing = entries[last_terminal_idx + 1:]
        if not any(e.get("outcome") in bad_outcomes for e in trailing):
            clean_ids.add(case_id)
    return clean_ids
