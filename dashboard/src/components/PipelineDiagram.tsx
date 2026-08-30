interface Stage {
  n: number
  label: string
  detail: string
  kind: "code" | "model" | "data"
}

const STAGES: Stage[] = [
  { n: 1, label: "Detect", detail: "Router classifies surface + severity — plain code", kind: "code" },
  { n: 2, label: "Reason", detail: "Case agent — real LLM tool-calling loop", kind: "model" },
  { n: 3, label: "Govern", detail: "Guardrail engine — hard-coded, inside execute_action", kind: "code" },
  { n: 4, label: "Act", detail: "Autonomous action, human approval, or hard stop", kind: "data" },
  { n: 5, label: "Audit", detail: "Every decision logged — replayable per case", kind: "data" },
]

const KIND_COLOR: Record<Stage["kind"], string> = {
  code: "var(--color-brand)",
  model: "var(--color-warn)",
  data: "var(--color-ink-muted)",
}

/**
 * The pipeline as a real diagram, not prose — five numbered stages, each labeled with whether
 * it's deterministic code or the model's own reasoning. That distinction (stage 2 vs. stage 3)
 * is the whole guardrails-as-code argument in one glance: the model decides, the code governs.
 * Built with flex boxes + connectors rather than a canvas/SVG library — same "build fresh, stay
 * dependency-free" approach as the rest of this polish pass.
 */
export function PipelineDiagram() {
  return (
    <div className="flex flex-col gap-0 sm:flex-row sm:items-stretch sm:gap-0">
      {STAGES.map((stage, i) => (
        <div key={stage.n} className="flex flex-1 items-stretch">
          <div
            className="flex flex-1 flex-col gap-2 rounded-xl p-4"
            style={{ background: "var(--color-surface)" }}
          >
            <div className="flex items-center gap-2">
              <span
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold"
                style={{ background: KIND_COLOR[stage.kind], color: "var(--color-surface)" }}
              >
                {stage.n}
              </span>
              <span className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
                {stage.label}
              </span>
            </div>
            <p className="text-xs leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
              {stage.detail}
            </p>
            <span
              className="mt-auto w-fit rounded-[var(--radius-pill)] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide"
              style={{
                color: KIND_COLOR[stage.kind],
                background: "var(--color-canvas)",
              }}
            >
              {stage.kind === "model" ? "model reasoning" : stage.kind === "code" ? "deterministic code" : "outcome"}
            </span>
          </div>
          {i < STAGES.length - 1 && (
            <div className="hidden w-6 shrink-0 items-center justify-center sm:flex" aria-hidden="true">
              <svg width="20" height="12" viewBox="0 0 20 12" fill="none">
                <path d="M0 6h16M12 1l5 5-5 5" stroke="var(--color-border-subtle)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
