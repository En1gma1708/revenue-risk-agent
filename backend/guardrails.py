"""
Guardrail / policy engine — the compliance layer the agent CANNOT talk its way around.

Design principle (see NOVELTY.md): a guardrail enforced only in a system prompt is a suggestion,
not a guarantee, because a prompt is just more context the model reads — it cannot force the
model's next action. Every rule here is a pure function evaluated in CODE, inside
`execute_action`'s dispatch handler (agent_loop.py), before any action is allowed to run. The
model's only power is to PROPOSE an action; this module is the only thing with power to let it
happen.

Two distinct checks exist, and they are not interchangeable:
  - `check_guardrails_advisory()` — used by the `check_policy_guardrails` tool. Lets the agent
    reason about constraints *before* committing to a proposal. This is a quality-of-reasoning
    aid, NOT a security boundary — never trust it as enforcement.
  - `enforce_guardrails()` — the real enforcement point, called from inside `execute_action`'s
    handler. Deterministic, unconditional, cannot be bypassed by anything the model says.

Both call the same underlying `evaluate_guardrails()` — the split is about *when* it's called and
what happens with the result, not about different logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from models import (
    ActionTier,
    Case,
    GuardrailResult,
    Surface,
)


# ---------------------------------------------------------------------------
# The proposed action shape the agent's `propose_intervention` tool must produce.
# Kept here (not models.py) because it's guardrail-input-shaped, not a persisted entity.
# ---------------------------------------------------------------------------

@dataclass
class ProposedAction:
    action_type: str                 # e.g. "schedule_retry", "send_payment_link", "request_new_mandate"
    channel: Optional[str] = None    # e.g. "upi_autopay", "card", "email", "whatsapp"
    target_time: Optional[datetime] = None   # when the action would execute
    notify_time: Optional[datetime] = None   # when the customer would be notified (RBI pre-debit rule)
    amount: float = 0.0


@dataclass
class AttemptHistory:
    attempts_this_cycle: list[datetime] = field(default_factory=list)   # execution times, this mandate cycle


# ---------------------------------------------------------------------------
# Action tiers — a property of ACTION TYPE, hard-coded. The model cannot self-assign a tier;
# it can only propose an action_type, and this table (plus guardrail violations) decides the tier.
# ---------------------------------------------------------------------------

ACTION_TIER_DEFAULTS: dict[str, ActionTier] = {
    "send_reminder_message": ActionTier.AUTONOMOUS,
    "send_payment_link": ActionTier.AUTONOMOUS,
    "schedule_retry": ActionTier.AUTONOMOUS,
    "offer_alternate_instrument": ActionTier.AUTONOMOUS,
    "request_new_mandate": ActionTier.APPROVE_FIRST,      # customer-facing re-consent
    "discount_or_waiver": ActionTier.APPROVE_FIRST,        # money left on the table needs a human
    "escalate_to_collections": ActionTier.APPROVE_FIRST,   # B2B relationship risk
    "close_case_unrecoverable": ActionTier.LOG_ONLY,
}

# Amount above which ANY action escalates to APPROVE_FIRST, regardless of action type.
HIGH_VALUE_APPROVAL_THRESHOLD_INR = 50_000.0

# RBI: transactions above this amount require the AFA (Additional Factor Authentication)
# pre-debit notice window even under the e-mandate AFA-exemption rules.
RBI_AFA_EXEMPT_THRESHOLD_INR = 15_000.0
RBI_PREDEBIT_NOTICE_HOURS = 24

# NPCI UPI Autopay: 1 initial attempt + up to 3 retries = 4 total attempts per mandate cycle.
NPCI_MAX_ATTEMPTS_PER_CYCLE = 4
# Required minimum spacing between successive retry attempts (hours since the FIRST attempt).
NPCI_RETRY_SPACING_HOURS = [24, 72, 168]   # T+24h, T+72h, T+168h (day 7)
# NPCI-permitted non-peak windows for Autopay debits, as (start_hour, end_hour) in 24h local time.
NPCI_ALLOWED_HOURS = [(0, 10), (13, 17), (21, 24)]

HARD_DECLINE_ACTION = "retry_same_instrument"   # retrying a hard decline on the same instrument is futile


# ---------------------------------------------------------------------------
# Individual rule checks — each returns True if the action VIOLATES the rule.
# ---------------------------------------------------------------------------

def _in_allowed_hours(t: datetime) -> bool:
    hour = t.hour
    return any(start <= hour < end for start, end in NPCI_ALLOWED_HOURS)


def _violates_npci_max_attempts(case: Case, action: ProposedAction, hist: AttemptHistory) -> bool:
    if action.channel != "upi_autopay":
        return False
    return len(hist.attempts_this_cycle) >= NPCI_MAX_ATTEMPTS_PER_CYCLE


def _violates_npci_spacing(case: Case, action: ProposedAction, hist: AttemptHistory) -> bool:
    if action.channel != "upi_autopay" or not action.target_time or not hist.attempts_this_cycle:
        return False
    first_attempt = min(hist.attempts_this_cycle)
    # attempts_this_cycle includes the initial attempt (index 0 of the cycle).
    # NPCI_RETRY_SPACING_HOURS[0] = required gap before retry #1, [1] = before retry #2, etc.
    # With N attempts so far (>=1, since the initial one is always attempt #1), the NEXT
    # action is retry #N, whose required gap lives at spacing[N - 1].
    retry_index = len(hist.attempts_this_cycle) - 1
    if retry_index >= len(NPCI_RETRY_SPACING_HOURS):
        return True   # already exhausted the defined spacing schedule -> treat as a violation
    required_gap = timedelta(hours=NPCI_RETRY_SPACING_HOURS[retry_index])
    return action.target_time < (first_attempt + required_gap)


def _violates_npci_allowed_hours(case: Case, action: ProposedAction, hist: AttemptHistory) -> bool:
    if action.channel != "upi_autopay" or not action.target_time:
        return False
    return not _in_allowed_hours(action.target_time)


def _violates_rbi_predebit_notice(case: Case, action: ProposedAction, hist: AttemptHistory) -> bool:
    if action.amount <= RBI_AFA_EXEMPT_THRESHOLD_INR:
        return False
    if not action.target_time or not action.notify_time:
        return True   # no notification planned at all for a high-value debit -> violation
    gap = action.target_time - action.notify_time
    return gap < timedelta(hours=RBI_PREDEBIT_NOTICE_HOURS)


def _violates_hard_decline_no_blind_retry(case: Case, action: ProposedAction, hist: AttemptHistory) -> bool:
    if case.surface != Surface.PAYMENT_FAILURE or not case.payment_details:
        return False
    from models import DeclineClass
    return (
        case.payment_details.decline_class == DeclineClass.HARD
        and action.action_type == HARD_DECLINE_ACTION
    )


def _violates_subscription_halted_terminal(case: Case, action: ProposedAction, hist: AttemptHistory) -> bool:
    if case.surface != Surface.PAYMENT_FAILURE or not case.payment_details:
        return False
    from models import SubscriptionStatus
    return (
        case.payment_details.subscription_status == SubscriptionStatus.HALTED
        and action.action_type != "request_new_mandate"
    )


def _violates_high_value_approval(case: Case, action: ProposedAction, hist: AttemptHistory) -> bool:
    return action.amount > HIGH_VALUE_APPROVAL_THRESHOLD_INR


# ---------------------------------------------------------------------------
# The rule table — one row per hard constraint, evaluated in order.
# Declarative and inspectable: this table IS the demo-able "here are the rules the agent
# cannot violate" artifact (dashboard's guardrail ledger panel reads straight from this).
# ---------------------------------------------------------------------------

@dataclass
class GuardrailRule:
    rule_id: str
    description: str
    applies_to: list[Surface]
    check: Callable[[Case, ProposedAction, AttemptHistory], bool]   # True = violated
    tier_on_violation: ActionTier


GUARDRAILS: list[GuardrailRule] = [
    GuardrailRule(
        rule_id="npci_max_attempts",
        description="NPCI UPI Autopay caps a mandate cycle at 1 initial + 3 retries (4 total attempts).",
        applies_to=[Surface.PAYMENT_FAILURE],
        check=_violates_npci_max_attempts,
        tier_on_violation=ActionTier.HARD_STOP,
    ),
    GuardrailRule(
        rule_id="npci_retry_spacing",
        description="Retry does not respect NPCI's required T+24h / T+72h / T+168h spacing.",
        applies_to=[Surface.PAYMENT_FAILURE],
        check=_violates_npci_spacing,
        tier_on_violation=ActionTier.HARD_STOP,
    ),
    GuardrailRule(
        rule_id="npci_allowed_hours",
        description="UPI Autopay retry scheduled outside NPCI-permitted non-peak windows.",
        applies_to=[Surface.PAYMENT_FAILURE],
        check=_violates_npci_allowed_hours,
        tier_on_violation=ActionTier.HARD_STOP,
    ),
    GuardrailRule(
        rule_id="rbi_predebit_notice",
        description="RBI requires >=24h pre-debit notice for debits above the Rs.15,000 AFA-exempt threshold.",
        applies_to=[Surface.PAYMENT_FAILURE],
        check=_violates_rbi_predebit_notice,
        tier_on_violation=ActionTier.HARD_STOP,
    ),
    GuardrailRule(
        rule_id="hard_decline_no_blind_retry",
        description="Hard-decline reason codes cannot be blind-retried on the same instrument.",
        applies_to=[Surface.PAYMENT_FAILURE],
        check=_violates_hard_decline_no_blind_retry,
        tier_on_violation=ActionTier.HARD_STOP,
    ),
    GuardrailRule(
        rule_id="subscription_halted_terminal",
        description="A halted subscription only accepts a request_new_mandate action.",
        applies_to=[Surface.PAYMENT_FAILURE],
        check=_violates_subscription_halted_terminal,
        tier_on_violation=ActionTier.HARD_STOP,
    ),
    GuardrailRule(
        rule_id="high_value_approval",
        description="Actions above Rs.50,000 require human approval regardless of action type.",
        applies_to=[Surface.PAYMENT_FAILURE, Surface.CHECKOUT_ABANDONMENT, Surface.OVERDUE_RECEIVABLE],
        check=_violates_high_value_approval,
        tier_on_violation=ActionTier.APPROVE_FIRST,
    ),
]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_guardrails(
    case: Case,
    action: ProposedAction,
    history: AttemptHistory,
) -> GuardrailResult:
    """
    Runs every applicable rule. A HARD_STOP violation always wins (most severe tier).
    If no HARD_STOP but an APPROVE_FIRST violation exists, that wins next.
    Otherwise falls back to the action type's default tier.
    """
    violated: list[str] = []
    messages: list[str] = []
    worst_tier: Optional[ActionTier] = None

    tier_severity = {
        ActionTier.HARD_STOP: 3,
        ActionTier.APPROVE_FIRST: 2,
        ActionTier.LOG_ONLY: 1,
        ActionTier.AUTONOMOUS: 0,
    }

    for rule in GUARDRAILS:
        if case.surface not in rule.applies_to:
            continue
        if rule.check(case, action, history):
            violated.append(rule.rule_id)
            messages.append(rule.description)
            if worst_tier is None or tier_severity[rule.tier_on_violation] > tier_severity[worst_tier]:
                worst_tier = rule.tier_on_violation

    if worst_tier is not None:
        return GuardrailResult(
            passed=(worst_tier not in (ActionTier.HARD_STOP,)),
            tier=worst_tier,
            violated_rule_ids=violated,
            messages=messages,
        )

    default_tier = ACTION_TIER_DEFAULTS.get(action.action_type, ActionTier.APPROVE_FIRST)
    return GuardrailResult(passed=True, tier=default_tier, violated_rule_ids=[], messages=[])


def check_guardrails_advisory(case: Case, action: ProposedAction, history: AttemptHistory) -> GuardrailResult:
    """Advisory pre-check for the `check_policy_guardrails` tool. Read-only; never the enforcement point."""
    return evaluate_guardrails(case, action, history)


def enforce_guardrails(case: Case, action: ProposedAction, history: AttemptHistory) -> GuardrailResult:
    """
    The real enforcement point. Call this from inside execute_action's dispatch handler,
    never from anywhere the model's own output could skip it.
    """
    return evaluate_guardrails(case, action, history)
