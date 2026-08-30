"""
Proof-of-concept orchestrator + specialist agent, deliberately SEPARATE from the project's main
architecture and never wired into run_batch.py/the dashboard/the demo dataset.

Why this exists: after direct, repeated pushback (see DEVLOG.md 2026-08-30 "Revisited single-agent-
vs-multi-agent, again, properly this time"), the real question was never "should the whole project be
rebuilt as multi-agent" -- an unfinished rebuild under a 5-day deadline is a worse outcome than the
proven single-agent system already working end to end. The real, defensible question was "can we
show we considered AND BUILT the alternative, not just reasoned about it on paper." This file is
that artifact: a genuine two-agent system (a router agent that makes a real LLM classification call,
handing off to a specialized payment-failure agent) built small enough to finish and prove correctly
in the remaining time, rather than large enough to risk the main submission.

Design choice worth being explicit about: the specialist agent REUSES agent_loop.py's TOOLS,
guardrail engine, and execute_action dispatch wholesale -- it does NOT reimplement compliance
checking. This is deliberate, not laziness: NOVELTY.md's whole argument against multi-agent was that
splitting into separate agents risks a compliance rule being implemented (and drifting) in multiple
places. This PoC proves that risk is avoidable -- a specialist agent can have its OWN narrower system
prompt and reasoning focus while still calling into the exact same evaluate_guardrails()/GUARDRAILS
table every other agent in this project uses. The guardrail engine is the one thing that must NEVER
be duplicated per agent; everything else (prompt framing, focus, which tools get emphasized) can be.

What this deliberately does NOT do (scope boundary, see the DEVLOG entry for the reasoning):
- Does not add specialists for checkout_abandonment or overdue_receivable -- those surfaces keep
  running through the existing single-agent path in agent_loop.py, untouched.
- Is not called by run_batch.py, the dashboard, or any user-facing flow -- this is a standalone,
  directly-runnable comparison artifact (run this file's __main__ block), not production code path.
- Does not change MAX_ITERATIONS, the guardrail table, or any existing tool -- imports them as-is.

Run with: python backend/orchestrator.py [gemini|groq|openrouter]
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

from agent_loop import TOOLS, run_case_agent
from guardrails import AttemptHistory
from llm_client import LLMClient, Message, TextBlock, ToolDefinition, ToolUseBlock, get_llm_client
from models import Case, InstrumentType, PaymentFailureDetails, Surface, decline_class_for

# ---------------------------------------------------------------------------
# Router agent -- a REAL LLM classification call, not a hardcoded if/else. This is the one place
# in this file that's genuinely "orchestration": given a raw event description, the router decides
# whether it warrants the specialist path or should fall back to the general path.
# ---------------------------------------------------------------------------

ROUTER_SYSTEM_PROMPT = """You are a routing agent for a revenue-recovery system. You receive a raw \
description of an at-risk-revenue event and must decide which specialist should handle it.

Available specialists:
- "payment_failure_specialist": handles failed/declined payments -- has deep tools and reasoning \
focus for card/UPI decline codes, NPCI retry-cycle rules, and subscription mandate state.
- "general": handles checkout abandonment and overdue receivables -- the standard case agent.

Call classify_and_route exactly once with your decision and a one-sentence reason."""

ROUTER_TOOL = ToolDefinition(
    name="classify_and_route",
    description="Classify the event and route it to the correct specialist.",
    input_schema={
        "type": "object",
        "properties": {
            "specialist": {
                "type": "string",
                "enum": ["payment_failure_specialist", "general"],
            },
            "reason": {"type": "string", "description": "One-sentence justification for this routing decision."},
        },
        "required": ["specialist", "reason"],
    },
)


def route_via_orchestrator(event_description: str, llm_client: LLMClient) -> tuple[str, str]:
    """Makes a real LLM call to classify the event. Returns (specialist_name, reason)."""
    messages: list[Message] = [
        Message(role="user", content=[TextBlock(text=f"Event: {event_description}")])
    ]
    result = llm_client.generate(system=ROUTER_SYSTEM_PROMPT, messages=messages, tools=[ROUTER_TOOL])

    for block in result.content:
        if isinstance(block, ToolUseBlock) and block.name == "classify_and_route":
            specialist = block.input.get("specialist", "general")
            reason = block.input.get("reason", "")
            return specialist, reason

    # Router failed to call the tool (e.g. only returned text) -- fail safe to "general" rather
    # than crash, same per-case-isolation principle as the rest of this project.
    return "general", "Router did not return a structured decision; defaulting to general path."


# ---------------------------------------------------------------------------
# Specialist agent -- a narrower system prompt, but calls the SAME TOOLS and (via run_case_agent's
# existing dispatch, unchanged) the SAME guardrail engine as every other agent in this project.
# ---------------------------------------------------------------------------

PAYMENT_SPECIALIST_SYSTEM_PROMPT = """You are a SPECIALIZED payment-failure recovery agent -- you \
handle ONLY failed/declined payment cases, and you have deep, focused expertise in exactly this \
domain: card and UPI decline codes, hard-vs-soft decline classification, NPCI UPI Autopay retry-cycle \
rules (4-attempt cap, T+24h/T+72h/T+168h spacing, non-peak-hour windows), RBI pre-debit notice \
requirements, and subscription mandate state (active/halted/pending).

