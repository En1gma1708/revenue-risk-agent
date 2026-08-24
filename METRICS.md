# METRICS.md — what we measure, and why

The brief's bar is explicit: *"Show measured money recovered across a batch, with compliant
escalation, stopping rules, and an audit trail."* Every metric below maps to one clause of that
sentence. Nothing here is vanity — if a number doesn't answer a grading-checklist question (see
PRD.md §2), it doesn't belong on the dashboard.

## Headline batch metrics (dashboard top bar)

| Metric | Definition | Answers |
|---|---|---|
| **₹ at risk** | Sum of `amount_inr` across all cases in the batch at start | Did we detect revenue at risk? |
| **₹ recovered** | Sum of `amount_recovered_inr` across `DecisionLogEntry` rows where `outcome` indicates success (payment succeeded, cart converted, invoice paid / PTP kept) | "Measured money recovered across a batch" — the brief's literal ask |
| **₹ escalated** | Sum of `amount_inr` for cases currently sitting in `APPROVE_FIRST` (queued, not yet actioned) | Compliant escalation — showing the agent correctly deferred to a human rather than over-acting |
| **₹ blocked by policy** | Sum of `amount_inr` for cases where the agent's proposed action was vetoed by a `HARD_STOP` guardrail | Stopping rules — proof the guardrail engine actually fires, not just exists |
| **Recovery rate** | ₹ recovered / ₹ at risk | Standalone comparability number — useful against public dunning-industry benchmarks (~15-25% uplift over static schedules is the typical claimed range; cite honestly, don't inflate) |

## Per-surface breakdown

Same five metrics, filtered by `surface` (`payment_failure` / `checkout_abandonment` /
`overdue_receivable`). This is what makes "one policy, three surfaces" a checkable claim instead of a
slogan — the same metric definitions, computed identically, applied to structurally different case
types.

## Guardrail ledger metrics

For each rule in `guardrails.py`'s `GUARDRAILS` table: a fired-count this batch, and which cases it
fired on (linked to their trace view). This is the artifact that proves compliance rules aren't
decorative — if a rule never fires across a realistic batch, either the synthetic data isn't
representative enough, or the rule is dead code. Both are worth noticing before the demo, not after.

## Agent-quality metrics (secondary, for the DEVLOG / interview narrative, not necessarily the dashboard)

- **Iterations per case** (mean / max) — sanity-checks the loop isn't wastefully calling tools; also
  flags if `MAX_ITERATIONS` is being hit often (a sign of a genuinely hard case, a prompt problem, or a
  tool returning unhelpful data).
- **Guardrail veto rate** — % of `propose_intervention` calls that failed the post-check on first try.
  Non-zero is expected and good (proves the model is exploring real options, not just rubber-stamping
  something pre-approved) — near-zero is worth being suspicious of, not proud of, since it could mean
  the agent is being overly conservative or the guardrails are too permissive to ever bind.
- **Cost / latency per case** — token usage and wall-clock time per case loop. Relevant to the "why
  Sonnet not Opus" / "why a cheap router before the expensive loop" engineering-judgment story for the
  panel interview, not a judging-bar requirement itself.

## Honesty rules for this file

- Never report a number without also being able to show the underlying `DecisionLogEntry` rows that
  produced it — every headline metric must be a live query over real log data from the batch that was
  actually run, not a hand-picked or hardcoded figure.
- If a metric looks too good (e.g. 100% recovery rate), treat that as a bug/data-generation problem to
  investigate, not a result to keep — a batch with zero blocked cases and zero escalations means the
  guardrails and the "bounded" story have nothing to point to on camera.
- Curate the demo batch's random seed (see PRD.md §11 / generate_cases.py) so it reliably contains at
  least one clean recovery, one HARD_STOP block, and one APPROVE_FIRST escalation — the video needs all
  three visible, and leaving this to chance risks a demo run that only shows the easy path.

## Update cadence

Update this file if a new metric gets added to the dashboard, or if a metric definition changes (e.g.
if "recovered" comes to include partial recoveries). Keep definitions precise enough that DEVLOG.md
entries about specific numbers stay unambiguous later.
