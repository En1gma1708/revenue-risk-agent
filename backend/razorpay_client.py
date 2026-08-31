"""
Thin wrapper around Razorpay's test-mode Payments / Subscriptions / Payment Links APIs. This
isolates the ONE real external dependency in the project (see CLAUDE.md "Key files"). Everything
else (checkout abandonment, overdue receivables) is structurally synthetic because Razorpay's
platform does not expose that data even in test mode -- this file only ever touches test-mode
endpoints (rzp_test_ keys), never live/production ones.

Data honesty (see models.py, NOVELTY.md): cases built from real API responses here are always
tagged is_real_razorpay_data=True so the dashboard can visibly distinguish them from synthetic
cases -- never blend silently.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import razorpay
from dotenv import load_dotenv

from models import (
    Case,
    CaseStatus,
    DeclineClass,
    InstrumentType,
    PaymentFailureDetails,
    Surface,
    SubscriptionStatus,
    decline_class_for,
)

load_dotenv()


def get_client() -> razorpay.Client:
    key_id = os.environ["RAZORPAY_KEY_ID"]
    key_secret = os.environ["RAZORPAY_KEY_SECRET"]
    client = razorpay.Client(auth=(key_id, key_secret))
    return client


# ---------------------------------------------------------------------------
# Payments -- pull real test-mode failed payments into Case records
# ---------------------------------------------------------------------------

def _infer_instrument(payment: dict) -> InstrumentType:
    method = payment.get("method", "")
    return {
        "card": InstrumentType.CARD,
        "upi": InstrumentType.UPI,
        "netbanking": InstrumentType.NETBANKING,
    }.get(method, InstrumentType.OTHER)


def _infer_error_reason(payment: dict) -> str:
    # Razorpay's error_reason field is the closest match to the taxonomy in models.py; fall back
    # to error_code/error_description if a payment lacks a structured reason (older test payments
    # sometimes do).
    return payment.get("error_reason") or payment.get("error_code") or "bank_technical_error"


def fetch_failed_payments(client: razorpay.Client, count: int = 20) -> list[Case]:
    """
    Pulls real test-mode payments with status='failed' and converts them into Case records,
    tagged is_real_razorpay_data=True. Returns an empty list gracefully if the test account has
    no failed payments yet (a brand-new account starts with none -- this is expected, not an
    error; see fabricate_test_failures() below to seed some).
    """
    response = client.payment.all({"count": count})
    cases: list[Case] = []

    for payment in response.get("items", []):
        if payment.get("status") != "failed":
            continue

        instrument = _infer_instrument(payment)
        reason = _infer_error_reason(payment)
        decline_class = decline_class_for(instrument, reason)

        cases.append(Case(
            case_id=f"PMT-REAL-{payment['id']}",
            surface=Surface.PAYMENT_FAILURE,
            created_at=datetime.fromtimestamp(payment["created_at"]),
            customer_id=payment.get("customer_id") or payment.get("email", "unknown"),
            customer_name=payment.get("email", "Unknown Customer"),
            amount_inr=payment["amount"] / 100,   # Razorpay amounts are in paise
            status=CaseStatus.OPEN,
            payment_details=PaymentFailureDetails(
                razorpay_payment_id=payment["id"],
                error_code=payment.get("error_code", ""),
                error_description=payment.get("error_description", ""),
                error_source=payment.get("error_source", "bank"),
                error_step=payment.get("error_step", "payment_authorization"),
                error_reason=reason,
                instrument_type=instrument,
                decline_class=decline_class,
                attempt_number=1,
                is_real_razorpay_data=True,
            ),
        ))

    return cases


def fabricate_test_failures(client: razorpay.Client, n: int = 5) -> list[dict]:
    """
    Razorpay's test mode requires a payment to actually be attempted (via the mock checkout page
    with its Success/Failure buttons, or a forced UPI failure via the failure@razorpay VPA) --
    there is no server-side API to directly create a 'failed payment' record without a checkout
    flow. This function creates Orders (a real API call) that CAN be attempted through the
    hosted checkout to produce real failures; it does NOT simulate the failure itself. See
    DEVLOG.md for how this was actually exercised (manual test-mode checkout runs, or accepted
    as a live-only demo step rather than something the batch script can do headlessly).
    """
    orders = []
    for i in range(n):
        order = client.order.create({
            "amount": (500 + i * 137) * 100,   # paise
            "currency": "INR",
            "receipt": f"synthetic_recovery_test_{i}",
        })
        orders.append(order)
    return orders


# ---------------------------------------------------------------------------
# Subscriptions -- walk the real pending -> halted state machine in test mode
# ---------------------------------------------------------------------------

def create_test_plan_and_subscription(client: razorpay.Client, amount_inr: float, interval_days: int = 30) -> dict:
    plan = client.plan.create({
        "period": "daily",
        "interval": interval_days,
        "item": {
            "name": "Revenue Risk Agent Test Plan",
            "amount": int(amount_inr * 100),
            "currency": "INR",
        },
    })
    subscription = client.subscription.create({
        "plan_id": plan["id"],
        "total_count": 12,
        "quantity": 1,
    })
    return subscription


def get_subscription_status(client: razorpay.Client, subscription_id: str) -> Optional[SubscriptionStatus]:
    """
    Maps Razorpay's real subscription status values onto this project's 3-state
    SubscriptionStatus enum (pending/halted/charged -- see models.py). Real Razorpay states are
    broader than that (created, authenticated, active, pending, halted, cancelled, completed,
    expired -- confirmed live 2026-08-31 by creating a real test subscription and fetching it: a
    freshly-created, not-yet-authenticated subscription returns status="created", which this
    function used to silently drop to None instead of a sensible PENDING mapping, a real gap found
    on that live call, not caught before since this function had never actually been exercised
    against a real subscription until then). "created"/"authenticated" both mean "mandate not yet
    active, no charge has failed" -- the same real-world meaning as this project's own PENDING
    state, so both map there. "cancelled"/"completed"/"expired" are genuinely outside this
    project's 3-state model (there's no guardrail behavior defined for them) and correctly return
    None rather than being force-mapped to something misleading.
    """
    sub = client.subscription.fetch(subscription_id)
    status_map = {
        "created": SubscriptionStatus.PENDING,
        "authenticated": SubscriptionStatus.PENDING,
        "pending": SubscriptionStatus.PENDING,
        "halted": SubscriptionStatus.HALTED,
        "active": SubscriptionStatus.CHARGED,
    }
    return status_map.get(sub.get("status"))


# ---------------------------------------------------------------------------
# Payment Links -- the one REAL recovery action: create/resend a link in test mode
# ---------------------------------------------------------------------------

def create_recovery_payment_link(client: razorpay.Client, case: Case, description: str) -> dict:
    """
    This is execute_action's real-world counterpart for send_payment_link on payment-failure
    cases -- an actual test-mode API call, not a simulation. Called from agent_loop.py's
    action_executor callback when action_type == "send_payment_link" and the case is real
    Razorpay data (is_real_razorpay_data=True); synthetic cases still use the simulated executor.
    """
    link = client.payment_link.create({
        "amount": int(case.amount_inr * 100),
        "currency": "INR",
        "description": description,
        "customer": {
            "name": case.customer_name,
            "contact": "",
            "email": case.customer_id if "@" in case.customer_id else "",
        },
        "notify": {"sms": False, "email": False},   # never actually send in a demo/test run
        "reminder_enable": False,
    })
    return link
