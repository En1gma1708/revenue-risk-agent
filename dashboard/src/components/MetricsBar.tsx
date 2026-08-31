import { SlidingNumber } from "./SlidingNumber"
import type { BaselineComparison, HeadlineMetrics } from "../types"

function formatInr(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(amount)
}

interface StatProps {
  label: string
  amount: number
  accent: "neutral" | "good" | "warn" | "bad"
}

const ACCENT_COLOR: Record<StatProps["accent"], string> = {
  neutral: "var(--color-ink)",
  good: "var(--color-good)",
  warn: "var(--color-warn)",
  bad: "var(--color-bad)",
}

function Stat({ label, amount, accent }: StatProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <span
        className="text-[11px] font-medium uppercase tracking-[0.08em]"
        style={{ color: "var(--color-ink-faint)" }}
      >
        {label}
      </span>
      <SlidingNumber
        value={amount}
        format={formatInr}
        className="text-2xl font-semibold tabular-nums"
        style={{ color: ACCENT_COLOR[accent] }}
      />
    </div>
  )
}

export function MetricsBar({ metrics, baseline }: { metrics: HeadlineMetrics; baseline?: BaselineComparison }) {
  const agentRate = metrics.recovery_rate * 100
  const baselineRate = baseline ? baseline.recovery_rate * 100 : null
  const lift = baselineRate !== null ? agentRate - baselineRate : null

  return (
    <div
      className="rounded-[var(--radius-card)] p-6"
      style={{ background: "var(--color-surface)", boxShadow: "0 1px 2px rgba(28,26,23,0.04), 0 1px 12px rgba(28,26,23,0.03)" }}
    >
      <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
        <Stat label="At risk" amount={metrics.amount_at_risk_inr} accent="neutral" />
        <Stat label="Recovered" amount={metrics.amount_recovered_inr} accent="good" />
        <Stat label="Escalated" amount={metrics.amount_escalated_inr} accent="warn" />
        <Stat label="Blocked by policy" amount={metrics.amount_blocked_inr} accent="bad" />
      </div>
      <div
        className="mt-5 flex items-center gap-3 border-t pt-4 text-sm"
        style={{ borderColor: "var(--color-border-subtle)", color: "var(--color-ink-muted)" }}
      >
        <span>{metrics.case_count} cases in batch</span>
        <span className="h-1 w-1 rounded-full" style={{ background: "var(--color-ink-faint)" }} />
        <span className="inline-flex items-baseline">
          <SlidingNumber value={agentRate} format={(n) => `${n.toFixed(1)}%`} />
          <span className="ml-1">recovery rate</span>
        </span>
      </div>

      {/* Comparing a 0%-recovery empty batch against a baseline computed from a separate demo
          dataset produces a nonsensical negative "lift" (e.g. "-9.8 pts") that reads as the agent
          underperforming, when really there's just no data yet. Redesign-skill audit finding,
          2026-08-30 polish pass. */}
      {baseline && baselineRate !== null && metrics.case_count > 0 && (
        <div className="mt-4 rounded-xl p-4" style={{ background: "var(--color-canvas)" }}>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
            <span style={{ color: "var(--color-ink-muted)" }}>
              vs. naive fixed-retry baseline:{" "}
              <span className="font-semibold" style={{ color: "var(--color-ink)" }}>
                {baselineRate.toFixed(1)}%
              </span>
            </span>
            {lift !== null && (
              <span
                className="font-semibold"
                style={{ color: lift >= 0 ? "var(--color-good)" : "var(--color-bad)" }}
              >
                {lift >= 0 ? "+" : ""}
                {lift.toFixed(1)} pts
              </span>
            )}
          </div>
          <p className="mt-1 text-xs" style={{ color: "var(--color-ink-faint)" }}>
            {baseline.note}
          </p>
        </div>
      )}
    </div>
  )
}
