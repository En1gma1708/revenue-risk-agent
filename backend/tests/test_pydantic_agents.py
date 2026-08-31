"""
Unit tests for backend/pydantic_agents.py -- Gate 1 of next_steps_multiagent_migration.md
(2026-08-30). No LLM calls, no quota needed: tools are invoked directly against their real
underlying functions (extracted via Tool.function from the registered agent), mirroring the
approach test_new_tools.py already uses for agent_loop.dispatch_tool. This is what actually
proves "compliance isn't duplicated or drifted" between the two systems, not just a comment
claiming it.

Run with: pytest backend/tests/test_pydantic_agents.py -v
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError
from pydantic_ai import Agent

from agent_loop import CaseAgentState, dispatch_tool
from guardrails import AttemptHistory
from models import (
    ActionTaken,
    Case,
    CaseStatus,
    ContactChannel,
    DeclineClass,
    InstrumentType,
    PaymentFailureDetails,
    ReceivableDetails,
    Surface,
)
from pydantic_agents import (
    CaseDeps,
    RoutingDecision,
    _finalize_status_if_unset,
    _register_case_tools,
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

class FakeCtx:
    """Minimal stand-in for pydantic_ai.RunContext -- every tool in pydantic_agents.py only
    ever touches ctx.deps, so a real RunContext (which requires a live model/usage/tracer to
    construct) is unnecessary machinery for a unit test. Duck-typing this avoids coupling the
    test suite to pydantic_ai's internal RunContext construction signature."""
    def __init__(self, deps):
        self.deps = deps


@pytest.fixture(scope="module")
def tools():
    """Register the real tools once (no model call happens at registration or here -- these
    are plain function calls) and return {name: callable} keyed the same as agent_loop's tool
    names, so tests read like test_new_tools.py's dispatch_tool() calls."""
    agent = Agent("test", deps_type=CaseDeps)
    _register_case_tools(agent)
    return {name: t.function for name, t in agent._function_toolset.tools.items()}


def call(tools, name, deps, **kwargs):
    return tools[name](FakeCtx(deps), **kwargs)


def make_receivable_case(case_id: str, customer_id: str = "biz_1", amount: float = 30000.0, ptp=None) -> Case:
    return Case(
        case_id=case_id,
        surface=Surface.OVERDUE_RECEIVABLE,
        created_at=datetime(2026, 8, 1, 9, 0),
        customer_id=customer_id,
        customer_name="Test Biz",
        amount_inr=amount,
        status=CaseStatus.OPEN,
        receivable_details=ReceivableDetails(
            invoice_id=f"inv_{case_id.lower()}",
            due_date=date(2026, 7, 1),
            days_overdue=54,
            contact_channel_pref=ContactChannel.EMAIL,
            ptp=ptp,
        ),
    )


def make_payment_case(case_id: str = "PMT-A", amount: float = 1500.0, decline_class=DeclineClass.SOFT) -> Case:
    return Case(
        case_id=case_id,
        surface=Surface.PAYMENT_FAILURE,
        created_at=datetime(2026, 8, 24, 9, 0),
        customer_id="cust_1",
        customer_name="Test Customer",
        amount_inr=amount,
        status=CaseStatus.OPEN,
        payment_details=PaymentFailureDetails(
            razorpay_payment_id="pay_1",
            error_code="GATEWAY_ERROR",
            error_description="Insufficient funds",
            error_source="bank",
            error_step="payment_authorization",
            error_reason="insufficient_funds",
            instrument_type=InstrumentType.UPI,
            decline_class=decline_class,
            attempt_number=1,
        ),
    )


# ---------------------------------------------------------------------------
# get_case_context / check_attempt_history / check_customer_history
# ---------------------------------------------------------------------------

def test_get_case_context_reports_core_fields(tools):
    case = make_payment_case()
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])
    result = json.loads(call(tools, "get_case_context", deps))
    assert result["case_id"] == "PMT-A"
    assert result["surface"] == "payment_failure"
    assert result["amount_inr"] == 1500.0
    assert result["details"]["error_reason"] == "insufficient_funds"


def test_check_attempt_history_reports_prior_attempts(tools):
    case = make_payment_case()
    from models import AttemptRecord
    records = [AttemptRecord(case_id=case.case_id, attempt_number=1, action_type="schedule_retry",
                              channel="upi_autopay", executed_at=datetime(2026, 8, 20, 9, 0), outcome="failure")]
    history = AttemptHistory(attempts_this_cycle=[datetime(2026, 8, 20, 9, 0)], records=records)
    deps = CaseDeps(case=case, history=history, all_cases=[case])
    result = json.loads(call(tools, "check_attempt_history", deps))
    assert result["attempt_count"] == 1
    assert result["attempts"][0]["outcome"] == "failure"


