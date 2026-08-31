import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { fetchCaseTrace } from "../api"
import { AmbientBackground } from "../components/AmbientBackground"
import { PipelineDiagram } from "../components/PipelineDiagram"
import { Reveal } from "../components/Reveal"
import type { CaseTraceResponse } from "../types"

const TIER_TOKEN: Record<string, { fg: string; bg: string }> = {
  HARD_STOP: { fg: "var(--color-bad)", bg: "var(--color-bad-soft)" },
  APPROVE_FIRST: { fg: "var(--color-warn)", bg: "var(--color-warn-soft)" },
  AUTONOMOUS: { fg: "var(--color-good)", bg: "var(--color-good-soft)" },
  LOG_ONLY: { fg: "var(--color-ink-muted)", bg: "var(--color-border-subtle)" },
}

// The hero's signature moment: PMT-0020's real trace, rendered live from the API rather than a
// mocked screenshot. Grounds "the model decides, the code enforces" in the actual subject matter
// (a real case where the specialist proposed 2 different, genuinely sensible actions -- a payment
// link, then an RBI pre-debit notice -- and was blocked both times by the same rule before
// correctly escalating) instead of a generic stat-tile hero. Falls back to a static illustrative
// shape if the API/case isn't reachable, so the landing page still tells its story with the
// backend down. Switched from PMT-0002 to PMT-0020 on the Gate 4 migration (2026-08-30) -- the
// old case's story changed under the new router+specialist architecture (now resolves in 1 turn,
// no longer a multi-block story), so re-found the strongest real multi-turn case in the new
// dataset rather than let the hero quietly show a mismatched narrative.
function HeroTraceSnippet() {
  const [data, setData] = useState<CaseTraceResponse | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    fetchCaseTrace("PMT-0020")
      .then(setData)
      .catch(() => setFailed(true))
  }, [])

  const rows =
    data?.trace.map((e) => ({
      tier: e.action_tier,
      label: e.action_taken.replace(/_/g, " "),
      blocked: e.guardrail_check.violated_rule_ids.length > 0,
    })) ??
    (failed
      ? [
          { tier: "HARD_STOP", label: "send payment link", blocked: true },
          { tier: "HARD_STOP", label: "send predebit notice", blocked: true },
          { tier: "APPROVE_FIRST", label: "queue for approval", blocked: false },
        ]
      : [])

  return (
    <div
      className="rounded-[var(--radius-card)] p-5"
      style={{ background: "var(--color-surface)", boxShadow: "0 1px 2px rgba(28,26,23,0.04), 0 4px 24px rgba(28,26,23,0.06)" }}
    >
      <div className="mb-3 flex items-center justify-between">
        <span className="font-mono text-xs" style={{ color: "var(--color-ink-faint)" }}>
          case PMT-0020 · ₹46,588 payment failure
        </span>
        <span
          className="text-[10px] font-medium uppercase tracking-wide"
          style={{ color: failed ? "var(--color-ink-faint)" : "var(--color-brand)" }}
        >
          {data ? "live" : failed ? "illustrative" : "loading"}
        </span>
      </div>
      <div className="flex flex-col gap-2">
        {rows.length === 0 && (
          <span className="text-xs" style={{ color: "var(--color-ink-faint)" }}>
            Loading a real case trace…
          </span>
        )}
        {rows.map((r, i) => {
          const tier = TIER_TOKEN[r.tier] ?? TIER_TOKEN.LOG_ONLY
          return (
            <div key={i} className="flex items-center gap-2">
              <span
                className="rounded-[var(--radius-pill)] px-2 py-0.5 text-[10px] font-semibold"
                style={{ color: tier.fg, background: tier.bg }}
              >
                {r.tier}
              </span>
              <span className="text-xs" style={{ color: "var(--color-ink-muted)" }}>
                proposes {r.label}
              </span>
              {r.blocked && (
                <span className="text-xs font-medium" style={{ color: "var(--color-bad)" }}>
                  — blocked by guardrail
                </span>
              )}
            </div>
          )
        })}
      </div>
      <p className="mt-3 text-xs leading-relaxed" style={{ color: "var(--color-ink-faint)" }}>
        Two different, genuinely sensible proposals — a payment link, then an RBI pre-debit notice
        — both blocked by the same hard-coded rule, before the specialist correctly escalates to a
        human. This isn't a prompt the model chose to follow — it's code its tool calls can't get
        past.
      </p>
    </div>
  )
}

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

