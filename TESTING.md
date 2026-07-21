# Testing the analyses locally

## Setup

```bash
pip install -r requirements.txt          # meshlib, numpy, scipy, loguru, fastapi, uvicorn
cd frontend && npm install && npm run build && cd ..   # builds the viewer UI once
```

Requires meshlib >= 3 (the code also still runs on 2.x via API fallbacks).
STEP input needs the pip meshlib wheel (bundles the STEP reader); STL always works.
The web UI needs node >= 18 for the one-time `npm run build` (or `npm run dev`
while hacking on it).

## Sanity check

```bash
python test_zmap.py
```

Builds a synthetic pocket + slot part and verifies ball / bull-nose / flat
tool behaviour against analytic expectations.

## Analyzing a part

Stage 1 — mesh (accepts .stl, .stp, .step; the result is cached in a working
directory named after the part). `--resolution` is the single analysis
resolution: it drives the BREP tessellation sag (resolution/8), the
subdivide edge target (= resolution), the heal voxel size (resolution/5)
and the default zmap pixel of every later stage (resolution/5, via
`mesh_meta.json`). Two preparation modes:

```bash
# clean CAD input (STEP): exact geometry, curved faces tessellated at
# analysis scale, everything refined to the resolution
python main.py mesh tests/testpart_42.stp --resolution 0.6

# dirty STL: voxel-remesh it watertight (this DOES change the geometry:
# edges get rounded to the voxel size - do not use it on clean STEP)
python main.py mesh part.stl --heal --resolution 0.75
```

When left unset the resolution is automatic: 0.5% of the bounding-box
diagonal, clamped to 0.3–2 mm, so every analysis mesh has bounded,
well-spaced vertices even on large flat faces — a curvature-driven
tessellation alone leaves those nearly empty, which starves the
vertex-anchored thickness/skeleton analyses. `--subdivide` and
`--deflection` remain expert overrides; `--subdivide 0` stores the input
tessellation untouched.

Stage 2 — accessibility per direction. `--axes` prepends exact ±X/±Y/±Z as
indices 0..5 (so +Z is index 4). Near-vertical walls are handled by the
visibility test's angular tolerance (`--tollerance`, degrees) — they count
deterministically as front-facing:

```bash
python main.py directions testpart_42 --count 16 --axes
```

## Testing a whole tool catalog

Tool reachability runs on the Z-map engine (`zmap.py`, `precompute` +
`compose`): the undercut-fixed volume is a heightfield along the approach
direction, so each tool tip becomes a 2D grayscale closing of a rendered
depth map, cached per direction as per-vertex scalar fields:

```bash
# once per direction: depth map + gap field per tip + clearance field per shank/holder radius
python main.py precompute testpart_42 --directions 4 5 \
    --tips 6:0 6:1 6:3 10:0 10:2 --clearances 3 5 8

# then any full tool assembly is a sub-second numpy threshold over the cache
python main.py compose testpart_42 4 --diameter 6 --corner_radius 0 \
    --stickout 120 --holder 5:0,8:40 --serve
```

Unreachable-face indices land in `<dir>/highlights.json`; the "Last CLI
highlights.json" view in the viewer replays exactly what the command
flagged.

`--holder` stacks concentric cylinders (radius:start-height from the tip), so
shank, holder and spindle nose are all one string; `--sweep 93 120 150 210`
reports coverage per stickout without recomputing anything. Lengths are free:
the clearance fields already encode the tallest obstruction within each
radius, so all 45 catalog lengths are thresholds over the same field.

## Interactive viewer (web app)

Everything the caches know can be inspected interactively, and everything
the CLI can compute can also be launched from the browser:

```bash
python main.py view testpart_42            # open the app preloaded on a workdir
python main.py view tests/testpart_42.stp  # register a raw STEP/STL and open on it
python main.py view testpart_42 --port 9000 --no-browser --timeout 3600
```

The app is a FastAPI server (`api/`) plus a Vite/React frontend
(`frontend/`, built once with `npm run build`; `npm run dev` proxies `/api`
to port 8000 for live-reload hacking — start `uvicorn api.app:app` next to
it). The server treats the parent directory of the target as the parts
root, so sibling working directories all show up in the part picker, and
the UI can upload a new STEP/STL, mesh it, sample directions, precompute
tool fields and run any other registered analysis — all through the same
`pipeline.py` code the CLI uses, writing to the same per-part cache. A field
precomputed in the UI is immediately visible to `compose` on the CLI and
vice versa.