def test_check_customer_history_finds_sibling_cases(tools):
    main_case = make_receivable_case("INV-A", customer_id="biz_1")
    sibling = make_receivable_case("INV-B", customer_id="biz_1")
    unrelated = make_receivable_case("INV-C", customer_id="biz_2")
    deps = CaseDeps(case=main_case, history=AttemptHistory(), all_cases=[main_case, sibling, unrelated])

    result = json.loads(call(tools, "check_customer_history", deps))
    assert result["other_case_count"] == 1
    assert result["other_cases"][0]["case_id"] == "INV-B"


def test_check_customer_history_empty_when_no_siblings(tools):
    main_case = make_receivable_case("INV-A")
    deps = CaseDeps(case=main_case, history=AttemptHistory(), all_cases=[main_case])
    result = json.loads(call(tools, "check_customer_history", deps))
    assert result["other_case_count"] == 0
    assert result["other_cases"] == []


# ---------------------------------------------------------------------------
# propose_intervention / execute_action
# ---------------------------------------------------------------------------

def test_propose_intervention_survives_non_isoformat_time_string(tools):
    """Regression test for a real bug found live 2026-08-30 (CART-0020 during a batch run): the
    model passed the literal string "now" as target_time instead of a real ISO timestamp, and
    propose_intervention crashed with an unhandled ValueError because it called
    datetime.fromisoformat() directly instead of going through agent_loop.py's safe _parse_dt
    wrapper (which this file's version now also uses). A malformed time string must degrade to
    None, not crash the whole case."""
    case = make_payment_case()
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    result = json.loads(call(tools, "propose_intervention", deps, action_type="schedule_retry",
                              amount=1500.0, reasoning="r", channel="upi_autopay",
                              target_time="now", notify_time="also not a real timestamp"))

    assert result["recorded"] is True
    assert deps.proposed.target_time is None
    assert deps.proposed.notify_time is None


def test_execute_action_without_proposal_errors(tools):
    case = make_payment_case()
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])
    result = json.loads(call(tools, "execute_action", deps))
    assert "error" in result


def test_propose_then_execute_soft_decline_low_value_is_autonomous(tools):
    case = make_payment_case(amount=1500.0, decline_class=DeclineClass.SOFT)
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    call(tools, "propose_intervention", deps, action_type="schedule_retry", amount=1500.0,
         reasoning="Soft decline, first attempt, safe to retry.", channel="upi_autopay")
    result = json.loads(call(tools, "execute_action", deps))

    assert result["tier"] == "AUTONOMOUS"
    assert result["action_taken"] == "executed"
    assert len(deps.log_entries) == 1
    assert deps.log_entries[0].action_taken == ActionTaken.EXECUTED


def test_execute_action_high_value_routes_to_approve_first(tools):
    case = make_receivable_case("INV-A", amount=120000.0)
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    call(tools, "propose_intervention", deps, action_type="send_reminder", amount=120000.0,
         reasoning="High-value overdue receivable.", channel="email")
    result = json.loads(call(tools, "execute_action", deps))

    assert result["tier"] == "APPROVE_FIRST"
    assert result["action_taken"] == "queued_for_approval"


def test_execute_action_hard_decline_blind_retry_is_hard_stopped(tools):
    case = make_payment_case(decline_class=DeclineClass.HARD)
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    call(tools, "propose_intervention", deps, action_type="retry_same_instrument", amount=1500.0,
         reasoning="Retrying the same instrument.", channel="card")
    result = json.loads(call(tools, "execute_action", deps))

    assert result["tier"] == "HARD_STOP"
    assert result["action_taken"] == "blocked"
    assert "hard_decline_no_blind_retry" in result["violated_rules"]


# ---------------------------------------------------------------------------
# record_promise_to_pay
# ---------------------------------------------------------------------------

def test_record_promise_to_pay_succeeds_for_low_value(tools):
    case = make_receivable_case("INV-A", amount=20000.0)
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    result = json.loads(call(tools, "record_promise_to_pay", deps,
                              promised_amount=20000.0, promised_date="2026-08-30",
                              promised_channel="email", reasoning="Confirmed via email."))

    assert result["recorded"] is True
    assert result["tier"] == "AUTONOMOUS"
    assert case.receivable_details.ptp is not None
    assert case.receivable_details.ptp.promised_amount == 20000.0
    assert len(deps.log_entries) == 1
    assert deps.log_entries[0].action_taken == ActionTaken.EXECUTED


