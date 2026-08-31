# NOVELTY.md — why this project, not the obvious version of it

## The core claim

> Razorpay's own Agent Studio ships separate agents per surface (Subscription Recovery, Abandoned Cart
> Conversion) — confirmed to be disconnected PRODUCTS with no documented handoff between them, not a
> genuine multi-agent system — and its Failed Payment Recovery product retries all failures uniformly
> without root-cause diagnosis; Stripe Smart Retries and Chargebee dunning are similarly single-surface,
> rule-driven retry schedulers layered with ML timing, not reasoning agents. This project runs **one
> compliance core** — a router agent that classifies each case and hands it to one of 3 real surface
> specialists (payment failures, checkout abandonment, overdue B2B receivables), all sharing the same
> tool set, the same hard-coded compliance guardrails, and the same audit trail. The unification is in
> the ENFORCEMENT and DECISION-MAKING MACHINERY, not in flattening the surfaces into indistinguishable
> data: a decline reason code, a stalled checkout, and a broken payment promise are still reasoned about
> on their own terms by a specialist that knows that surface (decline classification and NPCI retry
> state for one, cart stage and time-since-abandonment for another, days-overdue and promise-to-pay
> history for the third) — what's shared is that all three specialists, and the router itself, are
> checked against the SAME compliance core rather than three disconnected systems each free to interpret
> "risky" and "compliant" differently. This started as a single unified agent (a real, defensible choice
> at the time — see "Migration history" below); the router+specialist system was built and validated
> afterward, specifically to prove the specialization-vs-unification tradeoff wasn't settled by argument
> alone.
> The differentiation isn't "we added an LLM" — every competitor already has ML somewhere in their
> pipeline — it's that the **decision of what to do** is made at runtime by the model reasoning over
> case-specific tool results, not selected from a pre-built decision tree, while the compliance boundary
> (NPCI retry caps, RBI notice windows) is enforced in code the model cannot talk its way around.

## Why this matters for a Razorpay panel specifically

This is Razorpay's own hiring buildathon. They already ship close analogues to the obvious
interpretations of this track:

- **Razorpay Agent Studio** — pre-built agents including Subscription Recovery and Abandoned Cart
  Conversion, built on the Anthropic Claude Agent SDK.
- **Razorpay Failed Payment Recovery** — auto-resends payment links on failure via WhatsApp/email/SMS,
  claiming ~20% recovery uplift, but explicitly treats all failures uniformly with no root-cause
  diagnosis (their own blog post states this gap).

A project that reimplements "detect failure → resend payment link" will read, to this specific judging
panel, as a weaker copy of their own shipped product. The differentiation has to live somewhere they
haven't already built:

1. **Unification across surfaces.** No competitor — not Razorpay's own agents, not Stripe, not
   Chargebee — runs one shared decision policy across payment failures, checkout abandonment, and
   receivables. Each treats revenue loss as siloed by type. The brief's own "why now" language
   ("revenue loss rarely happens in one clean step") is explicitly gesturing at this gap.
2. **Root-cause-aware intervention, not uniform retry.** Razorpay's own blog names "no root-cause
   diagnosis" as their current product's limitation. This project's hard/soft decline classification,
   derived from Razorpay's real reason-code taxonomy, is a direct, citable answer to that named gap.
3. **Guardrails as code, not prompt.** NPCI's UPI Autopay rules (4-attempt cap, T+24h/T+72h/T+168h
   spacing, non-peak-hour windows) and RBI's pre-debit notice rule are real regulatory constraints,
   encoded as deterministic functions the agent's chosen action is checked against — not instructions
   the model is asked to follow. This is a concrete, defensible answer to "compliant escalation,
   stopping rules" that goes beyond "we told the LLM to be careful."

## What would make this NOT novel (things to avoid)

- Building only the payment-failure surface and calling it "unified" — unification is only a real claim
  if at least two structurally different surfaces share the same policy core and guardrail engine in
  the actual code, not just in the pitch.
- Letting the LLM's system prompt be the only place compliance rules live — if a guardrail can be
  bypassed by rephrasing the prompt, it isn't a guardrail, it's a suggestion. Every hard constraint must
  be enforced in `execute_action`'s handler, in code, unconditionally.
