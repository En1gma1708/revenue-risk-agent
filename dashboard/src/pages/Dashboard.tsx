import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { fetchCases, fetchMetrics } from "../api"
import { CaseTable } from "../components/CaseTable"
import { GuardrailLedger } from "../components/GuardrailLedger"
import { MetricsBar } from "../components/MetricsBar"
import { ReliabilityPanel } from "../components/ReliabilityPanel"
import { TopNav } from "../components/TopNav"
import type { Case, MetricsResponse, Surface } from "../types"

export default function Dashboard() {
  const navigate = useNavigate()
  const [cases, setCases] = useState<Case[]>([])
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null)
  const [selectedSurface, setSelectedSurface] = useState<Surface | "all">("all")
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([fetchCases(), fetchMetrics()])
      .then(([casesRes, metricsRes]) => {
        setCases(casesRes.cases)
        setMetrics(metricsRes)
      })
      .catch((e) => setLoadError(String(e)))
  }, [])

  const visibleCases = selectedSurface === "all" ? cases : cases.filter((c) => c.surface === selectedSurface)

  return (
    <div className="min-h-screen px-6 py-8" style={{ background: "var(--color-canvas)" }}>
      <div className="mx-auto max-w-6xl">
        <TopNav />

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
              totalCases={cases.length}
              selectedSurface={selectedSurface}
              onSelectSurface={setSelectedSurface}
              onSelectCase={(caseId) => navigate(`/case/${encodeURIComponent(caseId)}`)}
            />
          </div>

          <div className="space-y-6">
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
