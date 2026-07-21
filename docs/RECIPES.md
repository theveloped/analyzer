# Recipes — step-by-step procedures for common changes

Follow these literally; each ends with a verification step. Paths and contracts are
in docs/CODEMAP.md; background in APPROACH.md.

## Which tests cover what

| You touched | Run |
|---|---|
| `zmap.py` (maps, closings, gaps, DirectionCache) | `python test_zmap.py`, `python test_gap_probes.py`, `python test_accessibility.py` |
| `analysis.py` offsets/closings/undercuts | `python test_endmill.py` (slow, minutes) |
| `molding.py`, `brep.py`, mold assignment | `python test_mold.py` |
| `splits.py`, split-aware assignment/manifest | `python test_splits.py` |
| `pipeline.compute_thickness` / thickness analyses | `python test_thickness.py` |
| `pipeline.compute_ray_thickness` / ray analyses | `python test_ray_thickness.py` |
| `pipeline.wall_skeleton` | `python test_skeleton.py` |
| `pipeline.flow_voxels` / `flow_fill` (voxel flow) | `python test_flow.py` |
| `aag.py` (face/edge classification, sheet base) | `python test_aag.py` |
| `step_import.py` (assemblies, colors/names, id bridging) | `python test_import.py` |
| `step_import.py` PMI/GD&T extraction (dimensions/tolerances/datums) | `python test_pmi.py` |
| `machining_features.py`, `cnc/features` | `python test_features.py` |
| `sheet.py`, `unfold.py`, `dxfexport.py`, sheet_metal process | `python test_sheet.py` |
| `tube.py`, tube_laser process | `python test_tube.py` |
| `pressbrake/` pure core (kinematics, envelope, tooling, search) | `python test_pressbrake.py` |
| `pressbrake/adapter.py`, sheet_metal/bend_plan | `python test_bendplan.py` |

Sheet/tube changes should additionally be scored against the instapart
example corpus (166 real STEP files with expected thickness/bends/volume):
`python benchmark/sheet_corpus.py --smoke` for a quick pass,
no arguments for the full fast set (needs the instapart checkout as a
sibling of the repo parent, or `--instapart <path>`). Not CI-gating.
| Engine parity (zmap vs voxel) | `python benchmark_engines.py` |
| Anything in `frontend/src/` | `cd frontend && npx tsc -b && npm run build`, then `node smoke.mjs` against a running server |
| `api/`, `processes/` | smoke workflow below + open the viewer once |

Minimal end-to-end smoke (fast; uses the small STEP fixture):

```bash
python main.py mesh tests/testpart_42.stp -o testpart_42 --subdivide 1.0
python main.py directions testpart_42 --count 8 --axes
python main.py precompute testpart_42 --directions 4 --tips 6:0 --clearances 5
python main.py compose testpart_42 4 --diameter 6 --corner_radius 0
python main.py view testpart_42 --no-browser --timeout 30   # server must boot clean
```

## Add a new analysis to an existing process

Example: a new injection-molding check.

1. Implement the computation in `pipeline.py` as
   `def my_analysis(workdir, *, param1=..., progress=None)`. Load geometry with
   `load_mesh_arrays(workdir)`; report with the `_report(progress, frac, msg)`
   pattern; keep inner loops numpy-vectorized.
2. Persist via `processes.base.store_result(workdir, "injection_molding",
   "my_analysis", params, stats, arrays={...}, field_meta={...})`. Arrays are
   per-vertex `(V,)` or per-face `(F,)`; `field_meta` (e.g. `{"kind": ...,
   "association": "vertex"}` — copy from `_run_sphere_field` in
   `processes/injection_molding.py`) is what the manifest surfaces to the frontend.
3. Register in `processes/injection_molding.py`: add an `AnalysisDef(id=...,
   label=..., params=[Param(...)], run=run_my_analysis, requires=["mesh"])` to the
   `PROCESS.analyses` list. The `run` wrapper unpacks the params dict and returns
   `AnalysisResult(stats=...)`. Params declared here auto-render as a form in the UI.
