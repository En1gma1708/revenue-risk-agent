"""
Proof-of-concept: a genuine handoff-architecture split, separate from and additive to the main
single-agent system (agent_loop.py), same discipline as orchestrator.py's router+specialist PoC.

The split, per the user's explicit design (2026-08-30): the CORE agent is responsible for
investigating a case and deciding the workflow (what should happen and why) -- it stops after
propose_intervention. A SEPARATE EXECUTION SUBAGENT then receives that proposed action as a real
handoff (its own LLM call, its own small reasoning loop) and is responsible ONLY for carrying it
out: deciding HOW to execute compliantly, calling execute_action, and reporting back what happened.

Why this is a genuine handoff and not just a renamed function call: the execution subagent gets its
OWN system prompt, its OWN turn(s) of LLM reasoning, and can itself decide to retry with a different
concrete execution detail (e.g. a different notify_time to satisfy the RBI pre-debit window) before
giving up and reporting failure back to the core agent -- it is not guaranteed to succeed just
because the core agent proposed something. The core agent then decides what to do with that report
(try a different action_type via a new propose_intervention, or escalate).

Same non-negotiable as orchestrator.py: the execution subagent calls the EXACT SAME
guardrails.enforce_guardrails() as the main system -- compliance enforcement is never duplicated or
reimplemented per agent, only the reasoning framing differs.

What this deliberately does NOT do:
- Not wired into run_batch.py, custom_case.py, bulk_upload.py, or the dashboard -- standalone,
  directly-runnable comparison artifact (run this file's __main__ block).
- Does not change agent_loop.py, guardrails.py, or MAX_ITERATIONS in the main system.
- Does not attempt multi-turn retry indefinitely -- capped at EXECUTOR_MAX_ATTEMPTS to keep this a
  bounded, provable demo rather than an open-ended loop.

Run with: python backend/execution_handoff.py [gemini|groq|openrouter]
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime

from guardrails import AttemptHistory, ProposedAction, enforce_guardrails
from llm_client import LLMClient, Message, TextBlock, ToolDefinition, ToolUseBlock, get_llm_client
from models import (
    ActionTaken,
    ActionTier,
    Case,
    DecisionLogEntry,
    GuardrailResult,
    InstrumentType,
    PaymentFailureDetails,
    Surface,
    decline_class_for,
)

EXECUTOR_MAX_ATTEMPTS = 3

# Note on scope: this PoC's __main__ block stands in for the core agent's side of the handoff by
# constructing a ProposedAction directly, rather than re-running agent_loop.py's full investigation
# loop (which already exists, is unchanged, and is what a real integration would call first) -- the
# interesting new behavior this file demonstrates is entirely on the EXECUTION side.


# ---------------------------------------------------------------------------
# Execution subagent -- receives ONE proposed action via handoff, gets its own LLM turns to try to
# execute it compliantly, can revise concrete execution details (not the decision itself -- that's
# the core agent's job) and retry, reports a structured result back.
# ---------------------------------------------------------------------------

EXECUTOR_SYSTEM_PROMPT = """You are an EXECUTION agent. You do not decide WHAT should happen to a \
case -- another agent already decided that and handed you a specific proposed action. Your job is \
narrower and different: figure out HOW to carry out that exact action_type compliantly, and attempt \
it.

You have access to compliance requirements that will be enforced automatically when you call \
attempt_execution -- you cannot see the exact rules, but if you're blocked you'll be told which \
rule and why, and you may adjust concrete execution details (timing, notification lead time) and \
try again. You may NOT change the action_type itself -- if the action type is fundamentally wrong \
for this case, report that back as a failure so the core agent can decide on a different \
intervention; that is not your call to make.

