# Agent guide — analyzer

Manufacturability (DFM) analyzer for CAD parts: CNC tool accessibility, injection-mold
orientation/parting, and wall-thickness checks. Python + meshlib backend, FastAPI
server, Vite/React/three.js viewer.

Read this file first. Then, depending on the task:

- **APPROACH.md** — the algorithms and *why* they work (Minkowski/offset tricks,
  Z-map engine, mold assignment). Read the relevant section before touching any
  geometry code.
- **TESTING.md** — how to run every workflow end-to-end (CLI commands, viewer,
  resolution/runtime knobs).
- **docs/CODEMAP.md** — where everything lives: per-file map, on-disk cache
  contracts (npy/npz/json layouts), API routes, frontend plugin interface.
- **docs/RECIPES.md** — step-by-step procedures for the common change types
  (add an analysis, add a view mode, add a CLI command, verify a change).

## Hard rules

1. **Do not "fix" the spelling `tollerance`.** CLI flags, function kwargs and cached
   param dicts all use this spelling consistently. Renaming it breaks the CLI surface
   and invalidates `params_hash` result caches. Leave it.
2. **meshlib must never run concurrently.** The API executes jobs on a single worker
   thread on purpose (`api/jobs.py`). Never add thread/process parallelism around
   meshlib calls, and never run two analyses at once in one process.
3. **Face indices are the currency of the whole pipeline.** Every result is expressed
   as indices into `fine_faces.npy` (or per-vertex fields over `fine_verts.npy`).
   Anything that re-meshes, re-orders faces or re-indexes vertices after Stage 1
   invalidates every cached artifact in a workdir. The viewer renders un-indexed:
   face `f` owns vertex buffer entries `3f, 3f+1, 3f+2`.
4. **Respect the cache versioning.** If you change the semantics or layout of fields
   stored in `zcache/dir_*.npz`, bump `DirectionCache.VERSION` (zmap.py) so stale
   caches are discarded. Generic results are keyed by `params_hash(params)`
   (processes/base.py) — changing a param name/default silently changes the hash and
   orphans old results; that is acceptable, corrupting old files in place is not.
5. **`--heal` is for dirty STLs only.** It voxel-remeshes and rounds edges. Clean
   STEP input keeps exact geometry and is refined with `--subdivide` (midpoint split,
   moves nothing). Never suggest or apply `--heal` to STEP.
6. **Registry seam, both sides.** New analyses go into `processes/<process>.py`
   (backend `AnalysisDef`) and are rendered by `frontend/src/processes/<process>/`
   (a `ProcessPlugin`). Do not wire analyses into the API or App.tsx directly —
   the manifest endpoint rebuilds from disk, so CLI- and UI-computed results are
   interchangeable by construction.
7. **Keep `processes/base.py` framework-free** (importable without fastapi) — the
   CLI uses the registry too.
8. **Don't commit generated part caches.** `zcache/`, `results/`, `normals.npy`,
   `part.json`, `/testpart_42/`, `/aligator/`, `frontend/dist/` are gitignored.
   `large_part/` and the two `21007-*` directories are intentionally committed
   sample workdirs — leave them alone.

## Environment & commands

```bash
pip install -r requirements.txt        # meshlib>=3, numpy, scipy, loguru, fastapi, uvicorn, cadquery-ocp
cd frontend && npm install && npm run build && cd ..   # one-time viewer build (node >= 18)
```

- CLI entry point: `python main.py <command>` — commands: `mesh`, `directions`,
  `options`, `thickness`, `setups`, `verdict`, `precompute`, `compose`,
  `serve`, `view`. `python main.py <command> -h` for flags; TESTING.md for workflows.
- Typical smoke workflow (fast, small part):
  ```bash
  python main.py mesh tests/testpart_42.stp -o testpart_42 --subdivide 1.0
  python main.py directions testpart_42 --count 8 --axes
  python main.py precompute testpart_42 --directions 4 5 --tips 6:0 --clearances 5
  python main.py compose testpart_42 4 --diameter 6 --corner_radius 0
  ```
- Viewer: `python main.py view testpart_42` (FastAPI on :8080 serving
  `frontend/dist`). Frontend dev loop: `uvicorn api.app:app` (port 8000) +
  `cd frontend && npm run dev` (proxies `/api`). Typecheck: `cd frontend && npx tsc -b`.

## Tests

Test files are **plain scripts, not pytest** — run them directly:

```bash
python test_zmap.py          # zmap engine vs analytic expectations (fast-ish)
python test_mold.py          # mold orientation / assignment fixtures
python test_splits.py        # user face splits (relabel, replay, split-aware runs)
python test_thickness.py     # rolling-sphere plate/gap probes
python test_accessibility.py # visibility raster on a synthetic pocket part
python test_gap_probes.py    # Euclidean gap metric probes
python test_skeleton.py      # wall-thickness skeleton graph
python test_nesting.py       # 2D contour nesting sandbox (NFP grids, interlock, spacing)
```

They build synthetic parts with known-correct answers and assert on them; a green
run prints assertions passed. Run the test(s) covering the module you touched —
mapping in docs/RECIPES.md. Frontend: `frontend/smoke.mjs` walks every view mode
against a running server (needs `CHROMIUM_PATH`).

## Conventions

- Python: snake_case, dataclasses over classes where possible, `loguru` for logging,
  numpy-vectorized inner loops (no per-face Python loops in new code — see the
  `get_inside_indices` rough edge as the anti-pattern).
- Results are per-face boolean masks or per-vertex scalar fields; combining checks
  is numpy logic over those arrays, never new geometry passes.
- Frontend: TypeScript strict, zustand store, no CSS framework (plain
  `styles.css`). View modes paint via `ctx.paintFaces` / heatmap helpers in
  `colorizers/core.ts`; interactive thresholds recompute client-side from cached
  fields — no Python round-trips for slider changes.
- Commit messages: short imperative summary line, like the existing history
  ("Add rolling-sphere wall thickness...", "Mesh STEP through the BREP...").

## Known rough edges (do not "clean up" blindly)

See the end of APPROACH.md. Highlights: `get_inside_indices` is a slow
per-face loop (`inside_test.py` is the sandbox for a bulk alternative);
`toolart.py` / `drawer.py` / `tooltest.py` are standalone sketches not wired into
the pipeline.
