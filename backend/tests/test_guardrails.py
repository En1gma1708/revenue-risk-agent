"""
One hand-built scenario per guardrail rule, proving each one actually fires when it should
and stays quiet when it shouldn't. This file is the receipts for the NOVELTY.md claim that
compliance rules are enforced in code, not decoration.

Run with: pytest backend/tests/test_guardrails.py -v
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guardrails import (
    AttemptHistory,
    ProposedAction,
    HARD_DECLINE_ACTION,
    enforce_guardrails,
)
from models import (
    ActionTier,
    Case,
    CaseStatus,
    DeclineClass,
    InstrumentType,
    PaymentFailureDetails,
    ReceivableDetails,
    ContactChannel,
    Surface,
    SubscriptionStatus,
)


def make_payment_case(**overrides) -> Case:
    details = PaymentFailureDetails(
        razorpay_payment_id="pay_test123",
        error_code="BAD_REQUEST_ERROR",
        error_description="Insufficient funds",
        error_source="bank",
        error_step="payment_authorization",
        error_reason="insufficient_funds",
        instrument_type=InstrumentType.UPI,
        decline_class=DeclineClass.SOFT,
        attempt_number=1,
    )
    for k, v in overrides.pop("payment_details_overrides", {}).items():
        setattr(details, k, v)

    case = Case(
        case_id="PMT-0001",
        surface=Surface.PAYMENT_FAILURE,
        created_at=datetime(2026, 8, 20, 9, 0),
        customer_id="cust_1",
        customer_name="Test Customer",
        amount_inr=1000.0,
        status=CaseStatus.OPEN,
        payment_details=details,
    )
    for k, v in overrides.items():
        setattr(case, k, v)
    return case


def make_receivable_case(**overrides) -> Case:
    case = Case(
        case_id="INV-0001",
        surface=Surface.OVERDUE_RECEIVABLE,
        created_at=datetime(2026, 8, 1, 9, 0),
        customer_id="cust_2",
        customer_name="Test Biz",
        amount_inr=20000.0,
        status=CaseStatus.OPEN,
        receivable_details=ReceivableDetails(
            invoice_id="inv_1",
            due_date=date(2026, 7, 15),
            days_overdue=30,
            contact_channel_pref=ContactChannel.EMAIL,
        ),
    )
    for k, v in overrides.items():
        setattr(case, k, v)
    return case


# ---------------------------------------------------------------------------
# npci_max_attempts
# ---------------------------------------------------------------------------

def test_npci_max_attempts_violated_at_5th_attempt():
    case = make_payment_case()
    action = ProposedAction(
        action_type="schedule_retry",
        channel="upi_autopay",
        target_time=datetime(2026, 8, 27, 2, 0),
        amount=1000.0,
    )
    hist = AttemptHistory(attempts_this_cycle=[
        datetime(2026, 8, 20, 9, 0),
        datetime(2026, 8, 21, 9, 0),
        datetime(2026, 8, 24, 9, 0),
        datetime(2026, 8, 27, 1, 0),
    ])
    result = enforce_guardrails(case, action, hist)
    assert "npci_max_attempts" in result.violated_rule_ids
    assert result.tier == ActionTier.HARD_STOP


def test_npci_max_attempts_passes_under_cap():
    case = make_payment_case()
    action = ProposedAction(
        action_type="schedule_retry",
        channel="upi_autopay",
        target_time=datetime(2026, 8, 21, 2, 0),
        notify_time=datetime(2026, 8, 20, 0, 0),
        amount=1000.0,
    )
    hist = AttemptHistory(attempts_this_cycle=[datetime(2026, 8, 20, 9, 0)])
    result = enforce_guardrails(case, action, hist)
    assert "npci_max_attempts" not in result.violated_rule_ids


# ---------------------------------------------------------------------------
# npci_retry_spacing
# ---------------------------------------------------------------------------

def test_npci_spacing_violated_when_too_soon():
    case = make_payment_case()
    first = datetime(2026, 8, 20, 9, 0)
    action = ProposedAction(
        action_type="schedule_retry",
        channel="upi_autopay",
        target_time=first + timedelta(hours=2),   # way under the required 24h gap
        amount=1000.0,
    )
    hist = AttemptHistory(attempts_this_cycle=[first])
    result = enforce_guardrails(case, action, hist)
    assert "npci_retry_spacing" in result.violated_rule_ids
    assert result.tier == ActionTier.HARD_STOP


def test_npci_spacing_passes_when_correctly_spaced():
    case = make_payment_case()
    first = datetime(2026, 8, 20, 9, 0)
    # +26h lands at 11:00 the next day, inside no allowed window boundary ambiguity,
    # and clearly past the 24h minimum gap - isolates the spacing check from the hours check.
    action = ProposedAction(
        action_type="schedule_retry",
        channel="upi_autopay",
        target_time=first + timedelta(hours=28),   # 2026-08-21 13:00 - inside the 13-17 window
        amount=1000.0,
    )
    hist = AttemptHistory(attempts_this_cycle=[first])
    result = enforce_guardrails(case, action, hist)
    assert "npci_retry_spacing" not in result.violated_rule_ids
    assert "npci_allowed_hours" not in result.violated_rule_ids


# ---------------------------------------------------------------------------
# npci_allowed_hours
# ---------------------------------------------------------------------------

def test_npci_allowed_hours_violated_outside_window():
    case = make_payment_case()
    action = ProposedAction(
        action_type="schedule_retry",
        channel="upi_autopay",
        target_time=datetime(2026, 8, 21, 11, 30),   # 11:30am, outside all 3 windows
        amount=1000.0,
    )
    hist = AttemptHistory(attempts_this_cycle=[])
    result = enforce_guardrails(case, action, hist)
    assert "npci_allowed_hours" in result.violated_rule_ids
    assert result.tier == ActionTier.HARD_STOP


def test_npci_allowed_hours_passes_inside_window():
    case = make_payment_case()
    action = ProposedAction(
        action_type="schedule_retry",
        channel="upi_autopay",
        target_time=datetime(2026, 8, 21, 14, 0),   # 2pm, inside 13:00-17:00 window
        amount=1000.0,
    )
    hist = AttemptHistory(attempts_this_cycle=[])
    result = enforce_guardrails(case, action, hist)
    assert "npci_allowed_hours" not in result.violated_rule_ids


# ---------------------------------------------------------------------------
# rbi_predebit_notice
# ---------------------------------------------------------------------------

def test_rbi_predebit_notice_violated_for_high_value_no_notice():
    case = make_payment_case(amount_inr=20000.0)
    action = ProposedAction(
        action_type="schedule_retry",
        channel="card",
        target_time=datetime(2026, 8, 21, 10, 0),
        notify_time=None,
        amount=20000.0,   # above Rs.15,000 threshold
    )
    hist = AttemptHistory()
    result = enforce_guardrails(case, action, hist)
    assert "rbi_predebit_notice" in result.violated_rule_ids
    assert result.tier == ActionTier.HARD_STOP


def test_rbi_predebit_notice_passes_with_sufficient_lead_time():
    case = make_payment_case(amount_inr=20000.0)
    action = ProposedAction(
        action_type="schedule_retry",
        channel="card",
        target_time=datetime(2026, 8, 22, 10, 0),
        notify_time=datetime(2026, 8, 21, 9, 0),   # 25h lead time
        amount=20000.0,
    )
    hist = AttemptHistory()
    result = enforce_guardrails(case, action, hist)
    assert "rbi_predebit_notice" not in result.violated_rule_ids


def test_rbi_predebit_notice_not_applicable_under_threshold():
    case = make_payment_case(amount_inr=5000.0)
    action = ProposedAction(
        action_type="schedule_retry",
        channel="card",
        target_time=datetime(2026, 8, 21, 10, 0),
        notify_time=None,
        amount=5000.0,   # under Rs.15,000, rule doesn't apply
    )
    hist = AttemptHistory()
    result = enforce_guardrails(case, action, hist)
    assert "rbi_predebit_notice" not in result.violated_rule_ids


# ---------------------------------------------------------------------------
# hard_decline_no_blind_retry
# ---------------------------------------------------------------------------

def test_hard_decline_blocks_blind_retry():
    case = make_payment_case(payment_details_overrides={
        "error_reason": "card_expired",
        "decline_class": DeclineClass.HARD,
        "instrument_type": InstrumentType.CARD,
    })
    action = ProposedAction(action_type=HARD_DECLINE_ACTION, channel="card", amount=1000.0)
    hist = AttemptHistory()
    result = enforce_guardrails(case, action, hist)
    assert "hard_decline_no_blind_retry" in result.violated_rule_ids
    assert result.tier == ActionTier.HARD_STOP


def test_hard_decline_allows_alternate_instrument():
    case = make_payment_case(payment_details_overrides={
        "error_reason": "card_expired",
        "decline_class": DeclineClass.HARD,
        "instrument_type": InstrumentType.CARD,
    })
    action = ProposedAction(action_type="offer_alternate_instrument", channel="card", amount=1000.0)
    hist = AttemptHistory()
    result = enforce_guardrails(case, action, hist)
    assert "hard_decline_no_blind_retry" not in result.violated_rule_ids


# ---------------------------------------------------------------------------
# subscription_halted_terminal
# ---------------------------------------------------------------------------

def test_halted_subscription_blocks_non_mandate_action():
    case = make_payment_case(payment_details_overrides={
        "subscription_status": SubscriptionStatus.HALTED,
        "subscription_id": "sub_1",
    })
    action = ProposedAction(action_type="schedule_retry", channel="upi_autopay", amount=1000.0)
    hist = AttemptHistory()
    result = enforce_guardrails(case, action, hist)
    assert "subscription_halted_terminal" in result.violated_rule_ids
    assert result.tier == ActionTier.HARD_STOP


def test_halted_subscription_allows_new_mandate_request():
    case = make_payment_case(payment_details_overrides={
        "subscription_status": SubscriptionStatus.HALTED,
        "subscription_id": "sub_1",
    })
    action = ProposedAction(action_type="request_new_mandate", channel="upi_autopay", amount=1000.0)
    hist = AttemptHistory()
    result = enforce_guardrails(case, action, hist)
    assert "subscription_halted_terminal" not in result.violated_rule_ids


# ---------------------------------------------------------------------------
# high_value_approval
# ---------------------------------------------------------------------------

def test_high_value_forces_approve_first():
    case = make_receivable_case(amount_inr=75000.0)
    action = ProposedAction(action_type="send_reminder_message", channel="email", amount=75000.0)
    hist = AttemptHistory()
    result = enforce_guardrails(case, action, hist)
    assert "high_value_approval" in result.violated_rule_ids
    assert result.tier == ActionTier.APPROVE_FIRST
    assert result.passed is True   # APPROVE_FIRST is not a hard block, just a routing change


def test_low_value_stays_autonomous_default():
    case = make_receivable_case(amount_inr=5000.0)
    action = ProposedAction(action_type="send_reminder_message", channel="email", amount=5000.0)
    hist = AttemptHistory()
    result = enforce_guardrails(case, action, hist)
    assert result.violated_rule_ids == []
    assert result.tier == ActionTier.AUTONOMOUS


# ---------------------------------------------------------------------------
# Cross-cutting: a HARD_STOP always outranks an APPROVE_FIRST when both apply
# ---------------------------------------------------------------------------

def test_hard_stop_outranks_approve_first_when_both_violated():
    case = make_payment_case(amount_inr=75000.0, payment_details_overrides={
        "error_reason": "card_expired",
        "decline_class": DeclineClass.HARD,
        "instrument_type": InstrumentType.CARD,
    })
    action = ProposedAction(action_type=HARD_DECLINE_ACTION, channel="card", amount=75000.0)
    hist = AttemptHistory()
    result = enforce_guardrails(case, action, hist)
    assert result.tier == ActionTier.HARD_STOP
    assert result.passed is False
