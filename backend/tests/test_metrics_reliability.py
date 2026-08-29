"""
Regression coverage for compute_reliability_metrics() -- a SEPARATE implementation of the same
"is this case clean" logic as db.get_cleanly_completed_case_ids(), duplicated because the dashboard
metric and --resume's skip logic are computed in different layers. Both had the identical bug (fixed
2026-08-29, see DEVLOG.md): judging a case by ANY error in its full history instead of only the
entries after its LAST terminal outcome. Found only because the user asked directly whether the two
numbers agreed after the db.py fix landed -- they didn't, because this file hadn't been fixed yet.

Run with: pytest backend/tests/test_metrics_reliability.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metrics import compute_reliability_metrics


def _case(case_id: str) -> dict:
    return {"case_id": case_id, "amount_inr": 1000.0}


def _entry(case_id: str, outcome: str | None) -> dict:
    return {"case_id": case_id, "outcome": outcome}


def test_stale_error_from_an_earlier_attempt_does_not_poison_a_later_clean_attempt():
    cases = [_case("INV-0017")]
    log_entries = [
        _entry("INV-0017", "generation_error"),
        _entry("INV-0017", "generation_error"),
        _entry("INV-0017", '{"simulated": true}'),
    ]
    rel = compute_reliability_metrics(cases, log_entries)
    assert rel["clean_cases"] == 1
    assert rel["failed_cases"] == 0
    assert "INV-0017" not in rel["failed_case_ids"]


def test_case_with_a_clean_attempt_then_a_later_failed_attempt_is_not_clean():
    cases = [_case("C")]
    log_entries = [
        _entry("C", '{"simulated": true}'),
        _entry("C", "generation_error"),
    ]
    rel = compute_reliability_metrics(cases, log_entries)
    assert rel["clean_cases"] == 0
    assert "C" in rel["failed_case_ids"]


def test_this_metric_agrees_with_db_get_cleanly_completed_case_ids():
    """The specific gap that let this bug slip through the first fix: the two implementations
    silently disagreeing with each other. This test pins them together so a future change to one
    without the other fails loudly instead of producing two different "clean count" numbers."""
    import sqlite3
    from datetime import datetime

    from db import get_cleanly_completed_case_ids, init_db, insert_decision_log_entry
    from models import ActionTaken, ActionTier, DecisionLogEntry, GuardrailResult

    conn = sqlite3.connect(":memory:")
    init_db(conn)

    def db_entry(case_id, outcome, minute):
        return DecisionLogEntry(
            log_id=f"{case_id}-{minute}", case_id=case_id,
            timestamp=datetime(2026, 8, 29, 0, minute, 0),
            iteration=1, observed={}, decision={}, reasoning="t",
            guardrail_check=GuardrailResult(passed=True, tier=ActionTier.AUTONOMOUS,
                                             violated_rule_ids=[], messages=[]),
            action_taken=ActionTaken.EXECUTED if outcome not in
            ("generation_error", "max_iterations_exceeded") else ActionTaken.BLOCKED,
            action_tier=ActionTier.AUTONOMOUS, outcome=outcome, amount_at_risk_inr=1000.0,
        )

    insert_decision_log_entry(conn, db_entry("X", "generation_error", 0))
    insert_decision_log_entry(conn, db_entry("X", '{"simulated": true}', 10))
    insert_decision_log_entry(conn, db_entry("Y", '{"simulated": true}', 0))
    insert_decision_log_entry(conn, db_entry("Y", "generation_error", 10))

    db_clean = get_cleanly_completed_case_ids(conn)

    cases = [_case("X"), _case("Y")]
    log_entries = [
        _entry("X", "generation_error"), _entry("X", '{"simulated": true}'),
        _entry("Y", '{"simulated": true}'), _entry("Y", "generation_error"),
    ]
    rel = compute_reliability_metrics(cases, log_entries)
    metrics_clean = {c["case_id"] for c in cases} - set(rel["failed_case_ids"])

    assert db_clean == metrics_clean == {"X"}
