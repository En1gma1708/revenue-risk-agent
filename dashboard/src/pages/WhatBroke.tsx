import { Link } from "react-router-dom"
import { TopNav } from "../components/TopNav"

interface Bug {
  title: string
  broke: string
  found: string
  fix: string
}

const BUGS: Bug[] = [
  {
    title: "A hardcoded compliance rule was silently wrong",
    broke:
      "guardrails.py's NPCI retry-spacing check indexed NPCI_RETRY_SPACING_HOURS[len(attempts_this_cycle)] " +
      "instead of [len(attempts_this_cycle) - 1] — an off-by-one that let a 28-hour retry gap pass a " +
      "72-hour minimum check.",
    found: "A unit test asserting the exact boundary, not manual review of the rule table.",
    fix: "Corrected the index. Covered permanently by the same test that caught it.",
  },
  {
    title: "One provider's schema quirk broke 100% of its batch share",
    broke:
      "Gemini's function-calling API rejects JSON Schema's [\"string\", \"null\"] nullable-union syntax " +
      "outright. Every Gemini-routed case in a batch run failed.",
    found:
      "Invisibly at first — the failures looked like ordinary completions until someone noticed the " +
      "completion times were suspiciously instant (0.0s) rather than the multi-second calls a real " +
      "generation takes.",
    fix: "Changed the affected schema fields to plain \"type\": \"string\" across the tool definitions.",
  },
  {
    title: "An action tier silently defaulted to the wrong value",
    broke:
      "record_promise_to_pay was missing from ACTION_TIER_DEFAULTS, so it silently fell back to " +
      "APPROVE_FIRST instead of the intended AUTONOMOUS.",
    found: "A unit test asserting the expected tier for this action — not by reading the dispatch table.",
    fix: "Added the explicit entry to ACTION_TIER_DEFAULTS.",
  },
  {
    title: "The system's own reliability tracking hid real progress",
    broke:
      "get_cleanly_completed_case_ids() (db.py) and compute_reliability_metrics() (metrics.py) both " +
      "judged a case's cleanliness against its ENTIRE flat log history — so a case that failed once, " +
      "days earlier, could never be recognized as clean again even after a later attempt fully " +
      "succeeded. Quantified cost while the bug existed (DEVLOG.md, 2026-08-30): 42 cases had already " +
      "succeeded at least once and were needlessly re-run anyway — 227 wasted LLM calls against an " +
      "already-scarce free-tier quota, worst case one payment-failure case re-attempted 17 times after " +
      "its first real success.",
    found:
      "Found independently in two places, not one — fixing db.py alone wasn't enough, since it and " +
      "metrics.py could silently disagree with each other on the same data. Caught by the user directly " +
      "asking whether the reported numbers actually matched, not by either implementation failing on " +
      "its own.",
    fix:
      "Both rewritten to judge a case by the trailing slice of log entries strictly after its last " +
      "terminal (non-error) outcome, plus a dedicated cross-check test pinning the two implementations " +
      "to agree with each other — the specific regression class that let this bug survive in one file " +
      "after being fixed in the other.",
  },
]

const QUOTA_TIMELINE: { date: string; text: string }[] = [
  { date: "Aug 26–29", text: "Clean-case count stuck at exactly 31/95 across multiple days, despite --resume, tighter iteration caps, prompt tuning, and evidence-based weighted provider scheduling." },
  { date: "Aug 29", text: "Tried a 2nd Groq API key for real added capacity. Root-caused why it didn't help: both keys resolved to the same Groq organization ID — one quota pool, not two." },
  { date: "Aug 29, night", text: "Found and fixed the reliability-tracking bug in db.py. Clean count corrected 31 → 34 with zero new LLM calls." },
  { date: "Aug 29, later", text: "Found the identical bug duplicated in metrics.py. Fixed and cross-checked — 34/95 doubly verified as the real, settled number." },
  { date: "Aug 30", text: "A scheduled daily retry landed the biggest single jump yet: 34/95 → 58/95." },
  { date: "Aug 30", text: "Checked the real per-provider numbers before deciding anything: of 61 attributed clean cases, Groq produced 25, OpenRouter 3, Gemini only 2 — and 100% of Gemini's 74 failures were 429 daily-quota errors, not transient flakiness. Switched the default provider from Gemini to Groq everywhere, including the live-submission feature, so a visitor's first attempt isn't ~92% likely to land on the already-exhausted option." },
]

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-[var(--radius-card)] p-5" style={{ background: "var(--color-surface)" }}>
      {children}
    </div>
  )
}

