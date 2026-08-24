# PRD — AI Revenue Recovery Agent

**Program:** Razorpay AI Buildathon — Track 3, "AI Revenue Recovery"
**Owner:** sahilsaisharma@gmail.com
**Deadline:** Sept 4, 2026 (build window: Aug 24 – Sep 4, 2026)
**Submission format:** GitHub repo that runs + 5-minute demo video. No live deployment required.

---

## 1. Problem statement (from the brief, verbatim)

> Find revenue that's slipping away and win it back. Build an agent that detects revenue at risk,
> determines the right intervention, and executes a bounded recovery workflow: from payment failures
> and checkout abandonment to overdue receivables.
>
> Why now: Revenue loss rarely happens in one clean step. A payment degrades, a checkout gets
> abandoned, a subscription fails, or an invoice goes overdue. AI can now close the loop from detecting
> the problem to diagnosing it, choosing the right intervention, and recovering the money.
>
> The bar: Don't just identify the problem. Show measured money recovered across a batch, with
> compliant escalation, stopping rules, and an audit trail.

## 2. Grading checklist (map every deliverable back to this)

| Bar requirement | How this project satisfies it |
|---|---|
| Detects revenue at risk | Synthetic + real event ingestion across 3 surfaces, routed by a plain-code classifier |
| Determines the right intervention | A genuine Claude/LLM tool-calling agent loop reasons over case context at runtime and decides — not a hardcoded if/else tree |
| Executes a bounded recovery workflow | `execute_action` only runs after passing the hardcoded guardrail engine |
| Measured money recovered across a batch | Dashboard headline metric computed from actual case outcomes: "₹X at risk → ₹Y recovered / ₹Z escalated / ₹W blocked by policy" |
| Compliant escalation | NPCI (UPI Autopay retry caps/spacing/hours) and RBI (pre-debit notice, AFA threshold) rules encoded as hard-coded guardrails |
| Stopping rules | Hard iteration cap on the agent loop; HARD_STOP guardrail tier; terminal states (halted subscription, exhausted attempts) |
| Audit trail | One `DecisionLogEntry` row per real agent action: what it observed, decided, why, guardrail result, outcome — replayable per case in the dashboard |

## 3. Target "user" of the system

Not a consumer-facing product — this is an internal ops agent a merchant's finance/growth team would
run. The dashboard is the artifact a judge (playing that internal stakeholder) evaluates.

## 4. Scope: the three surfaces (unified under one policy)

This unification — one shared agent policy applied identically across all three surfaces — is the
project's core novelty claim. See [NOVELTY.md](NOVELTY.md) for the full argument.

1. **Payment failures / degradation** (real Razorpay test-mode data where possible)
   Real Razorpay reason codes (card: `insufficient_funds`, `card_expired`, `authentication_failed`, etc;
   UPI: `invalid_vpa`, `payment_collect_request_expired`, etc.) drive a hard-decline/soft-decline
   classification. Subscriptions/mandates walk Razorpay's real `pending → halted` state machine.
