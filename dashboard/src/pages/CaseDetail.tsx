import { Link, useParams } from "react-router-dom"
import { CaseTrace } from "../components/CaseTrace"
import { TopNav } from "../components/TopNav"

export default function CaseDetail() {
  const { caseId } = useParams<{ caseId: string }>()

  return (
    <div className="min-h-screen px-6 py-8" style={{ background: "var(--color-canvas)" }}>
      <div className="mx-auto max-w-3xl">
        <TopNav />
        <Link
          to="/dashboard"
          className="mb-4 inline-flex items-center gap-1 text-sm font-medium no-underline"
          style={{ color: "var(--color-ink-muted)" }}
        >
          ← Back to live batch
        </Link>
        <div className="mt-4">{caseId ? <CaseTrace caseId={caseId} /> : null}</div>
      </div>
    </div>
  )
}
