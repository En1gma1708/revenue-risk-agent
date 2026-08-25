"""
Stage 1 — the case agent. This is the real agent: the LLM decides, at runtime, which of 7 tools
to call and in what order, based on what it learns from each tool result. It is not a fixed
pipeline of prompt-chained steps (see NOVELTY.md "Agentic pattern audit").

The single most important property of this file: the model's chosen action NEVER executes
directly. `propose_intervention` only records what the model wants to do; `execute_action` is the
only tool that performs a real effect, and it runs `guardrails.enforce_guardrails()` before doing
anything. This is what makes the compliance claim in NOVELTY.md true rather than aspirational --
the model has no path to bypass a guardrail, because guardrails aren't in its instructions, they're
in the code path its tool calls pass through.

Correctness details that matter (these are the standard ways tool-use loops break):
  - Append the FULL response.content (not just extracted text) so tool_use blocks stay intact.
  - Return ALL tool_result blocks for a turn in a SINGLE user message, never split across messages.
  - A hard MAX_ITERATIONS cap is itself a guardrail -- a model stuck looping must terminate into a
    logged HARD-STOP outcome, never loop forever or silently drop the case.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from guardrails import AttemptHistory, ProposedAction, enforce_guardrails, check_guardrails_advisory
from llm_client import (
    GenerateResult,
    LLMClient,
    Message,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from models import (
    ActionTaken,
    ActionTier,
    Case,
    CaseStatus,
    DecisionLogEntry,
    GuardrailResult,
)

MAX_ITERATIONS = 8


# ---------------------------------------------------------------------------
# Tool definitions (schema the LLM sees)
# ---------------------------------------------------------------------------

TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="get_case_context",
        description="Get the full details of the case you are working on: surface, amount, "
                     "customer, and surface-specific details (payment error/decline info, "
                     "checkout cart info, or receivable/invoice info).",
        input_schema={"type": "object", "properties": {}},
    ),
    ToolDefinition(
        name="check_attempt_history",
        description="Get the prior recovery attempts made on this case, with timestamps and "
                     "outcomes. Use this before proposing a retry to reason about spacing and "
                     "attempt counts.",
        input_schema={"type": "object", "properties": {}},
    ),
    ToolDefinition(
        name="check_policy_guardrails",
        description="Advisory check: given a proposed action, tells you whether it WOULD pass "
                     "policy, without committing to it. Use this to sanity-check an idea before "
                     "calling propose_intervention. This is advisory only -- the real check "
                     "happens automatically when you call execute_action.",
        input_schema={
            "type": "object",
            "properties": {
                "action_type": {"type": "string"},
                "channel": {"type": "string"},
                "target_time": {"type": ["string", "null"], "description": "ISO 8601 datetime"},
                "notify_time": {"type": ["string", "null"], "description": "ISO 8601 datetime, optional"},
                "amount": {"type": "number"},
            },
            "required": ["action_type", "amount"],
        },
    ),
    ToolDefinition(
        name="propose_intervention",
        description="Record your decided intervention for this case. This does NOT execute "
                     "anything by itself -- you must call execute_action next for it to take "
                     "effect, and execute_action will enforce policy regardless of what you "
                     "propose here.",
        input_schema={
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "description": "e.g. schedule_retry, "
                                "send_payment_link, send_reminder_message, offer_alternate_instrument, "
                                "request_new_mandate, discount_or_waiver, escalate_to_collections, "
                                "close_case_unrecoverable"},
                "channel": {"type": ["string", "null"], "description": "e.g. upi_autopay, card, "
                            "email, sms, whatsapp, call"},
                "target_time": {"type": ["string", "null"], "description": "ISO 8601 datetime the "
                                "action would execute"},
                "notify_time": {"type": ["string", "null"], "description": "ISO 8601 datetime the "
                                "customer would be notified, if applicable"},
                "amount": {"type": "number"},
                "reasoning": {"type": "string", "description": "Why this intervention, in your "
                              "own words -- this is recorded in the audit trail."},
            },
            "required": ["action_type", "amount", "reasoning"],
        },
    ),
    ToolDefinition(
        name="execute_action",
        description="Actually perform the most recently proposed action. This is where policy "
                     "is enforced: if the action violates a hard rule, it will be BLOCKED and you "
                     "will be told why -- you may then propose an alternative. If it requires "
                     "human approval, it will be queued instead of executed immediately.",
        input_schema={"type": "object", "properties": {}},
    ),
    ToolDefinition(
        name="escalate_to_human",
        description="Route this case to a human instead of taking automated action. Use this "
                     "when the case is ambiguous, unusually high-risk, or outside anything you "
                     "have a confident, compliant intervention for.",
        input_schema={
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    ),
    ToolDefinition(
        name="log_decision",
        description="Call this LAST, once the case is fully handled (action executed, escalated, "
                     "or you've determined it's currently unrecoverable), to close out this case "
                     "with a final summary. Always call this before finishing.",
        input_schema={
            "type": "object",
            "properties": {
                "final_status": {"type": "string", "description": "One of: recovered, escalated, "
                                 "blocked, closed_unrecoverable, in_progress"},
                "summary": {"type": "string"},
            },
            "required": ["final_status", "summary"],
        },
    ),
]


SYSTEM_PROMPT = """You are a revenue-recovery agent for an Indian payments platform. You are \
handed ONE case at a time -- a specific instance of at-risk revenue (a failed payment, an \
abandoned checkout, or an overdue invoice). Your job is to decide the right intervention and act \
on it, within compliance limits you cannot see the full text of but which WILL be enforced \
automatically when you call execute_action.

