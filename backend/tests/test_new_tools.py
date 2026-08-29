"""
Unit tests for the two novelty-gap-check tool additions (see DEVLOG.md 2026-08-25):
check_customer_history (cross-case root-causing) and record_promise_to_pay (active PTP
negotiation). Tests dispatch_tool() directly -- no LLM call, no quota needed, mirrors the
guardrail test suite's approach of hand-built scenarios.

Run with: pytest backend/tests/test_new_tools.py -v
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_loop import CaseAgentState, dispatch_tool
from guardrails import AttemptHistory
from models import (
    ActionTaken,
    ActionTier,
    Case,
    CaseStatus,
    ContactChannel,
    PromiseToPay,
    PTPStatus,
    ReceivableDetails,
    Surface,
)


def make_receivable_case(case_id: str, customer_id: str, amount: float = 30000.0, ptp=None) -> Case:
    return Case(
        case_id=case_id,
        surface=Surface.OVERDUE_RECEIVABLE,
        created_at=datetime(2026, 8, 1, 9, 0),
        customer_id=customer_id,
        customer_name="Test Biz",
        amount_inr=amount,
        status=CaseStatus.OPEN,
        receivable_details=ReceivableDetails(
            invoice_id=f"inv_{case_id.lower()}",
            due_date=date(2026, 7, 1),
            days_overdue=54,
            contact_channel_pref=ContactChannel.EMAIL,
            ptp=ptp,
        ),
    )


# ---------------------------------------------------------------------------
# check_customer_history
# ---------------------------------------------------------------------------

def test_check_customer_history_finds_sibling_cases():
    main_case = make_receivable_case("INV-A", customer_id="biz_1")
    sibling = make_receivable_case("INV-B", customer_id="biz_1")
    unrelated = make_receivable_case("INV-C", customer_id="biz_2")

    state = CaseAgentState(case=main_case, history=AttemptHistory(),
                            all_cases=[main_case, sibling, unrelated])

    result_text, is_error = dispatch_tool("check_customer_history", {}, state, iteration=1)
    result = json.loads(result_text)

    assert is_error is False
    assert result["other_case_count"] == 1
    assert result["other_cases"][0]["case_id"] == "INV-B"


def test_check_customer_history_empty_when_no_siblings():
    main_case = make_receivable_case("INV-A", customer_id="biz_1")
    state = CaseAgentState(case=main_case, history=AttemptHistory(), all_cases=[main_case])

    result_text, _ = dispatch_tool("check_customer_history", {}, state, iteration=1)
    result = json.loads(result_text)

    assert result["other_case_count"] == 0
    assert result["other_cases"] == []


def test_check_customer_history_surfaces_prior_missed_ptp():
    missed_ptp = PromiseToPay(
        promised_amount=10000.0, promised_date=date(2026, 8, 1),
        promised_channel="email", made_at=datetime(2026, 7, 20, 9, 0), status=PTPStatus.MISSED,
    )
    main_case = make_receivable_case("INV-A", customer_id="biz_1")
    sibling_with_missed_ptp = make_receivable_case("INV-B", customer_id="biz_1", ptp=missed_ptp)

    state = CaseAgentState(case=main_case, history=AttemptHistory(),
                            all_cases=[main_case, sibling_with_missed_ptp])

    result_text, _ = dispatch_tool("check_customer_history", {}, state, iteration=1)
    result = json.loads(result_text)

    assert result["other_cases"][0]["prior_ptp_status"] == "missed"


# ---------------------------------------------------------------------------
# record_promise_to_pay
# ---------------------------------------------------------------------------

def test_record_promise_to_pay_succeeds_for_low_value():
    case = make_receivable_case("INV-A", customer_id="biz_1", amount=20000.0)
    state = CaseAgentState(case=case, history=AttemptHistory(), all_cases=[case])

    result_text, is_error = dispatch_tool("record_promise_to_pay", {
        "promised_amount": 20000.0,
        "promised_date": "2026-08-30",
        "promised_channel": "email",
        "reasoning": "Customer confirmed via email they will pay by month end.",
    }, state, iteration=1)
    result = json.loads(result_text)

    assert is_error is False
    assert result["recorded"] is True
    assert result["tier"] == "AUTONOMOUS"
    assert case.receivable_details.ptp is not None
    assert case.receivable_details.ptp.promised_amount == 20000.0
    assert case.receivable_details.ptp.status == PTPStatus.PENDING
    assert state.ptp_recorded is not None
    assert len(state.log_entries) == 1
    assert state.log_entries[0].action_taken == ActionTaken.EXECUTED


def test_record_promise_to_pay_routes_high_value_to_approve_first():
    case = make_receivable_case("INV-A", customer_id="biz_1", amount=75000.0)
    state = CaseAgentState(case=case, history=AttemptHistory(), all_cases=[case])

    result_text, is_error = dispatch_tool("record_promise_to_pay", {
        "promised_amount": 75000.0,   # above HIGH_VALUE_APPROVAL_THRESHOLD_INR (50,000)
        "promised_date": "2026-08-30",
        "promised_channel": "call",
        "reasoning": "Large commitment, needs sign-off.",
    }, state, iteration=1)
    result = json.loads(result_text)

    assert is_error is False
    assert result["recorded"] is True
    assert result["tier"] == "APPROVE_FIRST"
    assert case.receivable_details.ptp is not None   # still recorded, just queued for approval
    assert state.log_entries[0].action_taken == ActionTaken.QUEUED_FOR_APPROVAL


def test_record_promise_to_pay_rejects_non_receivable_case():
    from models import CaseStatus as _CS  # local, avoid unused-import lint on module-level import
    case = Case(
        case_id="PMT-A", surface=Surface.PAYMENT_FAILURE, created_at=datetime(2026, 8, 1, 9, 0),
        customer_id="cust_1", customer_name="Test", amount_inr=1000.0, status=_CS.OPEN,
    )
    state = CaseAgentState(case=case, history=AttemptHistory(), all_cases=[case])

    result_text, is_error = dispatch_tool("record_promise_to_pay", {
        "promised_amount": 1000.0, "promised_date": "2026-08-30",
        "promised_channel": "email", "reasoning": "n/a",
    }, state, iteration=1)

    assert is_error is True
    assert "only valid for overdue_receivable" in json.loads(result_text)["error"]


def test_record_promise_to_pay_rejects_malformed_date():
    case = make_receivable_case("INV-A", customer_id="biz_1")
    state = CaseAgentState(case=case, history=AttemptHistory(), all_cases=[case])

    result_text, is_error = dispatch_tool("record_promise_to_pay", {
        "promised_amount": 20000.0,
        "promised_date": "not-a-date",
        "promised_channel": "email",
        "reasoning": "n/a",
    }, state, iteration=1)

    assert is_error is True
    assert "Invalid promised_amount/promised_date" in json.loads(result_text)["error"]
