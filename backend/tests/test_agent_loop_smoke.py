"""
Manual smoke test (hits a real free-tier LLM) proving the full case agent loop works end to
end: tool calls flow correctly, guardrails enforce inside execute_action, and a decision log
gets produced. Run this before trusting the loop against a full batch.

Run with: python backend/tests/test_agent_loop_smoke.py [gemini|groq|openrouter]
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_loop import run_case_agent
from guardrails import AttemptHistory
from llm_client import get_llm_client
from models import (
    AttemptRecord,
    Case,
    CaseStatus,
    ContactChannel,
    DeclineClass,
    InstrumentType,
    PaymentFailureDetails,
    ReceivableDetails,
    Surface,
    SubscriptionStatus,
)


def make_soft_decline_case() -> Case:
    """A simple, clearly-recoverable case: soft decline, first attempt, low value.
    Expect the agent to propose a retry and have it execute autonomously."""
    return Case(
        case_id="PMT-SMOKE-01",
        surface=Surface.PAYMENT_FAILURE,
        created_at=datetime(2026, 8, 24, 9, 0),
        customer_id="cust_smoke_1",
        customer_name="Smoke Test Customer",
        amount_inr=1500.0,
        status=CaseStatus.OPEN,
        payment_details=PaymentFailureDetails(
            razorpay_payment_id="pay_smoke_1",
            error_code="GATEWAY_ERROR",
            error_description="Insufficient funds",
            error_source="bank",
            error_step="payment_authorization",
            error_reason="insufficient_funds",
            instrument_type=InstrumentType.UPI,
            decline_class=DeclineClass.SOFT,
            attempt_number=1,
        ),
    )


def make_high_value_receivable_case() -> Case:
    """A high-value receivable: expect the agent's proposed action to get routed to
    APPROVE_FIRST by the high_value_approval guardrail, not executed autonomously."""
    return Case(
        case_id="INV-SMOKE-01",
        surface=Surface.OVERDUE_RECEIVABLE,
        created_at=datetime(2026, 8, 1, 9, 0),
        customer_id="biz_smoke_1",
        customer_name="Smoke Test Enterprises",
        amount_inr=120000.0,
        status=CaseStatus.OPEN,
        receivable_details=ReceivableDetails(
            invoice_id="inv_smoke_1",
            due_date=date(2026, 7, 1),
            days_overdue=54,
            contact_channel_pref=ContactChannel.EMAIL,
        ),
    )


def make_hard_decline_case() -> Case:
    """A hard-decline case (expired card): retrying the same instrument is futile and should
    be blocked by hard_decline_no_blind_retry if the agent tries it. A well-behaved agent should
    instead propose offer_alternate_instrument or request updated details -- either is a pass;
    the real thing this exercises is whether a blind-retry attempt gets correctly HARD_STOP'd."""
    return Case(
        case_id="PMT-SMOKE-HARD-01",
        surface=Surface.PAYMENT_FAILURE,
        created_at=datetime(2026, 8, 24, 9, 0),
        customer_id="cust_smoke_2",
        customer_name="Smoke Test Hard Decline",
        amount_inr=3200.0,
        status=CaseStatus.OPEN,
        payment_details=PaymentFailureDetails(
            razorpay_payment_id="pay_smoke_2",
            error_code="BAD_REQUEST_ERROR",
            error_description="Card expired",
            error_source="customer",
            error_step="payment_authorization",
            error_reason="card_expired",
            instrument_type=InstrumentType.CARD,
            decline_class=DeclineClass.HARD,
            attempt_number=1,
        ),
    )


