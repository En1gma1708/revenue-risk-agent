import { useEffect, useRef, useState } from "react"

/**
 * Fade + slide-up on scroll entry, built from scratch (IntersectionObserver + a CSS class
 * toggle) rather than pulled in via Framer Motion -- checked first, per this pass's own
 * instruction, and it turns out framer-motion isn't actually an installed dependency despite
 * being assumed present (see DEVLOG.md). Same "build fresh, stay dependency-free" precedent as
 * SlidingNumber.tsx. Applied only to pages that were previously fully static (Landing's sections,
 * Architecture's stage cards) -- per taste-skill's own restraint guidance, NOT added to
 * data-dense functional pages (Dashboard, CaseDetail, TryIt) where content should just be there,
 * not choreographed in.
 */
export function Reveal({
  children,
  delayMs = 0,
  className,
  style,
  as: Tag = "div",
}: {
  children: React.ReactNode
  delayMs?: number
  className?: string
  style?: React.CSSProperties
  /** Render as a different tag (e.g. "section") -- keeps this a real semantic element instead
      of always adding a div around content that has its own meaningful tag. */
  as?: "div" | "section"
}) {
  const ref = useRef<HTMLElement>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -10% 0px" }
    )
    observer.observe(el)

    // Safety fallback: a full-page capture tool (confirmed live -- Playwright's `fullPage`
    // screenshot resizes the viewport without firing the scroll/resize events a real user's
    // scroll produces, so below-the-fold content stayed permanently opacity:0 in screenshots
    // even though it revealed correctly for genuine scrolling) should never leave real content
    // invisible. If the observer hasn't fired shortly after mount, force visibility anyway --
    // content must never depend on a specific capture/rendering pipeline to be readable.
    const fallback = setTimeout(() => setVisible(true), 1200)

    return () => {
      observer.disconnect()
      clearTimeout(fallback)
    }
  }, [])

  return (
    <Tag
      ref={ref as never}
      className={`reveal ${visible ? "reveal-visible" : ""} ${className ?? ""}`}
      style={{ ...style, transitionDelay: visible ? `${delayMs}ms` : "0ms" }}
    >
      {children}
    </Tag>
  )
}