Processes are tabs (CNC machining, injection molding, sheet metal —
the registry seam for future DFM rules). CNC views: unified verdict
(reachable / tip-blocked / holder-blocked / side-milled / inaccessible),
accessibility, surface class, tip gap heatmap, required-stickout heatmap,
and the last CLI highlights.json. Tolerance, stickout and the
holder stack are recomputed live in the browser from the cached per-vertex
fields — no Python round trips — and clicking a face prints its exact gap /
clearance / accessibility values for step-by-step debugging. Injection
molding shows ranked parting-direction options with per-option coverage
masks.

`frontend/smoke.mjs` is a Playwright smoke test that walks every view mode
against a running server (`node smoke.mjs` inside `frontend/`, with
`CHROMIUM_PATH` pointing at a Chromium binary).

## Mold orientation, face assignment & parting lines

STEP parts mesh through the BREP (exact triangle→face mapping, shared
vertices along BREP edges). `--resolution` is the single analysis-resolution
knob: curved faces tessellate at a `resolution/8` sag budget (their true
shape at analysis scale), all edges refine to `resolution`, and later stages
default their zmap pixel to `resolution/5` (from `mesh_meta.json`).
`--deflection` / `--subdivide` / `--pixel` remain expert overrides:

```bash
python main.py mesh tests/testpart_42.stp -o testpart_42 --subdivide 1.0
python main.py directions testpart_42 --count 8 --axes
python main.py options testpart_42 --max_slides 2      # ranked feasibility table
python test_mold.py                                    # analytic fixtures
```

