"""
Regression coverage for get_cleanly_completed_case_ids() -- the function --resume relies on to
decide which cases to skip. Fixed 2026-08-29 (see DEVLOG.md): it used to disqualify a case
forever if ANY log row across its ENTIRE history was an error, even a stale one from an old
attempt long since superseded by a clean one. Confirmed live on real batch data (INV-0017,
PMT-0030) before this fix -- both had cleanly completed on their latest attempt but were still
being re-run every time because of leftover error rows from 2026-08-27.

Run with: pytest backend/tests/test_db_resume.py -v
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from db import get_cleanly_completed_case_ids, init_db, insert_decision_log_entry
from models import ActionTaken, ActionTier, DecisionLogEntry, GuardrailResult


@pytest.fixture
def conn():
    import sqlite3
    c = sqlite3.connect(":memory:")
    init_db(c)
    yield c
    c.close()


def _entry(case_id: str, iteration: int, outcome: str | None, minute: int) -> DecisionLogEntry:
    return DecisionLogEntry(
        log_id=f"{case_id}-{iteration}-{minute}",
        case_id=case_id,
        timestamp=datetime(2026, 8, 29, 0, minute, 0),
        iteration=iteration,
        observed={},
        decision={},
        reasoning="test entry",
        guardrail_check=GuardrailResult(passed=True, tier=ActionTier.AUTONOMOUS,
                                         violated_rule_ids=[], messages=[]),
        action_taken=ActionTaken.EXECUTED if outcome not in
        ("generation_error", "max_iterations_exceeded") else ActionTaken.BLOCKED,
        action_tier=ActionTier.AUTONOMOUS,
        outcome=outcome,
        amount_at_risk_inr=1000.0,
    )


def test_case_with_only_clean_entries_is_clean(conn):
    insert_decision_log_entry(conn, _entry("A", 1, '{"simulated": true}', 0))
    assert get_cleanly_completed_case_ids(conn) == {"A"}


def test_case_with_only_errors_is_not_clean(conn):
    insert_decision_log_entry(conn, _entry("A", 1, "generation_error", 0))
    insert_decision_log_entry(conn, _entry("A", 2, "generation_error", 1))
    assert get_cleanly_completed_case_ids(conn) == set()


def test_stale_error_from_an_earlier_attempt_does_not_poison_a_later_clean_attempt(conn):
    """The exact regression found live: an early failed attempt (pure errors, no terminal outcome)
    followed by a later attempt that DID reach a clean terminal outcome. The case must count as
    clean -- the stale attempt is superseded, not still-pending."""
    # attempt 1 (failed, cut off by quota exhaustion mid-loop -- no terminal entry)
    insert_decision_log_entry(conn, _entry("INV-0017", 1, "generation_error", 0))
    insert_decision_log_entry(conn, _entry("INV-0017", 2, "generation_error", 1))
    # attempt 2, run later, reaches a real terminal outcome
    insert_decision_log_entry(conn, _entry("INV-0017", 1, '{"simulated": true}', 10))
    assert get_cleanly_completed_case_ids(conn) == {"INV-0017"}


def test_a_partial_success_followed_by_an_error_in_the_same_attempt_is_still_not_clean(conn):
    """A mid-loop execute_action call can succeed and then a LATER turn in that SAME attempt can
    still fail before the loop reaches a real terminal state -- that trailing partial attempt has
    no terminal entry, so the case must still be treated as incomplete, not clean."""
    insert_decision_log_entry(conn, _entry("B", 1, '{"simulated": true}', 0))
    insert_decision_log_entry(conn, _entry("B", 2, "generation_error", 1))
    assert get_cleanly_completed_case_ids(conn) == set()


def test_case_with_a_clean_attempt_then_a_LATER_failed_attempt_is_not_clean(conn):
    """The opposite ordering from the main regression: if the MOST RECENT attempt is the one that
    failed, the case must NOT be considered clean, even though an earlier attempt succeeded --
    the latest recorded result is what matters for trustworthiness."""
    insert_decision_log_entry(conn, _entry("C", 1, '{"simulated": true}', 0))
    insert_decision_log_entry(conn, _entry("C", 1, "generation_error", 10))
    assert get_cleanly_completed_case_ids(conn) == set()


def test_multiple_cases_evaluated_independently(conn):
    insert_decision_log_entry(conn, _entry("CLEAN", 1, '{"simulated": true}', 0))
    insert_decision_log_entry(conn, _entry("DIRTY", 1, "generation_error", 0))
    insert_decision_log_entry(conn, _entry("RECOVERED", 1, "generation_error", 0))
    insert_decision_log_entry(conn, _entry("RECOVERED", 1, "escalated_to_human", 5))
    assert get_cleanly_completed_case_ids(conn) == {"CLEAN", "RECOVERED"}
