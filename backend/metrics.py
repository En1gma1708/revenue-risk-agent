"""
Metrics computation -- every number here maps to a specific clause of the brief's bar
("measured money recovered across a batch, with compliant escalation, stopping rules, and an
audit trail"), per METRICS.md. Pure functions over the decision log; the dashboard/API layer
just calls these and serializes the result, no metric logic duplicated in the frontend.

Honesty rule from METRICS.md: never report a number without being able to show the underlying
DecisionLogEntry rows that produced it -- every function here is a live query over real log
data, nothing hardcoded or hand-picked.
"""

from __future__ import annotations

from collections import defaultdict


def compute_headline_metrics(cases: list[dict], log_entries: list[dict]) -> dict:
    """
    The dashboard's top bar: "Rs.X at risk -> Rs.Y recovered / Rs.Z escalated / Rs.W blocked by
    policy". See METRICS.md SS "Headline batch metrics" for the exact definitions being
    implemented here.
    """
    at_risk = sum(c["amount_inr"] for c in cases)

    recovered = sum(
        e["amount_recovered_inr"] for e in log_entries
        if e["action_taken"] == "executed"
    )

    # escalated = currently-queued APPROVE_FIRST amounts, deduped per case (a case can have
    # multiple log entries; count its amount once even if escalated more than once)
    escalated_case_ids = {
        e["case_id"] for e in log_entries
        if e["action_tier"] == "APPROVE_FIRST" and e["action_taken"] == "queued_for_approval"
    }
    case_amounts = {c["case_id"]: c["amount_inr"] for c in cases}
    escalated = sum(case_amounts.get(cid, 0.0) for cid in escalated_case_ids)

    blocked_case_ids = {
        e["case_id"] for e in log_entries
        if e["action_tier"] == "HARD_STOP" and e["action_taken"] == "blocked"
    }
    blocked = sum(case_amounts.get(cid, 0.0) for cid in blocked_case_ids)

    recovery_rate = (recovered / at_risk) if at_risk > 0 else 0.0

    return {
        "amount_at_risk_inr": round(at_risk, 2),
        "amount_recovered_inr": round(recovered, 2),
        "amount_escalated_inr": round(escalated, 2),
        "amount_blocked_inr": round(blocked, 2),
        "recovery_rate": round(recovery_rate, 4),
        "case_count": len(cases),
    }


def compute_per_surface_metrics(cases: list[dict], log_entries: list[dict]) -> dict:
    """Same headline metrics, filtered per surface -- what makes 'one policy, three surfaces'
    a checkable claim instead of a slogan (METRICS.md SS 'Per-surface breakdown')."""
    surfaces = sorted({c["surface"] for c in cases})
    result = {}
    for surface in surfaces:
        surface_cases = [c for c in cases if c["surface"] == surface]
        surface_case_ids = {c["case_id"] for c in surface_cases}
        surface_entries = [e for e in log_entries if e["case_id"] in surface_case_ids]
        result[surface] = compute_headline_metrics(surface_cases, surface_entries)
    return result


def compute_guardrail_ledger(log_entries: list[dict]) -> list[dict]:
    """
    Fired-count per guardrail rule, plus which cases it fired on -- the artifact that proves
    compliance rules aren't decorative (METRICS.md SS 'Guardrail ledger metrics'). Rule
    descriptions aren't duplicated here; the dashboard/API can join against guardrails.GUARDRAILS
    by rule_id if it wants the human-readable text, this just aggregates fired counts + case ids.
    """
    fired: dict[str, dict] = defaultdict(lambda: {"count": 0, "case_ids": []})
    for entry in log_entries:
        for rule_id in entry.get("guardrail_check", {}).get("violated_rule_ids", []):
            fired[rule_id]["count"] += 1
            fired[rule_id]["case_ids"].append(entry["case_id"])

    return [
        {"rule_id": rule_id, "fired_count": data["count"], "case_ids": data["case_ids"]}
        for rule_id, data in sorted(fired.items(), key=lambda kv: -kv[1]["count"])
    ]


def compute_agent_quality_metrics(log_entries: list[dict]) -> dict:
    """Secondary metrics for the DEVLOG/interview narrative (METRICS.md SS 'Agent-quality
    metrics') -- iterations per case, guardrail veto rate. Not necessarily dashboard-headline
    material, but exposed via the API for completeness."""
    if not log_entries:
        return {"mean_iterations_per_case": 0.0, "max_iterations_seen": 0, "guardrail_veto_rate": 0.0}

    by_case: dict[str, list[dict]] = defaultdict(list)
    for e in log_entries:
        by_case[e["case_id"]].append(e)

    iterations_per_case = [max(e["iteration"] for e in entries) for entries in by_case.values()]
    mean_iterations = sum(iterations_per_case) / len(iterations_per_case)

    total_proposals = sum(1 for e in log_entries if e["action_taken"] in ("executed", "blocked", "queued_for_approval"))
    vetoed = sum(1 for e in log_entries if e["action_tier"] == "HARD_STOP" and e["action_taken"] == "blocked")
    veto_rate = (vetoed / total_proposals) if total_proposals > 0 else 0.0

    return {
        "mean_iterations_per_case": round(mean_iterations, 2),
        "max_iterations_seen": max(iterations_per_case),
        "guardrail_veto_rate": round(veto_rate, 4),
    }