export default function Landing() {
  return (
    <div className="min-h-screen" style={{ background: "var(--color-canvas)" }}>
      <div className="relative overflow-hidden px-6 pb-4 pt-10">
        <AmbientBackground />
        <div className="mx-auto max-w-5xl">
          <div className="mb-10 flex items-center justify-between">
            <span className="flex items-center gap-2">
              <span
                className="flex h-6 w-6 items-center justify-center rounded-md text-xs font-bold"
                style={{ background: "var(--color-ink)", color: "var(--color-surface)" }}
              >
                R
              </span>
              <span className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
                Revenue Recovery Agent
              </span>
            </span>
            <div className="flex items-center gap-5">
              <nav className="hidden items-center gap-5 text-sm sm:flex">
                <Link to="/architecture" className="no-underline" style={{ color: "var(--color-ink-muted)" }}>
                  Architecture
                </Link>
                <Link to="/what-broke" className="no-underline" style={{ color: "var(--color-ink-muted)" }}>
                  What broke
                </Link>
              </nav>
              <div className="flex items-center gap-3">
                <Link
                  to="/try"
                  className="rounded-[var(--radius-pill)] px-4 py-2 text-sm font-medium no-underline"
                  style={{ border: "1px solid var(--color-border-subtle)", color: "var(--color-ink)" }}
                >
                  Try your own case
                </Link>
                <Link
                  to="/dashboard"
                  className="rounded-[var(--radius-pill)] px-4 py-2 text-sm font-medium no-underline"
                  style={{ background: "var(--color-ink)", color: "var(--color-surface)" }}
                >
                  Open live batch →
                </Link>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 items-center gap-10 py-10 lg:grid-cols-[1.1fr_0.9fr]">
            <Reveal>
              <p className="text-xs font-medium uppercase tracking-[0.14em]" style={{ color: "var(--color-brand)" }}>
                Razorpay AI Buildathon — Track 3, Revenue Recovery
              </p>
              <h1
                className="mt-2 text-[2.5rem] font-semibold leading-[1.08] tracking-tight sm:text-[3rem]"
                style={{ color: "var(--color-ink)" }}
              >
                The model decides.
                <br />
                The code enforces.
              </h1>
              <p className="mt-4 max-w-lg text-base leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
                A router hands each case to a specialist for payment failures, checkout
                abandonment, or overdue B2B receivables. What any of them may actually do next is
                never left to a prompt — it's checked in code, every time, by one guardrail engine
                none of them can argue with.
              </p>
              <div className="mt-6 flex items-center gap-3">
                <Link
                  to="/dashboard"
                  className="rounded-[var(--radius-pill)] px-5 py-2.5 text-sm font-semibold no-underline"
                  style={{ background: "var(--color-brand)", color: "var(--color-surface)" }}
                >
                  See it decide, live →
                </Link>
                <a
                  href="#what-broke"
                  className="text-sm font-medium no-underline"
                  style={{ color: "var(--color-ink-muted)" }}
                >
                  What broke before release
                </a>
              </div>
            </Reveal>
            <Reveal delayMs={120}>
              <HeroTraceSnippet />
            </Reveal>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-5xl px-6">
        <Section eyebrow="How a decision happens" title="One loop, five stages, every surface">
          <PipelineDiagram />
        </Section>

        <Section eyebrow="Why one compliance core, not three" title="Revenue loss doesn't respect surface boundaries">
          <p className="max-w-2xl text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
            A payment degrades, a checkout gets abandoned, an invoice goes overdue — often for the
            same underlying customer. Most platforms, including Razorpay's own Agent Studio, ship
            separate, disconnected products per surface with no handoff between them. This project
            runs a router that hands each case to one of 3 specialist agents — but every one of
            them is checked against the exact same guardrail engine and shares the same tool set,
            so a decline reason code, a stalled checkout, and a broken payment promise are all
            evaluated under one compliance boundary, never three drifting copies of it.
          </p>
        </Section>

        <Section eyebrow="Data honesty" title="Real where the platform allows it">
          <div className="overflow-x-auto rounded-[var(--radius-card)]" style={{ background: "var(--color-surface)" }}>
            <table className="w-full text-left text-sm">
              <thead style={{ color: "var(--color-ink-faint)" }} className="text-[11px] uppercase tracking-wide">
                <tr>
                  <th className="px-4 py-3 font-medium">Surface</th>
                  <th className="px-4 py-3 font-medium">Data</th>
                  <th className="px-4 py-3 font-medium">Recovery action</th>
                </tr>
              </thead>
              <tbody className="divide-y" style={{ color: "var(--color-ink-muted)" }}>
                {[
                  ["Payment failures", "Real Razorpay test-mode reason codes", "Real Payment Links API (test mode)"],
                  ["Checkout abandonment", "Synthetic, schema-accurate", "Simulated / logged"],
                  ["Overdue B2B receivables", "Synthetic, schema-accurate", "Simulated / logged"],
                ].map((row) => (
                  <tr key={row[0]} style={{ borderColor: "var(--color-border-subtle)" }}>
                    {row.map((cell, i) => (
                      <td key={i} className="px-4 py-3" style={i === 0 ? { color: "var(--color-ink)", fontWeight: 500 } : undefined}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section eyebrow="Honesty" title="What broke before release">
          <div id="what-broke" className="flex flex-col gap-3">
            {[
              {
                title: "A hardcoded compliance rule was silently wrong",
                detail:
                  "NPCI retry-spacing check indexed one entry off — a 28h gap passed a 72h minimum. Caught by a unit test, not manual review.",
              },
              {
                title: "One provider's schema quirk broke 100% of its batch share",
                detail:
                  "Gemini rejected a JSON Schema nullable-union syntax. Every Gemini-routed case failed invisibly until someone noticed suspiciously instant completion times.",
              },
              {
                title: "The system's own reliability tracking hid real progress",
                detail:
                  "Two independent functions judged a case's cleanliness off its entire log history, not its latest attempt — 227 LLM calls wasted re-running cases that had already succeeded.",
              },
            ].map((item) => (
              <div
                key={item.title}
                className="rounded-[var(--radius-card)] p-4"
                style={{ background: "var(--color-surface)" }}
              >
                <p className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
                  {item.title}
                </p>
                <p className="mt-1 text-xs leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
                  {item.detail}
                </p>
              </div>
            ))}
            <p className="text-xs" style={{ color: "var(--color-ink-faint)" }}>
              Full list with file references in the repo README.
            </p>
          </div>
        </Section>

        <footer className="flex items-center justify-between border-t py-8 text-xs" style={{ borderColor: "var(--color-border-subtle)", color: "var(--color-ink-faint)" }}>
          <span>Built for the Razorpay AI Buildathon, Track 3.</span>
          <Link to="/dashboard" className="font-medium no-underline" style={{ color: "var(--color-brand)" }}>
            Open the live batch dashboard →
          </Link>
        </footer>
      </div>
    </div>
  )
}
