"""
The agent system rebuilt on Pydantic AI, per the user's explicit architecture (2026-08-30): a
router agent classifies surface + severity via a real LLM call and hands off to ONE of 3
surface-specialist agents, each of which investigates, decides, and executes its case fully --
all still enforcing the EXACT SAME guardrail engine (guardrails.py) as the original hand-built
agent_loop.py. This file does not replace agent_loop.py -- both exist side by side until this
version is proven at least as correct (see DEVLOG.md for the migration plan and verification
gates) via the SAME test approach: unit tests against guardrail behavior, then a real batch run
validated with compute_reliability_metrics, before agent_loop.py is retired.

Why the tools here call the SAME underlying functions as agent_loop.py's dispatch_tool (not
reimplemented): guardrails.enforce_guardrails(), models.py's schemas, and db.py's persistence are
all imported and used exactly as-is. Only the AGENT SHELL (system prompt structure, tool-calling
loop mechanics, and now the router/specialist split) is rebuilt on the framework -- the compliance
engine is never duplicated, per this project's own standing rule (see NOVELTY.md "what would make
this NOT novel").

Architecture:
    RouterAgent (real LLM classification call)
        -> "payment_failure" -> PaymentFailureSpecialist
        -> "checkout_abandonment" -> CheckoutAbandonmentSpecialist
        -> "overdue_receivable" -> ReceivableSpecialist
    Each specialist: investigate (get_case_context, check_attempt_history, check_customer_history)
    -> decide (propose_intervention / record_promise_to_pay) -> execute (execute_action, which
    enforces guardrails.enforce_guardrails()) -> escalate_to_human if stuck -> log_decision.

Run with: python backend/pydantic_agents.py [gemini|groq|openrouter]
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.groq import GroqProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

load_dotenv()

from config import DEMO_TODAY
from guardrails import ActionTier, AttemptHistory, GuardrailResult, ProposedAction, enforce_guardrails
from models import (
    ActionTaken,
    Case,
    CaseStatus,
    DecisionLogEntry,
    PromiseToPay,
    PTPStatus,
    Surface,
)

MAX_ITERATIONS = 6  # same cap as agent_loop.py, for a fair comparison


# ---------------------------------------------------------------------------
# Model resolution -- same 3 providers, same free-tier discipline as llm_client.py. Kept separate
# from llm_client.py's own normalization layer (that file stays untouched, still used by
# agent_loop.py) since Pydantic AI has its own model abstraction that supersedes it here.
# ---------------------------------------------------------------------------

_account_cursor: dict[str, int] = {}  # per-provider round-robin position, process-lifetime only


def _next_key(env_var: str) -> str:
    """Round-robins across ALL configured accounts for this provider (comma-separated in .env,
    same multi-account convention as llm_client.py) rather than always using the first -- fixed
    2026-08-30 after finding this PoC was hammering only key 1 while keys 2/3 sat unused, wasting
    every failed attempt against an already-exhausted account instead of spreading load. Not the
    full weighted-schedule logic from run_batch.py (this is a standalone PoC, not the batch
    orchestrator), just simple round-robin -- sufficient for a single-case demo."""
    keys = [k.strip() for k in os.environ.get(env_var, "").split(",") if k.strip()]
    if not keys:
        return ""
    i = _account_cursor.get(env_var, 0) % len(keys)
    _account_cursor[env_var] = i + 1
    return keys[i]


def resolve_model(provider: str):
    provider = provider.lower()
    if provider == "groq":
        # Model id matches llm_client.GroqClient's default -- confirmed working against the real
        # account (llama-3.3-70b-versatile is deprecated/unavailable on this account as of
        # 2026-08-30, found live when this file was first run).
        return GroqModel("openai/gpt-oss-120b", provider=GroqProvider(api_key=_next_key("GROQ_API_KEY")))
    if provider == "gemini":
        return GoogleModel("gemini-2.0-flash", provider=GoogleProvider(api_key=_next_key("GEMINI_API_KEY")))
    if provider == "openrouter":
        return OpenRouterModel("openai/gpt-oss-120b", provider=OpenRouterProvider(api_key=_next_key("OPENROUTER_API_KEY")))
    raise ValueError(f"Unknown provider: {provider!r} (expected gemini | groq | openrouter)")


# ---------------------------------------------------------------------------
# Shared deps -- the Pydantic AI equivalent of agent_loop.CaseAgentState, passed to every tool via
# RunContext so tools can read/mutate case state without global variables.
# ---------------------------------------------------------------------------

@dataclass
class CaseDeps:
    case: Case
    history: AttemptHistory
    all_cases: list[Case] = field(default_factory=list)
    proposed: Optional[ProposedAction] = None
    log_entries: list[DecisionLogEntry] = field(default_factory=list)
    provider: Optional[str] = None


# ---------------------------------------------------------------------------
# Shared tools -- registered on EVERY specialist agent below, identical logic to agent_loop.py's
# dispatch_tool cases of the same name, calling the same guardrails.py functions.
# ---------------------------------------------------------------------------

def _register_case_tools(agent: Agent) -> None:
    @agent.tool
    def get_case_context(ctx: RunContext[CaseDeps]) -> str:
        """Get the full context for the case you're handling: surface, amount, customer, and
        surface-specific details."""
        case = ctx.deps.case
        payload = {
            "case_id": case.case_id,
            "surface": case.surface.value,
            "amount_inr": case.amount_inr,
            "customer_name": case.customer_name,
            "status": case.status.value,
            "severity_score": case.severity_score,
            "today": DEMO_TODAY.date().isoformat(),
            "details": case.details_for_surface().model_dump(mode="json") if case.details_for_surface() else None,
        }
        return json.dumps(payload, default=str)

    @agent.tool
    def check_attempt_history(ctx: RunContext[CaseDeps]) -> str:
        """Check prior attempts on this case, if any -- important for payment retries governed by
        NPCI timing rules."""
        history = ctx.deps.history
        payload = [
            {"attempt_number": a.attempt_number, "action_type": a.action_type,
             "channel": a.channel, "executed_at": a.executed_at.isoformat(), "outcome": a.outcome}
            for a in history.records
        ]
        return json.dumps({"attempts": payload, "attempt_count": len(history.attempts_this_cycle)})

    @agent.tool
    def check_customer_history(ctx: RunContext[CaseDeps]) -> str:
        """Check this customer's OTHER open cases across any surface -- a customer with a pattern
        of issues may warrant more caution than an isolated incident."""
        case = ctx.deps.case
        siblings = [c for c in ctx.deps.all_cases if c.customer_id == case.customer_id and c.case_id != case.case_id]
        payload = []
        for sibling in siblings:
            entry = {
                "case_id": sibling.case_id, "surface": sibling.surface.value,
                "amount_inr": sibling.amount_inr, "status": sibling.status.value,
                "created_at": sibling.created_at.isoformat(),
            }
            if sibling.surface == Surface.PAYMENT_FAILURE and sibling.payment_details:
                entry["error_reason"] = sibling.payment_details.error_reason
                entry["decline_class"] = sibling.payment_details.decline_class.value
            if sibling.surface == Surface.OVERDUE_RECEIVABLE and sibling.receivable_details:
                pd = sibling.receivable_details
                entry["days_overdue"] = pd.days_overdue
                if pd.ptp:
                    entry["prior_ptp_status"] = pd.ptp.status.value
            payload.append(entry)
        return json.dumps({"other_case_count": len(siblings), "other_cases": payload})

    @agent.tool
    def propose_intervention(
        ctx: RunContext[CaseDeps], action_type: str, amount: float, reasoning: str,
        channel: Optional[str] = None, target_time: Optional[str] = None, notify_time: Optional[str] = None,
    ) -> str:
        """Record your decided intervention. Does NOT execute anything -- call execute_action next.
        execute_action enforces policy regardless of what you propose here."""
        ctx.deps.proposed = ProposedAction(
            action_type=action_type, channel=channel, amount=amount,
            target_time=datetime.fromisoformat(target_time) if target_time else None,
            notify_time=datetime.fromisoformat(notify_time) if notify_time else None,
        )
        ctx.deps.proposed.reasoning = reasoning  # type: ignore[attr-defined]
        return json.dumps({"recorded": True, "note": "Call execute_action to attempt this."})

    @agent.tool
    def record_promise_to_pay(
        ctx: RunContext[CaseDeps], promised_amount: float, promised_date: str,
        promised_channel: str, reasoning: str,
    ) -> str:
        """For overdue_receivable cases only: record a customer's payment commitment. Routed
        through the same guardrail engine as any other action (e.g. high-value approval still
        applies)."""
        case = ctx.deps.case
        if case.surface != Surface.OVERDUE_RECEIVABLE or case.receivable_details is None:
            return json.dumps({"error": "record_promise_to_pay is only valid for overdue_receivable cases."})

        try:
            date_val = datetime.fromisoformat(promised_date).date()
        except ValueError as e:
            return json.dumps({"error": f"Invalid promised_date: {e}"})

        ptp_action = ProposedAction(action_type="record_promise_to_pay", channel=promised_channel, amount=promised_amount)
        guardrail_result = enforce_guardrails(case, ptp_action, ctx.deps.history)

        if guardrail_result.tier == ActionTier.HARD_STOP:
            action_taken = ActionTaken.BLOCKED
        else:
            ptp = PromiseToPay(promised_amount=promised_amount, promised_date=date_val,
                                promised_channel=promised_channel, made_at=datetime.utcnow(),
                                status=PTPStatus.PENDING)
            case.receivable_details.ptp = ptp
            action_taken = (ActionTaken.EXECUTED if guardrail_result.tier == ActionTier.AUTONOMOUS
                             else ActionTaken.QUEUED_FOR_APPROVAL)

        entry = DecisionLogEntry(
            log_id=str(uuid.uuid4()), case_id=case.case_id, timestamp=datetime.utcnow(), iteration=1,
            observed={"promised_amount": promised_amount, "promised_date": str(date_val)},
            decision={"action_type": "record_promise_to_pay", "promised_amount": promised_amount,
                      "promised_date": str(date_val), "promised_channel": promised_channel},
            reasoning=reasoning, guardrail_check=guardrail_result, action_taken=action_taken,
            action_tier=guardrail_result.tier,
            outcome="ptp_recorded" if action_taken != ActionTaken.BLOCKED else None,
            amount_at_risk_inr=case.amount_inr, amount_recovered_inr=0.0, provider=ctx.deps.provider,
        )
        ctx.deps.log_entries.append(entry)
        if action_taken == ActionTaken.BLOCKED:
            return json.dumps({"recorded": False, "tier": guardrail_result.tier.value,
                               "violated_rules": guardrail_result.violated_rule_ids, "messages": guardrail_result.messages})
        return json.dumps({"recorded": True, "tier": guardrail_result.tier.value})

    @agent.tool
    def execute_action(ctx: RunContext[CaseDeps]) -> str:
        """Actually perform the most recently proposed action. THIS is where policy is enforced --
        if it violates a hard rule, it will be BLOCKED and you'll be told why."""
        case = ctx.deps.case
        if ctx.deps.proposed is None:
            return json.dumps({"error": "No action has been proposed yet. Call propose_intervention first."})

        guardrail_result: GuardrailResult = enforce_guardrails(case, ctx.deps.proposed, ctx.deps.history)
        reasoning = getattr(ctx.deps.proposed, "reasoning", "")

        if guardrail_result.tier == ActionTier.HARD_STOP:
            action_taken, outcome = ActionTaken.BLOCKED, None
        elif guardrail_result.tier == ActionTier.APPROVE_FIRST:
            action_taken, outcome = ActionTaken.QUEUED_FOR_APPROVAL, "queued_for_human_approval"
        elif guardrail_result.tier == ActionTier.LOG_ONLY:
            action_taken, outcome = ActionTaken.LOGGED_ONLY, "logged_only"
        else:
            action_taken, outcome = ActionTaken.EXECUTED, json.dumps({"simulated": True})

        entry = DecisionLogEntry(
            log_id=str(uuid.uuid4()), case_id=case.case_id, timestamp=datetime.utcnow(), iteration=1,
            observed={"proposed_action": ctx.deps.proposed.action_type, "channel": ctx.deps.proposed.channel},
            decision={"action_type": ctx.deps.proposed.action_type, "channel": ctx.deps.proposed.channel,
                      "amount": ctx.deps.proposed.amount},
            reasoning=reasoning, guardrail_check=guardrail_result, action_taken=action_taken,
            action_tier=guardrail_result.tier, outcome=outcome,
            amount_at_risk_inr=case.amount_inr,
            amount_recovered_inr=case.amount_inr if action_taken == ActionTaken.EXECUTED else 0.0,
            provider=ctx.deps.provider,
        )
        ctx.deps.log_entries.append(entry)
        return json.dumps({
            "action_taken": action_taken.value, "tier": guardrail_result.tier.value,
            "violated_rules": guardrail_result.violated_rule_ids, "messages": guardrail_result.messages,
        })

    @agent.tool
    def escalate_to_human(ctx: RunContext[CaseDeps], reason: str) -> str:
        """Route this case to a human instead of taking automated action."""
        case = ctx.deps.case
        entry = DecisionLogEntry(
            log_id=str(uuid.uuid4()), case_id=case.case_id, timestamp=datetime.utcnow(), iteration=1,
            observed={}, decision={"escalated": True, "reason": reason}, reasoning=reason,
            guardrail_check=GuardrailResult(passed=True, tier=ActionTier.APPROVE_FIRST),
            action_taken=ActionTaken.QUEUED_FOR_APPROVAL, action_tier=ActionTier.APPROVE_FIRST,
            outcome="escalated_to_human", amount_at_risk_inr=case.amount_inr,
            amount_recovered_inr=0.0, provider=ctx.deps.provider,
        )
        ctx.deps.log_entries.append(entry)
        case.status = CaseStatus.ESCALATED
        return json.dumps({"escalated": True})

    @agent.tool
    def log_decision(ctx: RunContext[CaseDeps], final_status: str = "in_progress") -> str:
        """Call this LAST, once the case is fully handled, to close it out."""
        try:
            ctx.deps.case.status = CaseStatus(final_status)
        except ValueError:
            ctx.deps.case.status = CaseStatus.IN_PROGRESS
        return json.dumps({"logged": True, "final_status": ctx.deps.case.status.value})


