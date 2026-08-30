"""
Lets a visitor submit their OWN case (not one from the pre-generated synthetic batch) and watch
the real agent process it live, end to end, through the same tool-calling loop and guardrail
engine every batch case goes through. Built directly in response to a real concern: without this,
the dashboard only ever shows the output of a script someone already ran -- there's no way for
anyone outside this repo to tell the difference between "a genuine interactive agent" and "a
pre-recorded batch someone is replaying." This is the honest fix for that, not a cosmetic one.

Deliberately NOT reusing run_batch.run_batch() for this -- that function resets the whole DB and
is built for a 95-case sweep. A single interactive submission needs to be purely additive (never
touches the existing batch's rows) and needs to run exactly one case through exactly one LLM call
chain, so it's built as its own small, direct path: construct a Case from the input, route it
(severity only), run the same run_case_agent() loop the batch uses, persist the result the same
way run_batch.py does per case, and hand back the case_id so the caller can fetch its trace via
the existing /cases/{id}/trace endpoint -- no new read path needed.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from agent_loop import run_case_agent
from db import get_connection, init_db, insert_decision_log_entry, upsert_case
from guardrails import AttemptHistory
from llm_client import get_llm_client
from models import (
    AbandonmentStage,
    Case,
    CheckoutAbandonmentDetails,
    ContactChannel,
    Device,
    InstrumentType,
    PaymentFailureDetails,
    ReceivableDetails,
    Surface,
    decline_class_for,
)
from router import route_case


class CustomCaseInput(BaseModel):
    """The simplified, form-friendly shape a visitor actually fills in -- deliberately narrower
    than the full Case schema (no decline_class, no case_id, no synthetic-data honesty flag --
    those are either derived or not meaningful for a live submission)."""

    surface: Surface
    customer_name: str = Field(min_length=1, max_length=80)
    amount_inr: float = Field(gt=0, le=10_000_000)
    provider: Optional[str] = None   # gemini | groq | openrouter; None -> server default

    # payment_failure fields
    instrument_type: Optional[InstrumentType] = None
    error_reason: Optional[str] = None
    attempt_number: Optional[int] = Field(default=None, ge=1, le=10)

    # checkout_abandonment fields
    items: Optional[list[str]] = None
    abandonment_stage: Optional[AbandonmentStage] = None
    device: Optional[Device] = None
    minutes_since_abandon: Optional[float] = Field(default=None, ge=0, le=20_160)

    # overdue_receivable fields
    days_overdue: Optional[int] = Field(default=None, ge=0, le=3650)
    contact_channel_pref: Optional[ContactChannel] = None

    @field_validator("customer_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()


def _build_case(payload: CustomCaseInput) -> Case:
    case_id = f"CUSTOM-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now(timezone.utc)

    payment_details = checkout_details = receivable_details = None

    if payload.surface == Surface.PAYMENT_FAILURE:
        if not payload.instrument_type or not payload.error_reason:
            raise ValueError("instrument_type and error_reason are required for payment_failure")
        payment_details = PaymentFailureDetails(
            razorpay_payment_id=f"pay_custom_{case_id.lower()}",
            error_code="CUSTOM_SUBMISSION",
            error_description=payload.error_reason.replace("_", " "),
            error_source="customer",
            error_step="payment_authorization",
            error_reason=payload.error_reason,
            instrument_type=payload.instrument_type,
            decline_class=decline_class_for(payload.instrument_type, payload.error_reason),
            attempt_number=payload.attempt_number or 1,
            is_real_razorpay_data=False,
        )
    elif payload.surface == Surface.CHECKOUT_ABANDONMENT:
        if not payload.abandonment_stage or not payload.device or payload.minutes_since_abandon is None:
            raise ValueError("abandonment_stage, device, and minutes_since_abandon are required for checkout_abandonment")
        checkout_details = CheckoutAbandonmentDetails(
            cart_value_inr=payload.amount_inr,
            items=payload.items or ["Item"],
            abandonment_stage=payload.abandonment_stage,
            device=payload.device,
            minutes_since_abandon=payload.minutes_since_abandon,
        )
    elif payload.surface == Surface.OVERDUE_RECEIVABLE:
        if payload.days_overdue is None or not payload.contact_channel_pref:
            raise ValueError("days_overdue and contact_channel_pref are required for overdue_receivable")
        receivable_details = ReceivableDetails(
            invoice_id=f"inv_custom_{case_id.lower()}",
            due_date=date.today(),
            days_overdue=payload.days_overdue,
            ptp=None,
            contact_channel_pref=payload.contact_channel_pref,
        )

    case = Case(
        case_id=case_id,
        surface=payload.surface,
        created_at=now,
        customer_id=f"cust_{case_id.lower()}",
        customer_name=payload.customer_name,
        amount_inr=payload.amount_inr,
        payment_details=payment_details,
        checkout_details=checkout_details,
        receivable_details=receivable_details,
    )
    return route_case(case)


def run_custom_case(payload: CustomCaseInput) -> str:
    """Builds, runs, and persists ONE case from a live submission. Returns the case_id so the
    caller can fetch its trace via the existing GET /cases/{id}/trace endpoint. Runs synchronously
    -- a single case is a handful of LLM calls (seconds, not minutes), unlike a full batch."""
    case = _build_case(payload)
    # get_llm_client() now defaults to LLM_PROVIDER (env, currently "groq") when no explicit
    # provider is given -- both now point at the same empirically-stronger provider, so this just
    # mirrors that resolution for the provider_name we attribute the decision to (reliability
    # reporting needs the name that was ACTUALLY used, not "whatever the caller happened to pass").
    # Earlier today this endpoint had its own hardcoded "groq" default specifically because
    # LLM_PROVIDER was still "gemini" at the time (Gemini's 20-req/day cap made it a bad silent
    # default for a live-visitor feature) -- that's now moot since LLM_PROVIDER itself changed.
    provider_name = (payload.provider or os.environ.get("LLM_PROVIDER", "groq")).lower()
    client = get_llm_client(provider_name)

    state = run_case_agent(
        case,
        AttemptHistory(),
        client,
        log_fn=lambda line: None,
        all_cases=[case],
        provider_name=provider_name,
    )

    conn = get_connection()
    init_db(conn)
    upsert_case(conn, case)
    for entry in state.log_entries:
        insert_decision_log_entry(conn, entry)
    conn.close()

    return case.case_id
