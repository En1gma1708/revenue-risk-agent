"""
Runs a small, deliberately-curated sample of real cases through run_case_via_orchestrator purely
to generate real Langfuse trace volume for eval work -- added 2026-08-31 after the user chose a
representative sample over a full 95-case re-run (same total quota cost as building the original
batch, for cases whose correctness is already known and doesn't need re-proving).

Deliberately does NOT touch db.py / data/revenue_risk_multiagent.db at all -- the real batch DB
already has correct results for every one of these cases; this script's only job is to make the
LLM calls again so Langfuse actually sees them (tracing happens at call time, it can't be
retroactively attached to already-completed DB rows the way the checker's retroactive pass could
add NEW review calls onto existing data). Given this session's earlier real incident (a batch
runner's default reset=True wiped the shared single-agent DB), this script intentionally has NO
persistence path into that DB at all -- there's no way for it to repeat that mistake.

The 16 cases below were hand-picked for real coverage, not randomly sampled: all 3 surfaces (~5-6
each), a mix of AUTONOMOUS/APPROVE_FIRST/HARD_STOP tiers (including real HARD_STOP-hitting cases
so the guardrail-block behavior is genuinely represented in Langfuse, not just approvals), and
several cases already known to trigger the checker (from demo_shortlist.md's real findings) so
its AGENT node and Score-worthy verdict actually appear in the sample.

Run with: python backend/run_langfuse_sample.py [--providers groq,openrouter]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from guardrails import AttemptHistory
from models import AttemptRecord, Case
from pydantic_agents import flush_langfuse, run_case_via_orchestrator
from run_batch_multiagent import _build_account_schedule

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SAMPLE_CASE_IDS = [
    # payment_failure -- mix of AUTONOMOUS/APPROVE_FIRST/HARD_STOP, including real hard declines
    "PMT-0001", "PMT-0006", "PMT-0020", "PMT-0028", "PMT-0032",
    # checkout_abandonment
    "CART-0002", "CART-0007", "CART-0009", "CART-0015", "CART-0019",
    # overdue_receivable -- includes INV-0020, the real checker catch-and-fix story
    "INV-0001", "INV-0002", "INV-0009", "INV-0012", "INV-0016", "INV-0020",
]


def load_cases_and_history() -> tuple[dict[str, Case], dict[str, AttemptHistory]]:
    cases_raw = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))
    attempts_raw = json.loads((DATA_DIR / "attempt_history.json").read_text(encoding="utf-8"))

    cases_by_id = {c["case_id"]: Case.model_validate(c) for c in cases_raw if c["case_id"] in SAMPLE_CASE_IDS}
    all_records = [AttemptRecord.model_validate(a) for a in attempts_raw]

    history_by_case: dict[str, AttemptHistory] = {}
    for record in all_records:
        h = history_by_case.setdefault(record.case_id, AttemptHistory())
        h.attempts_this_cycle.append(record.executed_at)
        h.records.append(record)

    return cases_by_id, history_by_case


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--providers", type=str, default="groq,openrouter")
    args = parser.parse_args()
    provider_names = [p.strip() for p in args.providers.split(",") if p.strip()]

    cases_by_id, history_by_case = load_cases_and_history()
    all_cases_for_context = list(cases_by_id.values())
    schedule = _build_account_schedule(provider_names)
    if not schedule:
        raise RuntimeError(f"No usable accounts configured for providers: {provider_names}")

    errors = 0
    for i, case_id in enumerate(SAMPLE_CASE_IDS):
        case = cases_by_id.get(case_id)
        if case is None:
            print(f"[{i + 1}/{len(SAMPLE_CASE_IDS)}] {case_id} -- not found in cases.json, skipping")
            continue
        provider, api_key = schedule[i % len(schedule)]
        history = history_by_case.get(case_id, AttemptHistory())

        print(f"[{i + 1}/{len(SAMPLE_CASE_IDS)}] {case_id} ({case.surface.value}) via {provider}...",
              end=" ", flush=True)
        try:
            decision, log_entries = run_case_via_orchestrator(
                case, history, provider, all_cases=all_cases_for_context, api_key=api_key,
            )
        except Exception as e:  # noqa: BLE001 - per-case isolation, same principle as every other runner
            print(f"FAILED (unhandled: {e})")
            errors += 1
            continue

        last = log_entries[-1] if log_entries else None
        print(f"done -> router said {decision.surface}/{decision.severity}, "
              f"{len(log_entries)} log entries, last outcome={last.outcome if last else None}")

    print(f"\n{'=' * 60}\nLangfuse sample run complete\n{'=' * 60}")
    print(json.dumps({"total": len(SAMPLE_CASE_IDS), "errors": errors}, indent=2))

    flush_langfuse()


if __name__ == "__main__":
    main()