# ---------------------------------------------------------------------------
# Specialist agents -- one per surface, each with a narrower system prompt but the SAME shared
# tools (and therefore the same guardrail engine).
# ---------------------------------------------------------------------------

PAYMENT_SPECIALIST_PROMPT = """You are a SPECIALIZED payment-failure recovery agent. You have deep \
expertise in card/UPI decline codes, NPCI UPI Autopay retry-cycle rules (4-attempt cap, T+24h/T+72h/ \
T+168h spacing, non-peak hours), RBI pre-debit notice requirements, and subscription mandate state.

Process: get_case_context -> check_attempt_history (retries are governed by strict NPCI timing) -> \
check_customer_history if relevant -> propose_intervention -> execute_action (if blocked, propose a \
genuinely DIFFERENT action, not a reworded retry) -> escalate_to_human if stuck -> ALWAYS finish \
with log_decision. A hard decline should never be blindly retried on the same instrument; a halted \
subscription needs a new mandate request, not a retry; high-value payments need human approval \
regardless of urgency. You have {max_iterations} turns -- act decisively."""

CHECKOUT_SPECIALIST_PROMPT = """You are a SPECIALIZED checkout-abandonment recovery agent. You focus \
on cart value, abandonment stage (OTP entry / instrument select / bank redirect / review), device, \
and time since abandonment to decide the right nudge channel and urgency.

Process: get_case_context -> check_customer_history if relevant -> propose_intervention -> \
execute_action -> escalate_to_human if stuck -> ALWAYS finish with log_decision. High cart values \
still need human approval regardless of urgency. You have {max_iterations} turns."""

