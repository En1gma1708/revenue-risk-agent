"""
Stage 0 router — plain Python, deliberately NOT an LLM call.

Classifying which surface a case belongs to and how severe it is are structural facts derivable
from the event shape (does it have a payment error_reason? a cart abandonment_stage? days_overdue?)
-- not judgment calls. Keeping this in code is both cheaper and a more honest answer to "where does
the real agent reasoning happen": it happens in the case agent loop (agent_loop.py), not here.
See NOVELTY.md "Agentic pattern audit" for why this is routing, not orchestrator-workers.
"""

from __future__ import annotations

from models import Case, DeclineClass, Surface, SubscriptionStatus


def compute_severity(case: Case) -> float:
    """
    Returns a 0-1 severity score used only to order/prioritize cases for processing and to
    surface in the dashboard -- NOT consulted by any guardrail (guardrails.py is the sole
    enforcement point, independent of this score).
    """
    if case.surface == Surface.PAYMENT_FAILURE and case.payment_details:
        d = case.payment_details
        score = 0.3
        if d.decline_class == DeclineClass.HARD:
            score += 0.2
        if d.subscription_status == SubscriptionStatus.HALTED:
            score += 0.3
        score += min(d.attempt_number / 4, 1.0) * 0.2
        return min(score, 1.0)

    if case.surface == Surface.CHECKOUT_ABANDONMENT and case.checkout_details:
        d = case.checkout_details
        # fresher abandonments are more recoverable -> higher priority to act on
        recency_score = max(0.0, 1.0 - (d.minutes_since_abandon / 2880))
        value_score = min(d.cart_value_inr / 60000, 1.0)
        return round(0.6 * recency_score + 0.4 * value_score, 3)

    if case.surface == Surface.OVERDUE_RECEIVABLE and case.receivable_details:
        d = case.receivable_details
        overdue_score = min(d.days_overdue / 90, 1.0)
        value_score = min(case.amount_inr / 500000, 1.0)
        ptp_missed_bump = 0.2 if (d.ptp and d.ptp.status.value == "missed") else 0.0
        return min(0.5 * overdue_score + 0.3 * value_score + ptp_missed_bump, 1.0)

    return 0.5


def route_case(case: Case) -> Case:
    """Assigns severity_score in place and returns the case. Surface is already set at creation
    time (it's part of the Case schema itself), so routing here is really just prioritization."""
    case.severity_score = compute_severity(case)
    return case


def route_batch(cases: list[Case]) -> list[Case]:
    for c in cases:
        route_case(c)
    # highest severity first -- if a batch run gets interrupted or rate-limited, the most
    # urgent/valuable cases are the ones that actually got processed.
    return sorted(cases, key=lambda c: c.severity_score, reverse=True)