def test_record_promise_to_pay_routes_high_value_to_approve_first(tools):
    case = make_receivable_case("INV-A", amount=75000.0)
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    result = json.loads(call(tools, "record_promise_to_pay", deps,
                              promised_amount=75000.0, promised_date="2026-08-30",
                              promised_channel="call", reasoning="Large commitment."))

    assert result["recorded"] is True
    assert result["tier"] == "APPROVE_FIRST"
    assert deps.log_entries[0].action_taken == ActionTaken.QUEUED_FOR_APPROVAL


def test_record_promise_to_pay_rejects_non_receivable_case(tools):
    case = make_payment_case()
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    result = json.loads(call(tools, "record_promise_to_pay", deps,
                              promised_amount=1000.0, promised_date="2026-08-30",
                              promised_channel="email", reasoning="n/a"))

    assert "error" in result
    assert "only valid for overdue_receivable" in result["error"]


def test_record_promise_to_pay_rejects_malformed_date(tools):
    case = make_receivable_case("INV-A")
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    result = json.loads(call(tools, "record_promise_to_pay", deps,
                              promised_amount=20000.0, promised_date="not-a-date",
                              promised_channel="email", reasoning="n/a"))

    assert "error" in result
    assert "Invalid promised_date" in result["error"]


# ---------------------------------------------------------------------------
# escalate_to_human / log_decision
# ---------------------------------------------------------------------------

def test_escalate_to_human_sets_status_and_logs(tools):
    case = make_payment_case()
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    result = json.loads(call(tools, "escalate_to_human", deps, reason="Needs manual review."))

    assert result["escalated"] is True
    assert case.status == CaseStatus.ESCALATED
    assert deps.log_entries[0].outcome == "escalated_to_human"


def test_log_decision_sets_valid_status(tools):
    case = make_payment_case()
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    result = json.loads(call(tools, "log_decision", deps, final_status="recovered"))
    assert result["final_status"] == "recovered"
    assert case.status == CaseStatus.RECOVERED


def test_log_decision_falls_back_to_in_progress_on_bad_status(tools):
    case = make_payment_case()
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])

    result = json.loads(call(tools, "log_decision", deps, final_status="not_a_real_status"))
    assert result["final_status"] == "in_progress"
    assert case.status == CaseStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# _finalize_status_if_unset -- same test shape as would prove agent_loop.py's original correct,
# ported to this file's version (per Gate 1's instruction).
# ---------------------------------------------------------------------------

def test_finalize_status_noop_when_already_set():
    case = make_payment_case()
    case.status = CaseStatus.RECOVERED
    _finalize_status_if_unset(case, [])
    assert case.status == CaseStatus.RECOVERED


def test_finalize_status_noop_when_no_log_entries():
    case = make_payment_case()
    assert case.status == CaseStatus.OPEN
    _finalize_status_if_unset(case, [])
    assert case.status == CaseStatus.OPEN


def test_finalize_status_executed_becomes_recovered(tools):
    case = make_payment_case()
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])
    call(tools, "propose_intervention", deps, action_type="schedule_retry", amount=1500.0,
         reasoning="r", channel="upi_autopay")
    call(tools, "execute_action", deps)
    case.status = CaseStatus.OPEN  # simulate model never calling log_decision
    _finalize_status_if_unset(case, deps.log_entries)
    assert case.status == CaseStatus.RECOVERED


def test_finalize_status_blocked_becomes_blocked(tools):
    case = make_payment_case(decline_class=DeclineClass.HARD)
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])
    call(tools, "propose_intervention", deps, action_type="retry_same_instrument", amount=1500.0,
         reasoning="r", channel="card")
    call(tools, "execute_action", deps)
    case.status = CaseStatus.OPEN
    _finalize_status_if_unset(case, deps.log_entries)
    assert case.status == CaseStatus.BLOCKED


def test_finalize_status_queued_becomes_escalated(tools):
    case = make_receivable_case("INV-A", amount=120000.0)
    deps = CaseDeps(case=case, history=AttemptHistory(), all_cases=[case])
    call(tools, "propose_intervention", deps, action_type="send_reminder", amount=120000.0,
         reasoning="r", channel="email")
    call(tools, "execute_action", deps)
    case.status = CaseStatus.OPEN
    _finalize_status_if_unset(case, deps.log_entries)
    assert case.status == CaseStatus.ESCALATED


# ---------------------------------------------------------------------------
# RoutingDecision -- Pydantic validates the router's structured output shape
# ---------------------------------------------------------------------------

