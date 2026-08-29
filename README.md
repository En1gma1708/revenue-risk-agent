# AI Revenue Recovery Agent

**Razorpay AI Buildathon — Track 3: "AI Revenue Recovery"**

A single Claude-style LLM tool-calling agent that watches three different revenue-leak surfaces —
failed payments, abandoned checkouts, and overdue B2B receivables — reasons over each case's context
at runtime using the same decision loop, and either acts autonomously, asks a human for approval, or
hard-stops, inside compliance guardrails it cannot override. Every real action is logged to a full
audit trail: what it saw, what it decided, and why.

Full product framing, novelty argument, and metrics methodology: [PRD.md](PRD.md) ·
[NOVELTY.md](NOVELTY.md) · [METRICS.md](METRICS.md).

## Why one agent across three surfaces, not three separate agents

Revenue loss rarely happens in one clean step, and it doesn't happen on only one surface — a payment
degrades, a checkout gets abandoned, an invoice goes overdue, often for the same underlying customer.
Most platforms (including Razorpay's own Agent Studio) ship a separate purpose-built agent per surface.
This project instead runs **one shared policy core** — the same tool set, the same guardrail engine,
the same reasoning loop — applied identically across all three. See [NOVELTY.md](NOVELTY.md) for the
full argument, including which of the standard "agentic patterns" were deliberately not used and why.

## Architecture

```
Synthetic + real event data (3 surfaces)
        │
        ▼
Stage 0 — Router (plain Python, NOT an LLM call)
   classifies surface + computes initial severity from the event shape
        │
        ▼
Stage 1 — Case Agent (the real agent loop, one per case)
   LLM + tool-calling loop, MAX_ITERATIONS hard cap
   tools: get_case_context, check_attempt_history, check_customer_history,
          check_policy_guardrails, propose_intervention, record_promise_to_pay,
          execute_action, escalate_to_human, log_decision
        │
        ▼
Guardrail engine (guardrails.py) — pure functions, data-driven rule table
   runs INSIDE execute_action's handler, not as a prompt instruction
   HARD_STOP / APPROVE_FIRST / AUTONOMOUS / LOG_ONLY tiers
        │
        ▼
Audit trail (DecisionLogEntry rows, SQLite) — one row per real action taken
        │
        ▼
Dashboard (React + Vite, served against FastAPI) — batch table, per-case trace
   timeline, guardrail ledger, headline recovery metrics
```

**The intervention decision for each case comes from the model reasoning over live tool results**, not
a hardcoded if/else tree that only uses the LLM to classify text — see [agent_loop.py](backend/agent_loop.py).
**Guardrails are the opposite: hardcoded, deterministic, and enforced in code the model's tool calls
pass through** — see [guardrails.py](backend/guardrails.py). Real regulatory grounding: NPCI UPI
Autopay rules (4-attempt cap, spacing, non-peak-hour windows) and RBI rules (₹15k AFA threshold, 24h
pre-debit notice).

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
OpenRouter (all no-card, no-billing).

```bash
# 1. Backend setup
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in your free-tier keys — see .env.example for where to get each

# 2. Generate the synthetic case data (Razorpay-realistic reason codes/distributions)
python generate_cases.py

# 3. Run the full batch — this is where the actual agent reasoning happens
python run_batch.py --providers gemini,groq,openrouter --resume

# 4. Serve the API
uvicorn app:app --reload

# 5. In a second terminal — the dashboard
cd dashboard
npm install
npm run dev
```

Then open the printed Vite dev server URL (typically `http://localhost:5173`).

`--resume` skips any case that already has a clean (non-error) result recorded, so re-running after a
provider rate-limit interruption only processes what's left — see [run_batch.py](backend/run_batch.py)'s
module docstring for why this exists.

## Free-tier constraint (stated honestly)

Every LLM call in this project runs on a free tier (Gemini / Groq / OpenRouter) — the project is built
payless, by design (see [CLAUDE.md](CLAUDE.md) if you have access, or ask). Free tiers carry real
requests-per-day quotas a paid tier wouldn't have, and a full batch of ~95 multi-turn cases is enough
volume to hit them. The system is built to degrade gracefully under this constraint rather than hide
it: every case is isolated (one case's failure never loses another's progress), every result is
persisted incrementally, and `--resume` lets a run continue exactly where quota cut it off. The
dashboard's reliability panel reports the real clean-completion rate rather than masking failed
generations as silent gaps.

## Key files

| File | What it is |
|---|---|
| [backend/models.py](backend/models.py) | Shared `Case` / `AttemptRecord` / `DecisionLogEntry` schemas |
| [backend/guardrails.py](backend/guardrails.py) | The hardcoded compliance/policy engine |
| [backend/agent_loop.py](backend/agent_loop.py) | The manual LLM tool-calling loop + tool dispatch table |
| [backend/llm_client.py](backend/llm_client.py) | Swappable interface over Gemini/Groq/OpenRouter |
| [backend/generate_cases.py](backend/generate_cases.py) | Synthetic data generator |
| [backend/razorpay_client.py](backend/razorpay_client.py) | Real Razorpay Payments/Payment Links API wrapper |
| [backend/run_batch.py](backend/run_batch.py) | Orchestrates the full batch run |
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
dashboard's clean-case counts agree with each other).

## License

Built for the Razorpay AI Buildathon submission window (Aug 24 – Sep 4, 2026). No license file yet —
add one if this repo is reused beyond the submission.
