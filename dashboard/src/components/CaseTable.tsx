import type { Case, CaseStatus, Surface } from "../types"

const SURFACE_LABEL: Record<Surface, string> = {
  payment_failure: "Payment failure",
  checkout_abandonment: "Checkout abandonment",
  overdue_receivable: "Overdue receivable",
}

// Consistent 1.5px-stroke line icons (no emoji) — one visual language across surfaces
// instead of the mismatched-weight look emoji glyphs give a data table.
function SurfaceIcon({ surface, className }: { surface: Surface; className?: string }) {
  const common = { width: 14, height: 14, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, className }
  if (surface === "payment_failure") {
    return (
      <svg {...common}>
        <rect x="2" y="5" width="20" height="14" rx="2.2" />
        <path d="M2 10h20" />
      </svg>
    )
  }
  if (surface === "checkout_abandonment") {
    return (
      <svg {...common}>
        <path d="M3 4h2l1.6 11.2A2 2 0 0 0 8.6 17h8.8a2 2 0 0 0 2-1.7L21 8H6" />
        <circle cx="9.5" cy="20.5" r="1.2" />
        <circle cx="17.5" cy="20.5" r="1.2" />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <path d="M6 3h9l3 3v15H6z" />
      <path d="M9 10h6M9 14h6M9 18h3" />
    </svg>
  )
}

const STATUS_TOKENS: Record<CaseStatus, { fg: string; bg: string }> = {
  open: { fg: "var(--color-ink-muted)", bg: "var(--color-border-subtle)" },
  in_progress: { fg: "var(--color-brand)", bg: "var(--color-brand-soft)" },
  recovered: { fg: "var(--color-good)", bg: "var(--color-good-soft)" },
  escalated: { fg: "var(--color-warn)", bg: "var(--color-warn-soft)" },
  blocked: { fg: "var(--color-bad)", bg: "var(--color-bad-soft)" },
  closed_unrecoverable: { fg: "var(--color-ink-faint)", bg: "var(--color-border-subtle)" },
}

function formatInr(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(amount)
}

interface CaseTableProps {
  cases: Case[]
  selectedSurface: Surface | "all"
  onSelectSurface: (surface: Surface | "all") => void
  onSelectCase: (caseId: string) => void
  selectedCaseId?: string
}

const SURFACES: Surface[] = ["payment_failure", "checkout_abandonment", "overdue_receivable"]

export function CaseTable({ cases, selectedSurface, onSelectSurface, onSelectCase, selectedCaseId }: CaseTableProps) {
  return (
    <div
      className="rounded-[var(--radius-card)]"
      style={{ background: "var(--color-surface)", boxShadow: "0 1px 2px rgba(28,26,23,0.04), 0 1px 12px rgba(28,26,23,0.03)" }}
    >
      <div className="flex items-center gap-2 border-b p-4" style={{ borderColor: "var(--color-border-subtle)" }}>
        <FilterPill active={selectedSurface === "all"} onClick={() => onSelectSurface("all")}>
          All surfaces
        </FilterPill>
        {SURFACES.map((s) => (
          <FilterPill key={s} active={selectedSurface === s} onClick={() => onSelectSurface(s)}>
            <SurfaceIcon surface={s} />
            {SURFACE_LABEL[s]}
          </FilterPill>
        ))}
      </div>
      <div className="max-h-[560px] overflow-y-auto overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead
            className="sticky top-0 text-[11px] uppercase tracking-[0.06em]"
            style={{ background: "var(--color-canvas)", color: "var(--color-ink-faint)" }}
          >
            <tr>
              <th className="px-4 py-2 font-medium">Case</th>
              <th className="px-4 py-2 font-medium">Surface</th>
              <th className="px-4 py-2 font-medium">Customer</th>
              <th className="px-4 py-2 text-right font-medium">Amount</th>
              <th className="px-4 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y" style={{ ["--tw-divide-opacity" as string]: 1 }}>
            {cases.map((c) => {
              const isSelected = selectedCaseId === c.case_id
              return (
                <tr
                  key={c.case_id}
                  onClick={() => onSelectCase(c.case_id)}
                  className="cursor-pointer transition-colors"
                  style={{
                    background: isSelected ? "var(--color-brand-soft)" : "transparent",
                    borderColor: "var(--color-border-subtle)",
                  }}
                  onMouseEnter={(e) => {
                    if (!isSelected) e.currentTarget.style.background = "var(--color-canvas)"
                  }}
                  onMouseLeave={(e) => {
                    if (!isSelected) e.currentTarget.style.background = "transparent"
                  }}
                >
                  <td className="px-4 py-2.5 font-mono text-xs" style={{ color: "var(--color-ink-muted)" }}>
                    {c.case_id}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="inline-flex items-center gap-1.5" style={{ color: "var(--color-ink-muted)" }}>
                      <SurfaceIcon surface={c.surface} />
                      {SURFACE_LABEL[c.surface]}
                    </span>
                  </td>
                  <td className="px-4 py-2.5" style={{ color: "var(--color-ink-muted)" }}>
                    {c.customer_name}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums font-medium" style={{ color: "var(--color-ink)" }}>
                    {formatInr(c.amount_inr)}
                  </td>
                  <td className="px-4 py-2.5">
                    <span
                      className="rounded-[var(--radius-pill)] px-2 py-0.5 text-xs font-medium"
                      style={{ color: STATUS_TOKENS[c.status].fg, background: STATUS_TOKENS[c.status].bg }}
                    >
                      {c.status.replace(/_/g, " ")}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {cases.length === 0 && (
          <div className="p-8 text-center text-sm" style={{ color: "var(--color-ink-faint)" }}>
            No cases match this filter.
          </div>
        )}
      </div>
    </div>
  )
}

function FilterPill({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] px-3 py-1.5 text-xs font-medium transition-colors"
      style={{
        background: active ? "var(--color-ink)" : "var(--color-canvas)",
        color: active ? "var(--color-surface)" : "var(--color-ink-muted)",
      }}
    >
      {children}
    </button>
  )
}
