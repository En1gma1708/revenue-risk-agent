/**
 * A quiet ambient backdrop for the landing hero — two soft, brand-tinted blobs, hand-built as
 * inline SVG rather than pulled from Haikei (an interactive generator, not something fetchable
 * as static output) or any background-generator library. Kept restrained per frontend-design's
 * guidance: one signature moment (the live trace snippet) carries the hero, this just gives it
 * atmosphere without competing for attention. pointer-events-none + aria-hidden since it's purely
 * decorative.
 */
export function AmbientBackground() {
  return (
    <svg
      className="pointer-events-none absolute inset-0 -z-10 h-full w-full"
      viewBox="0 0 1200 600"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
    >
      <defs>
        <radialGradient id="blob-a" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="var(--color-brand)" stopOpacity="0.10" />
          <stop offset="100%" stopColor="var(--color-brand)" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="blob-b" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="var(--color-warn)" stopOpacity="0.07" />
          <stop offset="100%" stopColor="var(--color-warn)" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="220" cy="120" rx="420" ry="260" fill="url(#blob-a)" />
      <ellipse cx="1020" cy="360" rx="380" ry="240" fill="url(#blob-b)" />
    </svg>
  )
}
