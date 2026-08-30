"""
Phase 3 — the full batch run. Loads the synthetic cases produced by generate_cases.py, routes
them (severity scoring, plain code, per router.py), and runs each through the real case agent
loop, persisting results incrementally to SQLite as they complete.

Provider rotation: originally round-robinned across all 3 free-tier providers, since Gemini's 5
requests/minute cap (confirmed live, see DEVLOG.md 2026-08-24) made hammering it alone across ~95
multi-request cases slow and rate-limit-heavy. As of 2026-08-30, Gemini is EXCLUDED from the
default --providers list: measured on real batch data, it converted only ~2.5% of its attempts
into clean cases (2 of 61) vs. Groq/OpenRouter's much higher conversion, and 100% of its failures
were daily-quota-exhaustion 429s -- a structural free-tier limit, not transient flakiness (see
DEVLOG.md 2026-08-30 "should we stop using gemini"). Still fully supported and can be re-added
explicitly via --providers if worth spending its quota on again.

Incremental persistence + per-case isolation: a batch this size will very likely hit at least one
transient provider-side failure (already proven true once on Groq, see DEVLOG.md). Each case is
independent -- a single case's failure (already handled gracefully inside agent_loop.py) does not
lose progress on the cases already completed, because each case's results are written to SQLite
as soon as that case finishes, not batched up for one write at the end.

Resume support: free-tier quota cutting a run off partway through is now a known, REPEATED failure
mode (4 times on 2026-08-25, again on 2026-08-26 -- see DEVLOG.md), not a rare edge case. Re-running
all 95 cases from zero every time wastes quota on cases that already produced a real, trustworthy
result. Pass --resume to skip any case that already has a clean (non-error) outcome recorded in the
DB and only process what's left -- see db.get_cleanly_completed_case_ids() for exactly what counts
as "clean" (conservatively: a case only skips if NONE of its log entries are generation_error/
max_iterations_exceeded artifacts).

Run with: python backend/run_batch.py [--limit N] [--providers groq,openrouter] [--resume]
(pass --providers gemini,groq,openrouter explicitly to include Gemini again)
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from agent_loop import run_case_agent
from config import DEMO_TODAY
from db import (
    get_cleanly_completed_case_ids,
    get_connection,
    init_db,
    insert_decision_log_entry,
    reset_db,
    upsert_case,
)
from guardrails import AttemptHistory
from llm_client import get_llm_clients_for_provider
from models import AttemptRecord, Case, PTPStatus, Surface
from router import route_batch

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_cases_and_history() -> tuple[list[Case], dict[str, AttemptHistory]]:
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


# Weighted provider schedule -- NOT even round-robin. Live batch data on 2026-08-27 showed Groq
# consistently the most resilient of the 3 free tiers under real batch-scale load (47.5% failure
# rate vs. Gemini's 87.5% and OpenRouter's 61.5% in the same run -- see DEVLOG.md "we really have
# to improve this"). Concentrating more cases on the historically stronger provider, while still
# using the other two for real added capacity rather than dropping them, is a direct, evidence-
# driven response to that data rather than continuing to split load evenly and hoping it improves.
PROVIDER_WEIGHTS = {"groq": 2, "openrouter": 1, "gemini": 1}


def _build_weighted_schedule(provider_names: list[str]) -> list[str]:
    """Expands provider_names into a repeating schedule respecting PROVIDER_WEIGHTS (providers
    not in the weight table default to weight 1). E.g. ['gemini','groq','openrouter'] becomes
    ['groq','groq','openrouter','gemini'] repeated -- Groq gets picked twice as often."""
    schedule: list[str] = []
    for name in provider_names:
        schedule.extend([name] * PROVIDER_WEIGHTS.get(name, 1))
    return schedule or provider_names


def _build_weighted_account_schedule(account_clients: list[tuple[str, object]]) -> list[tuple[str, object]]:
    """Same weighting idea as _build_weighted_schedule, but operating on (provider, client)
    account pairs instead of bare provider names -- so a 2nd free account for an already-strong
    provider (e.g. a 2nd Groq account) gets its fair share of the weighted load too, not just a
    flat 1/N split across however many accounts happen to be configured."""
    schedule: list[tuple[str, object]] = []
    for name, client in account_clients:
        schedule.extend([(name, client)] * PROVIDER_WEIGHTS.get(name, 1))
    return schedule or account_clients


def find_ptp_due_cases(cases: list[Case], today) -> list[Case]:
    """Extracted 2026-08-30 into a standalone, testable function -- the inline version of this
    filter had a real bug (fixed the same day, see DEVLOG.md): it filtered over the --resume-
    shrunk case list instead of the FULL batch, so a case already marked clean by an earlier run
    was silently never re-checked for a due promise-to-pay, even with a promise date that had
    genuinely arrived. Callers must pass the FULL case list (e.g. all_cases_for_context), not a
    --resume-filtered one."""
    return [
        c for c in cases
        if c.surface == Surface.OVERDUE_RECEIVABLE
        and c.receivable_details
        and c.receivable_details.ptp
        and c.receivable_details.ptp.status == PTPStatus.PENDING
        and c.receivable_details.ptp.promised_date <= today
    ]


def run_batch(limit: int | None, provider_names: list[str], reset: bool = True, resume: bool = False) -> dict:
    conn = get_connection()

    already_clean: set[str] = set()
    if resume:
        # --resume implies keeping existing data -- reset and resume are mutually exclusive by
        # construction (reset wipes exactly the data resume is trying to preserve).
        init_db(conn)
        already_clean = get_cleanly_completed_case_ids(conn)
        print(f"Resume mode: {len(already_clean)} case(s) already have a clean result and will be skipped.")
    elif reset:
        reset_db(conn)
    else:
        init_db(conn)

    cases, history_by_case = load_cases_and_history()
    cases = route_batch(cases)   # highest severity first
    if limit:
        cases = cases[:limit]

    all_cases_for_context = cases   # check_customer_history needs the FULL batch, even if we skip some below
    if resume:
        skipped = [c for c in cases if c.case_id in already_clean]
        cases = [c for c in cases if c.case_id not in already_clean]
        print(f"Skipping {len(skipped)} already-clean case(s), processing {len(cases)} remaining.")

    # Multi-account support (2026-08-29): each provider may have >1 free account configured
    # (comma-separated keys in .env, see llm_client.py). Build a flat ACCOUNT-level schedule --
    # e.g. 2 Groq accounts + 1 Gemini account + 1 OpenRouter account = 4 real client instances to
    # round-robin across, not 3 -- so real added capacity from a 2nd account actually gets used,
    # not silently ignored because the code only knew about "groq" as a single slot.
    account_clients: list[tuple[str, object]] = []   # (provider_name, client) pairs, one per account
    for name in provider_names:
        for client in get_llm_clients_for_provider(name):
            account_clients.append((name, client))

    # Weighted schedule now operates on (provider, client) pairs -- an account inherits its
    # provider's weight, so 2 Groq accounts together get roughly PROVIDER_WEIGHTS['groq'] * 2
    # relative share versus a single Gemini/OpenRouter account, spreading load proportionally
    # across whatever real capacity is actually configured.
    schedule = _build_weighted_account_schedule(account_clients)

    print(f"Running {len(cases)} cases across {len(account_clients)} account(s) "
          f"({[name for name, _ in account_clients]})")

    stats = {"total": 0, "errors": 0, "by_tier": {}, "by_surface": {}, "skipped_resumed": len(already_clean)}

    for i, case in enumerate(cases):
        provider, client = schedule[i % len(schedule)]
        history = history_by_case.get(case.case_id, AttemptHistory())

        def log_fn(line: str, cid=case.case_id):
            pass  # per-tool console noise suppressed for batch runs; see DEVLOG for rationale

        print(f"[{i + 1}/{len(cases)}] {case.case_id} ({case.surface.value}, "
              f"Rs.{case.amount_inr:,.0f}) via {provider}...", end=" ", flush=True)

        t0 = time.time()
        try:
            state = run_case_agent(case, history, client, log_fn=log_fn,
                                   all_cases=all_cases_for_context, provider_name=provider)
        except Exception as e:  # noqa: BLE001 - last-resort net, agent_loop already isolates
            # per-generation failures; this catches anything unexpected in dispatch itself so
            # ONE case can never take down the whole batch.
            print(f"FAILED (unhandled: {e})")
            stats["errors"] += 1
            continue

        upsert_case(conn, case)
        for entry in state.log_entries:
            insert_decision_log_entry(conn, entry)
            tier = entry.action_tier.value
            stats["by_tier"][tier] = stats["by_tier"].get(tier, 0) + 1

        stats["total"] += 1
        stats["by_surface"][case.surface.value] = stats["by_surface"].get(case.surface.value, 0) + 1

        elapsed = time.time() - t0
        print(f"done in {elapsed:.1f}s -> status={case.status.value}, "
              f"{len(state.log_entries)} log entries")

    # --- PTP re-evaluation pass -------------------------------------------------------------
    # Any receivable case with a PENDING promise-to-pay whose promised date has now arrived (or
    # passed) relative to DEMO_TODAY gets re-run through the SAME agent loop, so the agent can
    # actually see "was this promise kept or missed" and decide the next step (escalate, close,
    # renegotiate) -- this is what makes PTP a real agent BEHAVIOR across time, not just a data
    # field the agent reads once and never revisits (see DEVLOG.md 2026-08-25 "genuine novelty
    # gap-check", active PTP negotiation). Cases with a pre-existing PENDING PTP from
    # generate_cases.py's synthetic data are included here even on their first pass, since the
    # main loop above already evaluated them once with the PTP visible in get_case_context --
    # this second pass specifically targets promises that have crossed their due date, which is
    # the moment a real decision (kept vs missed) actually needs to be made.
    #
    # Filters over all_cases_for_context (the FULL batch), not `cases` -- see find_ptp_due_cases's
    # own docstring for the bug this guards against (found live 2026-08-30). all_cases_for_context
    # is safe to re-check every run: it's freshly reloaded from data/cases.json each time (always
    # PENDING in the source data), not mutated by the DB.
    ptp_due_cases = find_ptp_due_cases(all_cases_for_context, DEMO_TODAY.date())

    if ptp_due_cases:
        print(f"\nRe-evaluating {len(ptp_due_cases)} case(s) with a promise-to-pay due as of "
              f"{DEMO_TODAY.date()}...")

    for i, case in enumerate(ptp_due_cases):
        provider, client = schedule[i % len(schedule)]
        history = history_by_case.get(case.case_id, AttemptHistory())

        print(f"[PTP {i + 1}/{len(ptp_due_cases)}] {case.case_id} (promised "
              f"Rs.{case.receivable_details.ptp.promised_amount:,.0f} by "
              f"{case.receivable_details.ptp.promised_date}) via {provider}...", end=" ", flush=True)

        t0 = time.time()
        try:
            state = run_case_agent(case, history, client, log_fn=lambda line: None,
                                   all_cases=all_cases_for_context, provider_name=provider)
        except Exception as e:  # noqa: BLE001
            print(f"FAILED (unhandled: {e})")
            stats["errors"] += 1
            continue

        upsert_case(conn, case)
        for entry in state.log_entries:
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
                        help="Comma-separated provider names to round-robin across. Gemini is "
                             "excluded from the default as of 2026-08-30 -- measured on real "
                             "batch data, its free tier converted ~2.5%% of its attempts into "
                             "clean cases (2 of 61) vs. Groq/OpenRouter's much higher share, and "
                             "100%% of its failures were daily-quota-exhaustion 429s, not random "
                             "flakiness -- a structural free-tier limit, not noise (see DEVLOG.md "
                             "2026-08-30). Still fully supported via GeminiClient in llm_client.py "
                             "and can be passed explicitly (--providers gemini,groq,openrouter) "
                             "if its quota is ever worth spending again.")
    parser.add_argument("--no-reset", action="store_true", help="Don't wipe the DB before running")
    parser.add_argument("--resume", action="store_true",
                        help="Skip cases that already have a clean (non-error) result in the DB, "
                             "and only process what's left. Use this after a run got cut off by "
                             "quota exhaustion instead of re-running everything from zero.")
    args = parser.parse_args()

    provider_names = [p.strip() for p in args.providers.split(",") if p.strip()]
    stats = run_batch(limit=args.limit, provider_names=provider_names,
                      reset=not args.no_reset, resume=args.resume)

    print(f"\n{'=' * 60}\nBatch complete\n{'=' * 60}")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
