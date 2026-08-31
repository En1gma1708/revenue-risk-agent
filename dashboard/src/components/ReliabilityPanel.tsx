import type { ProviderReliabilityRow, ReliabilityMetrics } from "../types"

interface ReliabilityPanelProps {
  reliability: ReliabilityMetrics
  providerReliability: Record<string, ProviderReliabilityRow>
}

const FAILURE_LABEL: Record<string, string> = {
  generation_error: "LLM/provider error (e.g. rate limit, malformed response)",
  max_iterations_exceeded: "Agent didn't converge within its turn limit",
}

function rateColor(rate: number, invert = false): string {
  // invert=true means higher is worse (failure rate); invert=false means higher is better (reliability rate)
  const good = invert ? rate <= 0.1 : rate >= 0.9
  const bad = invert ? rate >= 0.5 : rate <= 0.5
  if (good) return "var(--color-good)"
  if (bad) return "var(--color-bad)"
  return "var(--color-warn)"
}

export function ReliabilityPanel({ reliability, providerReliability }: ReliabilityPanelProps) {
  const providers = Object.entries(providerReliability)
  // 0 of 0 cases is "no data yet," not "bad reliability" -- rateColor's math would otherwise
  // read an empty batch as a failing one (0% <= 0.5 threshold), which is misleading rather than
  // honest. Redesign-skill audit finding, 2026-08-30 polish pass.
  const hasData = reliability.total_cases > 0

  return (
    <div
      className="rounded-[var(--radius-card)] p-5"
      style={{ background: "var(--color-surface)", boxShadow: "0 1px 2px rgba(28,26,23,0.04), 0 1px 12px rgba(28,26,23,0.03)" }}
    >
      <h3 className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
        System reliability
      </h3>
      <p className="mt-1 text-xs" style={{ color: "var(--color-ink-muted)" }}>
        What fraction of this batch produced a genuine agent decision vs. an infrastructure/provider
        failure (e.g. free-tier quota exhaustion). Reported honestly, not smoothed over.
      </p>

      <div className="mt-4 flex items-baseline gap-3">
        <span
          className="text-3xl font-semibold tabular-nums"
          style={{ color: hasData ? rateColor(reliability.reliability_rate) : "var(--color-ink-faint)" }}
        >
          {hasData ? `${(reliability.reliability_rate * 100).toFixed(1)}%` : "—"}
        </span>
        <span className="text-xs" style={{ color: "var(--color-ink-muted)" }}>
          {hasData ? `${reliability.clean_cases} of ${reliability.total_cases} cases clean` : "No cases run yet"}
        </span>
      </div>

      {Object.keys(reliability.failure_breakdown).length > 0 && (
        <div className="mt-4 space-y-1.5">
          <p className="text-xs font-medium" style={{ color: "var(--color-ink-muted)" }}>
            Failure breakdown
          </p>
          {Object.entries(reliability.failure_breakdown).map(([reason, count]) => (
            <div
              key={reason}
              className="flex items-center justify-between rounded-lg px-3 py-1.5"
              style={{ background: "var(--color-canvas)" }}
            >
              <span className="text-xs" style={{ color: "var(--color-ink-muted)" }}>
                {FAILURE_LABEL[reason] ?? reason}
              </span>
              <span className="text-xs font-semibold tabular-nums" style={{ color: "var(--color-ink)" }}>
                {count}
              </span>
            </div>
          ))}
        </div>
      )}

      {providers.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium" style={{ color: "var(--color-ink-muted)" }}>
            Per-provider failure rate
          </p>
          <div className="mt-1.5 space-y-1.5">
            {providers.map(([name, stats]) => (
              <div
                key={name}
                className="flex items-center justify-between rounded-lg px-3 py-1.5"
                style={{ background: "var(--color-canvas)" }}
              >
                <span className="text-xs font-medium capitalize" style={{ color: "var(--color-ink)" }}>
                  {name}
                </span>
                <div className="flex items-center gap-2 text-xs">
                  <span style={{ color: "var(--color-ink-faint)" }}>
                    {stats.failed_entries}/{stats.total_log_entries}
                  </span>
                  <span className="font-semibold tabular-nums" style={{ color: rateColor(stats.failure_rate, true) }}>
                    {(stats.failure_rate * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
