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


def resolve_model(provider: str, api_key: Optional[str] = None):
    """api_key: explicit key to use for this call, e.g. one account from a batch runner's own
    weighted account schedule (see run_batch_multiagent.py). When omitted, falls back to this
    module's own internal round-robin (_next_key) -- kept for the standalone single-case demo at
    the bottom of this file, which has no external scheduler driving account selection."""
    provider = provider.lower()
    if provider == "groq":
        # Model id matches llm_client.GroqClient's default -- confirmed working against the real
        # account (llama-3.3-70b-versatile is deprecated/unavailable on this account as of
        # 2026-08-30, found live when this file was first run).
        return GroqModel("openai/gpt-oss-120b", provider=GroqProvider(api_key=api_key or _next_key("GROQ_API_KEY")))
    if provider == "gemini":
        return GoogleModel("gemini-2.0-flash", provider=GoogleProvider(api_key=api_key or _next_key("GEMINI_API_KEY")))
    if provider == "openrouter":
        return OpenRouterModel("openai/gpt-oss-120b", provider=OpenRouterProvider(api_key=api_key or _next_key("OPENROUTER_API_KEY")))
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

def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    """Ported from agent_loop.py's identical helper -- models sometimes pass a non-ISO sentinel
    like "now" instead of a real timestamp for target_time/notify_time (found live 2026-08-30,
    CART-0020 crashed propose_intervention with an unhandled ValueError during a real batch run
    because this file called datetime.fromisoformat() directly instead of going through this safe
    wrapper the way agent_loop.py already did). Falls back to None -- a missing/malformed time is
    still a valid ProposedAction shape (e.g. execute_action's RBI pre-debit-notice check already
    treats a missing target_time/notify_time as a violation on its own, not a crash)."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


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
            target_time=_parse_dt(target_time),
            notify_time=_parse_dt(notify_time),
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
# Hands off to the matching specialist via a genuine tool call (hand_off_to_specialist), not
# plain orchestration code picking the specialist after the router returns -- see
# hand_off_to_specialist's own docstring for why this changed 2026-08-30. Severity is passed
# through for the specialist's own situational awareness, but the actual severity_score field is
# still computed identically to router.py's compute_severity (imported, not reimplemented) for
# consistency with the rest of the project's reporting.
# ---------------------------------------------------------------------------

VALID_SURFACES = {"payment_failure", "checkout_abandonment", "overdue_receivable"}

SPECIALIST_PROMPTS = {
    "payment_failure": PAYMENT_SPECIALIST_PROMPT,
    "checkout_abandonment": CHECKOUT_SPECIALIST_PROMPT,
    "overdue_receivable": RECEIVABLE_SPECIALIST_PROMPT,
}


class RoutingDecision(BaseModel):
    surface: str = Field(description="One of: payment_failure, checkout_abandonment, overdue_receivable")
    severity: str = Field(description="One of: low, medium, high -- your read of how urgent this case is")
    reason: str = Field(description="One-sentence justification")


@dataclass
class RouterDeps:
    """Deps for the router agent -- separate from CaseDeps (the specialist's deps shape) because
    the router's only real job is picking a specialist and handing off; it doesn't need the full
    case-tool surface (get_case_context etc.), only enough to run one specialist and report what
    happened back to the caller. handoff_result is set BY the hand_off_to_specialist tool once the
    router actually calls it -- this is how run_case_via_orchestrator (below) tells whether a real
    handoff happened at all, vs. the router concluding without ever calling the tool."""
    case: Case
    history: AttemptHistory
    model: object
    provider: str
    all_cases: list[Case] = field(default_factory=list)
    handoff_result: Optional[tuple[RoutingDecision, list[DecisionLogEntry]]] = None


def _make_router(model) -> Agent:
    router = Agent(
        model,
        deps_type=RouterDeps,
        system_prompt=(
            "You are a routing agent for a revenue-recovery system. Given a raw case description, "
            "classify its surface and your read of its severity, then call hand_off_to_specialist "
            "with your classification -- that tool call IS how you route the case, not just a "
            "description of what should happen. You must call hand_off_to_specialist exactly once "
            "before finishing."
        ),
    )

    @router.tool
    async def hand_off_to_specialist(ctx: RunContext[RouterDeps], surface: str, severity: str, reason: str) -> str:
        """Call this exactly once, after classifying the case, to hand it off to the matching
        surface specialist (payment_failure / checkout_abandonment / overdue_receivable). This
        actually runs the specialist agent -- it is the real handoff mechanism, not advisory. If
        `surface` doesn't match a known specialist, the handoff is rejected; retry with a valid
        surface rather than guessing.

        async, not sync (fixed 2026-08-30, found live on the first real post-change run): Pydantic
        AI raises UserError if a tool body calls agent.run_sync() -- running a synchronous agent
        run inside another agent's synchronous tool call risks a deadlock. The fix is this tool
        being `async def` and using `await specialist.run(...)` (the async equivalent of
        run_sync) instead -- the OUTER run_case_via_orchestrator entry point can still safely use
        router.run_sync() itself, since that's the top-level call, not nested inside a tool."""
        if surface not in VALID_SURFACES:
            return json.dumps({
                "error": f"{surface!r} is not a known surface. Must be one of: {sorted(VALID_SURFACES)}. "
                         "Call hand_off_to_specialist again with a valid surface.",
            })

        deps = ctx.deps
        specialist = _make_specialist(SPECIALIST_PROMPTS[surface], deps.model)
        case_deps = CaseDeps(case=deps.case, history=deps.history,
                              all_cases=deps.all_cases or [deps.case], provider=f"pydantic-ai-{deps.provider}")
        await specialist.run(
            f"Handle case {deps.case.case_id} ({deps.case.surface.value}), "
            f"amount Rs.{deps.case.amount_inr:,.2f}.",
            deps=case_deps,
        )
        decision = RoutingDecision(surface=surface, severity=severity, reason=reason)
        deps.handoff_result = (decision, case_deps.log_entries)
        return json.dumps({
            "handed_off": True, "surface": surface,
            "log_entries_produced": len(case_deps.log_entries),
        })

    return router


def _escalate_routing_failure(case: Case, reason: str, provider: str) -> DecisionLogEntry:
    """Same DecisionLogEntry shape as the escalate_to_human tool (see _register_case_tools) --
    reused here, not reimplemented, so a routing-level escalation looks identical in the audit
    trail to a specialist-level one. Used when the router never produces a valid handoff even
    after a retry (see run_case_via_orchestrator) -- the case must still get a real, honest
    terminal record rather than silently defaulting to a guessed specialist."""
    case.status = CaseStatus.ESCALATED
    return DecisionLogEntry(
        log_id=str(uuid.uuid4()), case_id=case.case_id, timestamp=datetime.utcnow(), iteration=1,
        observed={}, decision={"escalated": True, "reason": reason}, reasoning=reason,
        guardrail_check=GuardrailResult(passed=True, tier=ActionTier.APPROVE_FIRST),
        action_taken=ActionTaken.QUEUED_FOR_APPROVAL, action_tier=ActionTier.APPROVE_FIRST,
        outcome="escalated_to_human", amount_at_risk_inr=case.amount_inr,
        amount_recovered_inr=0.0, provider=f"pydantic-ai-{provider}",
    )


# ---------------------------------------------------------------------------
# Checker agent -- a genuine reflection/QA pattern, distinct from both guardrails.py (hardcoded,
# deterministic, checks COMPLIANCE) and the router's retry-on-missing-handoff logic (error
# recovery, not judgment). This is a real second agent critiquing another agent's completed
# decision, added 2026-08-30 after explicit discussion of what "senior teams actually do" for
# LLM-as-judge review -- see DEVLOG.md for the full design rationale. Two deliberate cost controls
# that make this viable on an already quota-constrained project:
#   1. Single-shot review of the FINAL ARTIFACT (case facts + the specialist's completed decision),
#      never a live re-run of the specialist's whole multi-turn tool-calling loop. A judge call is
#      one cheap request, not a second expensive agentic run.
#   2. Only triggered on cases worth the extra cost (_needs_checker_review, below) -- a cheap,
#      structural, no-LLM-call rule, not "check everything" or "ask a model which cases to check"
#      (which would be circular and cost a call just to decide).
# ---------------------------------------------------------------------------

class CheckerVerdict(BaseModel):
    sound: bool = Field(description="True if the specialist's final decision and reasoning are "
                                     "well-justified given the case facts and guardrail result.")
    concern: str = Field(default="", description="If NOT sound: one-sentence explanation of what's "
                                                  "wrong. Leave empty if sound.")
    recommended_action: str = Field(description="One of: accept, retry_specialist, escalate_to_human. "
                                                 "Use 'accept' whenever sound=True.")


CHECKER_SYSTEM_PROMPT = """You are a quality-review agent for a revenue-recovery system. You are \
given a case that a specialist agent has ALREADY finished handling: the raw case facts, and the \
specialist's final decision (what it proposed, what the guardrail engine allowed, and its stated \
reasoning). Your job is NOT to re-decide the case -- it's to judge whether the specialist's \
decision and reasoning are actually sound given the facts, or whether something looks wrong: a \
mismatch between the facts and the stated reasoning, a decision that technically passed \
guardrails but doesn't make practical sense, or reasoning that doesn't actually support the \
action taken.

If sound, set sound=true, concern="", recommended_action="accept".
If NOT sound, set sound=false, explain the concern in one sentence, and recommend either \
"retry_specialist" (a different, better decision is plausibly available) or "escalate_to_human" \
(the case is too ambiguous or high-stakes for another automated attempt to help)."""


def _make_checker(model) -> Agent:
    return Agent(model, output_type=CheckerVerdict, system_prompt=CHECKER_SYSTEM_PROMPT)


def _needs_checker_review(decision: "RoutingDecision", log_entries: list[DecisionLogEntry]) -> bool:
    """Cheap, structural trigger -- no LLM call needed to decide whether a case is worth a second
    look. Checking every case would roughly double real LLM cost on top of an already
    quota-constrained project for little added value on routine, low-stakes cases; these signals
    target exactly the cases where a second opinion is actually worth its cost:
      - any log entry hit HARD_STOP at some point (the PMT-0002-style multi-turn interaction --
        inherently the most interesting/risky decisions to have gotten right)
      - the FINAL tier is APPROVE_FIRST (already flagged as needing a human anyway -- checking the
        reasoning quality behind that escalation is high-value)
      - the router's own severity read was "high"
    Deliberately excludes plain AUTONOMOUS/low-severity cases -- lowest stakes, already cheap and
    safe by construction, not worth a second LLM call."""
    if not log_entries:
        return False
    if any(e.action_tier == ActionTier.HARD_STOP for e in log_entries):
        return True
    if log_entries[-1].action_tier == ActionTier.APPROVE_FIRST:
        return True
    if decision.severity == "high":
        return True
    return False


def _build_checker_review_prompt(case: Case, decision: "RoutingDecision", log_entries: list[DecisionLogEntry]) -> str:
    last = log_entries[-1]
    details = case.details_for_surface()
    return (
        f"Case {case.case_id} ({case.surface.value}), amount Rs.{case.amount_inr:,.2f}. "
        f"Router classified this as surface={decision.surface}, severity={decision.severity}.\n"
        f"Case details: {details.model_dump(mode='json') if details else {}}\n"
        f"Specialist's final decision: {json.dumps(last.decision, default=str)}\n"
        f"Specialist's reasoning: {last.reasoning}\n"
        f"Guardrail result: tier={last.action_tier.value}, action_taken={last.action_taken.value}, "
        f"violated_rules={last.guardrail_check.violated_rule_ids}\n"
        f"Is this decision sound given the facts?"
    )


def _checker_log_entry(case: Case, provider: str, verdict: CheckerVerdict) -> DecisionLogEntry:
    """Always produced when the checker runs, sound OR not -- so 'this case was reviewed and
    approved' is as visible in the audit trail as 'this case was flagged,' not just the flagged
    ones. Uses a distinct provider tag (f'pydantic-ai-{provider}-checker') so checker-call volume
    and reliability can be tracked separately from the primary router/specialist calls in
    compute_provider_reliability, without polluting those stats."""
    sound = verdict.sound
    return DecisionLogEntry(
        log_id=str(uuid.uuid4()), case_id=case.case_id, timestamp=datetime.utcnow(), iteration=1,
        observed={}, decision={"sound": sound, "recommended_action": verdict.recommended_action},
        reasoning=verdict.concern or "Checker reviewed the specialist's decision and found it sound.",
        guardrail_check=GuardrailResult(passed=sound, tier=ActionTier.LOG_ONLY if sound else ActionTier.APPROVE_FIRST),
        action_taken=ActionTaken.LOGGED_ONLY if sound else ActionTaken.QUEUED_FOR_APPROVAL,
        action_tier=ActionTier.LOG_ONLY if sound else ActionTier.APPROVE_FIRST,
        outcome="checker_approved" if sound else "checker_flagged",
        amount_at_risk_inr=case.amount_inr, amount_recovered_inr=0.0,
        provider=f"pydantic-ai-{provider}-checker",
    )


def _run_checker_review(
    case: Case, decision: "RoutingDecision", log_entries: list[DecisionLogEntry],
    model, provider: str, history: AttemptHistory, all_cases: list[Case],
) -> list[DecisionLogEntry]:
    """Runs the checker IF _needs_checker_review says this case is worth it; otherwise a no-op.
    Reuses the SAME already-resolved model object as the case's router+specialist calls (no extra
    resolve_model/account lookup) -- keeps one case's calls on one consistent account.

    Bounded action space, same principle as the router's own bounded retry (see
    run_case_via_orchestrator): the checker can request AT MOST one specialist retry, and that
    retry's output is NEVER re-checked -- avoids any risk of a check-retry-check loop by
    construction, not just by prompting the model to stop."""
    if not _needs_checker_review(decision, log_entries):
        return log_entries

    checker = _make_checker(model)
    prompt = _build_checker_review_prompt(case, decision, log_entries)
    result = checker.run_sync(prompt)
    verdict: CheckerVerdict = result.output

    log_entries.append(_checker_log_entry(case, provider, verdict))

    if verdict.sound:
        return log_entries

    if verdict.recommended_action == "retry_specialist":
        prompt_template = SPECIALIST_PROMPTS.get(decision.surface, PAYMENT_SPECIALIST_PROMPT)
        specialist = _make_specialist(prompt_template, model)
        case_deps = CaseDeps(case=case, history=history, all_cases=all_cases or [case],
                              provider=f"pydantic-ai-{provider}")
        specialist.run_sync(
            f"A quality reviewer flagged your previous decision on case {case.case_id}: "
            f"{verdict.concern}. Reconsider and handle this case again, addressing that concern.",
            deps=case_deps,
        )
        log_entries.extend(case_deps.log_entries)
        case.status = _status_from_last_entry(log_entries)   # re-derive from the retry's real outcome
    else:   # escalate_to_human, or any other value the model returns
        case.status = CaseStatus.ESCALATED

    return log_entries


def run_case_via_orchestrator(
    case: Case, history: AttemptHistory, provider: str,
    all_cases: Optional[list[Case]] = None, api_key: Optional[str] = None,
):
    """Full pipeline: router classifies -> hands off to the matching specialist via a real tool
    call (hand_off_to_specialist) -> specialist runs its full investigate/decide/execute loop.
    Returns (routing_decision, list[DecisionLogEntry]).

    Retry + escalation (2026-08-30): previously, an unrecognized/malformed routing result silently
    defaulted to the payment specialist (specialist_map.get(..., PAYMENT_SPECIALIST_PROMPT)) --
    every case got SOME handling, but a misrouted checkout/receivable case would run through the
    wrong specialist with no record that anything went wrong. Now: if the router doesn't produce a
    valid handoff (never calls hand_off_to_specialist, or repeatedly passes an invalid surface),
    it's retried ONCE with an explicit nudge; if that also fails, the case is escalated to a human
    with a real DecisionLogEntry explaining why, rather than guessed at.

    api_key: explicit account key for this case, passed straight through to resolve_model -- lets
    a batch runner assign accounts via its own weighted schedule (see run_batch_multiagent.py)
    instead of relying on this module's internal round-robin, which has no visibility into what a
    batch runner has already assigned to other concurrent/prior cases."""
    model = resolve_model(provider, api_key=api_key)
    router = _make_router(model)

    event_description = (
        f"surface(pre-labeled)={case.surface.value}, amount=Rs.{case.amount_inr:,.2f}, "
        f"customer={case.customer_name}, details={case.details_for_surface().model_dump(mode='json') if case.details_for_surface() else {}}"
    )

    deps = RouterDeps(case=case, history=history, model=model, provider=provider, all_cases=all_cases or [case])
    router.run_sync(f"Classify this event: {event_description}", deps=deps)

    if deps.handoff_result is None:
        # Router concluded without ever calling hand_off_to_specialist, or every call it made used
        # an invalid surface (rejected by the tool itself, see hand_off_to_specialist above) --
        # retry once with an explicit nudge before giving up and escalating.
        router.run_sync(
            f"You did not complete a handoff. Classify this event and call hand_off_to_specialist "
            f"with a valid surface (one of {sorted(VALID_SURFACES)}) now: {event_description}",
            deps=deps,
        )

    if deps.handoff_result is None:
        reason = ("Router failed to produce a valid specialist handoff after 2 attempts -- "
                  "routing this case to a human rather than guessing which specialist should handle it.")
        entry = _escalate_routing_failure(case, reason, provider)
        decision = RoutingDecision(surface="unknown", severity="unknown", reason=reason)
        return decision, [entry]

    decision, log_entries = deps.handoff_result
    _finalize_status_if_unset(case, log_entries)

    log_entries = _run_checker_review(case, decision, log_entries, model, provider,
                                       history, all_cases or [case])

    return decision, log_entries


def _status_from_last_entry(log_entries: list[DecisionLogEntry]) -> CaseStatus:
    """Pure mapping, extracted 2026-08-30 from _finalize_status_if_unset (below) so the checker
    agent's retry path (see _run_checker_review) can re-derive status from a NEW final log entry
    after a retry, not just on first completion -- _finalize_status_if_unset only fires when
    case.status is still OPEN, which is already false by the time a retry happens (the first
    specialist run already set a real status), so that gate had to be split out from the mapping
    itself rather than reused as-is."""
    if not log_entries:
        return CaseStatus.OPEN
    last = log_entries[-1]
    if last.action_taken == ActionTaken.EXECUTED:
        return CaseStatus.RECOVERED
    elif last.action_taken == ActionTaken.BLOCKED:
        return CaseStatus.BLOCKED
    elif last.action_taken == ActionTaken.QUEUED_FOR_APPROVAL:
        return CaseStatus.ESCALATED
    else:
        return CaseStatus.IN_PROGRESS


def _finalize_status_if_unset(case: Case, log_entries: list[DecisionLogEntry]) -> None:
    """Ported directly from agent_loop.py's identical fix (same root cause: some models conclude
    with a final text summary instead of ever calling log_decision, leaving case.status at its
    initial OPEN value with no record of what happened -- observed live on this system's first
    real run, 2026-08-30). Derives the terminal status from what the log entries actually show
    happened, rather than trusting the model to narrate it -- a more reliable signal across
    providers than prompting alone (see agent_loop.py's own comment for the same reasoning)."""
    if case.status != CaseStatus.OPEN or not log_entries:
        return
    case.status = _status_from_last_entry(log_entries)


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