- Blending real and synthetic data without labeling which is which — silently presenting synthetic
  abandonment/receivables data as if it came from Razorpay's platform would be a credibility risk if a
  judge probes it. State plainly which surfaces used real test-mode API data and which are synthetic
  (and why — Razorpay's test mode doesn't expose abandonment/receivables data, so this isn't a shortcut,
  it's a platform constraint).

## Agentic pattern audit — which of the 5 "production agentic AI" patterns actually apply

Before adding any agentic complexity, we evaluated this project against the 5 patterns commonly cited
as markers of "real" agentic AI: tool use/function calling, reflection, planning/task decomposition,
orchestrator-worker multi-agent delegation, and memory/context management. The goal was 1-2 patterns
that are genuinely load-bearing here, not 5 checked off for coverage — a system that uses a pattern
because the task needs it is a stronger interview answer than one that uses a pattern because it's
trendy.

| Pattern | Verdict | Why |
|---|---|---|
| **Tool use / function calling** | **Real, core** | Every case needs data no model knows from training — this specific case's history, whether a specific proposed action passes policy. The whole architecture is built on the model (router and specialist alike) choosing which of its tools to call, in what order, based on what it learns. This is the centerpiece, not decoration. |
| **Orchestrator-worker (multi-agent delegation)** | **Real, core — the primary architecture, not a comparison artifact** | Started as a single unified agent (a real, defensible choice — a shared policy is trivially consistent when there's only one implementation of it). After direct, repeated pushback ("if they question me on why I didn't do specialized agents + genuine orchestrator, is that not genuinely more impressive?" — see DEVLOG.md 2026-08-30), the premise was fact-checked rather than just defended: Razorpay's own "agents" (Subscription Recovery, Abandoned Cart Conversion) turned out to be separate, disconnected PRODUCTS with no documented handoff or coordination — not a genuine multi-agent system either, which reframed the real comparison. A genuine router-classifies → hands-off-to-3-specialists architecture (`backend/pydantic_agents.py`, Pydantic AI) was then built, proven through explicit gates (unit tests pinning guardrail behavior byte-identical to the original system, then a real batch run validated with `compute_reliability_metrics`, matching and then exceeding the original's clean-case count under the same real quota constraints), and adopted as the primary architecture. The original single-agent loop (`agent_loop.py`) stays in the repo as proven prior art. |
| **Reflection** (generator produces, evaluator critiques, loop until it passes) | **Does not fit as headline Reflection — one narrow retry mechanism exists, honestly distinguished below** | Reflection needs a natural iterative output worth refining across multiple passes with a real error signal. Our `propose_intervention` → guardrail check → `execute_action` flow looks reflection-adjacent but isn't: the guardrail check is a **deterministic code check against fixed rules**, not a second LLM pass critiquing output quality. The model doesn't know NPCI's attempt count better on a re-read of the same context — looping self-critique here would risk compounding hallucination with no real signal driving improvement, not reduce it. **A separate, narrower thing was added 2026-08-30** (`run_case_via_orchestrator`, `pydantic_agents.py`): if the router never calls `hand_off_to_specialist` (or repeatedly names an invalid surface), it's retried once with an explicit nudge before the case is escalated to a human via a real `DecisionLogEntry` — no silent default to a guessed specialist. This is a **retry pattern, not Reflection**: it detects a missing/invalid TOOL CALL and retries the call, not the model critiquing its own completed output and revising it. Calling it Reflection would be overclaiming a pattern this project doesn't actually implement. |
| **Planning / task decomposition** | **Does not fit** | Planning fits an unknown, ambiguous multi-step goal (e.g. "book me a trip"). A single case's action sequence is short and convergent — gather context, check history, check policy, decide, act, log — chosen dynamically by the specialist, but not a decomposition of an ambiguous goal into an unknown plan. There's no genuine sub-goal structure here to plan against. |
| **Memory / context management** | **Real, but correctly a design detail, not a headline feature** | Two genuine instances: (1) within a case's loop, the message history accumulates across tool-calling turns — standard loop-correctness territory, not a separate architecture; (2) across a case's lifecycle, `AttemptRecord` history and `PromiseToPay` state are exactly "state deliberately held so it doesn't need to be re-derived each time" (e.g. the specialist reads prior attempt count rather than re-inferring whether the NPCI cap was hit). Both are folded into the existing data model and loop design rather than standing alone, matching how this pattern usually shows up in real systems. |

**Net result: 2 patterns are genuinely load-bearing (tool use, orchestrator-worker), 1 is a real
supporting detail (memory/state), 2 are deliberately absent (planning; Reflection proper — though a
narrower retry-on-missing-handoff mechanism exists and is explicitly NOT the same thing, see above).**
Orchestrator-worker's own history is the
most interesting thing to walk through in an interview — not "we used every pattern" or "we didn't
consider it," but "we shipped one architecture, took real pushback on it seriously, built and gated the
alternative properly instead of just arguing for it, and switched once it was actually proven — here's
the DEVLOG history of every gate it had to pass." That's a stronger answer than defending a single
unchanged decision would have been.

## Migration history: single-agent → router + specialists

Documented plainly because a project that quietly rewrote its own headline claim without saying so
would be a credibility risk if a judge noticed. The system shipped its first working version as one
unified agent handling all 3 surfaces (`backend/agent_loop.py`) — proven end-to-end, including the
PMT-0002 guardrail-blocks-twice-then-converges trace this project used as its centerpiece demo case for
several days. After real, repeated pushback questioning that choice, the alternative was built and
proven rather than argued: `backend/pydantic_agents.py`, a genuine router-classifies →
hands-off-to-one-of-3-specialists system on the Pydantic AI framework, reusing the exact same
`guardrails.py` engine so compliance logic is never duplicated per agent. It passed 4 explicit gates
(see `next_steps_multiagent_migration.md`, DEVLOG.md's 2026-08-30 entries) — unit test coverage
including a direct parity test proving guardrail enforcement is identical across both systems, real
batch infrastructure, a validated batch run matching and then exceeding the original system's clean-case
count under the same live quota constraints — before being adopted as the primary, user-facing
architecture. `agent_loop.py` remains in the repo, untouched, as documented prior art: the first proof
that the compliance-as-code claim works, and the baseline the second system had to match before earning
the switch.

## Update this file as the build evolves

If scope gets cut (see DEVLOG.md cut-list), update the novelty framing honestly rather than leaving
stale claims — e.g. if checkout abandonment gets dropped, the pitch becomes "architecture is
surface-agnostic by design; demonstrated end-to-end on payment failures + receivables, with the third
surface's schema and guardrail hooks already in place" rather than silently narrowing scope
undocumented.
