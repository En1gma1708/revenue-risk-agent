"""
Architecture-scale stress test -- separate from, and never touching, the curated 95-case demo
batch in data/. Generates a large synthetic dataset (default 1,000 cases) and runs it through
every part of the pipeline that ISN'T LLM-bound: routing, the guardrail engine, DB persistence,
and metrics computation.

Why this exists and what it deliberately does NOT claim (see DEVLOG.md 2026-08-30 "scalability
comparison" entry): the case agent loop makes real LLM calls per case and is bound by free-tier
provider quotas -- that's an external constraint on THROUGHPUT, not on the architecture. This
script proves the parts of the system that ARE ours to control (routing, guardrails, persistence,
reporting) scale cleanly to volumes far beyond the 95-case demo batch, without pretending the LLM
reasoning step scales the same way -- that would be dishonest given everything already documented
about free-tier quota limits.

Uses a fake ProposedAction per case (not a real agent_loop.py run) specifically so this measures
the deterministic pipeline's own performance, isolated from LLM latency/quota entirely.

Run with: python backend/stress_test.py [--n N_PER_SURFACE] [--seed SEED]
Writes to data_stress/ (gitignored, never data/) and prints timing + tier-distribution results.
Safe to re-run any time -- never modifies data/cases.json or data/attempt_history.json.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from db import init_db, insert_decision_log_entry, load_all_cases, load_all_decision_log_entries, upsert_case
from generate_cases import main as generate
from guardrails import AttemptHistory, ProposedAction, evaluate_guardrails
from metrics import compute_headline_metrics, compute_reliability_metrics
from models import ActionTaken, Case, DecisionLogEntry
from router import route_batch

STRESS_DATA_DIR = Path(__file__).resolve().parent.parent / "data_stress"


def run(n_per_surface: int, seed: int) -> dict:
    print(f"Generating {n_per_surface * 3} synthetic cases ({n_per_surface} per surface, seed={seed})...")
    generate(n_payment=n_per_surface, n_checkout=n_per_surface, n_receivable=n_per_surface,
              out_dir=STRESS_DATA_DIR, seed=seed)

    cases_raw = json.loads((STRESS_DATA_DIR / "cases.json").read_text(encoding="utf-8"))
    cases = [Case.model_validate(c) for c in cases_raw]

    t0 = time.time()
    routed = route_batch(cases)
    route_elapsed = time.time() - t0

    conn = sqlite3.connect(":memory:")
    init_db(conn)

    t1 = time.time()
    for c in routed:
        upsert_case(conn, c)
        action = ProposedAction(action_type="schedule_retry", channel="card", amount=c.amount_inr)
        result = evaluate_guardrails(c, action, AttemptHistory())
        entry = DecisionLogEntry(
            log_id=str(uuid.uuid4()), case_id=c.case_id, timestamp=datetime.utcnow(),
            iteration=1, observed={}, decision={},
            reasoning="stress-test synthetic decision -- NOT a real agent_loop.py run, see module docstring",
            guardrail_check=result,
            action_taken=ActionTaken.EXECUTED if result.passed else ActionTaken.BLOCKED,
            action_tier=result.tier,
            outcome='{"simulated": true}' if result.passed else None,
            amount_at_risk_inr=c.amount_inr,
        )
        insert_decision_log_entry(conn, entry)
    pipeline_elapsed = time.time() - t1

    t2 = time.time()
    db_cases = load_all_cases(conn)
    db_entries = load_all_decision_log_entries(conn)
    headline = compute_headline_metrics(db_cases, db_entries)
    reliability = compute_reliability_metrics(db_cases, db_entries)
    metrics_elapsed = time.time() - t2

    tiers = Counter(e["action_tier"] for e in db_entries)
    conn.close()

    return {
        "n_cases": len(routed),
        "route_ms": round(route_elapsed * 1000, 1),
        "pipeline_ms": round(pipeline_elapsed * 1000, 1),
        "metrics_ms": round(metrics_elapsed * 1000, 1),
        "total_ms": round((route_elapsed + pipeline_elapsed + metrics_elapsed) * 1000, 1),
        "tier_distribution": dict(tiers),
        "reliability": f"{reliability['clean_cases']}/{reliability['total_cases']}",
        "amount_at_risk_inr": headline["amount_at_risk_inr"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=334,
                         help="Cases per surface (default 334 -> ~1,000 total)")
    parser.add_argument("--seed", type=int, default=999,
                         help="Different from config.SEED on purpose, so this is structurally "
                              "distinguishable from the real demo batch.")
    args = parser.parse_args()

    result = run(args.n, args.seed)
    print()
    print("=" * 60)
    print("STRESS TEST RESULT (architecture, not LLM throughput)")
    print("=" * 60)
    print(json.dumps(result, indent=2))
