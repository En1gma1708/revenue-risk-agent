"""
Retroactively re-verifies the guardrail engine's original enforcement against real, already-
completed cases -- scored to Langfuse as `guardrail_consistent`. Added 2026-09-01 as the 4th of
the 4 evals scoped the evening before (see DEVLOG.md), deliberately built as its OWN script rather
than folded into pydantic_agents.py's inline `_score_case_evals()` (see that function's own
DEVLOG entry for why): re-running `enforce_guardrails()` immediately after `execute_action` already
called it, in the same process, on the same inputs, would be tautological -- always matches,
proves nothing. The real value here is independently re-verifying PERSISTED audit-trail data
later, against whatever the CURRENT guardrails.py rule table says -- catching serialization bugs,
or (more importantly) silent behavior drift if the rule table itself changes after a case was
originally decided. That is inherently an audit-over-stored-data operation, not something that
belongs inline in the real-time decision path.

**A real, honest data limitation, accounted for rather than ignored**: `execute_action`'s
persisted `decision` dict only stores action_type/channel/amount -- NOT target_time/notify_time,
which the `rbi_predebit_notice` rule specifically depends on (a missing target_time OR
notify_time is treated as a violation by that rule's own logic). Reconstructing a ProposedAction
from persisted data with target_time=notify_time=None would make EVERY sufficiently-large
retroactive re-check falsely look non-compliant, for a reason that has nothing to do with real
drift -- just missing historical data this file never persisted. Any case where the ORIGINAL
tier was HARD_STOP due to rbi_predebit_notice specifically, and re-checking WOULD now also flag
that same rule, is marked "unverifiable" and NOT scored as a mismatch -- an honest "can't tell"
is better than a false "drift detected."

Deliberately does NOT touch db.py's write path or mutate any stored case/entry -- read-only
against the real DB, write-only to Langfuse.

Run with: python backend/run_guardrail_audit.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from db import MULTIAGENT_DB_PATH, get_connection, init_db, load_all_cases, load_all_decision_log_entries
from guardrails import AttemptHistory, ProposedAction, enforce_guardrails
from models import ActionTier, Case, DecisionLogEntry
from pydantic_agents import _LANGFUSE_ENABLED, flush_langfuse, langfuse
from run_batch_multiagent import load_cases_and_history

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Entries produced by tools OTHER than execute_action (escalate_to_human, record_promise_to_pay,
# the checker itself) don't have a reconstructable ProposedAction the same way -- excluded, not
# silently mismatched.
_RECONSTRUCTABLE_OUTCOMES = {"queued_for_human_approval", "logged_only", None}  # None -- HARD_STOP/AUTONOMOUS have outcome=None or a json string


def _latest_trace_id_by_case() -> dict[str, str]:
    """Real Langfuse traces are found by input.case_id (confirmed live: `process-revenue-case`
    traces carry {"case_id": ..., ...} in their `input` field, set by `_case_trace_span`).
    `create_score` needs an explicit trace_id -- there's no metadata/input search filter on
    `trace.list`, so this pages through all `process-revenue-case` traces once and keeps the
    NEWEST trace per case_id (list is newest-first), rather than attaching a score with no
    target trace, which would be an orphaned, unfindable score in the UI."""
    if not _LANGFUSE_ENABLED:
        return {}
    by_case: dict[str, str] = {}
    page = 1
    while True:
        res = langfuse.api.trace.list(name="process-revenue-case", limit=100, page=page)
        if not res.data:
            break
        for t in res.data:
            cid = (t.input or {}).get("case_id") if isinstance(t.input, dict) else None
            if cid and cid not in by_case:  # newest-first -- keep first (=newest) seen
                by_case[cid] = t.id
        if len(res.data) < 100:
            break
        page += 1
    return by_case


def _real_case_ids() -> set[str]:
    cases_raw = json.loads((DATA_DIR / "cases.json").read_text(encoding="utf-8"))
    return {c["case_id"] for c in cases_raw}


def _is_execute_action_entry(entry: DecisionLogEntry) -> bool:
    """execute_action's own decision dict shape: exactly action_type/channel/amount, no
    escalated/promised_amount/sound keys that other tools use."""
    keys = set(entry.decision.keys())
    return keys == {"action_type", "channel", "amount"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be checked/scored without writing to Langfuse.")
    args = parser.parse_args()

    real_ids = _real_case_ids()
    conn = get_connection(MULTIAGENT_DB_PATH)
    init_db(conn)
    all_cases_raw = load_all_cases(conn)
    all_entries_raw = load_all_decision_log_entries(conn)
    conn.close()

    cases_by_id = {c["case_id"]: Case.model_validate(c) for c in all_cases_raw if c["case_id"] in real_ids}
    entries_by_case: dict[str, list[DecisionLogEntry]] = {}
    for e in all_entries_raw:
        cid = e["case_id"]
        if cid not in real_ids:
            continue
        entries_by_case.setdefault(cid, []).append(DecisionLogEntry.model_validate(e))

    _, history_by_case = load_cases_and_history()
    trace_id_by_case = _latest_trace_id_by_case() if not args.dry_run else {}

    checked, consistent, mismatched, unverifiable, no_trace = 0, 0, 0, 0, 0

    for case_id, case in sorted(cases_by_id.items()):
        entries = entries_by_case.get(case_id, [])
        history = history_by_case.get(case_id, AttemptHistory())

        for entry in entries:
            if not _is_execute_action_entry(entry):
                continue
            checked += 1
            action = ProposedAction(
                action_type=entry.decision["action_type"], channel=entry.decision.get("channel"),
                amount=entry.decision.get("amount", 0.0), target_time=None, notify_time=None,
            )
            fresh_result = enforce_guardrails(case, action, history)
            fresh_tier = fresh_result.tier
            original_tier = entry.action_tier

            new_rbi_hit = "rbi_predebit_notice" in fresh_result.violated_rule_ids
            original_rbi_hit = "rbi_predebit_notice" in entry.guardrail_check.violated_rule_ids
            # Unverifiable if the mismatch is attributable to the RBI rule specifically (missing
            # target_time/notify_time in persisted data), in either direction.
            if fresh_tier != original_tier and (new_rbi_hit or original_rbi_hit):
                unverifiable += 1
                print(f"{case_id}: UNVERIFIABLE (rbi_predebit_notice depends on unpersisted "
                      f"target_time/notify_time) -- original={original_tier.value}, fresh={fresh_tier.value}")
                continue

            is_consistent = fresh_tier == original_tier
            if is_consistent:
                consistent += 1
            else:
                mismatched += 1
                print(f"{case_id}: MISMATCH -- original={original_tier.value} "
                      f"({entry.guardrail_check.violated_rule_ids}), "
                      f"fresh={fresh_tier.value} ({fresh_result.violated_rule_ids})")

            if not args.dry_run and _LANGFUSE_ENABLED:
                trace_id = trace_id_by_case.get(case_id)
                if trace_id is None:
                    # No real trace for this case (e.g. run before Langfuse was wired up, or
                    # never included in a sample run) -- an orphaned score with no trace_id
                    # would be unfindable in the UI, so skip scoring rather than fake a target.
                    no_trace += 1
                else:
                    langfuse.create_score(
                        name="guardrail_consistent", value=1.0 if is_consistent else 0.0,
                        trace_id=trace_id, data_type="BOOLEAN",
                        comment=f"original={original_tier.value}, fresh={fresh_tier.value}",
                        metadata={"case_id": case_id, "original_tier": original_tier.value,
                                  "fresh_tier": fresh_tier.value},
                    )

    print(f"\n{'=' * 60}\nGuardrail-consistency audit complete\n{'=' * 60}")
    print(json.dumps({
        "checked": checked, "consistent": consistent, "mismatched": mismatched,
        "unverifiable": unverifiable, "no_matching_trace_skipped": no_trace,
    }, indent=2))

    if not args.dry_run:
        flush_langfuse()


if __name__ == "__main__":
    main()
