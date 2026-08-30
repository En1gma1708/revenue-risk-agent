import { useRef, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { submitBulkCases, submitCustomCase } from "../api"
import { TopNav } from "../components/TopNav"
import type { BulkCaseResult, CustomCaseInput, Surface } from "../types"

const inputStyle: React.CSSProperties = {
  background: "var(--color-canvas)",
  border: "1px solid var(--color-border-subtle)",
  color: "var(--color-ink)",
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium" style={{ color: "var(--color-ink-muted)" }}>
        {label}
      </span>
      {children}
    </label>
  )
}

const inputClass = "rounded-lg px-3 py-2 text-sm outline-none focus:ring-2"

export default function TryIt() {
  const navigate = useNavigate()
  const [surface, setSurface] = useState<Surface>("payment_failure")
  const [customerName, setCustomerName] = useState("")
  const [amount, setAmount] = useState("")
  const [instrumentType, setInstrumentType] = useState("card")
  const [errorReason, setErrorReason] = useState("insufficient_funds")
  const [attemptNumber, setAttemptNumber] = useState("1")
  const [abandonmentStage, setAbandonmentStage] = useState("otp_entry")
  const [device, setDevice] = useState("mobile_web")
  const [minutesSince, setMinutesSince] = useState("30")
  const [daysOverdue, setDaysOverdue] = useState("30")
  const [contactChannel, setContactChannel] = useState("email")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (!customerName.trim() || !amount) {
      setError("Fill in a customer name and amount.")
      return
    }

    const payload: CustomCaseInput = {
      surface,
      customer_name: customerName.trim(),
      amount_inr: Number(amount),
    }
    if (surface === "payment_failure") {
      payload.instrument_type = instrumentType as CustomCaseInput["instrument_type"]
      payload.error_reason = errorReason
      payload.attempt_number = Number(attemptNumber) || 1
    } else if (surface === "checkout_abandonment") {
      payload.abandonment_stage = abandonmentStage as CustomCaseInput["abandonment_stage"]
      payload.device = device as CustomCaseInput["device"]
      payload.minutes_since_abandon = Number(minutesSince) || 0
    } else {
      payload.days_overdue = Number(daysOverdue) || 0
      payload.contact_channel_pref = contactChannel as CustomCaseInput["contact_channel_pref"]
    }

    setSubmitting(true)
    try {
      const { case_id } = await submitCustomCase(payload)
      navigate(`/case/${encodeURIComponent(case_id)}`)
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err))
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen px-6 py-8" style={{ background: "var(--color-canvas)" }}>
      <div className="mx-auto max-w-2xl">
        <TopNav />

        <p className="text-xs font-medium uppercase tracking-[0.14em]" style={{ color: "var(--color-brand)" }}>
          Not a replay
        </p>
        <h1 className="mt-1.5 text-2xl font-semibold tracking-tight" style={{ color: "var(--color-ink)" }}>
          Give the agent a case it has never seen
        </h1>
        <p className="mt-1.5 max-w-lg text-sm leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
          Everything else in this dashboard replays a batch that already ran. This form builds a
          brand-new case from what you enter, runs it through the real agent loop right now, and
          takes you straight to its live trace — the same tool calls, the same guardrail engine,
          on a case nobody pre-generated.
        </p>

        <form
          onSubmit={handleSubmit}
          className="mt-6 flex flex-col gap-4 rounded-[var(--radius-card)] p-5"
          style={{ background: "var(--color-surface)" }}
        >
          <Field label="Surface">
            <select
              value={surface}
              onChange={(e) => setSurface(e.target.value as Surface)}
              className={inputClass}
              style={inputStyle}
            >
              <option value="payment_failure">Payment failure</option>
              <option value="checkout_abandonment">Checkout abandonment</option>
              <option value="overdue_receivable">Overdue receivable</option>
            </select>
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Customer name">
              <input
                value={customerName}
                onChange={(e) => setCustomerName(e.target.value)}
                placeholder="Priya Nair"
                className={inputClass}
                style={inputStyle}
              />
            </Field>
            <Field label="Amount (₹)">
              <input
                type="number"
                min="1"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="25000"
                className={inputClass}
                style={inputStyle}
              />
            </Field>
          </div>

          {surface === "payment_failure" && (
            <div className="grid grid-cols-3 gap-4">
              <Field label="Instrument">
                <select value={instrumentType} onChange={(e) => setInstrumentType(e.target.value)} className={inputClass} style={inputStyle}>
                  <option value="card">Card</option>
                  <option value="upi">UPI</option>
                  <option value="netbanking">Netbanking</option>
                </select>
              </Field>
              <Field label="Decline reason">
                <select value={errorReason} onChange={(e) => setErrorReason(e.target.value)} className={inputClass} style={inputStyle}>
                  <option value="insufficient_funds">Insufficient funds</option>
                  <option value="card_expired">Card expired</option>
                  <option value="authentication_failed">Authentication failed</option>
                  <option value="invalid_vpa">Invalid VPA</option>
                  <option value="bank_technical_error">Bank technical error</option>
                </select>
              </Field>
              <Field label="Attempt #">
                <input type="number" min="1" max="10" value={attemptNumber} onChange={(e) => setAttemptNumber(e.target.value)} className={inputClass} style={inputStyle} />
              </Field>
            </div>
          )}

          {surface === "checkout_abandonment" && (
            <div className="grid grid-cols-3 gap-4">
              <Field label="Stage">
                <select value={abandonmentStage} onChange={(e) => setAbandonmentStage(e.target.value)} className={inputClass} style={inputStyle}>
                  <option value="otp_entry">OTP entry</option>
                  <option value="instrument_select">Instrument select</option>
                  <option value="bank_redirect">Bank redirect</option>
                  <option value="review">Review</option>
                </select>
              </Field>
              <Field label="Device">
                <select value={device} onChange={(e) => setDevice(e.target.value)} className={inputClass} style={inputStyle}>
                  <option value="mobile_web">Mobile web</option>
                  <option value="desktop">Desktop</option>
                  <option value="app">App</option>
                </select>
              </Field>
              <Field label="Minutes since">
                <input type="number" min="0" value={minutesSince} onChange={(e) => setMinutesSince(e.target.value)} className={inputClass} style={inputStyle} />
              </Field>
            </div>
          )}

          {surface === "overdue_receivable" && (
            <div className="grid grid-cols-2 gap-4">
              <Field label="Days overdue">
                <input type="number" min="0" value={daysOverdue} onChange={(e) => setDaysOverdue(e.target.value)} className={inputClass} style={inputStyle} />
              </Field>
              <Field label="Preferred contact">
                <select value={contactChannel} onChange={(e) => setContactChannel(e.target.value)} className={inputClass} style={inputStyle}>
                  <option value="email">Email</option>
                  <option value="sms">SMS</option>
                  <option value="call">Call</option>
                  <option value="whatsapp">WhatsApp</option>
                </select>
              </Field>
            </div>
          )}

          {error && (
            <div className="rounded-lg px-3 py-2 text-xs" style={{ background: "var(--color-bad-soft)", color: "var(--color-bad)" }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="mt-1 rounded-[var(--radius-pill)] px-5 py-2.5 text-sm font-semibold disabled:opacity-60"
            style={{ background: "var(--color-brand)", color: "var(--color-surface)" }}
          >
            {submitting ? "Running the agent — this can take up to a minute…" : "Run the agent on this case →"}
          </button>
          <p className="text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
            Runs on a free-tier LLM in real time — if it fails, that's a real quota limit, not a
            demo trick. The resulting trace, including any failure, is shown exactly as recorded.
          </p>
        </form>

        <BulkUploadSection />
      </div>
    </div>
  )
}

function BulkUploadSection() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [results, setResults] = useState<BulkCaseResult[] | null>(null)
  const [bulkError, setBulkError] = useState<string | null>(null)

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setBulkError(null)
    setResults(null)
    setUploading(true)
    try {
      const { results } = await submitBulkCases(file)
      setResults(results)
    } catch (err) {
      setBulkError(String(err instanceof Error ? err.message : err))
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  return (
    <div className="mt-6 rounded-[var(--radius-card)] p-5" style={{ background: "var(--color-surface)" }}>
      <p className="text-xs font-medium uppercase tracking-[0.08em]" style={{ color: "var(--color-ink-faint)" }}>
        Or, several at once
      </p>
      <h3 className="mt-1 text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
        Upload a spreadsheet of cases
      </h3>
      <p className="mt-1.5 max-w-lg text-xs leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
        A .csv or .xlsx file, one row per case, same fields as the form above. Each row runs
        through the real agent one at a time — capped at 15 rows per upload to keep shared
        free-tier quota bounded.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <a
          href="/sample_cases.xlsx"
          download
          className="rounded-[var(--radius-pill)] px-4 py-2 text-xs font-medium no-underline"
          style={{ border: "1px solid var(--color-border-subtle)", color: "var(--color-ink-muted)" }}
        >
          Download sample template (9 rows)
        </a>
        <label
          className="cursor-pointer rounded-[var(--radius-pill)] px-4 py-2 text-xs font-semibold"
          style={{ background: "var(--color-ink)", color: "var(--color-surface)" }}
        >
          {uploading ? "Running…" : "Upload .csv / .xlsx"}
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.xlsx"
            onChange={handleFileChange}
            disabled={uploading}
            className="hidden"
          />
        </label>
      </div>

      {bulkError && (
        <div className="mt-3 rounded-lg px-3 py-2 text-xs" style={{ background: "var(--color-bad-soft)", color: "var(--color-bad)" }}>
          {bulkError}
        </div>
      )}

      {results && (
        <div className="mt-4 flex flex-col gap-1.5">
          {results.map((r) => (
            <div
              key={r.row}
              className="flex items-center justify-between gap-3 rounded-lg px-3 py-2 text-xs"
              style={{ background: "var(--color-canvas)" }}
            >
              <span style={{ color: "var(--color-ink-muted)" }}>
                row {r.row} · {r.customer_name}
              </span>
              {r.case_id ? (
                <Link to={`/case/${encodeURIComponent(r.case_id)}`} className="font-medium no-underline" style={{ color: "var(--color-brand)" }}>
                  view trace →
                </Link>
              ) : (
                <span style={{ color: "var(--color-bad)" }}>{r.error}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