RECEIVABLE_SPECIALIST_PROMPT = """You are a SPECIALIZED overdue-receivables recovery agent. You focus \
on days overdue, amount, and promise-to-pay (PTP) tracking for B2B customers.

Process: get_case_context -> check_customer_history if relevant -> propose_intervention (or, if the \
customer just committed to a payment date, record_promise_to_pay instead) -> execute_action -> \
escalate_to_human if stuck -> ALWAYS finish with log_decision. If the case shows an EXISTING PTP \
that has passed its promised date, that is the central fact: decide kept vs. missed and act \
accordingly rather than proposing a generic reminder. You have {max_iterations} turns."""


def _make_specialist(system_prompt_template: str, model, deps_type=CaseDeps) -> Agent:
    agent = Agent(model, deps_type=deps_type, system_prompt=system_prompt_template.format(max_iterations=MAX_ITERATIONS))
    _register_case_tools(agent)
    return agent


# ---------------------------------------------------------------------------
# Router agent -- a REAL LLM classification call (surface + severity), not hardcoded routing.
# Hands off to the matching specialist. Severity is passed through for the specialist's own
# situational awareness, but the actual severity_score field is still computed identically to
# router.py's compute_severity (imported, not reimplemented) for consistency with the rest of the
# project's reporting.
# ---------------------------------------------------------------------------

