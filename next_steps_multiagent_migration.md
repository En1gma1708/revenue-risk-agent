# Next steps: prove out the multi-agent system, then FULLY commit to it as the real submission

Read CLAUDE.md, CONVERSATION_SUMMARY.md, and DEVLOG.md's latest entries first for full context.

## The actual goal, stated precisely (user's final decision, 2026-08-30)

Do the real engineering work to PROVE `pydantic_agents.py` (router → 3 surface-specialist agents,
Pydantic AI framework) works at batch scale, run the real 95-case dataset through it, and then FULLY
COMMIT to it as the final shipped architecture — not a parallel PoC sitting alongside `agent_loop.py`
anymore. User has explicitly decided against keeping the old single-agent system as a fallback once
this is proven; the multi-agent system becomes THE project.

**The bar for "proven," clarified explicitly by the user (not literally 95/95 — quota scarcity has
been the hard ceiling for BOTH systems throughout this entire project, not a code problem)**: match
or exceed whatever `agent_loop.py` achieved under the same real quota constraints (61+/95 as of
2026-08-30). Do not treat literal 95/95 as the gate — that bar was never met by the original system
either, for reasons entirely outside code quality (free-tier daily quota). The actual gate is: same
validation discipline (`compute_reliability_metrics`, real numbers, no masked failures), a comparable
or better clean-case count under comparable quota conditions, and no systemic bug depressing results.

**Once Gate 3 passes at that bar, proceed straight to Gate 4 — full commit, no lingering fallback.**
`agent_loop.py` stays in the repo as documented prior art (its DEVLOG history and the real bugs found
in it remain valuable engineering evidence, worth keeping for the story of how the project evolved),
but `run_batch.py`/the dashboard/the actual submission all point at the multi-agent system afterward,
not both.

## Gate 1 — Unit test coverage for pydantic_agents.py

Currently zero tests exist for this file (the 32 passing tests cover `guardrails.py`/`db.py`/
`metrics.py`/the old `agent_loop.py` tools — none of that changed, so those stay green, but nothing
tests the NEW tool-wiring, the router's classification, or `_finalize_status_if_unset`'s port).

Write `backend/tests/test_pydantic_agents.py` covering:
- Each of the 7 shared tools (`get_case_context` through `log_decision`) behaves correctly given a
  `CaseDeps` fixture — mirror the coverage style already used in `test_guardrails.py`/
  `test_new_tools.py`, don't reinvent the testing approach.
- `_finalize_status_if_unset` — the exact same test cases that would prove `agent_loop.py`'s original
  version correct, ported to this file's version.
- The router's `RoutingDecision` output shape is validated by Pydantic (test that a malformed/missing
  field raises, not silently passes).
- Guardrail enforcement is IDENTICAL through this system as through `agent_loop.py` — the strongest
  possible test here is a shared-fixture test that runs the SAME case + proposed action through both
  `agent_loop.dispatch_tool`'s execute_action path and `pydantic_agents`'s `execute_action` tool, and
  asserts the guardrail result is byte-identical. This is the test that actually proves "compliance
  isn't duplicated or drifted," not just asserted in a comment.

Do not proceed to Gate 2 until this suite passes and covers the same ground `test_guardrails.py`/
`test_new_tools.py` cover for the original system.

## Gate 2 — Batch infrastructure (the real gap right now)

`pydantic_agents.py` currently has no equivalent of `run_batch.py`. Build one
(`backend/run_batch_multiagent.py`, or extend `pydantic_agents.py` itself — decide based on how much
shares cleanly with `run_batch.py`'s existing logic, don't duplicate `route_batch`/severity scoring,
import it) with:
- Incremental persistence per case (same principle as `run_batch.py` — a mid-batch crash must not
  lose already-completed work). Reuse `db.py`'s `upsert_case`/`insert_decision_log_entry` as-is; the
  `DecisionLogEntry` rows this system produces are the same schema.
- A `--resume`-equivalent — skip cases already cleanly completed. **Reuse
  `db.get_cleanly_completed_case_ids()` exactly as-is** (already fixed and tested 2026-08-30) rather
  than writing a second version that could drift from it, per this project's own hard-learned lesson
  about the reliability-tracking bug.
- Multi-account round-robin across all configured Groq/OpenRouter/Gemini keys — the per-call
  `_next_key()` round-robin added 2026-08-30 is a start; extend it to the same weighted-schedule
  approach `run_batch.py`'s `PROVIDER_WEIGHTS`/`_build_weighted_account_schedule` already use, don't
  reinvent that either.
- Per-case try/except isolation — one case's crash must not take down the batch (already proven
  necessary, `agent_loop.py`'s generation_error handling is the reference).

## Gate 3 — A real, validated batch run

Once Gate 1 and 2 pass:
1. Run this new batch runner against the SAME real 95-case dataset (`data/cases.json`) — NOT a
   separate/synthetic set, the actual demo data.
2. Validate with `compute_reliability_metrics`/`compute_provider_reliability` against the resulting
   DB rows — same non-negotiable discipline as every other batch claim in this project. Never trust
   the raw run summary alone.
3. Compare the resulting clean-case count and reliability rate honestly against `agent_loop.py`'s
   current 61+/95. It does not need to beat that number on the first run (quota scarcity affects both
   systems equally) — it needs to be in a comparable, trustworthy range, with no systemic bug
   depressing it.

**If Gate 3 reveals a real problem** (a systemic bug, a much worse reliability rate not explained by
quota alone, router misclassifications degrading outcomes) — stop, diagnose it properly the way every
other bug in this project has been diagnosed (real data, not guesses), fix it, and re-run Gate 3. Do
not paper over a bad result by switching the demo dataset or lowering the bar.

## Gate 4 — Only after Gates 1-3 pass: make the switch official

- Point `run_batch.py`'s actual code at the new system (or retire `run_batch.py` in favor of the new
  runner — decide which reads more honestly for the "how does this actually run" story).
- Update NOVELTY.md's orchestrator-worker row, CLAUDE.md's "Multi-agent status" section, README.md,
  and PRD.md to describe multi-agent as the shipped architecture, not a comparison PoC — these all
  currently say "PoC, not the production path" and need a real rewrite at this point, not a patch.
- Keep `agent_loop.py` in the repo as documented prior art (the DEVLOG history of building it, the
  bugs found and fixed in it, are still real, valuable engineering evidence) — just no longer the
  active path `run_batch.py` calls.
- Re-verify the dashboard still renders correctly against data produced by the new system (same
  `DecisionLogEntry` schema, so this should be a non-event, but verify with Playwright per this
  project's own discipline, don't assume).
- Re-curate `demo_shortlist.md` against the new system's actual output — the current shortlist
  (PMT-0002 etc.) is built from `agent_loop.py`'s trace data; find the new system's equivalent
  strongest cases (a real router-misclassification-then-correction moment, if one occurs, would be an
  even better demo beat than what exists now).

## Non-negotiables carried over (do not relax these under time pressure)

- Never run `git commit`/`git push`/`git init` a remote — user does all git themselves.
- Never include a "Co-Authored-By: Claude" trailer in any commit message.
- Write DEVLOG.md entries in the same turn as reporting a result, not queued for later.
- Never trust a raw batch summary — always validate via `compute_reliability_metrics`/
  `compute_provider_reliability`.
- If a gate fails, say so plainly and fix the real problem — do not quietly redefine "done" to make a
  failing gate look passed.
