"""
Synthetic data generator for the demo batch.

Produces data/cases.json + data/attempt_history.json across all 3 surfaces with realistic
distributions (see PRD.md SS7, METRICS.md "curate the demo batch" honesty rule). Seeded for
reproducibility so the dashboard/agent-loop work is done against a stable batch while iterating.

Data honesty (CLAUDE.md): payment-failure cases here are marked is_real_razorpay_data=False by
default -- Phase 4 will splice in a subset of REAL Razorpay test-mode payment-failure records once
the API key is available, which get is_real_razorpay_data=True. Checkout-abandonment and
overdue-receivable cases are and will remain fully synthetic, because Razorpay's platform does not
expose that data even in test mode -- this is a platform constraint, not a shortcut (see NOVELTY.md).
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from config import DEMO_TODAY, SEED
from models import (
    AbandonmentStage,
    AttemptRecord,
    Case,
    CaseStatus,
    CheckoutAbandonmentDetails,
    ContactChannel,
    DeclineClass,
    Device,
    InstrumentType,
    PaymentFailureDetails,
    PromiseToPay,
    PTPStatus,
    ReceivableDetails,
    Surface,
    SubscriptionStatus,
    decline_class_for,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

FIRST_NAMES = ["Aarav", "Vihaan", "Priya", "Ananya", "Rohan", "Ishita", "Kabir", "Meera",
               "Aditya", "Sanya", "Karthik", "Neha", "Rahul", "Divya", "Arjun", "Pooja"]
LAST_NAMES = ["Sharma", "Verma", "Iyer", "Reddy", "Nair", "Gupta", "Menon", "Rao",
              "Kapoor", "Joshi", "Pillai", "Chatterjee"]

CART_ITEMS_POOL = [
    "Wireless Earbuds", "Yoga Mat", "Desk Lamp", "Running Shoes", "Backpack",
    "Coffee Grinder", "Bluetooth Speaker", "Notebook Set", "Water Bottle", "Phone Case",
]


def rand_name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


# ---------------------------------------------------------------------------
# Surface 1: Payment failures
# ---------------------------------------------------------------------------

# Weighted so insufficient_funds / bank_technical_error dominate (soft), with rarer hard declines --
# matches the industry framing from research (soft declines are the majority of real-world failures).
CARD_REASON_WEIGHTS = [
    ("insufficient_funds", 30),
    ("bank_technical_error", 15),
    ("payment_timed_out", 12),
    ("card_expired", 10),
    ("authentication_failed", 10),
    ("transaction_limit_exceeded", 8),
    ("incorrect_cvv", 7),
    ("payment_risk_check_failed", 4),
    ("debit_instrument_blocked", 3),
    ("card_not_enrolled", 1),
]
UPI_REASON_WEIGHTS = [
    ("insufficient_funds", 35),
    ("bank_technical_error", 18),
    ("payment_collect_request_expired", 15),
    ("invalid_vpa", 10),
    ("payment_declined", 10),
    ("vpa_resolution_failed", 7),
    ("customer_bank_account_mismatch", 5),
]


def weighted_choice(rng: random.Random, weights: list[tuple[str, int]]) -> str:
    total = sum(w for _, w in weights)
    r = rng.uniform(0, total)
    upto = 0
    for item, w in weights:
        upto += w
        if upto >= r:
            return item
    return weights[-1][0]


def generate_payment_failures(rng: random.Random, n: int) -> tuple[list[Case], list[AttemptRecord]]:
    cases: list[Case] = []
    attempts: list[AttemptRecord] = []

    for i in range(n):
        case_id = f"PMT-{i + 1:04d}"
        instrument = rng.choice([InstrumentType.CARD, InstrumentType.CARD, InstrumentType.UPI, InstrumentType.UPI, InstrumentType.NETBANKING])
        reason = (
            weighted_choice(rng, CARD_REASON_WEIGHTS) if instrument == InstrumentType.CARD
            else weighted_choice(rng, UPI_REASON_WEIGHTS) if instrument == InstrumentType.UPI
            else weighted_choice(rng, CARD_REASON_WEIGHTS)   # netbanking reuses card-shaped reasons
        )
        decline_class = decline_class_for(instrument, reason)
        amount = round(rng.choice([
            rng.uniform(200, 2000),      # small consumer payments, most common
            rng.uniform(2000, 20000),
            rng.uniform(20000, 80000),   # rarer, high-value -- exercises the guardrail threshold
        ]), 2)

        created_days_ago = rng.randint(0, 10)
        created_at = DEMO_TODAY - timedelta(days=created_days_ago, hours=rng.randint(0, 23))

        has_subscription = rng.random() < 0.2
        subscription_status = None
        subscription_id = None
        attempt_number = 1
        if has_subscription:
            subscription_id = f"sub_{case_id.lower()}"
            attempt_number = rng.choice([1, 1, 2, 2, 3, 4, 4])   # skew toward earlier attempts, several at cap
            subscription_status = SubscriptionStatus.HALTED if attempt_number >= 4 else SubscriptionStatus.PENDING

            # populate attempt history so guardrail spacing/cap checks have something real to evaluate
            first_attempt_time = created_at - timedelta(days=attempt_number)
            for a in range(attempt_number):
                attempts.append(AttemptRecord(
                    case_id=case_id,
                    attempt_number=a + 1,
                    action_type="schedule_retry",
                    channel="upi_autopay" if instrument == InstrumentType.UPI else "card",
                    executed_at=first_attempt_time + timedelta(days=a),
                    outcome="failure",
                ))

        cases.append(Case(
            case_id=case_id,
            surface=Surface.PAYMENT_FAILURE,
            created_at=created_at,
            customer_id=f"cust_{i + 1:04d}",
            customer_name=rand_name(rng),
            amount_inr=amount,
            status=CaseStatus.OPEN,
            payment_details=PaymentFailureDetails(
                razorpay_payment_id=f"pay_synthetic_{case_id.lower()}",
                error_code="BAD_REQUEST_ERROR" if decline_class == DeclineClass.HARD else "GATEWAY_ERROR",
                error_description=reason.replace("_", " ").capitalize(),
                error_source=rng.choice(["bank", "customer", "gateway"]),
                error_step="payment_authorization",
                error_reason=reason,
                instrument_type=instrument,
                decline_class=decline_class,
                subscription_id=subscription_id,
                subscription_status=subscription_status,
                attempt_number=attempt_number,
                is_real_razorpay_data=False,   # Phase 4 splices in real ones separately
            ),
        ))

    return cases, attempts


# ---------------------------------------------------------------------------
# Surface 2: Checkout abandonment (fully synthetic -- Razorpay test mode doesn't expose this)
# ---------------------------------------------------------------------------

STAGE_WEIGHTS = [
    (AbandonmentStage.BANK_REDIRECT, 35),
    (AbandonmentStage.OTP_ENTRY, 30),
    (AbandonmentStage.INSTRUMENT_SELECT, 20),
    (AbandonmentStage.REVIEW, 15),
]


def generate_checkout_abandonments(rng: random.Random, n: int) -> list[Case]:
    cases: list[Case] = []
    for i in range(n):
        case_id = f"CART-{i + 1:04d}"
        stage = weighted_choice(rng, [(s.value, w) for s, w in STAGE_WEIGHTS])
        # log-normal-ish: mostly small carts, a tail of big-ticket ones
        cart_value = round(rng.choice([
            rng.uniform(300, 3000),
            rng.uniform(3000, 15000),
            rng.uniform(15000, 60000),
        ]), 2)
        minutes_since = rng.choice([
            rng.uniform(5, 60),
            rng.uniform(60, 720),
            rng.uniform(720, 2880),   # up to 48h, some stale carts to demo write-off logic
        ])
        n_items = rng.randint(1, 4)
        items = rng.sample(CART_ITEMS_POOL, n_items)

        cases.append(Case(
            case_id=case_id,
            surface=Surface.CHECKOUT_ABANDONMENT,
            created_at=DEMO_TODAY - timedelta(minutes=minutes_since),
            customer_id=f"cust_cart_{i + 1:04d}",
            customer_name=rand_name(rng),
            amount_inr=cart_value,
            status=CaseStatus.OPEN,
            checkout_details=CheckoutAbandonmentDetails(
                cart_value_inr=cart_value,
                items=items,
                abandonment_stage=AbandonmentStage(stage),
                device=rng.choice(list(Device)),
                minutes_since_abandon=round(minutes_since, 1),
            ),
        ))
    return cases


# ---------------------------------------------------------------------------
# Surface 3: Overdue receivables (fully synthetic), with pre-dated PTPs
# ---------------------------------------------------------------------------

def generate_receivables(rng: random.Random, n: int) -> list[Case]:
    cases: list[Case] = []
    for i in range(n):
        case_id = f"INV-{i + 1:04d}"
        days_overdue = rng.choice([
            rng.randint(1, 15),
            rng.randint(15, 45),
            rng.randint(45, 95),
        ])
        due_date = (DEMO_TODAY - timedelta(days=days_overdue)).date()
        # B2B scale amounts, skewed so several naturally cross the high-value approval threshold
        amount = round(rng.choice([
            rng.uniform(10000, 40000),
            rng.uniform(40000, 100000),
            rng.uniform(100000, 500000),
        ]), 2)

        ptp = None
        ptp_roll = rng.random()
        if ptp_roll < 0.5:
            # PTP already made, date in the simulated past relative to DEMO_TODAY -- lets the
            # agent's "check back on promised date" logic run immediately at batch time.
            made_days_ago = rng.randint(3, days_overdue)
            promised_offset = rng.randint(-10, 5)   # negative = promised date already passed
            made_at = DEMO_TODAY - timedelta(days=made_days_ago)
            promised_date = (DEMO_TODAY + timedelta(days=promised_offset)).date()
            if promised_offset < 0:
                status = rng.choice([PTPStatus.MISSED, PTPStatus.MISSED, PTPStatus.KEPT])
            else:
                status = PTPStatus.PENDING
            ptp = PromiseToPay(
                promised_amount=round(amount * rng.uniform(0.5, 1.0), 2),
                promised_date=promised_date,
                promised_channel=rng.choice(["email", "call", "whatsapp"]),
                made_at=made_at,
                status=status,
            )

        cases.append(Case(
            case_id=case_id,
            surface=Surface.OVERDUE_RECEIVABLE,
            created_at=datetime.combine(due_date, datetime.min.time()),
            customer_id=f"biz_{i + 1:04d}",
            customer_name=f"{rng.choice(FIRST_NAMES)} Enterprises",
            amount_inr=amount,
            status=CaseStatus.OPEN,
            receivable_details=ReceivableDetails(
                invoice_id=f"inv_{case_id.lower()}",
                due_date=due_date,
                days_overdue=days_overdue,
                ptp=ptp,
                contact_channel_pref=rng.choice(list(ContactChannel)),
            ),
        ))
    return cases


# ---------------------------------------------------------------------------
# Serialization + entrypoint
# ---------------------------------------------------------------------------

def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(f"Not serializable: {o!r}")


def _round_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def main(n_payment=40, n_checkout=30, n_receivable=25):
    rng = random.Random(SEED)

    payment_cases, attempts = generate_payment_failures(rng, n_payment)
    checkout_cases = generate_checkout_abandonments(rng, n_checkout)
    receivable_cases = generate_receivables(rng, n_receivable)

    all_cases = payment_cases + checkout_cases + receivable_cases
    for c in all_cases:
        c.created_at = _round_to_minute(c.created_at)
    rng.shuffle(all_cases)

    DATA_DIR.mkdir(exist_ok=True)

    cases_path = DATA_DIR / "cases.json"
    attempts_path = DATA_DIR / "attempt_history.json"

    cases_path.write_text(
        json.dumps([c.model_dump(mode="json") for c in all_cases], indent=2, default=_json_default),
        encoding="utf-8",
    )
    attempts_path.write_text(
        json.dumps([a.model_dump(mode="json") for a in attempts], indent=2, default=_json_default),
        encoding="utf-8",
    )

    print(f"Generated {len(all_cases)} cases -> {cases_path}")
    print(f"  payment_failure: {n_payment}")
    print(f"  checkout_abandonment: {n_checkout}")
    print(f"  overdue_receivable: {n_receivable}")
    print(f"Generated {len(attempts)} attempt records -> {attempts_path}")

    total_at_risk = sum(c.amount_inr for c in all_cases)
    print(f"Total at risk: Rs.{total_at_risk:,.2f}")


if __name__ == "__main__":
    main()