class RoutingDecision(BaseModel):
    surface: str = Field(description="One of: payment_failure, checkout_abandonment, overdue_receivable")
    severity: str = Field(description="One of: low, medium, high -- your read of how urgent this case is")
    reason: str = Field(description="One-sentence justification")


def _make_router(model) -> Agent:
    return Agent(
        model,
        output_type=RoutingDecision,
        system_prompt=(
            "You are a routing agent for a revenue-recovery system. Given a raw case description, "
            "classify its surface and your read of its severity, so it can be handed to the right "
            "specialist agent."
        ),
    )


def run_case_via_orchestrator(case: Case, history: AttemptHistory, provider: str, all_cases: Optional[list[Case]] = None):
    """Full pipeline: router classifies -> hands off to the matching specialist -> specialist runs
    its full investigate/decide/execute loop. Returns (routing_decision, list[DecisionLogEntry])."""
    model = resolve_model(provider)

    router = _make_router(model)
    event_description = (
        f"surface(pre-labeled)={case.surface.value}, amount=Rs.{case.amount_inr:,.2f}, "
        f"customer={case.customer_name}, details={case.details_for_surface().model_dump(mode='json') if case.details_for_surface() else {}}"
    )
    routing_result = router.run_sync(f"Classify this event: {event_description}")
    decision: RoutingDecision = routing_result.output

    specialist_map = {
        "payment_failure": PAYMENT_SPECIALIST_PROMPT,
        "checkout_abandonment": CHECKOUT_SPECIALIST_PROMPT,
        "overdue_receivable": RECEIVABLE_SPECIALIST_PROMPT,
    }
    prompt_template = specialist_map.get(decision.surface, PAYMENT_SPECIALIST_PROMPT)
    specialist = _make_specialist(prompt_template, model)

    deps = CaseDeps(case=case, history=history, all_cases=all_cases or [case], provider=f"pydantic-ai-{provider}")
    specialist.run_sync(
        f"Handle case {case.case_id} ({case.surface.value}), amount Rs.{case.amount_inr:,.2f}.",
        deps=deps,
    )

    _finalize_status_if_unset(case, deps.log_entries)
    return decision, deps.log_entries