2. **Checkout abandonment** (synthetic — Razorpay test mode doesn't expose this)
   Cart value, abandonment stage (OTP entry / instrument select / bank redirect / review), time since
   abandonment drive urgency and channel choice for a nudge.
3. **Overdue B2B receivables** (synthetic — Razorpay test mode doesn't expose this)
   Days overdue, amount, and a **Promise-to-Pay (PTP)** tracking mechanic: debtor commits to
   `{amount, date, channel}`, agent checks back on the promised date, escalates if missed.

## 5. Non-goals (explicitly out of scope)

- Live deployment / public hosting — not required by the brief.
- Real money movement of any kind — everything runs against Razorpay **test mode** only.
- Real SMS/WhatsApp/voice delivery — these channels are visibly simulated/logged, not actually sent,
  except where a real Razorpay API exists (Payment Links).
- A generalized multi-tenant product — this is a single-merchant, single-batch demo system.
- Any offense-capable or destructive tooling.

## 6. Agent architecture

See [CLAUDE.md](CLAUDE.md) "Architecture at a glance" for the diagram. Full design rationale (why a
manual loop over the raw LLM API rather than a framework, why routing is code not an LLM call, the full
tool surface, and the guardrail schema) lives in the project's internal design notes — key points:

- **Routing** (surface + initial severity) is deterministic Python, not an LLM call — it's structural,
  not a judgment call.
- **The case agent** is one real tool-calling loop per case: the model chooses which tools to call and
  in what order, based on what it learns, up to a hard `MAX_ITERATIONS` cap.
- **Tool surface** (7 tools): `get_case_context`, `check_attempt_history`, `check_policy_guardrails`
  (advisory), `propose_intervention`, `execute_action` (gated), `escalate_to_human`, `log_decision`.
- **Guardrails** are pure functions over `(case, proposed_action, attempt_history)`, evaluated inside
  `execute_action`'s handler — never as a prompt instruction the model could be talked out of.
- **Action tiers**: `AUTONOMOUS` / `LOG_ONLY` / `APPROVE_FIRST` / `HARD_STOP`, a property of action type
  and guardrail outcome, not something the model self-assigns.

## 7. Data model (summary — full schema in `models.py` once built)

- `Case` — shared envelope (`case_id`, `surface`, `amount_inr`, `status`, `severity_score`) +
  surface-specific details blob (`PaymentFailureDetails` / `CheckoutAbandonmentDetails` /
  `ReceivableDetails`).
- `AttemptRecord` — append-only history per case, needed for NPCI spacing/cap checks.
- `DecisionLogEntry` — the audit trail unit: observed context, decision, reasoning, guardrail check
  result, action taken, tier, outcome, amount recovered.
- Synthetic data generator produces ~50-100 cases across the three surfaces with realistic
  distributions (see `generate_cases.py` once built), seeded for reproducibility.

## 8. Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11 + FastAPI |
| Data store | SQLite (single file, no server — this is a demo/batch system, not production) |
| Agent/LLM | Manual tool-calling loop via a swappable `LLMClient`; provider TBD pending free-tier access confirmation (see CLAUDE.md) |
| Frontend | React + Vite + Tailwind CSS + shadcn/ui (fast, clean-looking dashboard components without hand-rolling every card/table/badge) |
| Recovery-action integration | Razorpay Payment Links API (real, test mode) for payment-failure recovery actions; Razorpay Payments + Subscriptions APIs for real test-mode failure/mandate-state data |

## 9. Dashboard requirements

Must be simple, visually clean, and legible to a non-technical judge inside a 5-minute video:

1. Headline metrics bar — "₹X at risk → ₹Y recovered / ₹Z escalated / ₹W blocked by policy," first
   thing visible.
2. Batch table — one row per case, filterable by surface (this is where "one policy, three surfaces"
   becomes visually obvious).
3. Per-case trace view — chronological decision timeline (observed → reasoning → guardrail check →
   action → outcome).
4. Guardrail ledger panel — the hardcoded rules, with a live fired-count per rule this batch.

## 10. Build plan

10 build days (Aug 24 – Sep 3), submission Sep 4. Full day-by-day breakdown and the cut-list ordering
(what to drop first if behind schedule, and the non-negotiable floor) is tracked live in
[DEVLOG.md](DEVLOG.md) so it stays accurate as the actual build diverges from the original estimate.

Non-negotiable floor: real agent loop + guardrail engine + audit trail + payment-failure surface fully
working end-to-end. This alone satisfies every item in the grading checklist (§2).

## 11. Open risks

Tracked live in DEVLOG.md as they resolve. At project start:
- LLM provider/cost path unresolved (payless constraint — see CLAUDE.md).
- Synthetic-vs-real data disclosure needs to be explicit in the README/video, not discovered by a judge.
- PTP "check back on promised date" needs pre-dated synthetic timestamps relative to a fixed demo
  "today," not a real wait — avoids needing a simulated-clock UI control.
