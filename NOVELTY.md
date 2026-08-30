# NOVELTY.md — why this project, not the obvious version of it

## The core claim

> Razorpay's own Agent Studio ships separate agents per surface (Subscription Recovery, Abandoned Cart
> Conversion) and its Failed Payment Recovery product retries all failures uniformly without root-cause
> diagnosis; Stripe Smart Retries and Chargebee dunning are similarly single-surface, rule-driven retry
> schedulers layered with ML timing, not reasoning agents. This project instead runs **one policy core**
> — a single tool-calling agent, the same tool set, the same hard-coded compliance guardrails, and the
> same audit trail — across payment failures, checkout abandonment, and overdue B2B receivables. The
> unification is in the ENFORCEMENT and DECISION-MAKING MACHINERY, not in flattening the surfaces into
> indistinguishable data: a decline reason code, a stalled checkout, and a broken payment promise are
> still reasoned about on their own terms (decline classification and NPCI retry state for one, cart
> stage and time-since-abandonment for another, days-overdue and promise-to-pay history for the third)
> — what's shared is that all three get routed through the SAME policy core rather than three
> disconnected systems, each free to interpret "risky" and "compliant" differently. This is a
> deliberate, defensible engineering choice, not a proven-superior one — a reasonable team could build
> three separate agents instead and be equally justified; the real, citable gap this fills is Razorpay's
> own product limitation (no root-cause diagnosis, stated on their own blog) and the brief's own framing
> of revenue loss as a cross-surface problem, not a claim that unification is objectively better than
> specialization in the abstract.
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
| **Tool use / function calling** | **Real, core** | The case agent needs data it cannot know from training — this specific case's history, whether a specific proposed action passes policy. The whole case-loop architecture (§ agent loop design) is built on the model choosing which of 7 tools to call, in what order, based on what it learns. This is the centerpiece, not decoration. |
| **Reflection** (generator produces, evaluator critiques, loop until it passes) | **Does not fit** | Reflection needs a natural iterative output worth refining across multiple passes with a real error signal. Our `propose_intervention` → guardrail check → `execute_action` flow looks reflection-adjacent but isn't: the guardrail check is a **deterministic code check against fixed rules**, not a second LLM pass critiquing output quality. The model doesn't know NPCI's attempt count better on a re-read of the same context — looping self-critique here would risk compounding hallucination with no real signal driving improvement, not reduce it. |
| **Planning / task decomposition** | **Does not fit** | Planning fits an unknown, ambiguous multi-step goal (e.g. "book me a trip"). A single case's action sequence is short and convergent — gather context, check history, check policy, decide, act, log — chosen dynamically by the model, but not a decomposition of an ambiguous goal into an unknown plan. There's no genuine sub-goal structure here to plan against. |
| **Orchestrator-worker (multi-agent delegation)** | **Not used in the main system — but built and proven as a separate, working comparison artifact, not just reasoned about on paper** | Considered seriously, twice: do payment-failure, checkout-abandonment, and receivables cases warrant separate worker agents, each specialized per surface? The tool surface already returns surface-specific context per case, so a single agent is already effectively "specialized" by the data it's handed, without needing separate model instances or prompts per surface. This project's core claim is that **one shared policy generalizes across all three surfaces** — building three worker agents INTO the main submission would directly undercut that claim. But after direct, repeated pushback on this exact point (see DEVLOG.md 2026-08-30), the premise was fact-checked rather than just defended: Razorpay's own "agents" (Subscription Recovery, Abandoned Cart Conversion) turned out to be separate, disconnected PRODUCTS with no documented handoff or coordination — not a genuine multi-agent system either, which reframes the real comparison. A genuine router-classifies -> hands-off-to-3-specialists architecture was then built as a standalone, additive proof-of-concept (`backend/pydantic_agents.py`, on the Pydantic AI framework) and verified live: the router correctly classified a case and handed off to a specialist, which proposed a compliant action through the exact same guardrail engine the main system uses. This demonstrates the rejection was a genuine engineering tradeoff decision (compliance-consistency and cross-surface customer-history visibility vs. specialization), backed by a working artifact proving the alternative was understood and buildable — not "we didn't have time" or "we didn't think of it." |
| **Memory / context management** | **Real, but correctly a design detail, not a headline feature** | Two genuine instances: (1) within a case's loop, the message history accumulates across tool-calling turns — standard loop-correctness territory, not a separate architecture; (2) across a case's lifecycle, `AttemptRecord` history and `PromiseToPay` state are exactly "state deliberately held so it doesn't need to be re-derived each time" (e.g. the agent reads prior attempt count rather than re-inferring whether the NPCI cap was hit). Both are folded into the existing data model and loop design rather than standing alone, matching how this pattern usually shows up in real systems. |

**Net result: 1 pattern is the true centerpiece (tool use), 1 is a real supporting detail (memory/state),
3 are deliberately absent from the main submission.** Orchestrator-worker is the most interesting of
those three to discuss — not because it was skipped, but because it's the one pattern that was
seriously reconsidered under real pushback, fact-checked against what competitors actually do, and
built as a working, verified comparison artifact rather than settled by argument alone (see the table
row above, and DEVLOG.md 2026-08-30). That's a stronger interview answer than either "we used every
pattern" or "we didn't consider it" — it's "we built both and can show you why we chose the one we
shipped."

## Update this file as the build evolves

If scope gets cut (see DEVLOG.md cut-list), update the novelty framing honestly rather than leaving
stale claims — e.g. if checkout abandonment gets dropped, the pitch becomes "architecture is
surface-agnostic by design; demonstrated end-to-end on payment failures + receivables, with the third
surface's schema and guardrail hooks already in place" rather than silently narrowing scope
undocumented.