def _finalize_status_if_unset(case: Case, log_entries: list[DecisionLogEntry]) -> None:
    """Ported directly from agent_loop.py's identical fix (same root cause: some models conclude
    with a final text summary instead of ever calling log_decision, leaving case.status at its
    initial OPEN value with no record of what happened -- observed live on this system's first
    real run, 2026-08-30). Derives the terminal status from what the log entries actually show
    happened, rather than trusting the model to narrate it -- a more reliable signal across
    providers than prompting alone (see agent_loop.py's own comment for the same reasoning)."""
    if case.status != CaseStatus.OPEN or not log_entries:
        return
    last = log_entries[-1]
    if last.action_taken == ActionTaken.EXECUTED:
        case.status = CaseStatus.RECOVERED
    elif last.action_taken == ActionTaken.BLOCKED:
        case.status = CaseStatus.BLOCKED
    elif last.action_taken == ActionTaken.QUEUED_FOR_APPROVAL:
        case.status = CaseStatus.ESCALATED
    else:
        case.status = CaseStatus.IN_PROGRESS


def _build_payment_failure_demo_case() -> Case:
    from models import InstrumentType, PaymentFailureDetails, decline_class_for
    return Case(
        case_id=f"PYDANTIC-{uuid.uuid4().hex[:8].upper()}",
        surface=Surface.PAYMENT_FAILURE,
        created_at=datetime.utcnow(),
        customer_id="cust_pydantic_demo_pmt",
        customer_name="Pydantic AI Demo Customer (Payment)",
        amount_inr=8500.0,
        payment_details=PaymentFailureDetails(
            razorpay_payment_id="pay_pydantic_demo",
            error_code="GATEWAY_ERROR",
            error_description="Insufficient funds",
            error_source="bank",
            error_step="payment_authorization",
            error_reason="insufficient_funds",
            instrument_type=InstrumentType.CARD,
            decline_class=decline_class_for(InstrumentType.CARD, "insufficient_funds"),
            attempt_number=1,
            is_real_razorpay_data=False,
        ),
    )


