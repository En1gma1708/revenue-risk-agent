# Prompt to give Claude Code CLI (paste as-is)

---

We're adding 2 new pages to an existing project (c:\Razorpay project — read CLAUDE.md, PRD.md,
NOVELTY.md, README.md, and DEVLOG.md first for full context before touching anything). No
deployment work in this task — that's deliberately parked for later. Don't touch the core agent
loop, guardrail engine, or the existing pages' logic (Landing, Dashboard, CaseDetail, TryIt). Verify
every change with Playwright screenshots before calling it done, per this project's own stated
discipline in CLAUDE.md/DEVLOG.md.

## Tools to use, and specifically why

1. **taste-skill plugin (installed)** — use its `redesign-skill` for auditing the 2 NEW pages against
   generic-AI-design patterns before calling them done (same process already used on the existing
   dashboard — see DEVLOG.md's "dashboard polish pass" entry for the pattern to follow: font/color
   token audit, kill emoji icons, remove generic card-with-border-and-shadow look). Do NOT re-run it
   on the existing pages — they've already been through this pass.

2. **Watermelon UI (ui.watermelon.sh)** — reference ONLY for a specific missing piece: the
   Architecture page needs a clean multi-step pipeline diagram (Ingest → Route → Reason → Guardrail →
   Persist → Report, matching README.md's architecture ASCII diagram). Browse Watermelon UI for a
   numbered-step/timeline component pattern to adapt (build fresh in our own token system — do NOT
   copy-paste their code wholesale, we don't want a dependency, just the layout idea).

3. **Motion Primitives (motion-primitives.com)** — reference ONLY for the sliding-number/counter
   pattern already partially in use on MetricsBar.tsx (check if a SlidingNumber component already
   exists in dashboard/src/components/ before building a new one — it may already be there). If a
   similar animated-reveal pattern is missing on the new Architecture page's stage cards, look at
   their staggered-entry examples for reference, adapt in Framer Motion (already a dependency), don't
   add motion-primitives as an npm package.

4. **Haikei (haikei.app)** — use ONLY if the Architecture page's hero section needs a subtle background
   texture and looks flat/empty without one. Generate a low-opacity, on-brand-teal-tinted SVG blob/wave
   background (matches the --color-brand token — do not use a default Haikei color, recolor it to
   match). Skip this entirely if the page reads fine without it — per taste-skill's own guidance, a
   data-dense page doesn't need decoration for its own sake.

5. **Playwright (already a dependency)** — mandatory verification step. After every page change,
   actually load it and screenshot it. Never report "done" on visual work without a real screenshot
   confirming it renders correctly.

## New pages to build

### 1. `/architecture` — `dashboard/src/pages/Architecture.tsx`

Content sourced from README.md's "Architecture" section and NOVELTY.md's core claim + agentic-pattern
audit (§ "Agentic pattern audit — which of the 5 patterns actually apply"). Structure:

- Hero: restate the one-line pitch ("one policy core, three surfaces") with the pipeline diagram
  (Router → Case Agent → Guardrail engine → Audit trail → Dashboard) as a visual, numbered-stage
  layout — not just README's ASCII block pasted in.
- A section making the "genuine LLM reasoning vs. guardrails enforced in code" distinction visually
  obvious — two contrasting blocks/columns, one showing the agent's tool-calling loop (non-deterministic,
  reasons over context), one showing the guardrail engine (deterministic, pure functions, cannot be
  talked around). This is the single most important claim in the whole project — give it real visual
  weight, don't bury it in paragraph text.
- A short "why one agent, not three" callout, same as the README section — this is the core novelty
  claim vs. Razorpay's own Agent Studio.
- Link out to the real guardrail ledger (already on the Dashboard page) rather than duplicating that
  content here.

### 2. `/what-broke` — `dashboard/src/pages/WhatBroke.tsx`

Content sourced directly from README.md's "What broke before release" section (already written,
just needs a dedicated visual page, not just a README paragraph). One card per bug:
- The NPCI retry-spacing off-by-one
- Gemini's schema quirk breaking 100% of its batch share
- record_promise_to_pay's missing ACTION_TIER_DEFAULTS entry
- The reliability-tracking bug (get_cleanly_completed_case_ids / compute_reliability_metrics judging
  a case by its full history instead of its latest attempt) — this one has real numbers to show:
  quantify the 227 wasted LLM calls / 42 cases affected, cite DEVLOG.md's 2026-08-30 entry.
- The free-tier quota reality — not a "bug" exactly, but the single biggest real constraint fought
  throughout the build; give it its own card with the actual timeline (stuck at 31/95 for days, the
  reliability-bug fix recovering cases for free, the Gemini-deprioritization decision with real
  measured numbers: 2/61 clean cases from Gemini vs 25/61 from Groq).

Each card: what broke, how it was found (a lot of these were caught by unit tests, not manual review —
that's a real, quotable engineering-discipline point), what the fix was. This is deliberately the
page that makes "we tested it" mean something concrete — using OUR real bugs.

Add both to `dashboard/src/main.tsx`'s routes and `TopNav.tsx`'s nav links.

## Context: don't rebuild what already exists

Check `backend/custom_case.py`, `backend/bulk_upload.py`, and `dashboard/src/pages/TryIt.tsx` before
building anything — a single-case live-agent form AND a CSV/XLSX bulk-upload feature (15-row cap,
downloadable sample file) already exist and are verified working. This prompt is only for the 2 new
pages above; don't duplicate that work.

## Non-negotiables (carried over from this project's standing rules)

- Never run `git commit`/`git push`/`git init` — the user does all git themselves. You may `git add`/
  stage. Never touch remote git config.
- Never include a "Co-Authored-By: Claude" trailer in any commit message you draft for the user.
- Verify every claim against real evidence (a screenshot, a working curl, an actual test run) before
  reporting it as done — this project has a documented history of catching its own "looked done but
  wasn't" mistakes (stale servers, dropped font imports, a metrics bug hiding real progress) by
  actually checking, not assuming. Do the same here.
- No deployment work in this task. That's explicitly parked for after everything else is done.
