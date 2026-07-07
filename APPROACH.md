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

#### `endmill` — flat endmill (work in progress)

A ball mill is exactly a sphere Minkowski sum, so offsets model it natively. A flat
endmill is a cylinder, which offsets don't directly express. Two attempts live in the
code:

1. (Commented out) Approximate the cylinder by **sampling K translations on a circle**
   of radius `diameter/2` perpendicular to the axis (`generate_circle_translations`),
   running the inside-boolean test at each station, and unioning the per-face results.
2. (Currently active) Reuse the `double_offset` closing as an approximation — correct
   for the nose radius but not for the flat bottom.

This is the main open geometric problem in the repo.

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

- `map_result_faces` uses Python `and` between two numpy conditions in the
  `min_range and max_range` branch — this raises on arrays; only the single-bound
  branches currently work. Also `projectAllMeshVertices`' limits are *squared*
  distances (`upDistLimitSq`), which the call sites treat as plain distances.
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