Given the proposed action, decide concrete execution details (when to run it, when to notify the \
customer if applicable) and call attempt_execution. If blocked, adjust ONLY the timing/notification \
details based on the block reason and try again -- you have {max_attempts} attempts. If you exhaust \
your attempts or determine no compliant timing exists for this action_type, call report_outcome with \
success=false and a clear reason."""

ATTEMPT_EXECUTION_TOOL = ToolDefinition(
    name="attempt_execution",
    description="Attempt to execute the proposed action with these concrete timing details.",
    input_schema={
        "type": "object",
        "properties": {
            "target_time": {"type": "string", "description": "ISO 8601 datetime to execute at"},
            "notify_time": {"type": "string", "description": "ISO 8601 datetime to notify the customer, if applicable"},
        },
        "required": [],
    },
)

REPORT_OUTCOME_TOOL = ToolDefinition(
    name="report_outcome",
    description="Report the final outcome back to the core agent -- call this once you've either "
                "succeeded or exhausted your attempts.",
    input_schema={
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["success", "reason"],
    },
)


def run_execution_subagent(
    case: Case,
    proposed: ProposedAction,
    history: AttemptHistory,
    llm_client: LLMClient,
) -> dict:
    """The genuine handoff: a separate agent, its own LLM turns, its own small loop. Returns a
    structured report the core agent would use to decide its next move. Every attempt_execution
    call goes through the EXACT SAME enforce_guardrails() the main single-agent system uses."""
    messages: list[Message] = [
        Message(role="user", content=[TextBlock(
            text=f"Proposed action for {case.case_id}: action_type={proposed.action_type}, "
                 f"channel={proposed.channel}, amount=Rs.{proposed.amount:,.2f}. "
                 f"Decide execution timing and attempt it."
        )])
    ]
    log_entries: list[DecisionLogEntry] = []
    system = EXECUTOR_SYSTEM_PROMPT.format(max_attempts=EXECUTOR_MAX_ATTEMPTS)

    for attempt in range(1, EXECUTOR_MAX_ATTEMPTS + 1):
        result = llm_client.generate(
            system=system, messages=messages,
            tools=[ATTEMPT_EXECUTION_TOOL, REPORT_OUTCOME_TOOL],
        )
        messages.append(Message(role="assistant", content=result.content))

        tool_call = next((b for b in result.content if isinstance(b, ToolUseBlock)), None)
        if tool_call is None:
            return {"success": False, "reason": "Executor did not call a tool.", "log_entries": log_entries}

        if tool_call.name == "report_outcome":
            return {
                "success": tool_call.input.get("success", False),
                "reason": tool_call.input.get("reason", ""),
                "log_entries": log_entries,
            }

        # attempt_execution -- build a real ProposedAction with the executor's chosen timing and
        # run it through the SAME guardrail engine every other action in this project goes through.
        target_time = _parse_iso(tool_call.input.get("target_time"))
        notify_time = _parse_iso(tool_call.input.get("notify_time"))
        attempt_action = ProposedAction(
            action_type=proposed.action_type, channel=proposed.channel,
            target_time=target_time, notify_time=notify_time, amount=proposed.amount,
        )
        guardrail_result: GuardrailResult = enforce_guardrails(case, attempt_action, history)

        entry = DecisionLogEntry(
            log_id=str(uuid.uuid4()), case_id=case.case_id, timestamp=datetime.utcnow(),
            iteration=attempt, observed={"handoff_attempt": attempt, "proposed_by": "core_agent"},
            decision={"action_type": attempt_action.action_type, "target_time": str(target_time), "notify_time": str(notify_time)},
            reasoning=f"Execution subagent attempt {attempt}/{EXECUTOR_MAX_ATTEMPTS}",
            guardrail_check=guardrail_result,
            action_taken=ActionTaken.EXECUTED if guardrail_result.tier == ActionTier.AUTONOMOUS else ActionTaken.BLOCKED,
            action_tier=guardrail_result.tier,
            outcome="{\"simulated\": true}" if guardrail_result.tier == ActionTier.AUTONOMOUS else None,
            amount_at_risk_inr=case.amount_inr,
            amount_recovered_inr=case.amount_inr if guardrail_result.tier == ActionTier.AUTONOMOUS else 0.0,
            provider="execution-subagent-poc",
        )
        log_entries.append(entry)

        result_payload = {
            "blocked": guardrail_result.tier == ActionTier.HARD_STOP,
            "tier": guardrail_result.tier.value,
            "violated_rules": guardrail_result.violated_rule_ids,
            "messages": guardrail_result.messages,
        }
        if guardrail_result.tier != ActionTier.HARD_STOP:
            return {"success": True, "reason": f"Executed on attempt {attempt}", "log_entries": log_entries}

        messages.append(Message(role="user", content=[TextBlock(text=json.dumps(result_payload))]))

    return {"success": False, "reason": f"Exhausted {EXECUTOR_MAX_ATTEMPTS} attempts.", "log_entries": log_entries}


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# End-to-end demo: a high-value case deliberately proposed with insufficient notice, forcing the
# executor to discover the RBI pre-debit-notice block and self-correct the timing.
# ---------------------------------------------------------------------------

def _build_demo_case() -> Case:
    return Case(
        case_id=f"HANDOFF-{uuid.uuid4().hex[:8].upper()}",
        surface=Surface.PAYMENT_FAILURE,
        created_at=datetime.utcnow(),
        customer_id="cust_handoff_demo",
        customer_name="Handoff Demo Customer",
        amount_inr=62000.0,  # above RBI_AFA_EXEMPT_THRESHOLD_INR -- forces the pre-debit-notice rule
        payment_details=PaymentFailureDetails(
            razorpay_payment_id="pay_handoff_demo",
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
    proposed = ProposedAction(
        action_type="schedule_retry", channel="card", amount=case.amount_inr,
    )

    print(f"Core agent hands off proposed action to execution subagent ({provider})...")
    print(f"  case: {case.case_id}, amount: Rs.{case.amount_inr:,.2f} (above high-value threshold)")
    print(f"  proposed by core agent: {proposed.action_type}")
    print()

    report = run_execution_subagent(case, proposed, AttemptHistory(), client)

    print("=" * 60)
    print(f"Executor report back to core agent: success={report['success']}")
    print(f"  reason: {report['reason']}")
    print(f"  {len(report['log_entries'])} log entries produced during handoff")
    print("=" * 60)
    for entry in report["log_entries"]:
        print(json.dumps({
            "iteration": entry.iteration,
            "action_tier": entry.action_tier.value,
            "outcome": entry.outcome,
            "guardrail_messages": entry.guardrail_check.messages,
        }, indent=2))
