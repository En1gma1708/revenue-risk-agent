import type { Case, CaseTraceResponse, MetricsResponse, Surface } from "./types"

// Vite dev proxy (vite.config.ts) forwards /api/* to the FastAPI backend on :8317 -- keeps the
// frontend free of a hardcoded backend port/host, which matters since we already hit one
// hardcoded-port surprise today (see DEVLOG.md, port 8000 collision).
const BASE = "/api"

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export function fetchCases(surface?: Surface): Promise<{ cases: Case[]; count: number }> {
  const qs = surface ? `?surface=${encodeURIComponent(surface)}` : ""
  return getJson(`/cases${qs}`)
}

export function fetchCaseTrace(caseId: string): Promise<CaseTraceResponse> {
  return getJson(`/cases/${encodeURIComponent(caseId)}/trace`)
}

export function fetchMetrics(): Promise<MetricsResponse> {
  return getJson(`/metrics`)
}
