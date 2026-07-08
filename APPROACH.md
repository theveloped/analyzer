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
| `highlights.json` | Face indices to color red in the three.js viewer (`index.html` + `server.py`) |

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

`compute_accessibility` then runs `mm.findUndercuts(mesh, dir)` per direction: a face
is *accessible* from direction `d` iff it is **not** an undercut when looking along
`d`. Inverting the undercut bitset gives one boolean row per direction — the
`(D, F)` accessibility matrix. This is the "everything at once" primitive: one meshlib
call classifies all faces for a direction.

Optional **relaxation** (`relax_accessibility`): faces that are exactly tangent to a
direction (vertical walls) flip between accessible/inaccessible due to numerical
noise. To tolerate this, `generate_cone_vectors` builds `n` directions on a small cone
(e.g. 1°) around the nominal direction, recomputes undercuts for each, and ORs the
results. A face counts as accessible if any direction within the tolerance cone can
see it.

### Stage 3 — `options`: pick setups / parting directions

`find_combinations_matching_best` searches for the best set of approach directions:

- For each antipodal pair, union the two accessibility rows → coverage of a
  two-sided setup (or a mold's two halves).
- Optionally add up to `max_slides` extra directions that are **perpendicular** to the
  pair axis (validated by cosine tolerance in `find_valid_directions`) — these model
  mold slides or extra machining setups — and union those rows in too.
- Score every combination by `covered_faces / total_faces` and rank.

Because accessibility is precomputed, this combinatorial search is pure numpy `any()`
over rows — no geometry is touched.

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
threshold, and mask with the accessibility row.

**Depth / holder check** (`--length` and `--holder_diameter`): because cross sections
of the undercut-fixed part only shrink along the approach direction, two things hold:

1. the cylindrical tool body adds no constraint beyond the tip closing (if the tip
   disk fits at some height, every higher disk fits), and
2. the holder only needs checking at its **bottom** disk.

So a tip contact point `q` is depth-reachable iff the holder disk clears the part at
`q + L·d`. The set of blocked points is one construction (`endmill_depth_obstacle`):
the undercut-fixed part grown by the holder radius **in-plane** (the same scale
trick), translated down the tool axis by the usable length `L`; part faces inside it
(mesh boolean `InsideA`) are deeper than the tool can reach. This supersedes the older
`length` command, which approximated the holder with a spherical offset. The
`highlights.json` written by `endmill` now carries color groups: red for
tip-unreachable faces, orange for depth-blocked ones (`index.html` renders both).

Validated end-to-end by `test_endmill.py` (synthetic pocket + slot part): a ball flags
the pocket floor edges, a flat endmill leaves them clean, all tip types flag the
too-narrow slot and the vertical internal corners, and with a holder wider than the
pocket a usable length shorter than the pocket depth flags the floor while a longer
one clears it. The vertical-corner cases only appear when accessibility was computed
with `--relax`, since strictly vertical walls otherwise count as undercuts and are
masked out.

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
surface passes below it counts only the lateral distance — measured to the nearest
*edge* of the column, so a vertex exactly on a machined face reads ~0 no matter which
pixel it falls in — and walls swept by the tool side never flag. Critical for
2D-milled parts (exact 90° walls) and molds (89–91° draft).

The window always covers at least the tool radius (plus the `--window` accuracy
floor): a smaller window would make wall vertices near unreachable wedges (vertical
corners, too-narrow slots) miss every machined column and fall back to the *vertical*
distance to the surface above them — painting whole walls with depth gradients
instead of the small lateral distance to the machined boundary. Offsets are visited
in rings of increasing lateral distance with per-vertex early exit, so the larger
window costs little (machined regions resolve in the first rings). Beyond the window,
gaps are lower bounds, which is all thresholding and display saturation need.

**Clearance metric.** Holder clearance maps are exact flat disk dilations at full
resolution: the disk is decomposed into one 1D horizontal running-max per row offset
(each row covers a chord), `O(radius/pixel)` linear passes with no big 2D footprint.
An earlier max-pooled implementation smeared tall edges outward by up to a pooled
block — isolated pixels on boss tops and stripe bands inside bores read the full
obstruction height — which showed up as speckle in the required-stickout heatmap.

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

Independent of tooling: `computeInSphereThicknessAtVertices` (maximal inscribed
sphere) gives local wall thickness per vertex; faces thinner than 0.7× or thicker than
1.3× the mean are flagged. Useful for molding/DFM feedback with the same
highlight-and-view workflow.

### Visualization

Every stage ends the same way: dump flagged face indices to `highlights.json`, then
`server.py` serves `index.html` — a three.js viewer that loads `fine_mesh.obj`
(un-indexed, 3 vertices per face, so face index × 3 addresses its vertices) and paints
highlighted faces red.

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
- `find_combinations_matching_best` duplicates its sort/truncate block, and the slide
  search enumerates `combinations` over *all* perpendicular directions, which explodes
  for large direction counts.
- `get_inside_indices` loops face-by-face building one-bit bitsets — correct but very
  slow; `inside_test.py` is a sandbox exploring a bulk-mapping alternative.
- The `serve` flow is Windows-specific (`webbrowser.get('windows-default')`) and the
  viewer relies on CDN scripts.
- Per-face vertex coloring in `index.html` assumes the OBJ loader keeps faces
  un-indexed; fine for meshes written by `save_obj_mesh`, but fragile in general.
- `toolart.py`, `drawer.py`, `tooltest.py` are standalone sketches for drawing tool
  geometry (SVG/ASCII) — presumably groundwork for parameterizing real tool/holder
  stacks, not yet wired into the analysis.
