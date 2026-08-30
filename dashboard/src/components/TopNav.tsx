import { Link, useLocation } from "react-router-dom"

export function TopNav() {
  const location = useLocation()
  const onDashboard = location.pathname === "/dashboard"
  const onTryIt = location.pathname === "/try"
  const onArchitecture = location.pathname === "/architecture"
  const onWhatBroke = location.pathname === "/what-broke"

  return (
    <nav className="mb-8 flex items-center justify-between">
      <Link to="/" className="flex items-center gap-2 no-underline">
        <span
          className="flex h-6 w-6 items-center justify-center rounded-md text-xs font-bold"
          style={{ background: "var(--color-ink)", color: "var(--color-surface)" }}
        >
          R
        </span>
        <span className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
          Revenue Recovery Agent
        </span>
      </Link>
      <div className="flex items-center gap-4 text-sm">
        <Link
          to="/dashboard"
          className="no-underline"
          style={{ color: onDashboard ? "var(--color-brand)" : "var(--color-ink-muted)", fontWeight: onDashboard ? 600 : 500 }}
        >
          Live batch
        </Link>
        <Link
          to="/try"
          className="no-underline"
          style={{ color: onTryIt ? "var(--color-brand)" : "var(--color-ink-muted)", fontWeight: onTryIt ? 600 : 500 }}
        >
          Try your own case
        </Link>
        <Link
          to="/architecture"
          className="no-underline"
          style={{ color: onArchitecture ? "var(--color-brand)" : "var(--color-ink-muted)", fontWeight: onArchitecture ? 600 : 500 }}
        >
          Architecture
        </Link>
        <Link
          to="/what-broke"
          className="no-underline"
          style={{ color: onWhatBroke ? "var(--color-brand)" : "var(--color-ink-muted)", fontWeight: onWhatBroke ? 600 : 500 }}
        >
          What broke
        </Link>
      </div>
    </nav>
  )
}
