# v2 workspace — production-engineer UI

An alternative front-end that runs **side by side** with the original viewer so
it can be grown incrementally and the old one retired bit by bit.

- `index.html` → `src/main.tsx` — the original plain-CSS viewer (unchanged).
- `v2.html` → `src/v2/main.tsx` — this app.

Both are built by one `npm run build` and both are dev-served by `npm run dev`
(open `/` or `/v2.html`).

## What it reuses (the "bits" being kept)

The heavy, proven machinery is imported directly from the original app — this is
what makes an incremental migration real rather than a rewrite:

- `src/api/*` — the API client + types.
- `src/viewer/*` — the three.js `Scene3D`, the imperative `controller` (`attach`,
  `selectPart`, `flyToFocus`), and the job runner (`runAnalysisJob`).
- `src/state/store.ts` — the shared zustand store (part, manifest, jobs, legend,
  stats, viewer params).
- `src/processes/injection` — the existing `thickness` / `gaps` view modes and
  their client-side threshold recolor.

## What it adds (the new shell)

- `components/ui/*` — a small vendored subset of shadcn/ui (Tailwind v4) plus a
  compact `sidebar` port (the "inset" layout: collapsible left nav + floating
  content card).
- `nav/AppSidebar.tsx` — the left **outer** sidebar: cross-part navigation and
  global settings (advanced-mode + theme). Deliberately holds things *not* tied
  to the current card.
- `workspace/*` — the single-part workspace that fills the card (page 4a):
  top bar, left pipeline of checks, center viewer with a floating analysis
  toolbar / legend / orientation triad, and a right **in-card** settings rail
  scoped to the active check.
- `analyses.ts` — the engineer-facing catalog. Starts with **wall thickness**
  and **gap/clearance** (the checks nearly always run). Each exposes only the one
  threshold an engineer sets; the computational-geometry knobs (`max_radius`,
  `sharp_deg`, …) are set correctly by default and hidden behind **Advanced**.
- `store.ts` — v2-only UI state (advanced reveal, theme, per-analysis compute
  params).

## Design intent

Tailored to production engineers, not computational-geometry engineers: the
primary surface is thresholds → run → findings. "Advanced mode" (left sidebar)
reveals the extra analyses and the compute knobs when they're actually needed.

## Growing it

Add an analysis by appending to `ANALYSES` in `analyses.ts` (id = an existing
viewer `modeId`). More structural levels from the wireframe — the project /
assembly status board (3a), card wall (3b), org home (3c), and branch/fork
compare (2a/2b) — are the next pieces to build on top of this shell.
