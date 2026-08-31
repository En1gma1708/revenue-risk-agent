import { useEffect, useState } from "react"
import { fetchCaseTrace } from "../api"
import type { CaseTraceResponse, DecisionLogEntry } from "../types"

function formatInr(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(amount)
}

const TIER_STYLES: Record<string, { dot: string; fg: string; bg: string }> = {
  HARD_STOP: { dot: "var(--color-bad)", fg: "var(--color-bad)", bg: "var(--color-bad-soft)" },
  APPROVE_FIRST: { dot: "var(--color-warn)", fg: "var(--color-warn)", bg: "var(--color-warn-soft)" },
  LOG_ONLY: { dot: "var(--color-ink-faint)", fg: "var(--color-ink-muted)", bg: "var(--color-border-subtle)" },
  AUTONOMOUS: { dot: "var(--color-good)", fg: "var(--color-good)", bg: "var(--color-good-soft)" },
}

// Checker (reflection agent) entries are a genuinely different KIND of trace row -- a second
// agent reviewing the specialist's already-completed decision, not another turn in the same
// investigate/decide/execute loop -- so they get their own icon + label instead of being read as
// "just another turn" (which is what raw outcome text alone looked like before this).
const CHECKER_OUTCOMES = new Set(["checker_approved", "checker_flagged"])

// Consistent 1.6px-stroke line icons (no emoji), matching CaseTable.tsx's SurfaceIcon convention.
function ShieldCheckIcon({ className }: { className?: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M12 3l7 3v6c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  )
}

function ShieldAlertIcon({ className }: { className?: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M12 3l7 3v6c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6z" />
      <path d="M12 7.5v5" />
      <path d="M12 15.5v.01" strokeWidth={2.4} />
    </svg>
  )
}

function CheckerTraceEntry({ entry }: { entry: DecisionLogEntry }) {
  const sound = entry.outcome === "checker_approved"
  const style = sound
    ? { fg: "var(--color-good)", bg: "var(--color-good-soft)" }
    : { fg: "var(--color-warn)", bg: "var(--color-warn-soft)" }
  const recommendedAction = typeof entry.decision?.recommended_action === "string" ? entry.decision.recommended_action : null

  return (
    <div className="relative flex gap-3 pb-6 last:pb-0">
      <div className="flex flex-col items-center">
        <span
          className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full"
          style={{ color: style.fg, background: style.bg }}
        >
          {sound ? <ShieldCheckIcon /> : <ShieldAlertIcon />}
        </span>
        <span className="mt-1 w-px flex-1" style={{ background: "var(--color-border-subtle)" }} />
      </div>
      <div className="min-w-0 flex-1 rounded-lg border px-3 py-2.5" style={{ borderColor: style.bg, background: "color-mix(in srgb, " + style.bg + " 45%, transparent)" }}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide" style={{ color: style.fg }}>
            Reviewed by checker agent
          </span>
          <span
            className="rounded-[var(--radius-pill)] px-2 py-0.5 text-xs font-semibold"
            style={{ color: style.fg, background: "var(--color-surface)" }}
          >
            {sound ? "Sound" : "Flagged"}
          </span>
        </div>

        {entry.reasoning && (
          <p className="mt-1.5 text-sm leading-relaxed" style={{ color: "var(--color-ink)" }}>
            {entry.reasoning}
          </p>
        )}

        {!sound && recommendedAction && (
          <p className="mt-1.5 text-xs" style={{ color: style.fg }}>
            Recommended: <span className="font-mono">{recommendedAction.replace(/_/g, " ")}</span>
            {recommendedAction === "retry_specialist" && " — specialist re-ran, see below"}
          </p>
        )}
      </div>
    </div>
  )
}

