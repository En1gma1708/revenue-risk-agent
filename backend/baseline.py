"""
Naive baseline for comparison against the agent's real batch results (depth-add #1, see
DEVLOG.md 2026-08-25 "depth-add plan"). Turns an absolute "Rs.X recovered" claim into a
comparative one: "our policy-driven agent recovered X%, vs. an estimated Y% for a naive
fixed-retry-schedule baseline" -- directly strengthens the brief's "show measured money
recovered" bar with evidence, not just a number in isolation.

Design principle: this must be a genuinely naive policy, not a strawman built to lose. It
mirrors the simplest real-world pattern actually used before reason-aware/compliant retry
logic existed (see NOVELTY.md's citation of Razorpay's own Failed Payment Recovery product:
"treats all failures uniformly... no root-cause diagnosis") -- one fixed retry, same schedule,
regardless of case type or decline reason. No LLM call, no guardrail awareness, no reasoning.
This IS the thing the project is differentiating against, made concrete and runnable rather
than just asserted in prose.
"""

from __future__ import annotations

import json
from pathlib import Path

from models import Case, DeclineClass, Surface

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# The single fixed rule: retry once, unconditionally, regardless of surface or reason.
# This is deliberately dumb -- it's what "no root-cause diagnosis" (NOVELTY.md's citation of
# the gap in Razorpay's own current product) looks like as an actual runnable policy.
BASELINE_RETRY_SUCCESS_RATE = {
    # A blind, single retry succeeds only when the underlying problem was transient AND
    # happened to have cleared by the time of the fixed retry -- estimated from the hard/soft
    # decline split already used elsewhere in this project (soft declines are transient by
    # definition, hard declines cannot be fixed by retrying the same instrument at all).
    # These are illustrative estimates for a demo comparison, not a claimed real-world stat --
    # labeled as such wherever this baseline's numbers are surfaced (dashboard, video, README).
    "payment_failure_soft": 0.35,   # a same-instrument retry sometimes catches insufficient
                                     # funds clearing or a transient bank/gateway blip
    "payment_failure_hard": 0.0,    # cannot succeed by definition -- retrying an expired card
                                     # or invalid VPA on the same instrument never works
    "checkout_abandonment": 0.15,   # a generic un-timed nudge recovers some abandoned carts,
                                     # but far fewer than a stage/timing-aware intervention
    "overdue_receivable": 0.10,     # a single generic reminder, no PTP tracking, no escalation
}


def _bucket_for(case: Case) -> str:
    if case.surface == Surface.PAYMENT_FAILURE and case.payment_details:
        suffix = "hard" if case.payment_details.decline_class == DeclineClass.HARD else "soft"
        return f"payment_failure_{suffix}"
    return case.surface.value


def run_naive_baseline(cases: list[Case], seed: int = 42) -> dict:
    """
    Applies the fixed single-retry policy to every case and computes the same headline
    metrics shape as metrics.compute_headline_metrics(), so the two are directly comparable
    in the dashboard/report. Uses a seeded RNG (not real randomness) so this baseline run is
    reproducible across re-runs, same as generate_cases.py's own seeding.
    """
    import random
    rng = random.Random(seed)

    at_risk = sum(c.amount_inr for c in cases)
    recovered = 0.0
    recovered_count = 0

    per_case_outcomes = []
    for case in cases:
        bucket = _bucket_for(case)
        success_rate = BASELINE_RETRY_SUCCESS_RATE.get(bucket, 0.1)
        succeeded = rng.random() < success_rate
        if succeeded:
            recovered += case.amount_inr
            recovered_count += 1
        per_case_outcomes.append({
            "case_id": case.case_id,
            "surface": case.surface.value,
            "bucket": bucket,
            "amount_inr": case.amount_inr,
            "recovered": succeeded,
        })

    return {
        "policy": "naive_fixed_retry_baseline",
        "amount_at_risk_inr": round(at_risk, 2),
        "amount_recovered_inr": round(recovered, 2),
        "recovery_rate": round(recovered / at_risk, 4) if at_risk > 0 else 0.0,
        "case_count": len(cases),
        "recovered_case_count": recovered_count,
        "per_case": per_case_outcomes,
    }


def load_cases() -> list[Case]:
    cases_raw = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))
    return [Case.model_validate(c) for c in cases_raw]


if __name__ == "__main__":
    result = run_naive_baseline(load_cases())
    print(json.dumps({k: v for k, v in result.items() if k != "per_case"}, indent=2))
