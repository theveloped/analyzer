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
| `processes/` | Backend analysis registry (see below) | `processes.base`, one module per process |
| `api/` | FastAPI server (see routes below) | `api.app.create_app`, `serve_app` |
| `utils.py`, `pathtypes.py` | Small helpers: dirs, timing decorator, argparse `PathType` | |
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
| `fine_mesh.obj` | mesh (`--obj` only) | optional OBJ export for external tools; nothing in the pipeline or viewer reads it |
| `mesh_meta.json` | mesh | resolved `resolution` / `deflection` / `subdivide` / `diagonal`; `resolution/5` is the default zmap pixel of every later stage |
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

Registered today:

- `prep` — `mesh`, `directions`
- `cnc` — `precompute`, `compose`
- `injection_molding` — `mold_orientation`, `thickness`, `gaps`, `ray_thickness`, `ray_gap`, `slenderness`, `thin_span`, `wall_skeleton`, `sprue_proposals`, `ejection_sticking`, `flow_voxels`, `flow_fill`
- `sheet_metal` — empty placeholder

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
GET  /api/parts/{id}/mesh/{which}    raw arrays: verts f4, faces u4, normals f4 (un-indexed contract: face f → vertices 3f..3f+2)
GET  /api/parts/{id}/fields/{stem}/{key}          one zcache array
GET  /api/parts/{id}/highlights                   highlights.json
GET  /api/parts/{id}/results/{proc}/{an}/{hash}/{key}        one result npz array
GET/PUT .../results/{proc}/{an}/{hash}/overrides  mold face-assignment overrides
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
- `large_part/`, `21007-010-rev1-*/` — pre-built sample workdirs (committed on
  purpose).
- `highlights.json` at repo root — legacy sample output; not read by code.