# ---------------------------------------------------------------------------
# Reliability metrics -- distinguishes real agent decisions from infrastructure/provider
# failures (generation_error, max_iterations_exceeded). Added 2026-08-26 after a full day of
# free-tier quota exhaustion made it clear this distinction needed to be a first-class,
# queryable metric -- not something re-derived by hand from raw DB rows every time (see
# DEVLOG.md 2026-08-25/26 for the repeated manual version of exactly this query).
# ---------------------------------------------------------------------------

INFRA_FAILURE_OUTCOMES = {"generation_error", "max_iterations_exceeded"}


def compute_reliability_metrics(cases: list[dict], log_entries: list[dict]) -> dict:
    """
    Case-level reliability: what fraction of the batch produced a genuine agent decision vs.
    an infrastructure/provider failure (quota exhaustion, malformed generation, iteration cap).
    This is the metric that answers "how reliable is your system" honestly -- distinct from
    guardrail_veto_rate, which is about the AGENT choosing to be blocked, not the SYSTEM failing
    to produce a decision at all.

    Judges each case by the trailing slice of its log entries after its LAST terminal (non-error)
    outcome -- NOT by whether an error appears ANYWHERE in its full history. Fixed 2026-08-29
    alongside the identical bug in db.get_cleanly_completed_case_ids() (see DEVLOG.md): a case
    that failed once on an old, superseded attempt but later succeeded cleanly must count as
    clean here too, or this metric and --resume's skip logic silently disagree with each other --
    which defeats the purpose of the metric being the honest cross-check on the raw batch summary.
    Requires log_entries to be time-ordered (see db.load_all_decision_log_entries's ORDER BY
    timestamp) so "last terminal outcome" is meaningful.
    """
    entries_by_case: dict[str, list[dict]] = defaultdict(list)
    for e in log_entries:
        entries_by_case[e["case_id"]].append(e)

    case_ids = [c["case_id"] for c in cases]
    clean_case_ids = []
    for cid in case_ids:
        entries = entries_by_case.get(cid)
        if not entries:
            continue
        last_terminal_idx = None
        for i, e in enumerate(entries):
            if e.get("outcome") not in INFRA_FAILURE_OUTCOMES:
                last_terminal_idx = i
        if last_terminal_idx is None:
            continue  # never had a single terminal outcome -- not clean
        trailing = entries[last_terminal_idx + 1:]
        if not any(e.get("outcome") in INFRA_FAILURE_OUTCOMES for e in trailing):
            clean_case_ids.append(cid)
    failed_case_ids = [cid for cid in case_ids if cid not in clean_case_ids]

    failure_reasons: dict[str, int] = defaultdict(int)
    for cid in failed_case_ids:
        for e in entries_by_case.get(cid, []):
            if e.get("outcome") in INFRA_FAILURE_OUTCOMES:
                failure_reasons[e["outcome"]] += 1

    total = len(case_ids)
    return {
        "total_cases": total,
        "clean_cases": len(clean_case_ids),
        "failed_cases": len(failed_case_ids),
        "reliability_rate": round(len(clean_case_ids) / total, 4) if total else 0.0,
        "failure_breakdown": dict(failure_reasons),
        "failed_case_ids": failed_case_ids,
    }


def compute_provider_reliability(log_entries: list[dict]) -> dict:
    """
    Reliability broken down per LLM provider. `provider` is a first-class field on
    DecisionLogEntry (models.py), set explicitly by run_batch.py from the client it chose for
    each case -- not inferred after the fact, so this is exact, not a best-effort guess.
    Real per-provider breakdown is valuable for the "how does your system behave under
    real-world constraints" reliability story specifically (see DEVLOG.md 2026-08-25/26 for the
    actual free-tier quota exhaustion incidents this metric now makes visible without a manual
    DB query).
    """
    provider_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "failed": 0})
    for e in log_entries:
        provider = e.get("provider")
        if provider is None:
            continue
        provider_stats[provider]["total"] += 1
        if e.get("outcome") in INFRA_FAILURE_OUTCOMES:
            provider_stats[provider]["failed"] += 1

    result = {}
    for provider, stats in provider_stats.items():
        total = stats["total"]
        result[provider] = {
            "total_log_entries": total,
            "failed_entries": stats["failed"],
            "failure_rate": round(stats["failed"] / total, 4) if total else 0.0,
        }
    return result
