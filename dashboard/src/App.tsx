import { useEffect, useState } from "react"
import { fetchCases, fetchMetrics } from "./api"
import { CaseTable } from "./components/CaseTable"
import { CaseTrace } from "./components/CaseTrace"
import { GuardrailLedger } from "./components/GuardrailLedger"
import { MetricsBar } from "./components/MetricsBar"
import { ReliabilityPanel } from "./components/ReliabilityPanel"
import type { Case, MetricsResponse, Surface } from "./types"

export default function App() {
  const [cases, setCases] = useState<Case[]>([])
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null)
  const [selectedSurface, setSelectedSurface] = useState<Surface | "all">("all")
  const [selectedCaseId, setSelectedCaseId] = useState<string | undefined>()
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([fetchCases(), fetchMetrics()])
      .then(([casesRes, metricsRes]) => {
        setCases(casesRes.cases)
        setMetrics(metricsRes)
        if (casesRes.cases.length > 0) {
          setSelectedCaseId(casesRes.cases[0].case_id)
        }
      })
      .catch((e) => setLoadError(String(e)))
  }, [])

  const visibleCases = selectedSurface === "all" ? cases : cases.filter((c) => c.surface === selectedSurface)

  return (
    <div className="min-h-screen px-6 py-10" style={{ background: "var(--color-canvas)" }}>
      <div className="mx-auto max-w-6xl">
        <header className="mb-8">
          <p className="text-xs font-medium uppercase tracking-[0.14em]" style={{ color: "var(--color-brand)" }}>
            Revenue recovery
          </p>
          <h1 className="mt-1.5 text-[1.75rem] font-semibold tracking-tight" style={{ color: "var(--color-ink)" }}>
            One policy core, three surfaces
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
            Payment failures, checkout abandonment, and overdue receivables — the same guardrailed
            reasoning loop decides what happens next on every one of them.
          </p>
        </header>

        {loadError && (
          <div
            className="mb-6 rounded-2xl border p-4 text-sm"
            style={{
              borderColor: "var(--color-bad)",
              background: "var(--color-bad-soft)",
              color: "var(--color-bad)",
            }}
          >
            Couldn't reach the backend at /api — is it running? ({loadError})
          </div>
        )}

        {metrics && (
          <div className="mb-6">
            <MetricsBar metrics={metrics.headline} baseline={metrics.baseline} />
          </div>
        )}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <CaseTable
              cases={visibleCases}
              selectedSurface={selectedSurface}
              onSelectSurface={setSelectedSurface}
              onSelectCase={setSelectedCaseId}
              selectedCaseId={selectedCaseId}
            />
          </div>

          <div className="space-y-6">
            {selectedCaseId && <CaseTrace caseId={selectedCaseId} />}
            {metrics && (
              <ReliabilityPanel
                reliability={metrics.reliability}
                providerReliability={metrics.provider_reliability}
              />
            )}
            {metrics && <GuardrailLedger rules={metrics.guardrail_rules} ledger={metrics.guardrail_ledger} />}
          </div>
        </div>
      </div>
    </div>
  )
}
