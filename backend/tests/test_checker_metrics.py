"""
Regression coverage for compute_checker_metrics() -- added 2026-08-30 alongside
pydantic_agents.py's checker/reflection agent, so checker activity (how often it ran, what it
found) is a queryable metric like every other claim in this project, not something re-derived by
hand from raw DB rows (see metrics.py's own module comment for the same rationale applied
earlier to compute_reliability_metrics).

Run with: pytest backend/tests/test_checker_metrics.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metrics import compute_checker_metrics


def _case(case_id: str) -> dict:
    return {"case_id": case_id, "amount_inr": 1000.0}


def _checker_entry(case_id: str, sound: bool, recommended_action: str = "accept") -> dict:
    return {
        "case_id": case_id,
        "outcome": "checker_approved" if sound else "checker_flagged",
        "decision": {"sound": sound, "recommended_action": recommended_action},
    }


def test_no_checker_activity_reports_all_zeros():
    cases = [_case("PMT-A"), _case("PMT-B")]
    log_entries = [{"case_id": "PMT-A", "outcome": "executed"}, {"case_id": "PMT-B", "outcome": "executed"}]
    result = compute_checker_metrics(cases, log_entries)
    assert result["cases_reviewed"] == 0
    assert result["cases_flagged"] == 0
    assert result["review_rate"] == 0.0
    assert result["flag_rate_of_reviewed"] == 0.0


def test_mixed_approved_and_flagged_counted_correctly():
    cases = [_case("PMT-A"), _case("PMT-B"), _case("PMT-C"), _case("PMT-D")]
    log_entries = [
        _checker_entry("PMT-A", sound=True),
        _checker_entry("PMT-B", sound=False, recommended_action="retry_specialist"),
        _checker_entry("PMT-C", sound=False, recommended_action="escalate_to_human"),
        # PMT-D never reviewed at all (routine case, checker never triggered)
    ]
    result = compute_checker_metrics(cases, log_entries)
    assert result["cases_reviewed"] == 3
    assert result["cases_flagged"] == 2
    assert result["flagged_retried"] == 1
    assert result["flagged_escalated"] == 1
    assert result["review_rate"] == 0.75          # 3 of 4 cases
    assert result["flag_rate_of_reviewed"] == round(2 / 3, 4)


def test_non_checker_entries_are_ignored():
    """A case with plenty of ordinary (non-checker) log entries but no checker activity must not
    be miscounted as reviewed just because it has log entries at all."""
    cases = [_case("PMT-A")]
    log_entries = [
        {"case_id": "PMT-A", "outcome": "executed"},
        {"case_id": "PMT-A", "outcome": "queued_for_human_approval"},
    ]
    result = compute_checker_metrics(cases, log_entries)
    assert result["cases_reviewed"] == 0
    assert result["cases_flagged"] == 0
