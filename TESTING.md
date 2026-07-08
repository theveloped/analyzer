# Testing the endmill accessibility analysis locally

## Setup

```bash
git fetch origin
git checkout claude/cnc-tool-accessibility-analysis-rmm8wq
pip install meshlib numpy loguru
```

Requires meshlib >= 3 (the code also still runs on 2.x via API fallbacks).
STEP input needs the pip meshlib wheel (bundles the STEP reader); STL always works.

## Sanity check

```bash
python test_endmill.py
```

Builds a synthetic pocket + slot part and verifies ball / bull-nose / flat
behaviour end to end (takes a few minutes).

## Analyzing a part

Stage 1 — mesh (accepts .stl, .stp, .step; the result is cached in a working
directory named after the part). Two preparation modes:

```bash
# clean CAD input (STEP): keep the exact geometry, refine for reporting only
python main.py mesh tests/testpart_42.stp --subdivide 0.6

# dirty STL: voxel-remesh it watertight (this DOES change the geometry:
# edges get rounded to the voxel size - do not use it on clean STEP)
python main.py mesh part.stl --heal --tollerance 0.15
```

`--subdivide` splits edges without moving anything (planar faces stay planar,
walls stay exactly 90°); face size is the reporting resolution, since results
are per-face masks over per-vertex fields.

Stage 2 — accessibility per direction. `--axes` prepends exact ±X/±Y/±Z as
indices 0..5 (so +Z is index 4). `--relax` is important for endmill work:
without it, strictly vertical walls count as undercuts and can never be
flagged by the tool checks:

```bash
python main.py directions testpart_42 --count 16 --axes --relax
```

Stage 3 — tool tip check for one (direction, diameter, corner radius):

```bash
# flat endmill D6
python main.py endmill testpart_42 4 --diameter 6 --corner_radius 0 --tollerance 0.15
# bull nose D6 rc1
python main.py endmill testpart_42 4 --diameter 6 --corner_radius 1 --tollerance 0.15
# ball nose D6
python main.py endmill testpart_42 4 --diameter 6 --corner_radius 3 --tollerance 0.15
```

Unreachable-face indices land in `<dir>/highlights.json`; add `--serve` to
open the three.js viewer (Windows only as written — elsewhere run
`python -m http.server` in the repo root and open `index.html` served from
the working directory).

Tool length checks are still the separate `length` command (ball model):

```bash
python main.py length testpart_42 4 --diameter 6 --length 120
```

## Fast path — testing a whole tool catalog

The `endmill` command runs one 3D voxel closing per tool: exact, but minutes
per (direction, D, rc). To sweep a catalog, use the Z-map engine instead
(`zmap.py`, `precompute` + `compose`): the undercut-fixed volume is a
heightfield along the approach direction, so each tool tip becomes a 2D
grayscale closing of a rendered depth map, cached per direction as
per-vertex scalar fields:

```bash
# once per direction: depth map + gap field per tip + clearance field per shank/holder radius
python main.py precompute testpart_42 --directions 4 5 --pixel 0.1 \
    --tips 6:0 6:1 6:3 10:0 10:2 --clearances 3 5 8

# then any full tool assembly is a sub-second numpy threshold over the cache
python main.py compose testpart_42 4 --diameter 6 --corner_radius 0 \
    --stickout 120 --holder 5:0,8:40 --serve
```

`--holder` stacks concentric cylinders (radius:start-height from the tip), so
shank, holder and spindle nose are all one string; `--sweep 93 120 150 210`
reports coverage per stickout without recomputing anything. Lengths are free:
the clearance fields already encode the tallest obstruction within each
radius, so all 45 catalog lengths are thresholds over the same field.

## Interactive viewer

Everything the caches know can be inspected interactively:

```bash
python main.py view testpart_42            # exports <dir>/viewer/ and serves it
```

Views: unified verdict (reachable / tip-blocked / holder-blocked /
inaccessible), accessibility, tip gap heatmap, required-stickout heatmap,
engine diff (zmap vs voxel, needs the same tip precomputed with both
engines), and the last CLI highlights.json. Tolerance, stickout and the
holder stack are recomputed live in the browser from the cached per-vertex
fields — no Python round trips — and clicking a face prints its exact gap /
clearance / accessibility values for step-by-step debugging. The bundle is
self-contained (three.js is vendored into it), so it also works offline.

Catalog math: of the 16 x 13 nose-radius/diameter grid, ~156 combinations are
valid (rc <= D/2). Per direction that is ~156 tip closings at ~8 s each
(pixel 0.1 on a 100 mm part) ~ 20 min once, plus ~1 s per clearance radius —
after which every (tip, length, holder) query composes in ~0.2 s. The exact
voxel `endmill` path stays useful as a spot-check (it agrees within a couple
percent of flagged faces: 885 vs 904 on testpart_42, the difference being the
holder/stickout constraints only `compose` models).

## Resolution knobs

Three independent resolutions stack; jagged results usually mean the first
one is wrong:

| Knob | What it controls | Cost |
|---|---|---|
| `mesh --subdivide` (STEP) or `--heal --tollerance` (STL) | geometry fidelity + how finely results localize (faces are the reporting unit) | faces ~ area / edge² |
| `precompute --pixel` | field accuracy: gap/clearance quantization, wall noise threshold (2.5 × pixel) | maps + closings ~ 1/pixel², fields ~ verts |
| `precompute --window` | range in which gaps are Euclidean-exact (keep ≥ wall threshold) | per-vertex window² |

Reference: testpart_42 exact STEP at `--subdivide 0.6` = 683k faces; full
precompute (4 tips, 3 clearance radii, 12 coupled stickout fields) at
`--pixel 0.05` ≈ 6 min for one direction; composing any tool stays < 1 s.

## Knobs vs runtime (exact voxel path)

Reference timings, testpart_42 (100 x 100 x 40 mm) at voxel 0.15 mm, one
direction: ball ~1 min, flat ~4.5 min, bull nose ~6 min. The flat/bull cost
is dominated by the anisotropic stretch (`--scale`, default 10): the voxel
grid grows ~scale x along the tool axis.

- `--tollerance` is the voxel size; halving it costs ~8x. 0.3 mm is fine for
  roughing-level answers, 0.1-0.15 mm for fine detail.
- `--scale` bounds flat-floor sensitivity: deviations below
  ~0.41 * (D - 2 rc) / scale can't be distinguished from the disk
  approximation residual (the command warns and raises its threshold when
  that bound exceeds the tolerance).