def _build_checkout_abandonment_demo_case() -> Case:
    from models import AbandonmentStage, CheckoutAbandonmentDetails, Device
    return Case(
        case_id=f"PYDANTIC-{uuid.uuid4().hex[:8].upper()}",
        surface=Surface.CHECKOUT_ABANDONMENT,
        created_at=datetime.utcnow(),
        customer_id="cust_pydantic_demo_cart",
        customer_name="Pydantic AI Demo Customer (Checkout)",
        amount_inr=4200.0,
        checkout_details=CheckoutAbandonmentDetails(
            cart_value_inr=4200.0,
            items=["Wireless Earbuds"],
            abandonment_stage=AbandonmentStage.INSTRUMENT_SELECT,
            device=Device.MOBILE_WEB,
            minutes_since_abandon=45.0,
        ),
    )


def _build_receivable_demo_case() -> Case:
    from models import ContactChannel, ReceivableDetails
    from datetime import timedelta
    return Case(
        case_id=f"PYDANTIC-{uuid.uuid4().hex[:8].upper()}",
        surface=Surface.OVERDUE_RECEIVABLE,
        created_at=datetime.utcnow(),
        customer_id="cust_pydantic_demo_inv",
        customer_name="Pydantic AI Demo Customer (Receivable)",
        amount_inr=32000.0,
        receivable_details=ReceivableDetails(
            invoice_id="inv_pydantic_demo",
            due_date=(datetime.utcnow() - timedelta(days=25)).date(),
            days_overdue=25,
            ptp=None,
            contact_channel_pref=ContactChannel.EMAIL,
        ),
    )


if __name__ == "__main__":
    import sys

    provider = sys.argv[1] if len(sys.argv) > 1 else "groq"
    surface_arg = sys.argv[2] if len(sys.argv) > 2 else "all"

    builders = {
        "payment_failure": _build_payment_failure_demo_case,
        "checkout_abandonment": _build_checkout_abandonment_demo_case,
        "overdue_receivable": _build_receivable_demo_case,
    }
    to_run = builders.items() if surface_arg == "all" else [(surface_arg, builders[surface_arg])]

    for surface_name, build_case in to_run:
        case = build_case()
        print(f"\n{'=' * 60}\n{surface_name} -> {case.case_id}\n{'=' * 60}")
        print(f"Running through router -> specialist pipeline ({provider})...")
        try:
            decision, log_entries = run_case_via_orchestrator(case, AttemptHistory(), provider)
        except Exception as e:  # noqa: BLE001 - per-case isolation, same principle as agent_loop.py
            print(f"FAILED: {type(e).__name__}: {str(e)[:300]}")
            continue
        print(f"Router decision: surface={decision.surface}, severity={decision.severity}")
        print(f"Reason: {decision.reason}")
        print(f"Specialist produced {len(log_entries)} log entries, final status: {case.status.value}")
        for entry in log_entries:
            print(json.dumps({
                "action_tier": entry.action_tier.value, "outcome": entry.outcome,
                "reasoning": entry.reasoning[:200],
            }, indent=2))