def make_maxed_upi_mandate_case() -> tuple[Case, AttemptHistory]:
    """A UPI Autopay mandate already at the NPCI 4-attempt cap. ANY further schedule_retry
    proposal on upi_autopay must be HARD_STOP'd by npci_max_attempts, regardless of timing.
    This is the clearest possible test of the NPCI-citation guardrail actually holding."""
    case = Case(
        case_id="PMT-SMOKE-MAXED-01",
        surface=Surface.PAYMENT_FAILURE,
        created_at=datetime(2026, 8, 24, 9, 0),
        customer_id="cust_smoke_3",
        customer_name="Smoke Test Maxed Mandate",
        amount_inr=899.0,
        status=CaseStatus.OPEN,
        payment_details=PaymentFailureDetails(
            razorpay_payment_id="pay_smoke_3",
            error_code="GATEWAY_ERROR",
            error_description="Bank technical error",
            error_source="bank",
            error_step="payment_authorization",
            error_reason="bank_technical_error",
            instrument_type=InstrumentType.UPI,
            decline_class=DeclineClass.SOFT,
            subscription_id="sub_smoke_maxed",
            subscription_status=SubscriptionStatus.HALTED,
            attempt_number=4,
        ),
    )
    first = datetime(2026, 8, 17, 9, 0)
    records = [
        AttemptRecord(case_id=case.case_id, attempt_number=i + 1, action_type="schedule_retry",
                      channel="upi_autopay", executed_at=first, outcome="failure")
        for i in range(4)
    ]
    history = AttemptHistory(attempts_this_cycle=[first] * 4, records=records)
    return case, history


def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "gemini"
    client = get_llm_client(provider)

    maxed_case, maxed_history = make_maxed_upi_mandate_case()
    scenarios: list[tuple[Case, AttemptHistory]] = [
        (make_soft_decline_case(), AttemptHistory()),
        (make_high_value_receivable_case(), AttemptHistory()),
        (make_hard_decline_case(), AttemptHistory()),
        (maxed_case, maxed_history),
    ]

    tiers_seen: set[str] = set()

    for case, history in scenarios:
        print(f"\n{'=' * 70}\nCase: {case.case_id} ({case.surface.value}, Rs.{case.amount_inr:,.2f})\n{'=' * 70}")

        def log_fn(line: str):
            parsed = json.loads(line)
            print(f"  [{parsed.get('iteration', '-')}] {parsed.get('event')}: "
                  f"{parsed.get('tool', parsed.get('text', ''))[:120] if isinstance(parsed.get('tool', parsed.get('text', '')), str) else ''}")

        state = run_case_agent(case, history, client, log_fn=log_fn)

        print(f"\nFinal case status: {case.status.value}")
        print(f"Decision log entries: {len(state.log_entries)}")
        for entry in state.log_entries:
            print(f"  - tier={entry.action_tier.value} action_taken={entry.action_taken.value} "
                  f"outcome={entry.outcome} violated={entry.guardrail_check.violated_rule_ids}")
            tiers_seen.add(entry.action_tier.value)

        if not state.log_entries:
            print("WARNING: no decision log entries produced -- loop may not have called "
                  "execute_action or escalate_to_human at all.")

    print(f"\n{'=' * 70}\nGuardrail tier coverage across this run\n{'=' * 70}")
    all_tiers = {"AUTONOMOUS", "APPROVE_FIRST", "HARD_STOP", "LOG_ONLY"}
    for tier in sorted(all_tiers):
        mark = "seen" if tier in tiers_seen else "NOT seen"
        print(f"  {tier}: {mark}")
    if "HARD_STOP" not in tiers_seen:
        print("\nNOTE: HARD_STOP was not observed this run. This can happen if the model chose a "
              "compliant alternative (e.g. offered an alternate instrument instead of blind-"
              "retrying) rather than proposing the blocked action -- that's actually GOOD agent "
              "behavior, not a test failure. It only indicates a real gap if the model tried the "
              "blocked action and the guardrail failed to catch it (check the log entries above "
              "for a HARD_STOP tier that didn't appear, or an executed blind retry on a hard "
              "decline / maxed mandate).")


if __name__ == "__main__":
    main()