function Label({ children, color }: { children: React.ReactNode; color: string }) {
  return (
    <p className="text-[11px] font-semibold uppercase tracking-wide" style={{ color }}>
      {children}
    </p>
  )
}

export default function WhatBroke() {
  return (
    <div className="min-h-screen px-6 py-8" style={{ background: "var(--color-canvas)" }}>
      <div className="mx-auto max-w-3xl">
        <TopNav />

        <p className="text-xs font-medium uppercase tracking-[0.14em]" style={{ color: "var(--color-brand)" }}>
          Honesty
        </p>
        <h1 className="mt-1.5 text-2xl font-semibold tracking-tight sm:text-3xl" style={{ color: "var(--color-ink)" }}>
          What broke before release
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
          "We tested it" means less than showing what testing actually caught. These are real bugs
          found and fixed during the build — most caught by unit tests, not by reading code and
          hoping it was right.
        </p>

        <div className="mt-8 flex flex-col gap-4">
          {BUGS.map((bug) => (
            <Card key={bug.title}>
              <p className="text-base font-semibold" style={{ color: "var(--color-ink)" }}>
                {bug.title}
              </p>
              <div className="mt-4 flex flex-col gap-3">
                <div>
                  <Label color="var(--color-bad)">What broke</Label>
                  <p className="mt-1 text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
                    {bug.broke}
                  </p>
                </div>
                <div>
                  <Label color="var(--color-warn)">How it was found</Label>
                  <p className="mt-1 text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
                    {bug.found}
                  </p>
                </div>
                <div>
                  <Label color="var(--color-good)">The fix</Label>
                  <p className="mt-1 text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
                    {bug.fix}
                  </p>
                </div>
              </div>
            </Card>
          ))}

          <Card>
            <p className="text-base font-semibold" style={{ color: "var(--color-ink)" }}>
              Free-tier LLM quota is a real, load-bearing constraint — not a footnote
            </p>
            <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
              A batch of ~95 multi-turn cases is enough volume to hit daily request caps across all
              3 free-tier providers simultaneously. This is the real, dated sequence of what was
              tried and what actually moved the number — logged as it happened, not reconstructed
              afterward.
            </p>
            <div className="relative mt-5 flex flex-col gap-5 pl-5">
              <div
                className="absolute bottom-1 left-[3px] top-1 w-px"
                style={{ background: "var(--color-border-subtle)" }}
                aria-hidden="true"
              />
              {QUOTA_TIMELINE.map((item, i) => (
                <div key={i} className="relative">
                  <span
                    className="absolute -left-5 top-1 h-2 w-2 rounded-full"
                    style={{ background: "var(--color-brand)" }}
                    aria-hidden="true"
                  />
                  <p className="font-mono text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
                    {item.date}
                  </p>
                  <p className="mt-0.5 text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
                    {item.text}
                  </p>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <footer className="flex items-center justify-between border-t py-8 text-xs" style={{ borderColor: "var(--color-border-subtle)", color: "var(--color-ink-faint)" }}>
          <Link to="/architecture" className="font-medium no-underline" style={{ color: "var(--color-brand)" }}>
            ← How the decision loop works
          </Link>
          <Link to="/dashboard" className="font-medium no-underline" style={{ color: "var(--color-brand)" }}>
            Open the live batch dashboard →
          </Link>
        </footer>
      </div>
    </div>
  )
}
