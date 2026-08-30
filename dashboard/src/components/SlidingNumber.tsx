import { useEffect, useRef, useState } from "react"

/**
 * Animated count-up for numeric stats — the metrics bar's values tick up from 0 (or the
 * previous value) rather than popping in instantly. Built from scratch (requestAnimationFrame
 * + an ease-out curve) rather than pulled from a component library, same precedent as
 * Spotlight.tsx/BorderBeam.tsx in the sibling NeuralPath-AI project: keeps zero new
 * dependencies and full control over how it reads the design tokens.
 *
 * `format` receives the interpolated numeric value each frame and returns the display string
 * (so callers can keep using their own currency/percent formatters).
 */
export function SlidingNumber({
  value,
  format,
  durationMs = 700,
  className,
  style,
}: {
  value: number
  format: (n: number) => string
  durationMs?: number
  className?: string
  style?: React.CSSProperties
}) {
  const [display, setDisplay] = useState(value)
  const fromRef = useRef(value)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    const from = fromRef.current
    const to = value
    if (from === to) return

    const start = performance.now()
    const easeOutQuint = (t: number) => 1 - Math.pow(1 - t, 5)

    function tick(now: number) {
      const elapsed = now - start
      const t = Math.min(1, elapsed / durationMs)
      const eased = easeOutQuint(t)
      setDisplay(from + (to - from) * eased)
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        fromRef.current = to
      }
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  return (
    <span className={className} style={{ ...style, fontVariantNumeric: "tabular-nums" }}>
      {format(display)}
    </span>
  )
}
