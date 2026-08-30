import type { BulkCaseResult, Case, CaseTraceResponse, CustomCaseInput, MetricsResponse, Surface } from "./types"

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

export async function submitCustomCase(payload: CustomCaseInput): Promise<{ case_id: string }> {
  const res = await fetch(`${BASE}/cases/custom/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Submission failed: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export async function submitBulkCases(file: File): Promise<{ results: BulkCaseResult[] }> {
  const form = new FormData()
  form.append("file", file)
  const res = await fetch(`${BASE}/cases/custom/bulk`, { method: "POST", body: form })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Upload failed: ${res.status} ${res.statusText}`)
  }
  return res.json()
}