function TraceEntry({ entry }: { entry: DecisionLogEntry }) {
  if (entry.outcome && CHECKER_OUTCOMES.has(entry.outcome)) {
    return <CheckerTraceEntry entry={entry} />
  }

  const style = TIER_STYLES[entry.action_tier] ?? TIER_STYLES.LOG_ONLY
  const blocked = entry.guardrail_check.violated_rule_ids.length > 0

  return (
    <div className="relative flex gap-3 pb-6 last:pb-0">
      <div className="flex flex-col items-center">
        <span
          className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ background: style.dot, boxShadow: blocked ? `0 0 0 3px ${style.bg}` : undefined }}
        />
        <span className="mt-1 w-px flex-1" style={{ background: "var(--color-border-subtle)" }} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs" style={{ color: "var(--color-ink-faint)" }}>
            turn {entry.iteration}
          </span>
          <span
            className="rounded-[var(--radius-pill)] px-2 py-0.5 text-xs font-semibold"
            style={{ color: style.fg, background: style.bg }}
          >
            {entry.action_tier}
          </span>
          <span className="text-xs" style={{ color: "var(--color-ink-muted)" }}>
            {entry.action_taken.replace(/_/g, " ")}
          </span>
        </div>

        {entry.reasoning && (
          <p className="mt-1.5 text-sm leading-relaxed" style={{ color: "var(--color-ink)" }}>
            {entry.reasoning}
          </p>
        )}

        {blocked && (
          <div
            className="mt-2 rounded-lg border px-3 py-2"
            style={{ borderColor: "var(--color-bad)", background: "var(--color-bad-soft)" }}
          >
            <p className="text-xs font-semibold" style={{ color: "var(--color-bad)" }}>
              Blocked by: {entry.guardrail_check.violated_rule_ids.join(", ")}
            </p>
            {entry.guardrail_check.messages.map((m, i) => (
              <p key={i} className="mt-0.5 text-xs" style={{ color: "var(--color-bad)" }}>
                {m}
              </p>
            ))}
          </div>
        )}

        {entry.outcome && (
          <p className="mt-1.5 text-xs" style={{ color: "var(--color-ink-muted)" }}>
            outcome: <span className="font-mono">{entry.outcome}</span>
          </p>
        )}

        {entry.amount_recovered_inr > 0 && (
          <p className="mt-1 text-xs font-semibold" style={{ color: "var(--color-good)" }}>
            +{formatInr(entry.amount_recovered_inr)} recovered
          </p>
        )}
      </div>
    </div>
  )
}

export function CaseTrace({ caseId }: { caseId: string }) {
  const [data, setData] = useState<CaseTraceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setData(null)
    setError(null)
    fetchCaseTrace(caseId)
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [caseId])

  const cardStyle = { background: "var(--color-surface)", boxShadow: "0 1px 2px rgba(28,26,23,0.04), 0 1px 12px rgba(28,26,23,0.03)" }

  if (error) {
    return (
      <div
        className="rounded-[var(--radius-card)] border p-5 text-sm"
        style={{ borderColor: "var(--color-bad)", background: "var(--color-bad-soft)", color: "var(--color-bad)" }}
      >
        {error}
      </div>
    )
  }

  if (!data) {
    return (
      <div className="rounded-[var(--radius-card)] p-5 text-sm" style={{ ...cardStyle, color: "var(--color-ink-faint)" }}>
        Loading trace…
      </div>
    )
  }

  return (
    <div className="rounded-[var(--radius-card)] p-5" style={cardStyle}>
      <div className="flex items-center justify-between">
        <h3 className="font-mono text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
          {data.case.case_id}
        </h3>
        <span className="text-xs" style={{ color: "var(--color-ink-faint)" }}>
          {data.case.customer_name}
        </span>
      </div>
      <p className="mt-0.5 text-xs" style={{ color: "var(--color-ink-muted)" }}>
        {formatInr(data.case.amount_inr)} · {data.case.surface.replace(/_/g, " ")}
      </p>

      <div className="mt-5">
        {data.trace.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--color-ink-faint)" }}>
            No decisions logged for this case yet.
          </p>
        ) : (
          data.trace.map((entry) => <TraceEntry key={entry.log_id} entry={entry} />)
        )}
      </div>
    </div>
  )
}