4. (Optional but usual) add a CLI subcommand in `main.py` mirroring an existing one
   (`thickness` is the best template), calling the same `pipeline` function and
   ending with `pipeline.write_highlights(...)` + `--serve` support.
5. Frontend: add a `ViewMode` to `frontend/src/processes/injection/index.tsx` whose
   `paint(ctx)` fetches the field with `ctx.getField(descriptor-from-manifest)` and
   paints via the helpers in `colorizers/core.ts` (mask or heatmap). No new plugin
   needed for an existing process.
6. Verify: smoke workflow, run the analysis from the UI (AnalysisPanel), confirm the
   new view mode paints, and confirm the CLI-computed result also shows up in the UI
   (same cache). `npx tsc -b` must pass.

## Add a whole new process (e.g. filling in sheet_metal)

1. Backend: build out `processes/sheet_metal.py` with `PROCESS = ProcessDef(...)`
   and analyses as above; sheet_metal is already listed in the `REGISTRY` tuple in
   `processes/__init__.py` — a genuinely new module must be added there.
2. Frontend: create `frontend/src/processes/sheetmetal/index.ts` exporting a
   `ProcessPlugin` (contract in `registry/types.ts`; `processes/injection/` is the
   richer template, `processes/cnc/` shows custom Controls) and register it in
   `registry/index.ts`.
3. Verify as above — the process appears as a tab automatically once both sides exist.

## Add a new cached per-direction tool field (zcache)

1. Add a method on `DirectionCache` (zmap.py) following `tip_gap` /
   `clearance` / `tip_min_stickout`: compute → store under a stable key via a `_*_key(...)` helper
   (`%.6g` formatting) → `self._save()`. Implement it for **both** engines
   (`zmap` and `voxel`) or raise a clear error for the unsupported one.
2. Bump `DirectionCache.VERSION` if any existing key's meaning changes (new keys
   alone don't require a bump).
3. Consume it in `compose_unreachable` (zmap.py) and/or expose it: the manifest
   (`api/manifest.py::_zcache_fields`) auto-lists npz members, and the CNC frontend
   maps keys to fields in `processes/cnc/sources.ts`.
4. Verify with `python test_zmap.py` plus a `precompute`/`compose` smoke run; if
   both engines implement it, cross-check with `benchmark_engines.py`.

## Change mesh-time outputs (Stage 1)

Anything new that `pipeline.mesh_part` writes must be derived from the *same*
tessellation that produced `fine_faces.npy` — never re-tessellate separately (face
ids would not line up). For STEP, per-triangle provenance flows through
`brep.mesh_step` → `subdivide_tagged`; keep children inheriting their parent's face
id. After changes, run `python test_mold.py` (BREP meshing fixtures) and re-mesh
`testpart_42` from scratch (delete the workdir first — mesh outputs are not
version-checked).

## Frontend-only changes (view modes, controls, colors)

1. Dev loop: `uvicorn api.app:app` in one shell (port 8000), `cd frontend &&
   npm run dev` in another; open the Vite URL.
2. Interactive params (sliders, thresholds) belong in the plugin's `Controls` and
   must recompute from already-fetched fields client-side (see
   `processes/cnc/compose.ts`) — do not add API endpoints for them.
3. Before committing: `npx tsc -b`, `npm run build`, and `node smoke.mjs` with a
   server running on a part that has cached fields (mesh a `tests/` fixture
   first, e.g. `python main.py mesh tests/testpart_42.stp -o testpart_42
   --subdivide 1.0`).

## Performance sanity

Reference numbers to judge regressions against are in TESTING.md ("Knobs vs
runtime") and APPROACH.md (engine benchmark table). Rules of thumb: a single
`compose` must stay sub-second; `precompute` per (direction, tip) is seconds, not
minutes, on `testpart_42`; anything that scales per-voxel instead of per-pixel or
per-vertex is probably on the wrong engine path.
