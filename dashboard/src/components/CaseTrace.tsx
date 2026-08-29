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

function TraceEntry({ entry }: { entry: DecisionLogEntry }) {
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
