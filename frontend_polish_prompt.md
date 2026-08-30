# Prompt to give Claude Code CLI (paste as-is)

---

We're doing a full, premium polish pass on the existing dashboard frontend for this project
(c:\Razorpay project). **First, read for context before touching anything**: CLAUDE.md, PRD.md,
NOVELTY.md, README.md, METRICS.md, and DEVLOG.md in full (DEVLOG.md is long — read it end to end,
not just the tail; it has the full history of every design decision already made, including two
prior design-polish passes you must not contradict or redo from scratch). Also read
CONVERSATION_SUMMARY.md if present for a compressed index.

This is a **polish pass on an existing, working dashboard**, not a rewrite. Don't touch the core
agent loop (`backend/agent_loop.py`, `backend/pydantic_agents.py`), the guardrail engine
(`backend/guardrails.py`), or any backend logic. Don't change page structure/routing or component
data-fetching logic unless a specific visual fix requires it. The existing pages are: Landing,
Dashboard, CaseDetail, TryIt, Architecture, WhatBroke (all under `dashboard/src/pages/`).

## Tools to use, and specifically what each is for

This project has already been through two earlier design passes (see DEVLOG.md's 2026-08-29
"Dashboard design polish pass" and 2026-08-30 "Presentation/narrative pass" / "Multi-page split"
entries) — read those first so you don't redo settled decisions (the Outfit/JetBrains Mono font
pair, the warm-neutral canvas + single restrained teal `--color-brand` token, the desaturated
status-tier colors, the no-emoji-icons rule, the no-generic-card-border-and-shadow rule are all
already decided; keep them, refine on top of them, don't replace them with something new unless
you find a real reason to).

1. **`taste-skill` plugin** (should already be installed — verify with `claude plugin list`; if
   missing, install it). Use its `redesign-skill`: an audit-then-fix checklist against generic-
   AI-design patterns, explicitly meant for existing projects, not rewrites. This is the primary
   playbook for this whole pass — run it fresh across ALL pages this time (the first pass only
   covered the original single-page dashboard before the multi-page split; the newer pages
   Architecture/WhatBroke/TryIt/Landing/CaseDetail have not all been through a full redesign-skill
   audit yet — check DEVLOG.md to see exactly which pages have and haven't).

2. **`frontend-design` plugin** (Anthropic's own official plugin — install via
   `claude plugin install frontend-design@claude-plugins-official` if not already present; this is
   what "web-design-guidelines" turned out to actually be, see DEVLOG.md's plugin-reality-check
   entry). Framed as "distinctive, production-grade, avoids generic AI aesthetics." Use its
   guidance on: numbered-stage markers only when content is a genuine sequence (already validated
   as legitimate for PipelineDiagram — don't second-guess that), and the 3 named clusters AI-
   generated design defaults to (warm cream+serif+terracotta / near-black+neon accent / broadsheet
   hairline-rules) — consciously verify no page has drifted into one of these by default.

3. **`awesome-claude-design`** — a manual reference doc (not an installed plugin), read for design
   pattern inspiration/calibration, not copied wholesale.

4. **Motion Primitives** (motion-primitives.com) — reference site for animation patterns. A
   SlidingNumber component (animated count-up, requestAnimationFrame + ease-out-quint) already
   exists at `dashboard/src/components/SlidingNumber.tsx`, built from scratch inspired by their
   technique (their docs 429'd on fetch last time — don't assume that's fixed, check first, and if
   still unreachable, keep reasoning from the existing implementation rather than blocking on it).
   Look for other patterns worth adapting (staggered entry, reveal-on-scroll) for pages that still
   feel static, e.g. Architecture's stage cards or the Landing page's sections — adapt in Framer
   Motion (already a dependency), do not add motion-primitives as an npm package.

5. **Watermelon UI** (ui.watermelon.sh) — reference for layout/component patterns, e.g. the
   pipeline/step diagram pattern already partially adapted into `PipelineDiagram.tsx`. Browse for
   anything else applicable (data tables, timeline components for CaseDetail's trace view) — adapt
   the layout idea in our own token system, do not copy-paste their code or add it as a dependency.

6. **Haikei** (haikei.app) — for subtle background textures ONLY where a page's hero section reads
   flat/empty without one (per taste-skill's own guidance: a data-dense page doesn't need
   decoration for its own sake — skip this entirely on pages like Dashboard/CaseDetail that are
   already content-dense). If used, generate a low-opacity SVG blob/wave recolored to match the
   `--color-brand` teal token — never use a default Haikei color.

7. **image-to-code workflow** — if the user provides any reference screenshot/image during this
   session, use Claude's vision to translate specific layout/spacing/hierarchy ideas from it into
   our token system, not verbatim.

8. **Playwright** (already a dependency) — **mandatory verification step**, per this project's own
   stated discipline (see DEVLOG.md — every design change in this project has been verified this
   way, not just eyeballed). After every visual change: load the actual page in a real dev server,
   screenshot it, and inspect real rendered output (including DOM text content, not just a visual
   screenshot guess — DEVLOG.md has a real example of a spacing bug only caught by checking
   `innerText`, not the screenshot). Never report a visual change "done" without this.

## Scope of this pass

Go page by page across all 6 pages (Landing, Dashboard, CaseDetail, TryIt, Architecture,
WhatBroke) plus shared components (TopNav, MetricsBar, PipelineDiagram, AmbientBackground,
SlidingNumber). For each:

1. Run the `redesign-skill` audit checklist against it (generic-AI-design pattern check).
2. Fix what it flags, using the reference tools above where a concrete pattern is missing (motion,
   layout, background texture) — don't add decoration where the audit doesn't call for it.
3. Verify with Playwright (full-page screenshot at minimum; DOM content check for anything
   text-rendering-sensitive like animated numbers or truncated copy).
4. Confirm `tsc -b` stays clean after every change.

## Non-negotiables

- Do not touch backend logic, guardrail enforcement, or agent loop code.
- Do not introduce new npm dependencies for anything the reference tools above are meant to be
  *adapted from*, not installed as packages (Motion Primitives, Watermelon UI, Haikei are all
  reference-only).
- Preserve the already-settled design tokens (Outfit/JetBrains Mono, warm-neutral canvas, single
  teal brand token, desaturated status colors) — refine, don't replace.
- Keep the existing "The model decides. The code enforces." framing and the two-column
  reasoning-vs-guardrail visual distinction on the Architecture page — this is the project's core
  claim, don't dilute it in a redesign.
- Deployment is explicitly out of scope for this task.
- Log real findings/fixes to DEVLOG.md as you go, in the same style as its existing entries
  (specific, with real evidence — not "polished the UI," but what was actually wrong and what
  changed).