Your process:
1. get_case_context to see the decline reason, instrument, and subscription state
2. check_attempt_history -- payment retries are governed by strict NPCI timing rules you must respect
3. check_customer_history if this customer may have other open cases worth knowing about
4. propose_intervention with your decided action -- lean on your specialist knowledge: a HARD \
decline should never be blindly retried on the same instrument; a HALTED subscription needs a new \
mandate request, not a retry; a high-value payment needs human approval regardless of urgency
5. execute_action to attempt it -- if blocked, propose a genuinely different action, not a reworded \
retry of the same one
6. escalate_to_human if genuinely stuck
7. Always finish with log_decision

You have {max_iterations} turns. Act decisively -- your specialization means you should need FEWER \
turns than a generalist agent would, since you already know the compliance landscape for this \
surface without needing to discover it case by case."""


def run_specialist(case: Case, history: AttemptHistory, llm_client: LLMClient, max_iterations: int = 6):
    """Runs the payment-failure specialist. Reuses run_case_agent's entire loop/dispatch/guardrail
    machinery unchanged -- only the system prompt differs, via a lightweight monkey-patch of the
    module-level SYSTEM_PROMPT for the duration of this call. This is deliberate: it proves the
    specialist can plug into the EXACT SAME enforcement path (agent_loop.execute_action's guardrail
    check) rather than needing its own copy of it."""
    import agent_loop as _agent_loop_module

    original_prompt = _agent_loop_module.SYSTEM_PROMPT
    _agent_loop_module.SYSTEM_PROMPT = PAYMENT_SPECIALIST_SYSTEM_PROMPT.format(max_iterations=max_iterations)
    try:
        return run_case_agent(case, history, llm_client, all_cases=[case], provider_name="specialist-poc")
    finally:
        _agent_loop_module.SYSTEM_PROMPT = original_prompt


# ---------------------------------------------------------------------------
# End-to-end demo: build one payment-failure case, route it, run the specialist, print the trace.
# ---------------------------------------------------------------------------

def _build_demo_case() -> Case:
    return Case(
        case_id=f"POC-{uuid.uuid4().hex[:8].upper()}",
        surface=Surface.PAYMENT_FAILURE,
        created_at=datetime.utcnow(),
        customer_id="cust_poc_demo",
        customer_name="Orchestrator Demo Customer",
        amount_inr=18500.0,
        payment_details=PaymentFailureDetails(
            razorpay_payment_id="pay_poc_demo",
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


if __name__ == "__main__":
    provider = sys.argv[1] if len(sys.argv) > 1 else "groq"
    client = get_llm_client(provider)

    case = _build_demo_case()
    event_description = (
        f"{case.surface.value}: {case.customer_name}, Rs.{case.amount_inr:,.2f}, "
        f"decline reason: {case.payment_details.error_reason}, instrument: card"
    )

    print(f"Routing event via orchestrator ({provider})...")
    specialist, reason = route_via_orchestrator(event_description, client)
    print(f"  -> routed to: {specialist}")
    print(f"  -> reason: {reason}")
    print()

    if specialist == "payment_failure_specialist":
        print(f"Running payment-failure specialist on {case.case_id}...")
        state = run_specialist(case, AttemptHistory(), client)
    else:
        print(f"Running general agent on {case.case_id}...")
        state = run_case_agent(case, AttemptHistory(), client, all_cases=[case], provider_name="general-poc")

    print()
    print("=" * 60)
    print(f"Result: {len(state.log_entries)} log entries, final case status: {case.status.value}")
    print("=" * 60)
    for entry in state.log_entries:
        print(json.dumps({
            "iteration": entry.iteration,
            "action_tier": entry.action_tier.value,
            "outcome": entry.outcome,
            "reasoning": entry.reasoning[:200],
        }, indent=2))