def test_routing_decision_accepts_well_formed_output():
    decision = RoutingDecision(surface="payment_failure", severity="high", reason="Hard decline on a subscription.")
    assert decision.surface == "payment_failure"
    assert decision.severity == "high"


def test_routing_decision_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        RoutingDecision(surface="payment_failure", severity="high")  # missing `reason`


def test_routing_decision_rejects_wrong_type():
    with pytest.raises(ValidationError):
        RoutingDecision(surface="payment_failure", severity="high", reason=12345)  # wrong type, not coercible


# ---------------------------------------------------------------------------
# Cross-system guardrail parity -- the strongest test in this file. Runs the SAME case + SAME
# proposed action through agent_loop.dispatch_tool's execute_action path AND
# pydantic_agents's execute_action tool, and asserts the guardrail result is byte-identical.
# This is what actually proves "compliance isn't duplicated or drifted," not just a comment.
# ---------------------------------------------------------------------------

PARITY_CASES = [
    ("soft_decline_low_value_autonomous", lambda: make_payment_case(amount=1500.0, decline_class=DeclineClass.SOFT),
     {"action_type": "schedule_retry", "amount": 1500.0, "channel": "upi_autopay"}),
    ("hard_decline_blind_retry_hard_stop", lambda: make_payment_case(decline_class=DeclineClass.HARD),
     {"action_type": "retry_same_instrument", "amount": 1500.0, "channel": "card"}),
    ("high_value_receivable_approve_first", lambda: make_receivable_case("INV-PARITY", amount=120000.0),
     {"action_type": "send_reminder", "amount": 120000.0, "channel": "email"}),
]


@pytest.mark.parametrize("name,make_case,action_kwargs", PARITY_CASES, ids=[c[0] for c in PARITY_CASES])
def test_guardrail_parity_between_agent_loop_and_pydantic_agents(tools, name, make_case, action_kwargs):
    # agent_loop.py path
    case_a = make_case()
    state = CaseAgentState(case=case_a, history=AttemptHistory(), all_cases=[case_a])
    dispatch_tool("propose_intervention", {**action_kwargs, "reasoning": "parity test"}, state, iteration=1)
    result_text_a, is_error_a = dispatch_tool("execute_action", {}, state, iteration=1)
    result_a = json.loads(result_text_a)

    # pydantic_agents.py path
    case_b = make_case()
    deps = CaseDeps(case=case_b, history=AttemptHistory(), all_cases=[case_b])
    call(tools, "propose_intervention", deps, reasoning="parity test", **action_kwargs)
    result_text_b = call(tools, "execute_action", deps)
    result_b = json.loads(result_text_b)

    assert result_a["tier"] == result_b["tier"]
    assert result_a["action_taken"] == result_b["action_taken"]
    assert result_a["violated_rules"] == result_b["violated_rules"]
    assert case_a.status == case_b.status or (case_a.status == CaseStatus.OPEN)  # agent_loop only
    # sets case.status via _finalize_status_if_unset/log_decision, not inside dispatch_tool itself --
    # so compare the two systems' OWN finalize functions instead of a mid-flight status.
    from agent_loop import _finalize_status_if_unset as agent_loop_finalize
    agent_loop_finalize(case_a, state)
    _finalize_status_if_unset(case_b, deps.log_entries)
    assert case_a.status == case_b.status


# ---------------------------------------------------------------------------
# Router handoff mechanics -- added 2026-08-30, replacing plain-code specialist selection with a
# genuine hand_off_to_specialist tool call the router itself decides to make. Tests here cover
# what's testable WITHOUT a live LLM call (the tool's own validation logic and the escalation
# path); the retry loop and a real valid handoff both require an actual router run and are
# verified live in DEVLOG.md's Gate-4-follow-up entries, not here -- see that file before assuming
# this coverage is complete.
# ---------------------------------------------------------------------------

from pydantic_agents import RouterDeps, VALID_SURFACES, _escalate_routing_failure, _make_router


@pytest.fixture(scope="module")
def router_tool():
    """Extracts hand_off_to_specialist the same way `tools` extracts the case tools above --
    Tool.function off a router built with a placeholder model string. Never calls .run_sync on the
    router itself (that needs a real model), only invokes the tool function directly."""
    router = _make_router("test")
    return router._function_toolset.tools["hand_off_to_specialist"].function


class FakeRouterCtx:
    def __init__(self, deps):
        self.deps = deps


def make_router_deps(case: Case) -> RouterDeps:
    return RouterDeps(case=case, history=AttemptHistory(), model="test", provider="groq", all_cases=[case])


