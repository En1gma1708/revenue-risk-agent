"""
FastAPI backend for the dashboard (Phase 5). Four endpoints per PRD.md SS9:
  - POST /batch/run       -- trigger a batch run (wraps run_batch.run_batch)
  - GET  /cases           -- the batch table (filterable by surface)
  - GET  /cases/{id}/trace -- one case's full DecisionLogEntry timeline
  - GET  /metrics         -- headline + per-surface + guardrail ledger + agent-quality metrics

Deliberately thin: all real logic (metrics computation, guardrail rules, the agent loop) lives
in their own modules and is unit-testable independent of the web framework. This file is just
routing + serialization.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from baseline import load_cases as load_baseline_cases
from baseline import run_naive_baseline
from bulk_upload import parse_upload, run_bulk
from custom_case import CustomCaseInput, run_custom_case
from db import get_connection, init_db, load_all_cases, load_all_decision_log_entries
from guardrails import GUARDRAILS
from metrics import (
    compute_agent_quality_metrics,
    compute_guardrail_ledger,
    compute_headline_metrics,
    compute_per_surface_metrics,
    compute_provider_reliability,
    compute_reliability_metrics,
)
from run_batch import run_batch

app = FastAPI(title="Revenue Risk Agent API")

# Dashboard is a separate Vite dev server during development (different port) -- CORS needed
# for local dev; this is a demo project, not a public multi-tenant service, so a permissive
# local-dev CORS policy is an appropriate, deliberately narrow scope (not a production stance).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _all_data() -> tuple[list[dict], list[dict]]:
    conn = get_connection()
    init_db(conn)   # no-op if tables already exist; safe to call on every request
    cases = load_all_cases(conn)
    entries = load_all_decision_log_entries(conn)
    conn.close()
    return cases, entries


@app.get("/cases")
def get_cases(surface: Optional[str] = None):
    cases, _ = _all_data()
    if surface:
        cases = [c for c in cases if c["surface"] == surface]
    return {"cases": cases, "count": len(cases)}


@app.get("/cases/{case_id}/trace")
def get_case_trace(case_id: str):
    cases, entries = _all_data()
    case = next((c for c in cases if c["case_id"] == case_id), None)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

    trace = [e for e in entries if e["case_id"] == case_id]
    trace.sort(key=lambda e: (e["iteration"], e["timestamp"]))
    return {"case": case, "trace": trace}


@app.get("/metrics")
def get_metrics():
    cases, entries = _all_data()

    # Naive baseline comparison (depth-add #1, see DEVLOG.md 2026-08-25) -- run fresh against
    # the SAME case set the agent's headline metrics were computed from, so the two numbers are
    # directly comparable rather than drifting apart if cases.json changes between runs.
    baseline_cases = load_baseline_cases()
    baseline_result = run_naive_baseline(baseline_cases)

    return {
        "headline": compute_headline_metrics(cases, entries),
        "baseline": {
            "policy": baseline_result["policy"],
            "amount_recovered_inr": baseline_result["amount_recovered_inr"],
            "recovery_rate": baseline_result["recovery_rate"],
            "note": "Illustrative estimate: a single fixed retry regardless of decline reason or "
                    "surface, with no root-cause diagnosis -- the pattern this project's own "
                    "NOVELTY.md cites as the gap in existing uniform-retry products.",
        },
        "by_surface": compute_per_surface_metrics(cases, entries),
        "guardrail_ledger": compute_guardrail_ledger(entries),
        "agent_quality": compute_agent_quality_metrics(entries),
        "reliability": compute_reliability_metrics(cases, entries),
        "provider_reliability": compute_provider_reliability(entries),
        "guardrail_rules": [
            {"rule_id": r.rule_id, "description": r.description, "tier_on_violation": r.tier_on_violation.value}
            for r in GUARDRAILS
        ],
    }


@app.post("/batch/run")
def trigger_batch_run(limit: Optional[int] = None, providers: str = "gemini,groq,openrouter"):
    """
    Runs synchronously and returns when done -- acceptable for a demo-scale batch (95 cases,
    minutes not hours) triggered manually from the dashboard, not a production async job queue.
    """
    provider_names = [p.strip() for p in providers.split(",") if p.strip()]
    stats = run_batch(limit=limit, provider_names=provider_names, reset=True)
    return {"stats": stats}


@app.post("/cases/custom/run")
def submit_custom_case(payload: CustomCaseInput):
    """
    Runs a visitor-submitted case through the real agent loop, live -- the fix for "as far as
    anyone else knows, this is just a script": there is no way to tell a genuinely interactive
    agent apart from a replayed batch without actually letting someone feed it a new case and
    watch it decide in real time. Synchronous (a few seconds to ~1-2 minutes depending on how
    many turns the agent needs) -- acceptable for one interactive submission at a time, same
    reasoning as /batch/run being synchronous for a full batch.
    """
    try:
        case_id = run_custom_case(payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyError as e:
        # get_llm_client raises KeyError if the requested provider has no API key configured
        raise HTTPException(status_code=400, detail=str(e))
    return {"case_id": case_id}


@app.post("/cases/custom/bulk")
async def submit_bulk_cases(file: UploadFile = File(...)):
    """
    The multi-case version of /cases/custom/run -- upload a .csv or .xlsx of cases (matching
    bulk_upload.COLUMNS; the dashboard serves a ready-made example at /sample_cases.xlsx, a
    static file in dashboard/public/) and each row is run through the real agent loop, one at a
    time. Capped at bulk_upload.MAX_ROWS to protect shared free-tier quota from a single
    oversized upload.
    """
    content = await file.read()
    try:
        inputs = parse_upload(file.filename or "upload", content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    results = run_bulk(inputs)
    return {"results": results}


@app.get("/health")
def health():
    return {"status": "ok"}
