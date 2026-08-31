# AI Revenue Recovery Agent

**Razorpay AI Buildathon — Track 3: "AI Revenue Recovery"**

A router agent classifies each revenue-leak event — a failed payment, an abandoned checkout, an
overdue B2B receivable — and hands it to one of 3 real specialist agents, each of which investigates,
decides, and acts for its surface. Every one of them, and the router itself, is checked against the
exact same hard-coded compliance engine before anything is allowed to happen: act autonomously, ask a
human for approval, or hard-stop. Every real action is logged to a full audit trail: what it saw, what
it decided, and why.

Full product framing, novelty argument, and metrics methodology: [PRD.md](PRD.md) ·
[NOVELTY.md](NOVELTY.md) · [METRICS.md](METRICS.md).

## Why one compliance core, not three

Revenue loss rarely happens in one clean step, and it doesn't happen on only one surface — a payment
degrades, a checkout gets abandoned, an invoice goes overdue, often for the same underlying customer.
Most platforms (including Razorpay's own Agent Studio) ship separate, disconnected products per
surface with no handoff between them. This project runs 3 specialist agents that genuinely hand off
from one router, share the same tool set, and are checked against the same guardrail engine — so a
decline reason code, a stalled checkout, and a broken payment promise are all evaluated under one
compliance boundary, not three drifting copies of it. See [NOVELTY.md](NOVELTY.md) for the full
argument, including which of the standard "agentic patterns" apply and which were deliberately
rejected.

**This is a router + 3 specialists now, not a single agent** — a deliberate, gated migration, not a
same-day rewrite. The system shipped its first working version as one unified agent handling all 3
surfaces ([backend/agent_loop.py](backend/agent_loop.py)); after real pushback questioning that
choice, the router-and-specialists architecture ([backend/pydantic_agents.py](backend/pydantic_agents.py),
on [Pydantic AI](https://ai.pydantic.dev/)) was built and proven through explicit gates — unit tests
pinning guardrail behavior byte-identical across both systems, then a real batch run matching and
exceeding the original's clean-case count under the same quota constraints — before being adopted as
the primary, user-facing architecture. `agent_loop.py` stays in the repo, untouched, as proven prior
art. Full history: NOVELTY.md's "Migration history" section, DEVLOG.md's 2026-08-30 entries.

## Architecture

```
Synthetic + real event data (3 surfaces)
        │
        ▼
Stage 1 — Router agent (a real LLM call, NOT plain code)
   classifies surface + severity, hands off to one of 3 specialists
        │
        ▼
Stage 2 — Specialist agent (one per surface, one per case)
   LLM + tool-calling loop, MAX_ITERATIONS hard cap
   tools: get_case_context, check_attempt_history, check_customer_history,
          check_policy_guardrails, propose_intervention, record_promise_to_pay,
          execute_action, escalate_to_human, log_decision
        │
        ▼
Guardrail engine (guardrails.py) — pure functions, data-driven rule table
   runs INSIDE execute_action's handler, not as a prompt instruction
   shared identically by the router and all 3 specialists — never duplicated
   HARD_STOP / APPROVE_FIRST / AUTONOMOUS / LOG_ONLY tiers
        │
        ▼
Stage 3 — Checker agent (a real reflection pattern, not another rule check)
   reviews the specialist's ALREADY-COMPLETED decision for soundness,
   independent of whether it passed guardrails; triggered only on cases
   worth the cost (any HARD_STOP hit, final APPROVE_FIRST, high severity)
   on a flag: one bounded specialist retry, or escalate — never re-checked
        │
        ▼
Audit trail (DecisionLogEntry rows, SQLite) — one row per real action taken
        │
        ▼
Dashboard (React + Vite, served against FastAPI) — batch table, per-case trace
   timeline, guardrail ledger, headline recovery metrics
```

**The intervention decision for each case comes from a specialist reasoning over live tool results**,
not a hardcoded if/else tree that only uses the LLM to classify text — see
[pydantic_agents.py](backend/pydantic_agents.py). **Guardrails are the opposite: hardcoded,
deterministic, and enforced in code every agent's tool calls pass through, proven byte-identical
across the router+specialist system and the original single-agent system by a direct parity test** —
see [guardrails.py](backend/guardrails.py). Real regulatory grounding: NPCI UPI Autopay rules
(4-attempt cap, spacing, non-peak-hour windows) and RBI rules (₹15k AFA threshold, 24h pre-debit
notice).

## The checker agent — a real reflection pattern

A fourth agent reviews every specialist decision worth reviewing — independent of whether the
guardrail engine approved it. Guardrails only catch *policy* violations; a decision can pass every
rule and still be wrong (a reasoning error, a fact the specialist got backwards). The checker is a
second, separate LLM call that reads the case facts and the specialist's completed decision, and
judges whether it actually holds up.

Kept cheap on purpose, since this whole project runs on free-tier LLM quota: it reviews the
*finished artifact*, never re-runs the specialist's whole multi-turn tool-calling loop, and only
triggers on cases actually worth the cost (any `HARD_STOP` hit, a final `APPROVE_FIRST`, or high
router severity) — not every case. On a flag, it can request exactly one bounded specialist retry
(never re-checked again — no risk of an infinite check-retry-check loop) or escalate to a human.

This isn't hypothetical — it caught a real mistake in this project's own data. A specialist
reviewing an overdue receivable said a customer's promise-to-pay was **"missed,"** and proposed
re-requesting the full outstanding amount. The real record said the promise was **kept**. The
checker caught the contradiction, the specialist re-ran, and correctly proposed a reminder for just
the genuine remaining balance instead — a real, verified self-correction, not a demo script. Full
trace of this exact case, plus the checker's complete real-batch results (100% coverage of every
case it flagged as worth reviewing across the real dataset, ~13% flag rate, zero cases needing
human escalation), documented honestly in NOVELTY.md's agentic-pattern audit.

## Observability and eval (Langfuse)

Every LLM call — router, each specialist, the checker — is traced with
[Langfuse](https://langfuse.com), via Pydantic AI's own OpenTelemetry instrumentation (no
framework lock-in: switching observability backends later needs no rewrite). All 3 agent types for
one case nest under a single trace, not 3 disconnected ones, so a full case's reasoning is
readable end to end in one view.

The checker's own verdict is surfaced as a real Langfuse **Score** (`checker_sound`,
boolean, with the checker's actual reasoning as the comment) the moment it reviews a case — this
turns the checker from an internal audit-trail detail into a queryable, dashboardable eval signal,
filterable independent of this project's own database. Set `LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` in `.env` to enable it (optional — the whole system runs
identically, with zero code path changes, when it's unset).

## What's real vs. synthetic (stated upfront, not discovered)

Razorpay's test mode exposes real payment-failure and subscription data, but does **not** expose
abandoned-checkout or receivables/invoice data — those two surfaces are structurally synthetic no
matter what. So:

| Surface | Data | Recovery action |
|---|---|---|
| Payment failures | Real Razorpay test-mode reason codes / decline classification | Real Razorpay Payment Links API (test mode) |
| Checkout abandonment | Synthetic, schema-accurate (cart value, abandonment stage, device, time-since-abandon) | Simulated/logged |
| Overdue B2B receivables | Synthetic, schema-accurate (days overdue, promise-to-pay tracking) | Simulated/logged |

"Real where the platform allows it, schema-accurate and realistic where it doesn't."

## Running it

Requires Python 3.11+, Node 18+, and free-tier API keys for at least one of Gemini / Groq /
OpenRouter (all no-card, no-billing). Langfuse tracing/eval (`LANGFUSE_PUBLIC_KEY`/
`LANGFUSE_SECRET_KEY`/`LANGFUSE_HOST` in `.env`) is optional — the system behaves identically,
with zero code path changes, when unset.

```bash
# 1. Backend setup
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in your free-tier keys — see .env.example for where to get each

# 2. Generate the synthetic case data (Razorpay-realistic reason codes/distributions)
python generate_cases.py

# 3. Run the full batch through the primary architecture (router -> specialists) --
#    this is where the actual agent reasoning happens
python run_batch_multiagent.py --resume

# 4. Serve the API (reads data/revenue_risk_multiagent.db, the primary system's data)
uvicorn app:app --reload

# 5. In a second terminal — the dashboard
cd dashboard
npm install
npm run dev
```

Then open the printed Vite dev server URL (typically `http://localhost:5173`).

`--resume` skips any case that already has a clean (non-error) result recorded, so re-running after a
provider rate-limit interruption only processes what's left — see
[run_batch_multiagent.py](backend/run_batch_multiagent.py)'s module docstring for why this exists.
The original single-agent system is still fully runnable (`python run_batch.py --resume`, writing to
its own separate `data/revenue_risk.db`) if you want to reproduce the prior architecture directly —
see NOVELTY.md's "Migration history."

## Free-tier constraint (stated honestly)

Every LLM call in this project runs on a free tier (Gemini / Groq / OpenRouter) — the project is built
payless, by design (see [CLAUDE.md](CLAUDE.md) if you have access, or ask). Free tiers carry real
requests-per-day quotas a paid tier wouldn't have, and a full batch of ~95 multi-turn cases is enough
volume to hit them. The system is built to degrade gracefully under this constraint rather than hide
it: every case is isolated (one case's failure never loses another's progress), every result is
persisted incrementally, and `--resume` lets a run continue exactly where quota cut it off. The
dashboard's reliability panel reports the real clean-completion rate rather than masking failed
generations as silent gaps.

## What broke before release

Not a hypothetical list — these are real bugs found and fixed during the build, kept here because
"we tested it" means less than showing what testing actually caught.

- **A hardcoded compliance rule was silently wrong.** `guardrails.py`'s NPCI retry-spacing check
  indexed `NPCI_RETRY_SPACING_HOURS[len(attempts_this_cycle)]` instead of `[len(attempts_this_cycle) - 1]`
  — an off-by-one that let a 28-hour retry gap pass a 72-hour minimum check. Caught by a unit test
  asserting the boundary, not by manual review.
- **One provider's schema quirk silently broke 100% of its batch share.** Gemini's function-calling API
  rejects JSON Schema's `["string", "null"]` nullable-union syntax outright. Every Gemini-routed case in
  a batch run failed — invisibly, because the failures looked like ordinary completions until someone
  noticed the completion times were suspiciously instant (0.0s) rather than the multi-second calls a
  real generation takes.
- **An action tier defaulted to the wrong value.** `record_promise_to_pay` was missing from
  `ACTION_TIER_DEFAULTS`, so it silently fell back to `APPROVE_FIRST` instead of the intended
  `AUTONOMOUS`. Found by a unit test asserting the expected tier, not by reading the dispatch table.
- **The system's own reliability tracking had a bug that hid real progress.** `get_cleanly_completed_case_ids()`
  (`db.py`) and `compute_reliability_metrics()` (`metrics.py`) both judged a case's cleanliness against
  its *entire* flat log history — so a case that failed once, days earlier, could never be recognized as
  clean again even after a later attempt fully succeeded. Fixed in both places (found independently in
  each — fixing one alone wasn't enough, since they could silently disagree with each other), now
  covered by a cross-check test pinning the two implementations to the same answer. Quantified cost
  while the bug existed: 42 cases had already succeeded at least once and were needlessly re-run anyway
  — 227 wasted LLM calls against an already-scarce free-tier quota, worst case one payment-failure case
  re-attempted 17 times after its first real success.
- **Free-tier LLM quota is a real, load-bearing constraint, not a footnote.** A batch of ~95 multi-turn
  cases is enough volume to hit daily request caps across all three configured providers simultaneously.
  Multiple mitigations were tried and evaluated honestly against real data rather than assumed to work:
  weighted provider scheduling (evidence-based, based on measured per-provider failure rates), a 2nd
  Groq API key (found to add zero real capacity — both keys resolved to the same Groq organization ID,
  confirmed by inspecting the org id embedded in the provider's own error payloads), and multi-day
  `--resume` accumulation (the strategy that actually worked, moving the clean-case count up over
  several days without any code change once the reliability-tracking bug above was fixed).

## Does this scale beyond the demo batch?

The case agent loop makes real per-case LLM calls, so its throughput is bound by whichever free-tier
provider quota is available at the time — that's an honest external constraint (see above), not
something worth claiming to have "solved."

What's fair to claim: the rest of the pipeline — routing, the guardrail engine, DB persistence, and
metrics computation — is entirely deterministic Python with no LLM dependency, and scales independently
of that constraint. [backend/stress_test.py](backend/stress_test.py) generates a separate synthetic
batch (never touching the real demo data in `data/`) and pushes it through that full non-LLM pipeline:
**1,002 cases in ~200ms total** (routing: 2.8ms, guardrail evaluation + DB writes: 160ms, metrics
computation: 41ms), with a non-degenerate tier distribution across all three tiers. Run it yourself:

```bash
cd backend
python stress_test.py --n 334 --seed 999   # ~1,000 cases, writes to data_stress/ (gitignored)
```

## Key files

| File | What it is |
|---|---|
| [backend/models.py](backend/models.py) | Shared `Case` / `AttemptRecord` / `DecisionLogEntry` schemas |
| [backend/guardrails.py](backend/guardrails.py) | The hardcoded compliance/policy engine — the ONE engine every agent, in both systems, is checked against |
| [backend/pydantic_agents.py](backend/pydantic_agents.py) | **Primary architecture**: router agent + 3 specialist agents + the checker agent, on Pydantic AI, with Langfuse tracing/eval wired in |
| [backend/run_batch_multiagent.py](backend/run_batch_multiagent.py) | Orchestrates the full batch run through the primary architecture |
| [backend/run_checker_retroactive.py](backend/run_checker_retroactive.py) | Runs the checker agent against already-completed real cases, spending quota only on the review itself |
| [backend/run_langfuse_sample.py](backend/run_langfuse_sample.py) | Generates real Langfuse trace/eval volume from a curated real-case sample, without touching the batch DB |
| [backend/custom_case.py](backend/custom_case.py) / [backend/bulk_upload.py](backend/bulk_upload.py) | Live single-case and CSV/XLSX bulk submission, both through the primary architecture |
| [backend/agent_loop.py](backend/agent_loop.py) | The original single-agent tool-calling loop — proven prior art, kept for direct comparison, no longer user-facing |
| [backend/run_batch.py](backend/run_batch.py) | Orchestrates a batch run through the original single-agent system (its own separate DB) |
| [backend/llm_client.py](backend/llm_client.py) | Swappable interface over Gemini/Groq/OpenRouter, used by `agent_loop.py` |
| [backend/generate_cases.py](backend/generate_cases.py) | Synthetic data generator (shared by both systems) |
| [backend/razorpay_client.py](backend/razorpay_client.py) | Real Razorpay Payments/Payment Links API wrapper |
| [backend/stress_test.py](backend/stress_test.py) | Architecture-scale test on a separate large synthetic batch (never touches `data/`) |
| [backend/metrics.py](backend/metrics.py) | Headline/reliability/guardrail-ledger metric computations |
| [backend/app.py](backend/app.py) | FastAPI endpoints the dashboard reads from |
| [dashboard/](dashboard/) | React + Vite frontend |

## Tests

```bash
cd backend
python -m pytest tests/ -v
```

Covers the guardrail engine (NPCI/RBI rule enforcement, action-tier defaults), the
customer-history/promise-to-pay tools, and the resume/reliability-tracking logic that decides which
cases already have a trustworthy result (including a cross-check that the batch-runner's and the
dashboard's clean-case counts agree with each other). Also covers the checker agent's trigger
rule, its bounded retry/escalate behavior, and a direct parity test proving `guardrails.py`
enforcement is byte-identical between the router+specialist system and the original single-agent
one. 88/88 passing. Langfuse is force-disabled for the whole test session
([conftest.py](backend/tests/conftest.py)) so running tests never sends real data to a real
Langfuse project.

## License

Built for the Razorpay AI Buildathon submission window (Aug 24 – Sep 4, 2026). No license file yet —
add one if this repo is reused beyond the submission.