Your process should typically be:
1. get_case_context to understand what you're working with
2. check_attempt_history if this looks like a repeat case (especially for payment retries)
3. propose_intervention with your decided action and reasoning
4. execute_action to attempt it -- if blocked, propose an alternative and try again
5. If genuinely stuck or the case warrants human judgment, escalate_to_human instead
6. Always finish by calling log_decision

You have a SMALL, FIXED number of turns (8) to resolve each case -- act decisively. Call each \
tool ONCE per case unless its result genuinely changes (e.g. re-checking attempt_history after a \
new attempt). check_policy_guardrails is optional and rarely needed -- execute_action already \
tells you immediately if something was blocked and why, so prefer just proposing and executing \
over repeatedly pre-checking the same idea.

IMPORTANT: You MUST call log_decision as your final tool call for every case, even if you also \
want to write a summary. Do not end your turn with only a text message -- log_decision is what \
records the case as resolved. A case that ends without a log_decision call is treated as \
unresolved.

Guiding principles:
- Hard payment declines (expired card, invalid VPA, failed authentication) cannot be fixed by \
blindly retrying the same instrument -- offer an alternative or ask for updated details instead.
- Soft declines (insufficient funds, bank timeout) are usually worth a well-timed retry.
- A subscription that has been fully halted needs a new mandate, not another retry attempt.
- High-value cases or anything you're not confident about should be escalated to a human rather \
than acted on autonomously.
- Be decisive but not reckless -- you have a limited number of turns to resolve each case."""


# ---------------------------------------------------------------------------
# Tool dispatch — this is where guardrail enforcement physically lives.
# ---------------------------------------------------------------------------

@dataclass
class CaseAgentState:
    case: Case
    history: AttemptHistory
    proposed: Optional[ProposedAction] = None
    log_entries: list[DecisionLogEntry] = field(default_factory=list)
    executed_action: Optional[dict] = None


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _build_proposed_action(inp: dict) -> ProposedAction:
    return ProposedAction(
        action_type=inp.get("action_type", ""),
        channel=inp.get("channel"),
        target_time=_parse_dt(inp.get("target_time")),
        notify_time=_parse_dt(inp.get("notify_time")),
        amount=float(inp.get("amount", 0.0)),
    )


def dispatch_tool(
    tool_name: str,
    tool_input: dict,
    state: CaseAgentState,
    iteration: int,
    action_executor: Optional[Callable[[Case, ProposedAction], dict]] = None,
) -> tuple[str, bool]:
    """
    Returns (result_text, is_error). This is the ONLY place tool calls turn into real effects,
    and the ONLY place guardrails.enforce_guardrails() is invoked for a real (non-advisory) check.
    """
    case = state.case

    if tool_name == "get_case_context":
        payload = {
            "case_id": case.case_id,
            "surface": case.surface.value,
            "amount_inr": case.amount_inr,
            "customer_name": case.customer_name,
            "status": case.status.value,
            "severity_score": case.severity_score,
            "details": case.details_for_surface().model_dump(mode="json") if case.details_for_surface() else None,
        }
        return json.dumps(payload, default=str), False

    if tool_name == "check_attempt_history":
        payload = [
            {"attempt_number": a.attempt_number, "action_type": a.action_type,
             "channel": a.channel, "executed_at": a.executed_at.isoformat(), "outcome": a.outcome}
            for a in state.history.records
        ]
        return json.dumps({"attempts": payload, "attempt_count": len(state.history.attempts_this_cycle)}), False

    if tool_name == "check_policy_guardrails":
        action = _build_proposed_action(tool_input)
        result = check_guardrails_advisory(case, action, state.history)
        return json.dumps({
            "would_pass": result.passed,
            "tier": result.tier.value,
            "violated_rules": result.violated_rule_ids,
            "messages": result.messages,
        }), False

    if tool_name == "propose_intervention":
        state.proposed = _build_proposed_action(tool_input)
        state.proposed.reasoning = tool_input.get("reasoning", "")   # type: ignore[attr-defined]
        return json.dumps({"recorded": True, "note": "Call execute_action to attempt this."}), False

    if tool_name == "execute_action":
        if state.proposed is None:
            return json.dumps({"error": "No action has been proposed yet. Call propose_intervention first."}), True

        guardrail_result = enforce_guardrails(case, state.proposed, state.history)
        reasoning = getattr(state.proposed, "reasoning", "")

        if guardrail_result.tier == ActionTier.HARD_STOP:
            action_taken = ActionTaken.BLOCKED
            outcome = None
        elif guardrail_result.tier == ActionTier.APPROVE_FIRST:
            action_taken = ActionTaken.QUEUED_FOR_APPROVAL
            outcome = "queued_for_human_approval"
        elif guardrail_result.tier == ActionTier.LOG_ONLY:
            action_taken = ActionTaken.LOGGED_ONLY
            outcome = "logged_only"
        else:
            action_taken = ActionTaken.EXECUTED
            outcome = action_executor(case, state.proposed) if action_executor else {"simulated": True}
            outcome = json.dumps(outcome) if isinstance(outcome, dict) else str(outcome)

        entry = DecisionLogEntry(
            log_id=str(uuid.uuid4()),
            case_id=case.case_id,
            timestamp=datetime.utcnow(),
            iteration=iteration,
            observed={"proposed_action": state.proposed.action_type, "channel": state.proposed.channel},
            decision={"action_type": state.proposed.action_type, "channel": state.proposed.channel,
                      "amount": state.proposed.amount,
                      "target_time": state.proposed.target_time.isoformat() if state.proposed.target_time else None},
            reasoning=reasoning,
            guardrail_check=guardrail_result,
            action_taken=action_taken,
            action_tier=guardrail_result.tier,
            outcome=outcome,
            amount_at_risk_inr=case.amount_inr,
            amount_recovered_inr=case.amount_inr if action_taken == ActionTaken.EXECUTED else 0.0,
        )
        state.log_entries.append(entry)
        state.executed_action = {"action_taken": action_taken.value, "tier": guardrail_result.tier.value}

        result_payload = {
            "action_taken": action_taken.value,
            "tier": guardrail_result.tier.value,
            "violated_rules": guardrail_result.violated_rule_ids,
            "messages": guardrail_result.messages,
        }
        is_error = action_taken == ActionTaken.BLOCKED
        return json.dumps(result_payload), is_error

    if tool_name == "escalate_to_human":
        entry = DecisionLogEntry(
            log_id=str(uuid.uuid4()),
            case_id=case.case_id,
            timestamp=datetime.utcnow(),
            iteration=iteration,
            observed={},
            decision={"escalated": True, "reason": tool_input.get("reason", "")},
            reasoning=tool_input.get("reason", ""),
            guardrail_check=GuardrailResult(passed=True, tier=ActionTier.APPROVE_FIRST),
            action_taken=ActionTaken.QUEUED_FOR_APPROVAL,
            action_tier=ActionTier.APPROVE_FIRST,
            outcome="escalated_to_human",
            amount_at_risk_inr=case.amount_inr,
            amount_recovered_inr=0.0,
        )
        state.log_entries.append(entry)
        case.status = CaseStatus.ESCALATED
        return json.dumps({"escalated": True}), False

    if tool_name == "log_decision":
        final_status = tool_input.get("final_status", "in_progress")
        try:
            case.status = CaseStatus(final_status)
        except ValueError:
            case.status = CaseStatus.IN_PROGRESS
        return json.dumps({"logged": True, "final_status": case.status.value}), False

    return json.dumps({"error": f"Unknown tool: {tool_name}"}), True


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def run_case_agent(
    case: Case,
    history: AttemptHistory,
    llm_client: LLMClient,
    action_executor: Optional[Callable[[Case, ProposedAction], dict]] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> CaseAgentState:
    """
    Runs the full agentic loop for ONE case. Returns the final state, including all
    DecisionLogEntry rows produced. log_fn, if given, receives structured JSON log lines for
    dev-time console visibility (per the "structured console logging" decision in DEVLOG.md --
    the judge-facing observability artifact is the dashboard's trace view, built in Phase 5).
    """
    state = CaseAgentState(case=case, history=history)
    _log = log_fn or (lambda s: None)

    messages: list[Message] = [
        Message(role="user", content=[TextBlock(
            text=f"Handle case {case.case_id} ({case.surface.value}), amount Rs.{case.amount_inr:,.2f}."
        )])
    ]

    for iteration in range(1, MAX_ITERATIONS + 1):
        try:
            result: GenerateResult = llm_client.generate(
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOLS,
            )
        except Exception as e:  # noqa: BLE001 - provider SDKs raise their own types; a single
            # generation failure (e.g. a malformed tool-call generation the provider's own
            # parser rejects -- observed live on Groq, see DEVLOG.md) must not crash a whole
            # batch run. Fail this ONE case into a logged, escalated outcome and move on.
            _log(json.dumps({"case_id": case.case_id, "iteration": iteration,
                              "event": "generation_error", "error": str(e)[:300]}))
            entry = DecisionLogEntry(
                log_id=str(uuid.uuid4()), case_id=case.case_id, timestamp=datetime.utcnow(),
                iteration=iteration, observed={}, decision={},
                reasoning=f"LLM generation failed: {e}",
                guardrail_check=GuardrailResult(passed=False, tier=ActionTier.HARD_STOP,
                                                 violated_rule_ids=["generation_error"],
                                                 messages=[str(e)[:300]]),
                action_taken=ActionTaken.BLOCKED, action_tier=ActionTier.HARD_STOP,
                outcome="generation_error", amount_at_risk_inr=case.amount_inr,
                amount_recovered_inr=0.0,
            )
            state.log_entries.append(entry)
            case.status = CaseStatus.ESCALATED
            return state
        messages.append(Message(role="assistant", content=result.content))

        if result.stop_reason != "tool_use":
            _log(json.dumps({"case_id": case.case_id, "iteration": iteration, "event": "final_text",
                              "text": "".join(b.text for b in result.content if isinstance(b, TextBlock))}))
            break

        tool_results: list[ToolUseBlock | TextBlock | ToolResultBlock] = []
        saw_log_decision = False
        for block in result.content:
            if not isinstance(block, ToolUseBlock):
                continue
            result_text, is_error = dispatch_tool(block.name, block.input, state, iteration, action_executor)
            _log(json.dumps({"case_id": case.case_id, "iteration": iteration, "event": "tool_call",
                              "tool": block.name, "input": block.input, "result": result_text[:300],
                              "is_error": is_error}))
            tool_results.append(ToolResultBlock(tool_use_id=block.id, content=result_text, is_error=is_error))
            if block.name == "log_decision":
                saw_log_decision = True

        # ALL tool results for this turn go in ONE user message -- splitting across messages
        # is a standard way tool-use loops silently break.
        messages.append(Message(role="user", content=tool_results))

        if saw_log_decision:
            break
    else:
        # Hard iteration cap reached without the model concluding -- this is itself a guardrail:
        # a case can never be silently dropped or loop forever, it terminates into a logged
        # HARD-STOP outcome.
        entry = DecisionLogEntry(
            log_id=str(uuid.uuid4()),
            case_id=case.case_id,
            timestamp=datetime.utcnow(),
            iteration=MAX_ITERATIONS,
            observed={}, decision={}, reasoning="Exceeded MAX_ITERATIONS without resolution.",
            guardrail_check=GuardrailResult(passed=False, tier=ActionTier.HARD_STOP,
                                             violated_rule_ids=["max_iterations_exceeded"],
                                             messages=["Agent did not resolve the case within the iteration cap."]),
            action_taken=ActionTaken.BLOCKED,
            action_tier=ActionTier.HARD_STOP,
            outcome="max_iterations_exceeded",
            amount_at_risk_inr=case.amount_inr,
            amount_recovered_inr=0.0,
        )
        state.log_entries.append(entry)
        case.status = CaseStatus.ESCALATED
        _log(json.dumps({"case_id": case.case_id, "event": "max_iterations_exceeded"}))

    _finalize_status_if_unset(case, state)
    return state


def _finalize_status_if_unset(case: Case, state: CaseAgentState) -> None:
    """
    Some models conclude with a final text summary instead of ever calling log_decision --
    when that happens the loop correctly stops, but case.status is left at its initial OPEN
    value with no record of what actually happened. Rather than rely on prompting alone to
    force a tool call (fragile across providers, per DEVLOG.md's cross-provider bugs), derive
    the terminal status from the DecisionLogEntry rows that WERE produced -- those reflect what
    actually executed, which is a more reliable signal than trusting the model to narrate it.
    """
    if case.status != CaseStatus.OPEN or not state.log_entries:
        return   # either already set (log_decision ran) or genuinely nothing happened

    last = state.log_entries[-1]
    if last.action_taken == ActionTaken.EXECUTED:
        case.status = CaseStatus.RECOVERED
    elif last.action_taken == ActionTaken.BLOCKED:
        case.status = CaseStatus.BLOCKED
    elif last.action_taken == ActionTaken.QUEUED_FOR_APPROVAL:
        case.status = CaseStatus.ESCALATED
    else:
        case.status = CaseStatus.IN_PROGRESS
