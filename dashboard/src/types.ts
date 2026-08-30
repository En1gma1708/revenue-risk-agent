// Mirrors backend/models.py's shapes as they come over JSON from the FastAPI endpoints.
// Kept intentionally loose (strings for enums) rather than re-declaring every backend enum --
// the source of truth is models.py; this is just enough typing to catch obvious frontend bugs.

export type Surface = "payment_failure" | "checkout_abandonment" | "overdue_receivable"
export type CaseStatus =
  | "open"
  | "in_progress"
  | "recovered"
  | "escalated"
  | "blocked"
  | "closed_unrecoverable"
export type ActionTier = "AUTONOMOUS" | "LOG_ONLY" | "APPROVE_FIRST" | "HARD_STOP"
export type ActionTaken = "executed" | "blocked" | "queued_for_approval" | "logged_only"

export interface Case {
  case_id: string
  surface: Surface
  created_at: string
  customer_id: string
  customer_name: string
  amount_inr: number
  status: CaseStatus
  severity_score: number
  payment_details?: Record<string, unknown> | null
  checkout_details?: Record<string, unknown> | null
  receivable_details?: Record<string, unknown> | null
}

export interface GuardrailCheck {
  passed: boolean
  tier: ActionTier
  violated_rule_ids: string[]
  messages: string[]
}

export interface DecisionLogEntry {
  log_id: string
  case_id: string
  timestamp: string
  iteration: number
  observed: Record<string, unknown>
  decision: Record<string, unknown>
  reasoning: string
  guardrail_check: GuardrailCheck
  action_taken: ActionTaken
  action_tier: ActionTier
  outcome: string | null
  amount_at_risk_inr: number
  amount_recovered_inr: number
}

export interface HeadlineMetrics {
  amount_at_risk_inr: number
  amount_recovered_inr: number
  amount_escalated_inr: number
  amount_blocked_inr: number
  recovery_rate: number
  case_count: number
}

export interface GuardrailLedgerRow {
  rule_id: string
  fired_count: number
  case_ids: string[]
}

export interface GuardrailRuleInfo {
  rule_id: string
  description: string
  tier_on_violation: ActionTier
}

export interface AgentQualityMetrics {
  mean_iterations_per_case: number
  max_iterations_seen: number
  guardrail_veto_rate: number
}

export interface BaselineComparison {
  policy: string
  amount_recovered_inr: number
  recovery_rate: number
  note: string
}

export interface ReliabilityMetrics {
  total_cases: number
  clean_cases: number
  failed_cases: number
  reliability_rate: number
  failure_breakdown: Record<string, number>
  failed_case_ids: string[]
}

export interface ProviderReliabilityRow {
  total_log_entries: number
  failed_entries: number
  failure_rate: number
}

export interface MetricsResponse {
  headline: HeadlineMetrics
  baseline: BaselineComparison
  by_surface: Record<Surface, HeadlineMetrics>
  guardrail_ledger: GuardrailLedgerRow[]
  agent_quality: AgentQualityMetrics
  reliability: ReliabilityMetrics
  provider_reliability: Record<string, ProviderReliabilityRow>
  guardrail_rules: GuardrailRuleInfo[]
}

export interface CaseTraceResponse {
  case: Case
  trace: DecisionLogEntry[]
}

// Mirrors backend/custom_case.py's CustomCaseInput -- the simplified, form-friendly shape a
// visitor fills in for a live submission (narrower than the full Case schema).
export interface CustomCaseInput {
  surface: Surface
  customer_name: string
  amount_inr: number
  provider?: string

  instrument_type?: "card" | "upi" | "netbanking" | "other"
  error_reason?: string
  attempt_number?: number

  items?: string[]
  abandonment_stage?: "otp_entry" | "instrument_select" | "bank_redirect" | "review"
  device?: "mobile_web" | "desktop" | "app"
  minutes_since_abandon?: number

  days_overdue?: number
  contact_channel_pref?: "email" | "sms" | "call" | "whatsapp"
}

export interface BulkCaseResult {
  row: number
  customer_name: string
  case_id: string | null
  error: string | null
}
