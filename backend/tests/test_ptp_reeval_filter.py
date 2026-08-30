"""
Regression coverage for run_batch.find_ptp_due_cases() -- the promise-to-pay re-evaluation
filter shared by run_batch.py and run_batch_multiagent.py.

Real bug found live 2026-08-30 (see DEVLOG.md): the PTP re-evaluation pass filtered over the
--resume-shrunk `cases` list rather than the full batch (`all_cases_for_context`). Under --resume,
a case already marked clean by an earlier run gets excluded from `cases` BEFORE the PTP filter
runs -- so a case with a genuinely due promise-to-pay was silently never re-checked, even though
its promised date had already arrived. This was present in run_batch.py (the proven original)
itself, not something introduced while porting to run_batch_multiagent.py -- both files now share
one implementation (find_ptp_due_cases, defined in run_batch.py) instead of two copies that could
drift, per this project's own "one definition, reused" rule (see db.get_cleanly_completed_case_ids
for the prior instance of this exact lesson).

Run with: pytest backend/tests/test_ptp_reeval_filter.py -v
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Case, CaseStatus, ContactChannel, PromiseToPay, PTPStatus, ReceivableDetails, Surface
from run_batch import find_ptp_due_cases


def make_receivable_case(case_id: str, ptp=None) -> Case:
    return Case(
        case_id=case_id,
        surface=Surface.OVERDUE_RECEIVABLE,
        created_at=datetime(2026, 8, 1, 9, 0),
        customer_id="biz_1",
        customer_name="Test Biz",
        amount_inr=20000.0,
        status=CaseStatus.OPEN,
        receivable_details=ReceivableDetails(
            invoice_id=f"inv_{case_id.lower()}",
            due_date=date(2026, 7, 1),
            days_overdue=54,
            contact_channel_pref=ContactChannel.EMAIL,
            ptp=ptp,
        ),
    )


def make_ptp(promised_date: date, status: PTPStatus = PTPStatus.PENDING) -> PromiseToPay:
    return PromiseToPay(promised_amount=10000.0, promised_date=promised_date,
                         promised_channel="email", made_at=datetime(2026, 7, 20, 9, 0), status=status)


TODAY = date(2026, 8, 24)


def test_finds_case_with_due_ptp():
    case = make_receivable_case("INV-A", ptp=make_ptp(TODAY))
    assert find_ptp_due_cases([case], TODAY) == [case]


def test_finds_case_with_past_due_ptp():
    case = make_receivable_case("INV-A", ptp=make_ptp(TODAY - timedelta(days=5)))
    assert find_ptp_due_cases([case], TODAY) == [case]


def test_excludes_case_with_future_ptp():
    case = make_receivable_case("INV-A", ptp=make_ptp(TODAY + timedelta(days=5)))
    assert find_ptp_due_cases([case], TODAY) == []


def test_excludes_case_with_no_ptp():
    case = make_receivable_case("INV-A", ptp=None)
    assert find_ptp_due_cases([case], TODAY) == []


def test_excludes_non_pending_ptp():
    case = make_receivable_case("INV-A", ptp=make_ptp(TODAY, status=PTPStatus.KEPT))
    assert find_ptp_due_cases([case], TODAY) == []


def test_excludes_non_receivable_surface():
    case = Case(
        case_id="PMT-A", surface=Surface.PAYMENT_FAILURE, created_at=datetime(2026, 8, 1, 9, 0),
        customer_id="cust_1", customer_name="Test", amount_inr=1000.0, status=CaseStatus.OPEN,
    )
    assert find_ptp_due_cases([case], TODAY) == []


def test_regression_full_batch_list_still_finds_case_excluded_from_a_resume_shrunk_list():
    """The actual bug scenario: a case with a due PTP that got excluded from a --resume-shrunk
    list (because it was already clean from an earlier run) must still be found when the caller
    passes the FULL case list, as both run_batch.py and run_batch_multiagent.py now do."""
    due_case = make_receivable_case("INV-DUE", ptp=make_ptp(TODAY))
    other_case = make_receivable_case("INV-OTHER", ptp=None)

    full_batch = [due_case, other_case]
    resume_shrunk = [c for c in full_batch if c.case_id != "INV-DUE"]  # simulates --resume
    # excluding an already-clean case, exactly what happened in the real bug

    assert find_ptp_due_cases(resume_shrunk, TODAY) == []  # the bug's symptom, if this filter
    # were (incorrectly) run against the shrunk list
    assert find_ptp_due_cases(full_batch, TODAY) == [due_case]  # the fix: run against the full list
