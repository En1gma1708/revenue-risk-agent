import type { GuardrailLedgerRow, GuardrailRuleInfo } from "../types"

interface GuardrailLedgerProps {
  rules: GuardrailRuleInfo[]
  ledger: GuardrailLedgerRow[]
}

const TIER_TOKENS: Record<string, { fg: string; bg: string }> = {
  HARD_STOP: { fg: "var(--color-bad)", bg: "var(--color-bad-soft)" },
  APPROVE_FIRST: { fg: "var(--color-warn)", bg: "var(--color-warn-soft)" },
  LOG_ONLY: { fg: "var(--color-ink-muted)", bg: "var(--color-border-subtle)" },
  AUTONOMOUS: { fg: "var(--color-good)", bg: "var(--color-good-soft)" },
}

export function GuardrailLedger({ rules, ledger }: GuardrailLedgerProps) {
  const firedByRule = new Map(ledger.map((row) => [row.rule_id, row.fired_count]))

  return (
    <div
      className="rounded-[var(--radius-card)] p-5"
      style={{ background: "var(--color-surface)", boxShadow: "0 1px 2px rgba(28,26,23,0.04), 0 1px 12px rgba(28,26,23,0.03)" }}
    >
      <h3 className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
        Guardrail ledger
      </h3>
      <p className="mt-1 text-xs" style={{ color: "var(--color-ink-muted)" }}>
        Hard-coded compliance rules the agent's proposed actions are checked against. Enforced in code,
        not in a prompt.
      </p>
      <div className="mt-4 space-y-2">
        {rules.map((rule) => {
          const count = firedByRule.get(rule.rule_id) ?? 0
          const tier = TIER_TOKENS[rule.tier_on_violation]
          return (
            <div
              key={rule.rule_id}
              className="flex items-start justify-between gap-3 rounded-lg border px-3 py-2"
              style={{ borderColor: "var(--color-border-subtle)" }}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs" style={{ color: "var(--color-ink)" }}>
                    {rule.rule_id}
                  </span>
                  {tier && (
                    <span
                      className="shrink-0 rounded-[var(--radius-pill)] px-1.5 py-0.5 text-[10px] font-medium"
                      style={{ color: tier.fg, background: tier.bg }}
                    >
                      {rule.tier_on_violation}
                    </span>
                  )}
                </div>
                <p className="mt-0.5 text-xs" style={{ color: "var(--color-ink-muted)" }}>
                  {rule.description}
                </p>
              </div>
              <span
                className="shrink-0 rounded-[var(--radius-pill)] px-2 py-0.5 text-xs font-semibold tabular-nums"
                style={{
                  color: count > 0 ? "var(--color-surface)" : "var(--color-ink-faint)",
                  background: count > 0 ? "var(--color-ink)" : "var(--color-border-subtle)",
                }}
              >
                {count}×
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
