"""
Retroactively runs pydantic_agents.py's checker/reflection agent against the ALREADY-COMPLETED
real 95-case batch. Added 2026-08-30 to close an honest gap flagged directly (see DEVLOG.md "Honest
gap check"): the checker was built and proven correct in isolation (unit tests + hand-forced live
scenarios), but had NEVER been exercised against the real dataset -- it didn't exist yet when the
95/95 batch completed.

Deliberately NOT a full `run_batch_multiagent.py --resume` re-run: every one of the 95 real cases
is already correctly marked clean, so --resume would just skip all of them and spend zero quota on
the checker. A full non-resume re-run would re-spend ALL the quota that got the batch to 95/95 for
no reason -- that router+specialist work already succeeded and doesn't need repeating. This script
does the minimal, correct thing instead: reads each case's ALREADY-STORED final decision straight
from data/revenue_risk_multiagent.db, and runs ONLY the checker step against it. New quota is spent
exclusively on the genuinely new work (checker review calls, plus any retries the checker itself
decides to trigger) -- not on re-deriving decisions that were already correct.

Reconstructing enough of the router's original classification for _needs_checker_review's trigger
rule: `surface` is exact (stored on the Case itself, never ambiguous). `severity` (the string
"low"/"medium"/"high" the LIVE router originally produced, transiently, during the real batch run)
was never persisted anywhere -- only the case's structural severity_score (0-1 float,
router.py's compute_severity, identical scoring for both systems per pydantic_agents.py's own
module docstring) is. Approximated here as "high" if severity_score >= HIGH_SEVERITY_RETRO_THRESHOLD
(0.7, chosen because it selects roughly the top decile of the real 95-case dataset -- 9 of 95 cases
scored >= 0.7 when checked directly against the real data before picking this number, not guessed).
This is a deliberate, documented APPROXIMATION of the router's historical severity read, not the
literal value it produced at the time -- stated plainly rather than presented as more precise than
it is. The other two trigger conditions (any HARD_STOP hit, final tier APPROVE_FIRST) are exact,
derived directly from the real, already-stored DecisionLogEntry rows.

Run with: python backend/run_checker_retroactive.py [--providers groq,openrouter] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from db import (
    MULTIAGENT_DB_PATH,
    get_connection,
    init_db,
    insert_decision_log_entry,
    load_all_cases,
    load_all_decision_log_entries,
    upsert_case,
)
from guardrails import AttemptHistory
from metrics import compute_checker_metrics
from models import Case, DecisionLogEntry
from pydantic_agents import RoutingDecision, _needs_checker_review, _run_checker_review, resolve_model
from run_batch_multiagent import _build_account_schedule, load_cases_and_history

HIGH_SEVERITY_RETRO_THRESHOLD = 0.7
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _real_case_ids() -> set[str]:
    cases_raw = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))
    return {c["case_id"] for c in cases_raw}


def _reconstruct_decision(case: Case) -> RoutingDecision:
    severity = "high" if case.severity_score >= HIGH_SEVERITY_RETRO_THRESHOLD else "low"
    return RoutingDecision(
        surface=case.surface.value, severity=severity,
        reason="retroactive reconstruction, not the router's original historical output "
               "(see this script's module docstring)",
    )


def run(provider_names: list[str], dry_run: bool = False) -> dict:
    conn = get_connection(MULTIAGENT_DB_PATH)
    init_db(conn)

    real_ids = _real_case_ids()
    all_cases_raw = load_all_cases(conn)
    all_entries_raw = load_all_decision_log_entries(conn)

    cases_by_id = {c["case_id"]: Case.model_validate(c) for c in all_cases_raw if c["case_id"] in real_ids}
    entries_by_case: dict[str, list[DecisionLogEntry]] = {}
    for e in all_entries_raw:
        cid = e["case_id"]
        if cid not in real_ids:
            continue
        entries_by_case.setdefault(cid, []).append(DecisionLogEntry.model_validate(e))
    for entries in entries_by_case.values():
        entries.sort(key=lambda e: e.timestamp)

    _, history_by_case = load_cases_and_history()   # real attempt history, for the specialist's
                                                      # retry path if the checker flags anything
    all_cases_for_context = list(cases_by_id.values())

    schedule = _build_account_schedule(provider_names)
    if not schedule:
        raise RuntimeError(f"No usable accounts configured for providers: {provider_names}")

    triggered, reviewed, flagged, errors = 0, 0, 0, 0
    for i, (case_id, case) in enumerate(sorted(cases_by_id.items())):
        log_entries = entries_by_case.get(case_id, [])
        # Idempotency (2026-08-30, added after hitting a real per-case bug on the first live run):
        # skip cases already reviewed in an earlier pass -- _needs_checker_review's trigger
        # conditions are about the SPECIALIST's original decision, not about whether a checker
        # verdict already exists, so without this check a re-run (e.g. after fixing the bug below)
        # would re-review and waste quota on the ~80 cases that already succeeded the first time.
        if any(e.outcome in ("checker_approved", "checker_flagged") for e in log_entries):
            continue

        decision = _reconstruct_decision(case)

        if not _needs_checker_review(decision, log_entries):
            continue
        triggered += 1

        if dry_run:
            print(f"[DRY RUN] {case_id} ({case.surface.value}, severity_score={case.severity_score:.2f}) "
                  f"-- WOULD be reviewed by the checker.")
            continue

        provider, api_key = schedule[i % len(schedule)]
        model = resolve_model(provider, api_key=api_key)
        # .get(case_id, AttemptHistory()) -- NOT .get(case_id) alone. Real bug found live on this
        # script's first run (2026-08-30): a case with zero prior AttemptRecord rows (never
        # retried before) has no entry in history_by_case at all, so a bare .get() returned None,
        # and the checker's specialist-retry path (or check_attempt_history) crashed reading
        # .records off it. Isolated per-case (didn't kill the run), but real coverage loss on
        # ~10 of the first 70 cases before this was caught and fixed.
        history = history_by_case.get(case_id, AttemptHistory())
        original_len = len(log_entries)

        print(f"[{triggered}] {case_id} ({case.surface.value}, severity_score={case.severity_score:.2f}) "
              f"via {provider}...", end=" ", flush=True)
        try:
            log_entries = _run_checker_review(case, decision, log_entries, model, provider,
                                               history, all_cases_for_context)
        except Exception as e:  # noqa: BLE001 - per-case isolation, same principle as run_batch_multiagent.py
            print(f"FAILED (unhandled: {e})")
            errors += 1
            continue

        new_entries = log_entries[original_len:]
        for entry in new_entries:
            insert_decision_log_entry(conn, entry)
        upsert_case(conn, case)

        reviewed += 1
        outcome = new_entries[0].outcome if new_entries else None
        if outcome == "checker_flagged":
            flagged += 1
        print(f"done -> {outcome}, {len(new_entries)} new log entries, case.status={case.status.value}")

    conn.close()
    return {"triggered": triggered, "reviewed": reviewed, "flagged": flagged, "errors": errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--providers", type=str, default="groq,openrouter",
                        help="Comma-separated provider names for the checker's own account "
                             "scheduling. Same default as run_batch_multiagent.py.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List which real cases would trigger a checker review, without "
                             "spending any quota -- run this FIRST to see the scope before "
                             "committing real calls.")
    args = parser.parse_args()

    provider_names = [p.strip() for p in args.providers.split(",") if p.strip()]
    stats = run(provider_names, dry_run=args.dry_run)

    print(f"\n{'=' * 60}\nRetroactive checker pass complete\n{'=' * 60}")
    print(json.dumps(stats, indent=2))

    if not args.dry_run:
        conn = get_connection(MULTIAGENT_DB_PATH)
        init_db(conn)
        cases = load_all_cases(conn)
        entries = load_all_decision_log_entries(conn)
        print("\nchecker metrics (compute_checker_metrics, real query over the full DB):")
        print(json.dumps(compute_checker_metrics(cases, entries), indent=2))
        conn.close()


if __name__ == "__main__":
    main()
