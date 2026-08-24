"""
Shared data model for the Revenue Risk Agent.

One `Case` schema, three surfaces (payment_failure / checkout_abandonment / overdue_receivable),
one shared `AttemptRecord` history table, one shared `DecisionLogEntry` audit trail schema.
This unification at the data layer is what makes "one policy across three surfaces" a real,
checkable claim rather than a pitch — the guardrail engine and agent loop operate on this
one shape regardless of which surface a case came from.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Surface(str, Enum):
    PAYMENT_FAILURE = "payment_failure"
    CHECKOUT_ABANDONMENT = "checkout_abandonment"
    OVERDUE_RECEIVABLE = "overdue_receivable"


class CaseStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RECOVERED = "recovered"
    ESCALATED = "escalated"
    BLOCKED = "blocked"
    CLOSED_UNRECOVERABLE = "closed_unrecoverable"


class DeclineClass(str, Enum):
    HARD = "hard"   # permanently bad instrument — don't blind-retry, need a new one
    SOFT = "soft"   # transient — retry with reason-aware timing


class InstrumentType(str, Enum):
    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    OTHER = "other"


class SubscriptionStatus(str, Enum):
    PENDING = "pending"
    HALTED = "halted"
    CHARGED = "charged"


class AbandonmentStage(str, Enum):
    OTP_ENTRY = "otp_entry"
    INSTRUMENT_SELECT = "instrument_select"
    BANK_REDIRECT = "bank_redirect"
    REVIEW = "review"


class Device(str, Enum):
    MOBILE_WEB = "mobile_web"
    DESKTOP = "desktop"
    APP = "app"


class ContactChannel(str, Enum):
    EMAIL = "email"
    SMS = "sms"
    CALL = "call"
    WHATSAPP = "whatsapp"


class PTPStatus(str, Enum):
    PENDING = "pending"
    KEPT = "kept"
    MISSED = "missed"
    RENEGOTIATED = "renegotiated"


class ActionTier(str, Enum):
    AUTONOMOUS = "AUTONOMOUS"
    LOG_ONLY = "LOG_ONLY"
    APPROVE_FIRST = "APPROVE_FIRST"
    HARD_STOP = "HARD_STOP"


class ActionTaken(str, Enum):
    EXECUTED = "executed"
    BLOCKED = "blocked"
    QUEUED_FOR_APPROVAL = "queued_for_approval"
    LOGGED_ONLY = "logged_only"


# ---------------------------------------------------------------------------
# Real Razorpay reason-code taxonomy (documented, public) -> hard/soft decline map
#
# Source: razorpay.com/docs/errors/payments/cards/ and .../upi/
# This is the one piece of genuine fintech domain knowledge the project leans on,
# and it is exactly this: a static lookup table, not a discipline.
# ---------------------------------------------------------------------------

CARD_DECLINE_CLASS: dict[str, DeclineClass] = {
    "insufficient_funds": DeclineClass.SOFT,
    "card_expired": DeclineClass.HARD,
    "incorrect_cvv": DeclineClass.HARD,          # customer input error, not a blind-retry case
    "authentication_failed": DeclineClass.HARD,   # needs customer to re-authenticate, not silent retry
    "payment_risk_check_failed": DeclineClass.HARD,
    "debit_instrument_blocked": DeclineClass.HARD,
    "transaction_limit_exceeded": DeclineClass.SOFT,  # may clear next day/cycle
    "bank_technical_error": DeclineClass.SOFT,
    "payment_timed_out": DeclineClass.SOFT,
    "card_not_enrolled": DeclineClass.HARD,
}

UPI_DECLINE_CLASS: dict[str, DeclineClass] = {
    "insufficient_funds": DeclineClass.SOFT,
    "invalid_vpa": DeclineClass.HARD,
    "payment_collect_request_expired": DeclineClass.SOFT,  # ~10 min timeout, just re-request
    "payment_declined": DeclineClass.HARD,   # bank/customer declined outright
    "vpa_resolution_failed": DeclineClass.HARD,
    "customer_bank_account_mismatch": DeclineClass.HARD,
    "bank_technical_error": DeclineClass.SOFT,
}


def decline_class_for(instrument: InstrumentType, error_reason: str) -> DeclineClass:
    table = CARD_DECLINE_CLASS if instrument == InstrumentType.CARD else UPI_DECLINE_CLASS
    return table.get(error_reason, DeclineClass.SOFT)  # unknown reason -> treat cautiously as soft


# ---------------------------------------------------------------------------
# Surface-specific details
# ---------------------------------------------------------------------------

class PaymentFailureDetails(BaseModel):
    razorpay_payment_id: str
    error_code: str
    error_description: str
    error_source: str          # bank / gateway / customer / business
    error_step: str
    error_reason: str          # e.g. insufficient_funds, card_expired
    instrument_type: InstrumentType
    decline_class: DeclineClass
    subscription_id: Optional[str] = None
    subscription_status: Optional[SubscriptionStatus] = None
    attempt_number: int = 1    # of max 4 for UPI Autopay mandates
    is_real_razorpay_data: bool = False   # honesty flag — see CLAUDE.md data honesty rule


class CheckoutAbandonmentDetails(BaseModel):
    cart_value_inr: float
    items: list[str]
    abandonment_stage: AbandonmentStage
    device: Device
    minutes_since_abandon: float


class PromiseToPay(BaseModel):
    promised_amount: float
    promised_date: date
    promised_channel: str
    made_at: datetime
    status: PTPStatus = PTPStatus.PENDING


class ReceivableDetails(BaseModel):
    invoice_id: str
    due_date: date
    days_overdue: int
    ptp: Optional[PromiseToPay] = None
    contact_channel_pref: ContactChannel


# ---------------------------------------------------------------------------
# Case (the shared envelope)
# ---------------------------------------------------------------------------

class Case(BaseModel):
    case_id: str
    surface: Surface
    created_at: datetime
    customer_id: str
    customer_name: str
    amount_inr: float
    status: CaseStatus = CaseStatus.OPEN
    severity_score: float = 0.0   # 0-1, computed at routing stage

    payment_details: Optional[PaymentFailureDetails] = None
    checkout_details: Optional[CheckoutAbandonmentDetails] = None
    receivable_details: Optional[ReceivableDetails] = None

    def details_for_surface(self):
        return {
            Surface.PAYMENT_FAILURE: self.payment_details,
            Surface.CHECKOUT_ABANDONMENT: self.checkout_details,
            Surface.OVERDUE_RECEIVABLE: self.receivable_details,
        }[self.surface]


# ---------------------------------------------------------------------------
# Attempt history (append-only, needed for NPCI spacing/cap guardrails)
# ---------------------------------------------------------------------------

class AttemptRecord(BaseModel):
    case_id: str
    attempt_number: int
    action_type: str
    channel: str
    executed_at: datetime
    outcome: str = "pending"   # success / failure / pending


# ---------------------------------------------------------------------------
# Guardrail result (produced by guardrails.py, consumed by the audit log)
# ---------------------------------------------------------------------------

class GuardrailResult(BaseModel):
    passed: bool
    tier: ActionTier
    violated_rule_ids: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit trail — the single most judge-visible artifact
# ---------------------------------------------------------------------------

class DecisionLogEntry(BaseModel):
    log_id: str
    case_id: str
    timestamp: datetime
    iteration: int

    observed: dict
    decision: dict
    reasoning: str

    guardrail_check: GuardrailResult
    action_taken: ActionTaken
    action_tier: ActionTier

    outcome: Optional[str] = None
    amount_at_risk_inr: float
    amount_recovered_inr: float = 0.0