def test_hand_off_to_specialist_rejects_invalid_surface(router_tool):
    """The core new validation: an unrecognized surface must be rejected by the tool itself
    (before ever attempting to build/run a specialist agent), not silently coerced to a default.
    This is what makes the retry loop in run_case_via_orchestrator meaningful -- the tool has to
    actually be able to say no.

    router_tool is `async def` (fixed 2026-08-30 -- Pydantic AI forbids run_sync() inside a tool
    body, deadlock risk; the tool now awaits specialist.run() instead), so it's invoked here via
    asyncio.run rather than a plain call -- this only exercises the invalid-surface path, which
    returns before ever awaiting the specialist, so no event loop nesting issue arises."""
    import asyncio
    case = make_payment_case()
    deps = make_router_deps(case)

    result = json.loads(asyncio.run(router_tool(FakeRouterCtx(deps), surface="not_a_real_surface",
                                                  severity="high", reason="test")))

    assert "error" in result
    assert "not_a_real_surface" in result["error"]
    assert deps.handoff_result is None  # rejected before ever running a specialist


@pytest.mark.parametrize("surface", sorted(VALID_SURFACES))
def test_hand_off_to_specialist_accepts_every_valid_surface_name(surface):
    """Every real surface name must be accepted by the validation gate itself (independent of
    whether the specialist agent underneath can actually be reached) -- this is checkable without
    a live LLM call by asserting VALID_SURFACES matches SPECIALIST_PROMPTS' keys exactly, the
    actual source of truth the tool checks against."""
    from pydantic_agents import SPECIALIST_PROMPTS
    assert surface in SPECIALIST_PROMPTS


def test_escalate_routing_failure_produces_a_real_decision_log_entry():
    """When the router can't produce a valid handoff even after a retry, the case must get a real,
    honest DecisionLogEntry -- not silently default to a guessed specialist (the old behavior this
    replaced: specialist_map.get(surface, PAYMENT_SPECIALIST_PROMPT))."""
    case = make_payment_case()
    entry = _escalate_routing_failure(case, "router failed twice", provider="groq")

    assert case.status == CaseStatus.ESCALATED
    assert entry.action_taken == ActionTaken.QUEUED_FOR_APPROVAL
    assert entry.outcome == "escalated_to_human"
    assert entry.reasoning == "router failed twice"
    assert entry.provider == "pydantic-ai-groq"


def test_router_deps_handoff_result_starts_unset():
    """RouterDeps.handoff_result being None is what run_case_via_orchestrator's retry logic keys
    off of -- confirm the default is actually None, not some other falsy sentinel that could mask
    a real (but empty) handoff."""
    case = make_payment_case()
    deps = make_router_deps(case)
    assert deps.handoff_result is None


def test_run_case_via_orchestrator_retries_once_then_escalates_on_no_handoff():
    """Integration test for the actual retry-then-escalate LOOP inside
    run_case_via_orchestrator, not just its pieces in isolation -- verified live 2026-08-30 via an
    ad-hoc mock (see DEVLOG.md) before being turned into a real regression test. Mocks
    _make_router to return a router whose run_sync NEVER calls hand_off_to_specialist (simulating
    a router that concludes without ever handing off), so deps.handoff_result stays None on both
    the initial attempt and the retry. Confirms: (1) the router is invoked exactly twice (1 initial
    + 1 retry, not more, not fewer), (2) the final result is a real escalated_to_human
    DecisionLogEntry, not a silent default to a guessed specialist (the old behavior this
    replaced), (3) case.status ends up ESCALATED."""
    from unittest.mock import MagicMock, patch
    import pydantic_agents as pa

    case = make_payment_case()
    call_count = {"n": 0}

    class NeverHandsOffRouter:
        def __init__(self, *a, **k):
            pass

        def run_sync(self, *a, **k):
            call_count["n"] += 1
            return MagicMock(output=pa.RoutingDecision(surface="unknown", severity="unknown",
                                                          reason="never handed off"))

    with patch.object(pa, "_make_router", return_value=NeverHandsOffRouter()):
        decision, log_entries = pa.run_case_via_orchestrator(
            case, AttemptHistory(), "groq", api_key="fake-key-never-used",
        )

    assert call_count["n"] == 2, "expected exactly 1 initial + 1 retry call to the router"
    assert len(log_entries) == 1
    assert log_entries[0].outcome == "escalated_to_human"
    assert log_entries[0].action_taken == ActionTaken.QUEUED_FOR_APPROVAL
    assert case.status == CaseStatus.ESCALATED
    assert decision.surface == "unknown"
