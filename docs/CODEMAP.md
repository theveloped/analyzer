# Code map & data contracts

Where everything lives and the exact shapes of what gets written to disk. When code
and this document disagree, the code wins — update this file in the same commit.

## Python modules (repo root)

| File | Role | Key entry points |
|---|---|---|
| `main.py` | argparse CLI, one subcommand per stage; thin wrappers over `pipeline.py` | `mesh`, `directions`, `options`, `thickness`, `slender`, `span`, `flow`, `setups`, `verdict`, `precompute`, `compose`, `serve`, `view` |
| `pipeline.py` | The shared orchestration layer used by both CLI and API jobs. All workdir I/O funnels through here | `mesh_part`, `compute_directions`, `mold_orientation`, `compute_thickness`, `compute_ray_thickness`, `pocket_slenderness`, `span_ladder`, `thin_span`, `wall_skeleton`, `flow_voxels`, `flow_fill_solve`, `flow_frozen_skin`, `flow_fill`, `precompute_fields`, `compose_tool`, `write_highlights`, `load_mesh_arrays`, `parse_tips`, `parse_holder` |
| `analysis.py` | meshlib geometry primitives: loading, healing, subdivision, direction sampling, accessibility | `load_mesh`, `heal_mesh`, `offset_mesh`, `subdivide_mesh`, `sample_unity_vector_pairs`, `compute_accessibility` |
| `zmap.py` | 2D height-map (Z-map) engine: renders depth maps, grayscale closings, Euclidean gaps, clearance fields; owns the per-direction cache | `render_heightmap`, `face_visibility`, `close_heightmap`, `slenderness_ladder`, `euclidean_gap`, `clearance_heightmap`, `tip_aware_min_stickout`, `DirectionCache`, `compose_unreachable` |
| `molding.py` | Mold orientation search & face assignment (pure numpy over accessibility rows) | `mold_orientation_search`, `membership_field`, `internal_regions`, `brep_validity`, `brep_defaults`, `face_adjacency` |
| `brep.py` | BREP-aware STEP meshing via OCCT (OCP bindings): per-face tessellation, welding, conformal subdivision | `mesh_step`, `subdivide_tagged` |
| `splits.py` | User face splits: cuts along mesh edges relabel a BREP face's triangles into sub-face ids (no remeshing, framework-free numpy/scipy) | `add_cut`, `undo_last`, `clear`, `state`, `replay`, `cut_path`, `effective_face_ids`, `sanitize_retired` |
| `aag.py` | Attributed adjacency graph over BREP faces (instapart port): face convexity, edge tangency continuity + signed dihedrals, persisted stage artifact with **deterministic face/edge ids** (face = `brep.iter_faces` order, edge/vertex = `TopExp.MapShapes` order) | `build_aag`, `save_aag`, `load_aag`, `get_sheet_base`, `get_connected_subgraph`, `axial_span` |
| `step_import.py` | XCAF STEP front-end (instapart port): assembly tree + per-instance placements, face colors/names, semantic PMI; explodes assemblies into content-addressed child part workdirs, ids bridged geometrically (area+centroid signatures) | `import_step`, `extract_part_attributes`, `read_document`, `build_tree` |
| `sheet.py` | Sheet-metal recognition + flat pattern orchestration over the AAG | `detect_sheet`, `flat_pattern` |
| `unfold.py` | K-factor unfold onto the Z=0 plane: pcurve re-hosting with allowance scaling, BFS transform chains, topological outline loops, bend lines | `Unfolder`, `bend_allowance` |
| `tube.py` | Straight constant-section profile classification (round/rect/square) + outer-shell unroll | `analyse_profile`, `grouped_graph`, `cluster_directions` |
| `machining_features.py` | Rule-based CNC feature recognition (holes family + pockets) from concave C2 groups and coaxial stacks | `recognize_features` |
| `dxfexport.py` | DXF export of stored flat-pattern results (ezdxf; layers OUTLINE/BENDS/ENGRAVING) | `export_dxf` |
| `pressbrake/` | Press-brake bend planning (instapart port): panel/hinge kinematic model with bend deduction, sampled collision oracle + analytic REQUIRED/FORBIDDEN machine-X interval envelope, YAML punch/die/machine catalogues, segmented-tooling knapsack, bitmask-memoized sequence search. `adapter.py` builds the KinematicGraph from AAG + Unfolder (the only OCP module) and computes the per-vertex fold coordinates (`compute_fold_mesh`: exact per-panel rigid maps from the unfold chains + cylinder unrolls for bend zones); `foldmesh.py` (pure numpy) poses them at any hinge angles — rigid panels + fixed-neutral-radius progressive-wrap bend zones, exactly watertight at both zone edges (mirrored line-for-line by `frontend/src/processes/sheetmetal/foldmath.ts`); `meshcheck.py` verifies a final plan with meshlib `findCollidingTriangles` against extruded eps-inset tool sections (active bend zones excluded from punch/die tests — the mesh analogue of the oracle's pivot exclusion); `report.py` is the plain-dict serialization. Envelope perf split: `compute_sweep` (tool-independent SweepProfile, cached per (mask, group, rotation) in the search) vs `compute_envelope` (obstacle tests; exact numpy annular-sector predicate against pre-buffered obstacles — exclusion and t/2 moved to the obstacle side by Minkowski identity, PEN_EPS erosion absorbs designed tangency; `collision.py` stays the unchanged sampling oracle) | `adapter.build_kinematic_graph`, `adapter.compute_fold_mesh`, `foldmesh.pose_vertices`, `meshcheck.check_plan`, `plan.plan_graph`, `plan.plan_search`, `envelope.compute_sweep`, `machine.load_machine/punches/dies` |
| `processes/` | Backend analysis registry (see below) | `processes.base`, one module per process |
| `api/` | FastAPI server (see routes below) | `api.app.create_app`, `serve_app` |
| `utils.py`, `pathtypes.py` | Small helpers: dirs, timing decorator, argparse `PathType` | |
| `nesting.py` | 2D contour nesting sandbox (sheet metal), standalone — NFP/Minkowski via pyclipper, greedy gravity placement, periodic tiling patterns for instant sheet-count estimates; see docs/NESTING.md | `nest_single`, `find_tiling`, `write_svg` |
| `inside_test.py`, `toolart.py`, `drawer.py`, `tooltest.py` | Standalone sandboxes/sketches, NOT wired into the pipeline | |

Root `test_*.py` are self-checking scripts (synthetic parts with analytic
expectations), run as `python test_x.py` — see AGENTS.md.

## Per-part working directory (the cache everything shares)

Created by `main.py mesh <input> -o <workdir>` (or UI upload). All later stages and
the viewer read/write here. The server scans the parts root for legacy workdirs
plus `parts/` for uploads: an uploaded file's part id is `sha1(bytes)[:12]` and its
workdir `<root>/parts/<id>` (gitignored), so the same STEP always lands in the same
folder and re-uploads dedupe; the human name stays in `part.json`.

| File | Written by | Contents |
|---|---|---|
| `fine_verts.npy` / `fine_faces.npy` | mesh | float32 `(V,3)` / int `(F,3)` — **face/vertex indexing is stable from here on** |
| `coarse_verts.npy` / `coarse_faces.npy` | mesh / `prep/mesh_coarse` (STEP) | the raw BREP tessellation (pre-subdivision) as a **display-only** preview mesh — never an index space for results; the fine indexing stays sacred |
| `coarse_brep_faces.npy` / `coarse_normals.npy` | mesh / `prep/mesh_coarse` (STEP) | per-coarse-triangle BREP id (preview coloring) + facet normals (flat preview shading) |
| `coarse_meta.json` | `prep/mesh_coarse` (STEP) | resolution/deflection + `source_fingerprint` (the resolver's coarse-preview currency gate) |
| `fine_mesh.obj` | mesh (`--obj` only) | optional OBJ export for external tools; nothing in the pipeline or viewer reads it |
| `mesh_meta.json` | mesh | resolved `resolution` / `deflection` / `subdivide` / `diagonal` + `source_fingerprint` (resolver mesh-currency gate); `resolution/5` is the default zmap pixel of every later stage |
| `normals.npy` | mesh | per-face unit normals `(F,3)` used for classification: exact BREP surface normals on every STEP face (quadrics from analytic params, freeform via UV evaluation on the live shape; written eagerly), lazy facet fallback otherwise (gitignored, regenerated) |
| `brep_faces.npy` | mesh (STEP only) | int `(F,)` — source BREP face id per fine triangle |
| `brep_edges.npy` / `brep_edge_pairs.npy` | mesh (STEP only) | BREP edge polylines + the two BREP face ids adjacent to each edge (parting-line rendering) |
| `brep_meta.json` | mesh (STEP only) | per-BREP-face surface types + analytic `surface_params` (plane/cylinder/cone/sphere/torus; freeform faces have `null` params — their exact normals are evaluated at mesh time) |
| `directions.npy` | directions | float `(D,3)`, laid out as antipodal pairs `[d0,-d0,d1,-d1,...]`; `--axes` prepends ±X/±Y/±Z as indices 0–5 (+Z = 4) |
| `accessibility.npy` | directions | bool `(D,F)` — face f visible from direction d |
| `directions_meta.json` | directions | mesh fingerprint + pixel the accessibility was computed at; a re-meshed workdir flags `directions_stale` in the manifest |
| `highlights.json` | any CLI check | flat list of flagged face indices; replayed by the viewer's "Last CLI highlights" mode |
| `zcache/dir_<idx 04d>.npz` | precompute/compose | see next section |
| `face_splits.json` | splits API / `splits.py` | **source of truth for user face splits**: ordered cut list `{face_orig, face_at_cut, start, end, path}` (mesh-vertex ids) + the mesh fingerprint the cuts reference |
| `subfaces.npy` | `splits.py` (derived) | int `(F,)` — effective face id per triangle: sub-face ids `≥ n_brep` where cuts separated a face, `brep_faces` ids elsewhere; regenerated by deterministic replay on every mutation |
| `subface_edges.npy` / `subface_edge_pairs.npy` | `splits.py` (derived) | the `brep_edges` recipe over effective ids — includes the cut segments, replaces the mesh-time arrays in the viewer while splits exist |
| `subface_meta.json` | `splits.py` (derived) | `n_brep` / `n_effective` / `parents` (original id per sub-face id, retired ids included) / mesh fingerprint / per-cut `created`+`separated` |
| `results/<process>/<analysis>/<hash>.json[.npz]` | registry analyses | generic results, `<hash>` = `params_hash(params)` (sha1[:12] of canonical JSON); runners salt in schema, `directions` and `mesh` fingerprints so stale results orphan instead of misindexing — mold/setups additionally salt the `splits` fingerprint (of `subfaces.npy`), so cuts orphan assignments and an undo re-validates the older result |
| `results/<process>/<analysis>/<hash>_overrides.json` | viewer via API | user face-assignment overrides for a mold result |
| `part.json` | API upload/registration | part metadata (gitignored) |
| `source.stp` / `source.step` | upload / `mesh_part` (STEP input) | the retained source STEP; BREP-level stages (`prep/aag`, sheet unfold, import attributes) reload it — face/edge ids re-derive deterministically from the same bytes |
| `aag.npz` / `aag.json` | `prep/aag` (`pipeline.compute_aag`) | AAG stage artifact: per-face convexity/curvature/area/normal + C1/C2 group labels, per-edge face pairs/continuity/signed dihedral/polylines over **canonical edge ids**; json header carries schema, `source_sha`, mesh fingerprint and stats — consumers salt `aag_fingerprint` into cache keys |
| `face_attrs.json` | `step_import` | STEP face colors/names + PMI back-refs, keyed by 0-based BREP face id |
| `pmi.json` | `step_import` | semantic PMI (`schema` 3): dimensions (value, ±tol, qualifier, modifiers) / geometric tolerances (name, value, type, modifiers, material+zone modifiers, ordered `datum_refs` with precedence) / datums, all with 0-based face ids + canonical edge ids. Tolerance magnitudes OCCT leaves at 0 are backfilled from the STEP text by name (`_read_step_gdt_magnitudes`). Bump `step_import.PMI_SCHEMA` when entry fields change |
| `assembly.json` | `step_import` (assembly source workdir) | instance tree (translation + quaternion per instance) linking child part ids, quantities per unique part |
| `results/<p>/<a>/<hash>.dxf` | DXF export route / CLI `sheet --dxf` | cached DXF render of a stored flat-pattern result |

## `zcache/dir_*.npz` field keys (DirectionCache, zmap.py)

Always present: `version` (must equal `DirectionCache.VERSION`, else the cache is
discarded), `pixel`, `dirfp`/`meshfp` (directions.npy and fine mesh fingerprints —
a mismatch discards the cache). zmap engine also stores the rendered frame:
`heights`, `origin`, `x_axis`, `y_axis`, `direction`.

Per-tool fields, all per-vertex float arrays over `fine_verts.npy` (formatted with
`%.6g`):

- `tip_<D>_<rc>` — gap the tip leaves at each vertex (`_tip_key`)
- `clear_<r>` — clearance: tallest obstruction height within cylinder radius r (`_clear_key`)
- `sreq_<D>_<rc>_<r>` — tip-aware minimal stickout for tip (D,rc) with a cylinder of radius r (`_sreq_key`)

`compose_unreachable` thresholds these into face masks; a face flags only if all
three of its vertices flag, then it is ANDed with the direction's accessibility row.

## Backend registry (`processes/`)

`processes/base.py` (framework-free): `Param` (typed, auto-rendered as a form),
`AnalysisDef(id, label, params, run(workdir, params, progress), requires)`,
`ProcessDef`, plus result storage helpers (`store_result`, `params_hash`,
`result_paths`). `processes/__init__.py` collects `PROCESS` objects into the
registry served at `/api/processes`.

`processes/resolver.py` (framework-free) walks `AnalysisDef.requires` and runs
missing/stale prerequisites before the target, inline on the single job worker
(`api/jobs.py:_run` calls `resolver.ensure` instead of `analysis.run` directly).
`resolver.cache_key(workdir, "proc/analysis", params)` is the single builder for
every results-tier cache key: the analysis's own declared params + its `schema` +
the fingerprints of its transitive prep prerequisites (each prep stage's
`salt_fields`) + the `splits` salt (opt-in via `salts=("splits",)`) + any
`key_extra` discriminator (setup_verdict's `{"verdict": 1}`, which rides in the
`cnc/setups` store dir). Runners call it instead of hand-salting
`{**params, "mesh": mesh_fingerprint(...), ...}` — so the fingerprint set is
derived from `requires`, not duplicated per runner.
Only prerequisites declaring an `is_current(workdir, params)` gate (the `prep`
stages) are auto-run; results-tier analyses self-cache and are never auto-run, so
param-sensitive chains (e.g. CNC precompute→compose) are untouched. Invalidation
cascades by construction: a rebuilt upstream changes its content fingerprint,
flipping every downstream gate and re-salting results-tier keys. A module-level
LRU in `brep.load_step_shape_cached` (keyed by `(file_fingerprint, deflection)`)
collapses the redundant STEP re-parse across mesh/aag/sheet/tube/import.

Registered today:

- `prep` — `mesh_coarse`, `mesh`, `aag`, `directions`, `voxels`, `attributes`,
  `bundle` (`bundle` = the fixed first-load set `[mesh_coarse, aag, attributes]`,
  enqueued on STEP upload; idempotent). `aag` depends on `mesh_coarse` (it rebuilds
  from the source BREP, so it is available before the fine mesh); fine-mesh
  consumers (`cnc/features`, `sheet_metal/*`, `tube_laser/profile`) declare
  `prep/mesh` explicitly so the resolver builds the fine mesh on demand. `voxels`
  is the shared SDF (moved here from `injection_molding/flow_voxels`; that runner is
  now a thin forwarder, and `flow_fill` sub-runs `prep/voxels`)
- `cnc` — `features`, `setups`, `setup_verdict`, `precompute`, `compose`
- `injection_molding` — `mold_orientation`, `thickness`, `gaps`, `ray_thickness`, `ray_gap`, `slenderness`, `thin_span`, `wall_skeleton`, `sprue_proposals`, `ejection_sticking`, `flow_voxels`, `flow_fill`
- `sheet_metal` — `detect`, `flat_pattern` (SHEET_SCHEMA mirrored in `frontend/src/processes/sheetmetal/index.ts`), `bend_plan` (BENDPLAN_SCHEMA mirrored in `sheetmetal/bendplan.ts`). Schema-2 bend_plan additions: npz `flat_verts` f4 (3V, pattern-frame fold coordinates, z = material height with mid-surface at z_offset), `vertex_panel`/`vertex_bend` u1 (+1-encoded owners), `bend_t` f4, optional `collision_faces` u1 (mesh_check hits); stats gain `fold_mesh` (availability + base_transform), `tooling` (referenced punch/die/machine YZ profiles), per-plan-step `placement`/`lift_sign`/`theta_before`/`phi_target` (machine pose for the bend-sequence animation), and `mesh_check`. Viewer scene capabilities backing the animation: `Scene3D.setVertexPositions` / `addOverlayMesh`+`shiftOverlay` / `setAnimator` (reset on every repaint)
- `tube_laser` — `profile` (TUBE_SCHEMA mirrored in `frontend/src/processes/tubelaser/index.ts`; FEATURES_SCHEMA likewise in `cnc/features.ts`)

`run` callables must go through `pipeline.py` functions and write only into the
workdir cache, reporting via `progress(fraction, message)`.

## API (`api/`, FastAPI, created by `create_app(root, preload)`)

Jobs run on a **single worker thread** (`jobs.py`) because meshlib is not
concurrency-safe; clients poll. The manifest is rebuilt from disk on every request
(`manifest.py`) — that is why CLI results appear in the UI without registration.
Binary endpoints stream raw typed arrays with ETag caching (`fields.py`).

```
GET  /api/config                     server config
GET  /api/processes                  registry (processes, analyses, param schemas)
GET  /api/parts                      part list (parts root scan)
POST /api/parts                      upload STEP/STL → content-addressed workdir parts/<sha1[:12]> (idempotent)
GET  /api/parts/{id}                 part info
GET  /api/parts/{id}/manifest        all available fields/results for the part
GET  /api/parts/{id}/mesh/{which}    raw arrays: verts f4, faces u4, normals f4 (un-indexed contract: face f → vertices 3f..3f+2); also coarse_verts/coarse_faces/coarse_normals/coarse_brep_faces for the preview
POST /api/parts (STEP)               also enqueues the idempotent prep/bundle first-load job

GET  /api/parts/{id}/fields/{stem}/{key}          one zcache array
GET  /api/parts/{id}/highlights                   highlights.json
GET  /api/parts/{id}/results/{proc}/{an}/{hash}/{key}        one result npz array
GET/PUT .../results/{proc}/{an}/{hash}/overrides  mold face-assignment overrides
GET  .../results/{proc}/{an}/{hash}/export/dxf    flat pattern as DXF (generated + cached)
GET  /api/parts/{id}/face_attrs                   face_attrs.json (STEP colors/names)
GET  /api/parts/{id}/pmi                          pmi.json (semantic PMI)
GET  /api/parts/{id}/assembly                     assembly.json (imported assembly record)
POST /api/parts/{id}/explode                      split an uploaded assembly into child parts
GET  /api/parts/{id}/splits          user face-split state (cuts, sub-face parents, polylines)
POST /api/parts/{id}/splits          add a cut {face, start, end} (sync, numpy-only; 400 invalid, 409 stale mesh)
DELETE /api/parts/{id}/splits[/last] clear all cuts / undo the last one
POST /api/jobs                       submit {part_id, process_id, analysis_id, params}
GET  /api/jobs[/{id}]                poll status/progress
```

The `subfaces` / `subface_edges` / `subface_edge_pairs` fields are served like the
brep trio and advertised in the manifest only while `subface_meta.json` matches the
current mesh fingerprint.

## Frontend (`frontend/src/`)

Vite + React 18 + three.js + zustand. Build once with `npm run build` (output
`frontend/dist/`, served by the API); dev with `npm run dev` proxying `/api` to
`uvicorn api.app:app` on :8000.

| Path | Role |
|---|---|
| `App.tsx` | shell: part picker, process tabs, view-mode list, canvas |
| `api/client.ts`, `api/types.ts` | fetch wrappers + manifest/field descriptor types |
| `state/store.ts` | zustand store (active part/process/mode/params) |
| `viewer/scene.ts`, `viewer/controller.ts` | three.js scene, un-indexed mesh, picking, overlays (lines/arrows/graph); `runCtxAction` bridges a controls button to the live `ViewCtx` |
| `colorizers/core.ts` | generic painters: masks, heatmaps, highlights — new processes get rendering for free |
| `registry/types.ts` | **the plugin contract**: `ProcessPlugin { processId, modes: ViewMode[], defaults, Controls?, inspect?, onPick? }`; `ViewMode.paint(ctx) → PaintInfo`; `ViewCtx` gives `getField`, `paintFaces`, `setLines/Arrows/Graph`, params |
| `registry/index.ts` | plugin list — register new process plugins here |
| `processes/cnc/` | CNC plugin: verdict/gap/stickout modes (`modes.ts`), client-side tool composition (`compose.ts`), field lookup (`sources.ts`), holder/tolerance controls (`Controls.tsx`); setup-assignment view has the "optimize parting lines" button |
| `processes/injection/` | injection molding plugin: mold assignment view (with "optimize parting lines" button), thickness/gap heatmaps, skeleton overlay |
| `processes/parting.ts` | shared client-side parting-line optimizer for both categorical assignment views: reassigns every multi-valid face (effective sub-face ids when splits exist) to minimize, lexicographically, parting-line wire count then total length, written through the overrides mechanism (`optimizeParting`, `partingMetrics`) |
| `splits/` | face-split interaction shared by mold assignment and CNC setups: two-click snap-to-wire FSM (`splits.ts::handleSplitPick`), snap targets (corners/midpoints of boundary chains), cut overlays, auto re-run + overrides carry-forward (`resubmitAssignment`), `SplitControls.tsx` toggle/undo/clear UI |
| `components/` | `AnalysisPanel` (run analyses via jobs API), `ParamForm` (auto forms from `Param` schemas), `Readouts`, `PartPicker` |

Interactive thresholds (tolerance, stickout, holder stack) are recomputed
client-side from cached per-vertex fields — never add a Python round-trip for a
slider.

Screenshot/smoke scripts (Playwright, need `CHROMIUM_PATH`): `smoke.mjs` walks all
view modes; `shot_access.mjs` / `shot_mold.mjs` / `shot_thickness.mjs` capture
specific views.

## Committed sample data

- `tests/` — input fixtures: `testpart_42.stp/.stl` (small, use for smoke runs),
  `Aligator.STEP`, `large_part.stl`, two large real STLs, GLB assemblies.
- No sample workdirs are committed — build one locally by meshing a `tests/`
  fixture (see TESTING.md).
- `highlights.json` at repo root — legacy sample output; not read by code.
