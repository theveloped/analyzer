# CNC Tool Accessibility Analysis — Extracted Approach

This document captures the methodology currently implemented in this repo. The core
idea: instead of ray-casting or simulating the tool point-by-point, we use **whole-mesh
operations** — voxel-based offsets (Minkowski sums), rigid translations, and boolean
operations — so that each check answers an accessibility question **for every face of
the part simultaneously**. Per-face results are boolean masks over the (cached) face
array, which makes combining checks a matter of cheap numpy logic.

All geometry operations are backed by [meshlib](https://meshlib.io/) (`mrmeshpy` /
`mrmeshnumpy`).

## Pipeline overview

The CLI in `main.py` is a staged pipeline. Each stage reads/writes a per-part working
directory so expensive steps are cached between runs:

| File | Contents |
|---|---|
| `fine_mesh.obj` | The healed, canonical analysis mesh |
| `fine_verts.npy` / `fine_faces.npy` | Numpy cache of the same mesh (fast reload, stable face indexing) |
| `directions.npy` | Sampled candidate tool-approach directions, shape `(D, 3)` |
| `accessibility.npy` | Boolean matrix, shape `(D, F)` — face `f` visible from direction `d` |
| `highlights.json` | Face indices flagged by the last CLI run (replayed by the viewer's highlights mode) |

### Stage 1 — `mesh`: canonicalize the input

`load_mesh` (analysis.py) loads an STL and optionally:

- **Heals** it (`heal_mesh`): a zero-distance `generalOffsetMesh` re-mesh through the
  voxel grid (with `HoleWindingRule` sign detection when open boundaries exist),
  followed by decimation. This produces a watertight, well-conditioned mesh — a
  prerequisite for the offset/boolean tricks below.
- **Offsets** it (`offset_mesh`) if a stock allowance is wanted.

Vertices/faces are exported to numpy so that **face indices stay stable** for the rest
of the pipeline — every later result is expressed as indices into `fine_faces.npy`.

### Stage 2 — `directions`: direction-wise visibility via undercut detection

`sample_unity_vector_pairs` samples `N` directions uniformly on the sphere using the
golden-spiral method, restricted to the upper hemisphere and then mirrored, so the
array is laid out as **antipodal pairs** `[d0, -d0, d1, -d1, ...]`. Pairs matter
because both mold halves (injection molding) and 2-setup 3-axis machining approach the
part from opposite directions.

`compute_accessibility` builds the `(D, F)` accessibility matrix with our own
per-face visibility test (`zmap.face_visibility`): a face is *accessible* from
direction `d` iff it faces the tool within a small angular relaxation
(`n·d ≥ −sin(tol)`, default 0.1°, so a wall at exactly 90° is deterministically
front-facing) **and** nothing shadows it per a height map rendered along `d`
(the column top, sampled at the centroid pushed one pixel outward along the
lateral component of the normal with the subpixel-stable bracket-corner min,
must not rise above the face). One heightmap render plus vectorized NumPy per
direction; the map resolution is auto-derived from the bounding box
(`directions --pixel` overrides).

This replaced `mm.findUndercuts`, whose hard front/back-facing verdict flips
between accessible/inaccessible for faces tangent to the direction — the same
wall speckle the raster fixes in zmap.py solved for the tool fields. The
legacy path is kept as `compute_accessibility_meshlib` for A/B comparison,
and the cone-based **relaxation** (`relax_accessibility`: OR of `n` extra
undercut passes on a 1° cone, `--relax`) still works but is no longer needed —
the angular tolerance is built into the visibility test at no extra passes.
On the Aligator test part the isolated-face speckle count from ±Z drops from
~13.8k faces (meshlib) to ~180 (genuine shadow-boundary faces).

### Stage 3 — `options`: mold orientation, face assignment & parting lines

`molding.mold_orientation_search` ranks mold orientations. For each antipodal
pair (the two mold plates): slides are chosen by **greedy set cover** over the
directions perpendicular to the pull axis (within `--slide_tollerance`) —
each pick maximizes newly-covered residual faces and records its **marginal
contribution**, so redundant slides never appear and every slide's purpose is
explicit. Faces neither the pair nor any slide reaches are **internal
undercuts** (inside slides / hand-loads, identified separately). An option is
**feasible** iff that set is empty; ranking is feasible-first, fewer slides,
higher coverage.

Per top option, assignment is **membership based** (pure numpy over the
accessibility rows):

- **membership** — per mesh face, a bitmask of every feature (side A, side
  B, slide j) whose direction reaches it; a face can be valid for none, one
  or several. Faces reached by nothing form **numbered internal undercut
  regions** (connected components — the units a future internal-slide /
  hand-load solver will target).
- **BREP validity** — a feature is valid for a whole BREP face iff it
  reaches every one of its triangles (strict, via `brep_faces.npy`); faces
  with partial coverage but no full cover are **conflicts** (need a split —
  the future non-BREP-edge parting).
- **defaults + overrides** — each BREP face gets a deterministic default
  feature (sides beat slides; A/B ties break by exclusive-coverage
  majority); in the viewer, multi-valid faces render **striped** with all
  their valid colors (selected strong, others faded) and clicking cycles the
  face through its valid features. Choices persist as overrides next to the
  result (`<hash>_overrides.json`, GET/PUT via the API).
- **parting line** — the BREP edges (exported at mesh time as
  `brep_edges.npy` + `brep_edge_pairs.npy`) whose two faces carry different
  current assignments, recomputed client-side on every toggle, rendered with
  the mold/slide direction arrows.

Because accessibility is precomputed, the whole search and all field
derivations are numpy row operations — no geometry is touched.

### Stage 3b — `setups`: CNC setup combinations & per-setup assignment

The CNC mirror of the mold-orientation stage (`machining.py`,
`pipeline.cnc_setups`, the `cnc/setups` analysis): over the same
accessibility matrix, rank the **combinations of setups** that could
machine the part. A machine is modelled as a tilt cone — a setup fixes the
part once with a primary (untilted spindle) direction and covers the union
of the accessibility rows of every sampled direction within `tilt` degrees
of it. Tilt 0 is a plain 2.5D/3-axis setup (exactly one direction); tilt 90
(the default, parameterized) is an indexed 5-axis (3+2) setup that can
swing down to horizontal.

Options are generated per machine by **greedy set cover seeded at every
direction** (each added setup maximizes newly-covered faces and must gain
`--min_setup_faces`), with marginal-gain ties preferring the **antipode of
a chosen setup** — the classic flip re-fixture. The found set is then
re-ordered so the biggest setup machines the bulk first, sequential
marginals are recomputed (setups left redundant by the re-order are
dropped), and duplicates collapse. Ranking is machine-first (a two-setup
job on a plain 3-axis beats booking a 3+2), then fewer setups, coverage,
flips before other ties. Faces no setup covers are **unmachinable regions**
(numbered connected components — EDM / another process / more setups).

Assignment reuses the molding membership machinery one-to-one: membership
bit `s` = setup `s`'s *cover* reaches the face, whole-BREP-face validity,
defaults (the **earliest valid setup** wins — machine as much as possible
early; 254 = conflict/needs split, 255 = unmachinable), striped multi-valid
faces with click-to-cycle overrides in the viewer, and **setup boundary
lines** on the BREP edges between differently-assigned faces (the
witness/blend lines between setups). Per-face fields are derived for the
best option of each distinct (machine, setup count) signature, so a
single-setup 3+2 plan is explorable next to the 3-axis flips instead of
buried under equally-ranked pair variants.

Cover from sampled directions inside the cone both underestimates a
continuous tilt (finite sampling) and overestimates reality — the fixture
and table occlude nothing yet, so a "feasible single 3+2 setup" typically
still needs a flip for the clamped face. The fixture-aware 3D accessibility
recheck is the planned next stage; keeping the cone half-angle a parameter
means it can slot in as a per-setup effective-tilt reduction.

### BREP-aware STEP meshing

STEP input is tessellated through the BREP itself (`brep.py`, OCCT via the
OCP bindings) instead of meshlib's anonymous import: each TopoDS face is
triangulated separately and tagged, then the per-face node arrays are welded
into one conformal mesh — OCCT discretizes every shared BREP edge once and
reuses that polygon on both adjacent faces, so boundary vertices coincide
exactly and the welded mesh shares vertices along BREP edges by construction.
Refinement to analysis resolution is our own conformal midpoint subdivision
(`subdivide_tagged`) because meshlib's subdivide flips edges across face
boundaries; children inherit their parent's face id exactly. The result is
`brep_faces.npy`: every fine triangle knows its source BREP face — the basis
for whole-face mold assignments, the future attributed adjacency graph (AAG)
and draft-angle checks (per-face surface types are recorded in
`brep_meta.json`).

### Stage 4 — per-direction tool checks

These stages answer: *of the faces visible from direction `d`, which can a real tool
actually reach?* Both start from the same construction:

**Machinable volume:** `fix_undercuts(mesh, d)` extrudes all undercut regions down
along `d`, producing the volume a 3-axis machine could theoretically leave when
approaching from `d` (the "shadow-filled" part). All tool checks are done against this
mesh so results are consistent with the chosen direction.

#### `tool` — cutter radius check (ball mill / nose radius)

Morphological **closing** with the tool sphere:

```
radius_mesh = double_offset(undercut_mesh, +r, -r)   # dilate then erode by r
```

Offsetting outward by the tool radius `r` and back inward by `r` fills every concave
feature tighter than the tool radius (internal corners, narrow slots) — exactly the
material a ball-nose of radius `r` cannot remove. Then `map_result_faces` projects the
original mesh's vertices onto the closed mesh (`projectAllMeshVertices`): vertices
whose distance exceeds the tolerance lie inside a filled region, i.e. **unreachable by
this cutter**. Faces whose three vertices all deviate are flagged, and finally
intersected with the accessibility row so only faces relevant to this direction are
reported.

This is the Minkowski-sum insight: one `+r/−r` double offset evaluates the cutter
radius constraint for the entire part in a single operation.

#### `length` — tool length / holder collision check

Models the tool shank+holder envelope by construction:

```
radius_mesh     = single_offset(undercut_mesh, +diameter/2)   # part grown by tool radius
translated_mesh = translate(radius_mesh, d, distance = -(length + diameter/2))
inside_mesh     = boolean(mesh, translated_mesh, InsideA)
```

Growing the part by the tool radius gives the surface on which the **tool axis** may
lie (the classic C-space obstacle construction). Translating that grown volume *up*
along the approach direction by the usable tool length sweeps out where the
**holder** ends up when the tip touches each point of the surface. Any part of the
original mesh that falls *inside* this translated volume is deeper than the tool can
reach without the holder colliding — extracted with a mesh boolean (`InsideA`), mapped
back to face indices via projection distances, and again masked by accessibility.

#### `endmill` — unified tip model (ball, flat and radius end)

A ball mill is exactly a sphere Minkowski sum, so isotropic offsets model it natively.
A flat endmill needs a **disk** perpendicular to the tool axis, and a radius (bull
nose) endmill needs a disk with a rounded rim. Both reduce to one element:

```
tool bottom = disk(D/2 − rc) ⊕ sphere(rc)      rc = corner radius
```

- `rc = D/2` → ball nose (sphere only)
- `rc = 0`   → flat endmill (disk only)
- in between → radius / bull-nose endmill

MeshLib has no in-plane offset (see MeshInspector/MeshLib#2598), but linear transforms
commute with Minkowski sums: `T(A ⊕ B) = T(A) ⊕ T(B)`. So the disk offset is emulated
by the **scale trick** (`scale_along_axis` / `inplane_double_offset` in analysis.py):

1. stretch the mesh along the tool axis by factor `s` (matrix `I + (s−1)·ddᵀ`),
2. run the ordinary isotropic offset by the disk radius,
3. scale back by `1/s`.

The effective structuring element is an oblate ellipsoid: radius `r` in the plane
perpendicular to the axis, `r/s` along it — a disk to within `r/s`. Flat regions
perpendicular to the axis keep a residual rounding of about `0.41·(D − 2rc)/s`, so the
flagging threshold is raised to at least that residual (`endmill_flag_threshold`) and
`--scale` trades runtime (the voxel grid grows ~s× along the axis) against sensitivity.

The closing sequence for the general tip interleaves the two elements (dilations and
erosions each commute, so the disk/sphere order can be nested):

```
stretch → +(D/2−rc) → unstretch     dilate by disk
        → +rc → −rc                  dilate and erode by sphere
stretch → −(D/2−rc) → unstretch      erode by disk
```

Everything else matches the ball-mill flow: run on the undercut-fixed mesh, project
the original vertices onto the closed mesh, flag faces whose deviation exceeds the
threshold, and mask with the accessibility row. Validated end-to-end by
`test_endmill.py` (synthetic pocket + slot part): a ball flags the pocket floor edges,
a flat endmill leaves them clean, all tip types flag the too-narrow slot and the
vertical internal corners — the last only when accessibility was computed with
`--relax`, since strictly vertical walls otherwise count as undercuts and are masked
out.

#### `precompute` / `compose` — the cached height-map engine (zmap.py)

Scaling the analysis to many tools (types, diameters, lengths), many holders and
several directions on the same part needs the expensive work factored out of the
per-tool loop. The key observation: **the undercut-fixed volume is a heightfield
along the approach direction** — air above the visible surface stays air all the way
up. On a heightfield, a Minkowski closing with any rotationally symmetric tool bottom
is exactly a 2D grayscale closing of the *height map* with the tool's radial profile
(the classic Z-map / inverse tool offset construction from CAM). And the height map
itself is just the part's first-hit depth map along the direction — rendering it
subsumes `fixUndercuts` entirely. Every 3D voxel offset in the pipeline collapses to
2D image morphology:

| cached item | geometry cost | answers |
|---|---|---|
| height map (per direction) | one depth render, < 1 s | the undercut-fixed heightfield |
| tip gap field (per direction × tip `D:rc`) | one 2D grayscale closing, ~1 s | per-vertex gap the tip leaves — ball (`rc=D/2`), flat (`rc=0`), bull nose, exactly |
| clearance field (per direction × cylinder radius) | one 2D flat dilation, ~0.2 s | per-vertex height of the tallest obstruction within that radius |

All fields are sampled back **per vertex of the original 3D mesh** immediately
(`project_vertices`/`sample_map`), stored in `<dir>/zcache/dir_<idx>.npz`, and turned
into face masks the same way as the 3D path — so `highlights.json` and the three.js
viewer work unchanged; the 2D maps are purely an internal computation device.

Composition then never touches geometry again:

- **any tool tip** = threshold its gap field at the tolerance;
- **a holder/spindle modelled as stacked concentric cylinders** `(radius, start)`:
  the cylinder collides at a vertex iff `clearance(radius) − start > stickout`, so the
  per-vertex **minimal stickout** is `max_j(clearance(ρ_j) − start_j)`;
- **any tool length**: compare that cached scalar against the stickout — a full
  stickout sweep costs ~20 ms per value;
- the tool's own flank is the disk closing itself: on a heightfield, "the disk fits
  at this depth" is equivalent to "the semi-infinite cylinder fits", so flat/bull
  silhouettes need no separate flank check.

**Gap metric.** Tip gaps are *Euclidean* distances from each vertex to the machined
solid the closed height map describes (material below the closed surface, including
the vertical sheets between adjacent columns): `euclidean_gap` takes, over a window
of nearby pixels, `min sqrt(lateral² + max(closed − h, 0)²)`. The clamp is what makes
90° and near-90° draft walls behave: a wall vertex next to a column whose machined
surface passes below it counts only the sub-pixel lateral distance, so walls swept by
the tool side never flag — critical for 2D-milled parts (exact 90° walls) and molds
(89–91° draft). Gaps up to the `--window` parameter (default 0.3) are exact to pixel
resolution; larger gaps are reported as lower bounds, which is all thresholding
needs. Flat-disk dilations (holder clearance, tool flats) are decomposed into
per-row moving-max chords, so large radii stay cheap while remaining exact.

**The voxel engine behind the same cache.** `DirectionCache(engine="voxel")` fills
the very same per-vertex fields with the 3D pipeline instead — tip gaps from
`endmill_closing` + mesh projection distances, clearance from the in-plane-grown 3D
mesh's top surface — so `compose` works identically on either cache and the engines
can be cross-checked. Note the voxel flat/bull fields inherit the stretch-emulation
residual (`endmill_flag_threshold`), which the zmap engine does not have: its disk is
rasterized directly.

`benchmark_engines.py` runs both engines on a mold-like part (one pocket with exact
90° walls, one with 1° draft). Both engines: no false flags on either wall type,
identical region behaviour, and 97–98% per-vertex classification agreement on
accessible vertices. Per-field wall times (47k faces, pixel 0.1):

| field | zmap | voxel | speedup |
|---|---|---|---|
| tip ball D4 | 0.34 s | 12.1 s | ×35 |
| tip flat D4 | 0.33 s | 46.9 s | ×140 |
| tip bull D4 rc1 | 0.34 s | 29.6 s | ×86 |
| clearance r=8 | 0.13 s | 46.0 s | ×365 |

The zmap side additionally scales gently: 2D morphology is O(pixels × footprint)
versus O(voxels) 3D grids that also grow ~scale× along the stretch axis (the voxel
engine OOMs at pixel 0.05 on a 15 GB machine where the zmap needs a few MB).
Measured on the 656k-face housing: precompute of 2 directions × 6 tips × 3 clearance
radii ≈ 9 s total; composing a complete tool (tip + 2-cylinder holder) with a
5-value stickout sweep ≈ 1.7 s. `test_zmap.py` validates the engine against the same
synthetic expectations as the 3D path plus exact Euclidean fillet-gap and stickout
values.

**Tip-aware holder coupling.** The plain clearance field is vertex-centred: it
assumes the tool axis passes through the contact point with the tip at the vertex
height — exact only for a tool of negligible diameter (or pure bottom contact).
Real tools couple the tip with the holder: a ball touching a wall does so with its
flank, putting the axis up to D/2 away from the contact and the tip `rc` *below*
it, so the required stickout is `depth + rc`, not `depth`. The cache therefore also
stores per-(tip, cylinder radius) fields (`sreq_D_rc_r`, `tip_min_stickout`):

```
min_stickout(v) = min over feasible contact offsets o of
                  clearance_map(axis) - height(v) + profile(o)
    axis = v - o,  feasible iff  height(v) - profile(o) >= tip_position(axis)
```

built from two maps that already exist in the pipeline: the **inverse tool offset**
`tip_position_map` (the grey dilation that is the first half of the closing) and the
per-radius clearance dilation. Contact offsets are ring/angle *sampled* (constant
budget, ~1k samples regardless of tool size; skipping candidates is strictly
conservative), so each field costs O(samples × verts) — ~0.7 s at 34k verts —
independent of the tool diameter. Both dilations are decomposed by the same
Minkowski identity as the 3D path (tool bottom = disk ⊕ sphere → row-decomposed
flat dilation + chunked spherical dilations, `ball(r1) ⊕ ball(r2) = ball(r1+r2)`),
which turns the naive O((D/pixel)²) structuring elements into ~O(D/pixel): a D16
ball-nose ITO drops from 48 s to 6 s, a D10 flat from 11 s to 0.8 s.

Catalog scaling (156 valid tips, a handful of holder radii, 45 lengths): gap fields
≈ 156 × 2-4 s, stickout fields ≈ 156 × radii × 0.7 s — both linear, no length term
(lengths remain thresholds). Arbitrary holder stacks not precomputed are built on
demand at compose time and cached.

**Surface classification.** Face normals are exported once (direction-independent);
the angle between a face normal and any approach direction is then a dot product.
Near-90° faces (vertical walls) are finished by the tool *flank*, not its bottom, so
the gap field does not apply to them — the viewer classifies them as "side-milled"
(and the classification floor / slope / wall / overhang is the seed for assigning
finishing strategies: bottom milling, ball/step milling, side milling, chamfering).

**Why not a vertex-cloud Z-map?** A third option considered: skip the 2D grid and
run the morphology on the mesh vertices themselves, transformed into tool-aligned
coordinates. It is possible, but the closing needs candidate *tool positions*, not
just surface samples — the optimum rarely sits on a vertex — so a scattered-point
implementation ends up binning points into a lateral grid anyway, with neighbour
queries per vertex (O(N·k), k = vertices per footprint, thousands for realistic
tools) in place of vectorized image filters. And its motivating benefits already
exist in the grid pipeline: every field is sampled back per-vertex of the original
mesh immediately (exact round-trip to the 3D mesh and viewer), and the metric
concern is solved by the Euclidean gap above. The grid is purely an internal
computation device — the cached artifacts are per-vertex scalars on the real mesh.

### Auxiliary — `thickness`

Independent of tooling: `pipeline.compute_thickness` rolls meshlib's maximal
inscribed sphere (`computeInSphereThicknessAtVertices`) over the part to give
local wall thickness per vertex, and — run again on an orientation-flipped
copy of the mesh, so the exterior becomes the "inside" — the local **gap**
between opposing outside walls on the same vertex indexing (the inverted-shape
construction without any envelope boolean or cross-mesh mapping). Both are
first-class `injection_molding` analyses (`thickness`, `gaps`) cached as
per-vertex scalar fields under `results/`, rendered as heatmaps with absolute
mm thresholds in the viewer (which replaced the old mean-relative 0.7×/1.3×
flagging); values cap at `2*max_radius` (auto: half the smallest bbox
dimension), so a saturated gap reads as "no opposing wall worth considering".
The `thickness` CLI command flags faces below `--min` (and below `--min_gap`
with `--both`) into `highlights.json`.

### Visualization & the web app

Every CLI stage still ends the same way: dump flagged face indices to
`highlights.json`. Visualization is a FastAPI server (`api/`) plus a
Vite/React/three.js frontend (`frontend/`), launched by `main.py view
<workdir-or-file>` or any command's `--serve` flag. The mesh travels as raw
typed arrays (verts f4, faces u4, face normals f4) and is rendered
un-indexed — 3 vertices per face, so face index × 3 addresses its vertices —
exactly the contract the analyses' per-face masks assume.

The seam for adding analyses and processes is a registry on both sides:

- **Backend** (`processes/`): a `ProcessDef` per manufacturing process
  (prep, cnc, injection_molding, sheet_metal) holding `AnalysisDef`s. Each
  analysis declares typed params (rendered as an auto-generated form in the
  UI) and a `run(workdir, params, progress)` that calls the shared
  `pipeline.py` functions and writes into the per-part cache: CNC fields go
  to `zcache/dir_*.npz` via `DirectionCache`, generic results to
  `results/<process>/<analysis>/<paramhash>.json[.npz]`. The manifest
  endpoint rebuilds from disk on every request, so CLI- and UI-computed
  fields are interchangeable. Jobs run on a single worker thread (meshlib
  must not run concurrently) and are polled for progress.
- **Frontend** (`frontend/src/processes/`): a `ProcessPlugin` per process
  contributes view modes (`paint(ctx)` fills the face color buffer from
  fetched fields), optional custom controls and click-to-inspect lines.
  Generic mask/heatmap/highlights painters live in `colorizers/core.ts`, so
  a new process gets rendering for free; interactive thresholds (tolerance,
  stickout, holder stack) stay client-side over the cached per-vertex
  fields.

## The general recipe

Every check in the repo follows the same pattern:

1. **Reduce the tool/process constraint to a volume construction** on the whole part:
   - approach direction → undercut analysis / `fixUndercuts`
   - cutter radius → offset closing (`+r` then `−r`)
   - shank & holder length → offset (radius) + translation (length) + boolean
2. **Diff the construction against the original part** — either by projecting all
   vertices and thresholding distance (`map_result_faces`) or by a boolean
   `InsideA` — to get a per-face boolean mask.
3. **Combine masks with numpy logic** (accessibility ∧ radius-ok ∧ length-ok, unions
   over direction sets) — cheap, and where the "whole part at the same time" payoff
   lands.

## Known rough edges (as found in the code)

- `projectAllMeshVertices`' limits are *squared* distances (`upDistLimitSq`), which
  the `map_result_faces` call sites treat as plain distances.
- `get_inside_indices` loops face-by-face building one-bit bitsets — correct but very
  slow; `inside_test.py` is a sandbox exploring a bulk-mapping alternative.
- `toolart.py`, `drawer.py`, `tooltest.py` are standalone sketches for drawing tool
  geometry (SVG/ASCII) — presumably groundwork for parameterizing real tool/holder
  stacks, not yet wired into the analysis.
