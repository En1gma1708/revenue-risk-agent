import { Link } from "react-router-dom"
import { PipelineDiagram } from "../components/PipelineDiagram"
import { Reveal } from "../components/Reveal"
import { TopNav } from "../components/TopNav"

function Section({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return (
    <Reveal as="section" className="py-10">
      <p className="text-xs font-medium uppercase tracking-[0.14em]" style={{ color: "var(--color-brand)" }}>
        {eyebrow}
      </p>
      <h2 className="mt-1.5 text-xl font-semibold tracking-tight" style={{ color: "var(--color-ink)" }}>
        {title}
      </h2>
      <div className="mt-5">{children}</div>
    </Reveal>
  )
}

const REASONING_ROWS = [
  "A router agent classifies surface + severity, then hands off to 1 of 3 specialists",
  "Non-deterministic — the same case can take a different path across two runs",
  "The specialist reads case context, attempt history, and customer history to judge",
  "Decides WHAT to try — a retry, a payment link, a mandate request, an escalation",
]

const GUARDRAIL_ROWS = [
  "A pure function over (case, proposed action, attempt history) — no model call",
  "Deterministic — same input always produces the same tier, every time",
  "Runs INSIDE execute_action's handler, not as a system-prompt instruction",
  "One engine, shared identically by the router and all 3 specialists — never duplicated",
]

const PATTERN_AUDIT: { pattern: string; verdict: string; tone: "good" | "bad" | "warn" }[] = [
  { pattern: "Tool use / function calling", verdict: "Real, core — the whole loop is built on it", tone: "good" },
  { pattern: "Orchestrator-worker (multi-agent)", verdict: "Real, core — a router hands off to 1 of 3 verified specialist agents", tone: "good" },
  { pattern: "Reflection", verdict: "Does not fit — the guardrail check is code, not a 2nd LLM critique", tone: "bad" },
  { pattern: "Planning / task decomposition", verdict: "Does not fit — no ambiguous goal to decompose", tone: "bad" },
  { pattern: "Memory / context management", verdict: "Real, but a design detail — attempt history + PTP state", tone: "warn" },
]

const TONE_COLOR: Record<string, string> = {
  good: "var(--color-good)",
  bad: "var(--color-ink-faint)",
  warn: "var(--color-warn)",
}

export default function Architecture() {
  return (
    <div className="min-h-screen px-6 py-8" style={{ background: "var(--color-canvas)" }}>
      <div className="mx-auto max-w-4xl">
        <TopNav />

        <p className="text-xs font-medium uppercase tracking-[0.14em]" style={{ color: "var(--color-brand)" }}>
          System design
        </p>
        <h1 className="mt-1.5 text-2xl font-semibold tracking-tight sm:text-3xl" style={{ color: "var(--color-ink)" }}>
          How a decision actually gets made
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
          A router agent classifies each case and hands it to one of 3 specialists — but every one
          of them, and the router itself, is checked against the exact same layer of ordinary code
          before anything is allowed to happen. Two different kinds of logic share this system, and
          they are never allowed to blur into one another.
        </p>

        <Section eyebrow="The pipeline" title="Five stages, one loop per case">
          <PipelineDiagram />
        </Section>

        <Section eyebrow="The single most important claim in this project" title="Reasoning vs. governance are not the same layer">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div
              className="rounded-[var(--radius-card)] p-5"
              style={{ background: "var(--color-surface)", borderLeft: "3px solid var(--color-warn)" }}
            >
              <p className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: "var(--color-warn)" }}>
                LLM reasoning
              </p>
              <p className="mt-1 text-xs" style={{ color: "var(--color-ink-faint)" }}>
                non-deterministic
              </p>
              <ul className="mt-4 flex flex-col gap-3">
                {REASONING_ROWS.map((r) => (
                  <li key={r} className="text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
                    {r}
                  </li>
                ))}
              </ul>
            </div>
            <div
              className="rounded-[var(--radius-card)] p-5"
              style={{ background: "var(--color-surface)", borderLeft: "3px solid var(--color-brand)" }}
            >
              <p className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: "var(--color-brand)" }}>
                Guardrail engine
              </p>
              <p className="mt-1 text-xs" style={{ color: "var(--color-ink-faint)" }}>
                deterministic, code-enforced
              </p>
              <ul className="mt-4 flex flex-col gap-3">
                {GUARDRAIL_ROWS.map((r) => (
                  <li key={r} className="text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <p className="mt-4 text-xs leading-relaxed" style={{ color: "var(--color-ink-faint)" }}>
            If a rule can be bypassed by rephrasing the prompt, it isn't a guardrail — it's a
            suggestion. Every hard constraint here is enforced unconditionally inside{" "}
            <code className="font-mono">execute_action</code>'s handler, in code the model's tool
            calls pass through whether it "agrees" or not.
          </p>
          <Link
            to="/dashboard"
            className="mt-4 inline-flex items-center gap-1 text-sm font-medium no-underline"
            style={{ color: "var(--color-brand)" }}
          >
            See every rule, and how often it's actually fired, in the live guardrail ledger →
          </Link>
        </Section>

        <Section eyebrow="Why one compliance core, not three" title="Specialization is real. Unification is where it counts.">
          <p className="max-w-2xl text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
            No competitor — not Razorpay's own Agent Studio, not Stripe, not Chargebee — runs one
            shared decision policy across payment failures, checkout abandonment, and receivables.
            Razorpay's own "agents" (Subscription Recovery, Abandoned Cart Conversion) are
            confirmed, separate, disconnected products with no documented handoff between them —
            not a genuine multi-agent system in the sense a real router-and-specialists
            architecture is. This project runs 3 specialist agents that genuinely hand off to one
            another, share the same tool set, and are checked against the exact same guardrail
            engine, so a decline reason code, a stalled checkout, and a broken payment promise are
            all evaluated under one compliance boundary — not three drifting copies of it.
          </p>
          <div className="mt-4 rounded-[var(--radius-card)] p-5" style={{ background: "var(--color-surface)" }}>
            <p className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
              How this got here: proven single-agent first, then a deliberate, gated migration
            </p>
            <p className="mt-1.5 text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
              The system started as one agent handling all 3 surfaces — a real, defensible choice
              at the time, since a separate worker per surface risked the compliance engine
              drifting across copies. After direct pushback on that reasoning, the alternative was
              built and proven rather than just argued: a real router-classifies →
              hands-off-to-3-specialists system (Pydantic AI), verified through explicit gates —
              unit tests proving guardrail behavior is byte-identical to the original across both
              systems, then a real batch run validated the same way every other claim in this
              project is (<code className="font-mono">compute_reliability_metrics</code>, never a
              raw summary). It matched, then passed, the original system's clean-case count under
              the same real quota constraints before being adopted as the primary architecture —
              the original single-agent loop stays in the repo as proven prior art, not deleted.
            </p>
          </div>
        </Section>

        <Section eyebrow="Agentic pattern audit" title="Which of the 5 standard patterns actually apply">
          <p className="mb-4 max-w-2xl text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
            Evaluated against the 5 patterns commonly cited as markers of "real" agentic AI before
            adding any complexity — the goal was 1–2 patterns genuinely load-bearing here, not 5
            checked off for coverage.
          </p>
          <div className="flex flex-col gap-2">
            {PATTERN_AUDIT.map((row, i) => (
              <Reveal
                key={row.pattern}
                delayMs={i * 70}
                className="flex flex-col gap-1 rounded-[var(--radius-card)] p-4 sm:flex-row sm:items-center sm:justify-between sm:gap-4"
                style={{ background: "var(--color-surface)" }}
              >
                <span className="text-sm font-medium" style={{ color: "var(--color-ink)" }}>
                  {row.pattern}
                </span>
                <span className="text-xs sm:text-right" style={{ color: TONE_COLOR[row.tone] }}>
                  {row.verdict}
                </span>
              </Reveal>
            ))}
          </div>
        </Section>

        <footer className="flex items-center justify-between border-t py-8 text-xs" style={{ borderColor: "var(--color-border-subtle)", color: "var(--color-ink-faint)" }}>
          <Link to="/what-broke" className="font-medium no-underline" style={{ color: "var(--color-brand)" }}>
            What broke before release →
          </Link>
          <Link to="/dashboard" className="font-medium no-underline" style={{ color: "var(--color-brand)" }}>
            Open the live batch dashboard →
          </Link>
        </footer>
      </div>
    </div>
  )
}
