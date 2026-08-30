"""
Gate 2 of next_steps_multiagent_migration.md (2026-08-30) -- a run_batch.py-equivalent for
pydantic_agents.py's router -> 3-specialist multi-agent architecture. Deliberately a SEPARATE
file rather than a modification of run_batch.py: run_batch.py stays untouched and still drives
the proven single-agent system until Gate 3 passes and Gate 4 makes the official switch (see
next_steps_multiagent_migration.md) -- this file is not wired into anything yet, run explicitly.

Reuses, rather than reimplements:
- db.py's get_cleanly_completed_case_ids/upsert_case/insert_decision_log_entry/get_connection/
  init_db/reset_db, EXACTLY as run_batch.py uses them. This is a hard rule carried over from this
  project's own hard-learned lesson: a second, drifted implementation of "what counts as a clean
  case" is exactly the bug that silently stalled the original batch at 31/95 for days (see
  DEVLOG.md 2026-08-29). There must only ever be ONE definition of "clean."
- router.py's route_batch (severity-first ordering) -- identical to run_batch.py, not
  reimplemented.
- run_batch.py's own PROVIDER_WEIGHTS / _build_weighted_account_schedule -- imported directly
  rather than copy-pasted, so a future change to the weighting strategy doesn't need to be kept in
  sync by hand across two files.
- llm_client._keys_for_provider for raw account key enumeration (the same source of truth
  get_llm_clients_for_provider uses internally) -- this file needs raw key STRINGS (to pass as
  pydantic_agents.resolve_model's explicit api_key=), not llm_client.LLMClient wrapper objects,
  since pydantic_agents.py has its own model abstraction (Pydantic AI's GroqModel/GoogleModel/
  OpenRouterModel) that supersedes llm_client.py's normalization layer entirely (see
  pydantic_agents.py's module docstring).

Run with: python backend/run_batch_multiagent.py [--limit N] [--providers groq,openrouter] [--resume]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from config import DEMO_TODAY
from db import (
    get_cleanly_completed_case_ids,
    get_connection,
    init_db,
    insert_decision_log_entry,
    reset_db,
    upsert_case,
)
from db import DB_PATH as _SINGLE_AGENT_DB_PATH

# Separate DB file from the single-agent system's (data/revenue_risk.db) -- both systems used to
# share one file until a real test run here wrote 4 rows into it and made the two systems'
# progress ambiguous (found live 2026-08-30, fixed immediately rather than left for Gate 3 to trip
# over). db.get_connection() now takes an explicit path; every other db.py function is unchanged
# and still works the same regardless of which file the connection points at.
MULTIAGENT_DB_PATH = _SINGLE_AGENT_DB_PATH.parent / "revenue_risk_multiagent.db"
from guardrails import AttemptHistory
from llm_client import _keys_for_provider
from models import AttemptRecord, Case
from pydantic_agents import run_case_via_orchestrator
from router import route_batch
from run_batch import _build_weighted_account_schedule, find_ptp_due_cases

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_cases_and_history() -> tuple[list[Case], dict[str, AttemptHistory]]:
    """Identical to run_batch.py's version -- duplicated rather than imported ONLY because
    run_batch.py's own version isn't factored out as a standalone importable without pulling in
    that module's argparse-driven main(); the logic itself is a straight read of the same
    data/cases.json + data/attempt_history.json files, byte-for-byte the same real dataset."""
    cases_raw = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))
    attempts_raw = json.loads((DATA_DIR / "attempt_history.json").read_text(encoding="utf-8"))

    cases = [Case.model_validate(c) for c in cases_raw]
    all_records = [AttemptRecord.model_validate(a) for a in attempts_raw]

    history_by_case: dict[str, AttemptHistory] = {}
    for record in all_records:
        h = history_by_case.setdefault(record.case_id, AttemptHistory())
        h.attempts_this_cycle.append(record.executed_at)
        h.records.append(record)

    return cases, history_by_case


def _build_account_schedule(provider_names: list[str]) -> list[tuple[str, str]]:
    """(provider_name, api_key) pairs, one per configured account, expanded through the SAME
    weighted schedule run_batch.py uses (PROVIDER_WEIGHTS/_build_weighted_account_schedule) --
    imported, not reimplemented. Uses raw key strings via _keys_for_provider rather than
    llm_client.LLMClient instances (get_llm_clients_for_provider's return type) because
    pydantic_agents.resolve_model wants an explicit api_key string, not a wrapper object."""
    account_pairs: list[tuple[str, object]] = []
    for name in provider_names:
        try:
            keys = _keys_for_provider(name)
        except KeyError as e:
            print(f"WARNING: {e} -- skipping provider {name!r}")
            continue
        for key in keys:
            account_pairs.append((name, key))
    return _build_weighted_account_schedule(account_pairs)


def run_batch(limit: int | None, provider_names: list[str], reset: bool = True, resume: bool = False) -> dict:
    conn = get_connection(MULTIAGENT_DB_PATH)

    already_clean: set[str] = set()
    if resume:
        init_db(conn)
        already_clean = get_cleanly_completed_case_ids(conn)
        print(f"Resume mode: {len(already_clean)} case(s) already have a clean result and will be skipped.")
    elif reset:
        reset_db(conn)
    else:
        init_db(conn)

    cases, history_by_case = load_cases_and_history()
    cases = route_batch(cases)   # highest severity first -- identical ordering to run_batch.py
    if limit:
        cases = cases[:limit]

    all_cases_for_context = cases   # check_customer_history needs the FULL batch, even if we skip some below
    if resume:
        skipped = [c for c in cases if c.case_id in already_clean]
        cases = [c for c in cases if c.case_id not in already_clean]
        print(f"Skipping {len(skipped)} already-clean case(s), processing {len(cases)} remaining.")

    schedule = _build_account_schedule(provider_names)
    if not schedule:
        raise RuntimeError(f"No usable accounts configured for providers: {provider_names}")

    print(f"Running {len(cases)} cases across {len(schedule)} account slot(s) "
          f"({[name for name, _ in schedule]})")

    stats = {"total": 0, "errors": 0, "by_tier": {}, "by_surface": {}, "skipped_resumed": len(already_clean)}

    for i, case in enumerate(cases):
        provider, api_key = schedule[i % len(schedule)]
        history = history_by_case.get(case.case_id, AttemptHistory())

        print(f"[{i + 1}/{len(cases)}] {case.case_id} ({case.surface.value}, "
              f"Rs.{case.amount_inr:,.0f}) via {provider}...", end=" ", flush=True)

        t0 = time.time()
        try:
            decision, log_entries = run_case_via_orchestrator(
                case, history, provider, all_cases=all_cases_for_context, api_key=api_key,
            )
        except Exception as e:  # noqa: BLE001 - per-case isolation, same principle as run_batch.py:
            # ONE case's crash (router malformed output, model 429, network error, anything) must
            # never take down the whole batch and lose progress on cases already completed.
            print(f"FAILED (unhandled: {e})")
            stats["errors"] += 1
            continue

        upsert_case(conn, case)
        for entry in log_entries:
            insert_decision_log_entry(conn, entry)
            tier = entry.action_tier.value
            stats["by_tier"][tier] = stats["by_tier"].get(tier, 0) + 1

        stats["total"] += 1
        stats["by_surface"][case.surface.value] = stats["by_surface"].get(case.surface.value, 0) + 1

        elapsed = time.time() - t0
        print(f"done in {elapsed:.1f}s -> status={case.status.value}, "
              f"router said {decision.surface}/{decision.severity}, {len(log_entries)} log entries")

    # --- PTP re-evaluation pass -- identical rationale to run_batch.py's own pass: a receivable
    # case with a PENDING promise-to-pay whose promised date has now arrived gets re-run so the
    # agent can decide kept-vs-missed, the same "PTP is a real agent behavior across time" gap-
    # check this project's original system already covers. Kept as a straight port so this system
    # is judged on the same behavior, not a reduced feature set.
    #
    # find_ptp_due_cases is imported from run_batch.py, not reimplemented -- same "one definition,
    # reused" discipline as db.get_cleanly_completed_case_ids (see this file's module docstring).
    # Called on all_cases_for_context (the FULL batch), not `cases` -- see that function's own
    # docstring for the --resume-shrinkage bug this guards against (found live 2026-08-30, also
    # present in run_batch.py's own inline version before this fix -- this file's earlier port
    # inherited the same gap).
    ptp_due_cases = find_ptp_due_cases(all_cases_for_context, DEMO_TODAY.date())

    if ptp_due_cases:
        print(f"\nRe-evaluating {len(ptp_due_cases)} case(s) with a promise-to-pay due as of "
              f"{DEMO_TODAY.date()}...")

    for i, case in enumerate(ptp_due_cases):
        provider, api_key = schedule[i % len(schedule)]
        history = history_by_case.get(case.case_id, AttemptHistory())

        print(f"[PTP {i + 1}/{len(ptp_due_cases)}] {case.case_id} (promised "
              f"Rs.{case.receivable_details.ptp.promised_amount:,.0f} by "
              f"{case.receivable_details.ptp.promised_date}) via {provider}...", end=" ", flush=True)

        t0 = time.time()
        try:
            decision, log_entries = run_case_via_orchestrator(
                case, history, provider, all_cases=all_cases_for_context, api_key=api_key,
            )
        except Exception as e:  # noqa: BLE001
            print(f"FAILED (unhandled: {e})")
            stats["errors"] += 1
            continue

        upsert_case(conn, case)
        for entry in log_entries:
            insert_decision_log_entry(conn, entry)
            tier = entry.action_tier.value
            stats["by_tier"][tier] = stats["by_tier"].get(tier, 0) + 1

        elapsed = time.time() - t0
        print(f"done in {elapsed:.1f}s -> status={case.status.value}")

    stats["ptp_reevaluated"] = len(ptp_due_cases)

    conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N cases")
    parser.add_argument("--providers", type=str, default="groq,openrouter",
                        help="Comma-separated provider names to round-robin across. Same default "
                             "and same Gemini-deprioritization rationale as run_batch.py (see that "
                             "file's --providers help text) -- kept consistent between the two "
                             "systems so any comparison between them isn't confounded by different "
                             "provider mixes.")
    parser.add_argument("--no-reset", action="store_true", help="Don't wipe the DB before running")
    parser.add_argument("--resume", action="store_true",
                        help="Skip cases that already have a clean (non-error) result in the DB "
                             "(via db.get_cleanly_completed_case_ids -- the SAME function "
                             "run_batch.py uses, not a second implementation), and only process "
                             "what's left.")
    args = parser.parse_args()

    provider_names = [p.strip() for p in args.providers.split(",") if p.strip()]
    stats = run_batch(limit=args.limit, provider_names=provider_names,
                      reset=not args.no_reset, resume=args.resume)

    print(f"\n{'=' * 60}\nBatch complete (multi-agent)\n{'=' * 60}")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