`options` ranks antipodal plate pairs with greedy perpendicular slides
(per-slide marginal face counts), a FEASIBLE/infeasible verdict and the
internal-undercut count. In the UI (injection molding tab → "Mold
orientation assignment"): pick a ranked option; whole BREP faces are
colored by their assigned feature — faces valid for several features
render striped (selected color strong, other valid colors faded), conflict
faces (no single feature covers every triangle) get their own class, and
unreachable faces form numbered internal undercut regions. **Click a
striped face to cycle it through its valid sides/slides** — the parting
line (drawn along BREP edges between differently-assigned faces) jumps
accordingly, and the choice is saved to the workdir. The "BREP faces" view
mode (any tab) colors the mesh by source BREP face.

**Splitting conflict faces:** enable "split faces (two boundary clicks)" in
the assignment controls (also in CNC → Setup assignment — cuts are shared
per part). Click a face to show its wire snap points (white = corners, gold
= edge midpoints), then click two of them: the face is cut along the mesh
edges between the points, the assignment analysis re-runs automatically and
the pieces classify (and cycle/override) individually, with the parting
line following the cut. Cuts stack — a cylinder wall or annulus needs two
before it separates — and "undo last cut" restores the previous state
exactly (older results become valid again). `python test_splits.py` covers
the machinery end to end.

## Wall thickness and gaps (rolling sphere)

Two per-vertex fields from the maximal inscribed ("rolling") sphere: wall
thickness inside the part, and the gap between opposing outside walls
(the same search on the orientation-flipped mesh). Both cap at
`2 * max_radius` (auto: half the smallest bounding box dimension), so a
saturated gap means "no opposing wall worth considering".

```bash
python main.py thickness aligator --min 1.0 --both --min_gap 0.5 --serve
python test_thickness.py     # analytic plate/gap probes
```

In the UI: injection molding tab → Compute "Wall thickness" / "Wall gaps /
clearance" → the thickness and gaps heatmap view modes, with min-threshold
and heatmap-max inputs recomputed live; clicking a face prints both maps'
values at its three vertices.

## Wall skeleton, fill flow & sprue proposals

The rolling-sphere centers form a medial skeleton graph (`wall_skeleton`,
validated by `test_skeleton.py` against analytic plate/rib midplanes). The
"Skeleton & fill flow" view runs a client-side Dijkstra over
`length / r^4` edge resistances from a clicked gate.

Two cleanup passes keep the clustered graph structural: curvature-artifact
nodes (at convex rounded rims the inscribed sphere measures the fillet
radius, not the wall) are **absorbed** into the wall they hug (their
spheres overlap a much larger neighbor's — genuinely thin webs/hinges
extend away and survive), and edges spanning far beyond their endpoint
spheres (degenerate sliver triangles that would tether whole regions
through one phantom bridge) are **pruned**. Every skeleton result also
carries a **mesh spec** — p95 mesh edge vs the median measured wall
thickness, status ok/marginal/coarse — and downstream analyses surface a
warning when the shared analysis mesh is under-resolved for its walls.

`sprue_proposals` ranks injection-gate locations automatically over that
same flow model: surface candidates (grid-decimated, one per skeleton
node) pass hard filters (min gate thickness; slide/undercut/forbidden-side
faces when a `mold_orientation` result exists — skipped gracefully
otherwise), then a multi-source Dijkstra scores each on p95/max fill
resistance, unreached volume, overpack exposure, thick-region packing
access through wide channels, and weld-line/air-trap indicators. Scores
are p5–p95 normalized and weight-combined; every proposal carries its
subscores and human-readable pros/cons.

```bash
python test_skeleton.py   # analytic midplanes + result serving round-trip
python test_sprue.py      # plate → center gate, thick boss → packing access,
                          # T-part → junction balance, hard filters, round-trip
```

In the UI: injection molding tab → Compute "Sprue / gate proposals" → the
"Sprue proposals" view: ranked markers on the part (white = best), a
clickable proposal list with the pros/cons explanation, per-proposal fill
painting with a weld-front overlay, an all-candidates score heatmap
toggle, and "open in fill-flow mode" to carry the gate into the
interactive view.

## Ejector pins (sticking model + stiffness solve)

`ejection_sticking` estimates how the part grips the mold after shrinkage:
per-face draft angle vs the pull axis (from the mold orientation when
present, +Z otherwise), a grip mask (draft below a threshold, restricted
to B/core-reachable faces when orientation data exists), and a release
traction `p_shrink · area · max(mu·cosθ − sinθ, 0)` per face — stored as a
per-vertex heatmap and aggregated per skeleton node. Pin layouts are then
solved interactively by `POST /api/parts/{id}/ejector/simulate`: a 1-DOF
deflection model along the pull axis over the clustered skeleton (edge
springs `3·E·I/L³` with `I = (π/4)r⁴`), pins as supports, sticking forces
as loads — returning the deflection field plus per-pin force, pressure and
utilization against an allowable. Deflections are indicative (the spring
constant is uncalibrated); comparisons between layouts are the product.

```bash
python test_ejector.py   # analytic wall sticking, chain-spring deflection,
                         # pin-layout comparisons, equilibrium, round-trip
```

In the UI: injection molding tab → Compute "Ejection sticking" → the
"Ejector pins" view: sticking-force heatmap (or a draft-angle view via the
checkbox), click the part to place pins at the selected diameter, click a
pin to remove it; the deflection heatmap, utilization-colored pin markers
and the per-pin force/pressure list update after every change.

Catalog math: of the 16 x 13 nose-radius/diameter grid, ~156 combinations are
valid (rc <= D/2). Per direction that is ~156 tip closings at ~8 s each
(pixel 0.1 on a 100 mm part) ~ 20 min once, plus ~1 s per clearance radius —
after which every (tip, length, holder) query composes in ~0.2 s.

## Resolution knobs

Everything derives from `mesh --resolution` (auto = 0.5% of the part
diagonal, clamped 0.3–2 mm); the per-stage overrides stack as:

| Knob | What it controls | Cost |
|---|---|---|
| `mesh --resolution` | geometry fidelity (BREP sag = resolution/8) + how finely results localize (faces are the reporting unit) + downstream pixel defaults | faces ~ area / resolution² |
| `--pixel` override (directions/precompute/compose/verdict) | field accuracy: gap/clearance quantization, wall noise threshold (2.5 × pixel); default resolution/5 | maps + closings ~ 1/pixel², fields ~ verts |
| `precompute --window` | range in which gaps are Euclidean-exact (keep ≥ wall threshold) | per-vertex window² |

Reference: testpart_42 exact STEP at `--subdivide 0.6` = 683k faces; full
precompute (4 tips, 3 clearance radii, 12 coupled stickout fields) at
`--pixel 0.05` ≈ 6 min for one direction; composing any tool stays < 1 s.
