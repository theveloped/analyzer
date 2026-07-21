# Production-plan architecture — v2 workbench design

Status: agreed direction (2026-07-21). Phase 0 in progress; later phases land one per
session, updating this doc as decisions harden. Background: AGENTS.md (hard rules),
docs/CODEMAP.md (cache/data contracts), docs/BACKLOG.md (queued items that intersect:
9 feature-aware setups, 12 prep/classify, 17 workholding).

## Intent

Two surfaces for one seasoned engineer, sharing one viewer:

- **Investigate** — every tool at their fingertips, process-independent, no plan required:
  thickness/gap heatmaps, hole/bend features, PMI/MBD, STEP colors/names, BREP faces, setups,
  accessibility, flat patterns, bend animation. These are *inspection lenses*.
- **Plan** — work toward a full production plan, DFM-checked along the way: left-rail *Steps*
  markable ok / unmanufacturable / approval-needed, prefillable from machine templates, loose
  enough for mixed routes (laser → CNC → press brake; die-cast → machining). Selected content
  publishes to a customer-facing DFM report; operations carry structured quotation inputs.

Trajectory: manual power-user tool first, then increasingly automated DFM feedback and
quoting driving the *same objects through the same API* — automation is a dial, not a mode.

## Concepts

| Concept | Definition | Rule |
|---|---|---|
| **Lens** | Anything paintable/activatable over the model (static, computed, interactive, animated). The 44 existing ViewModes. | A lens is never a verdict. |
| **Study** | A broad reusable computation over many candidates (e.g. reachability per direction × tool × face). An ordinary `AnalysisDef` result. | Slicing a study is numpy/TS over its arrays, never a new geometry pass. |
| **Check** | A scoped interpretation of a result against a **pinned policy** (thresholds + aggregation), producing a verdict. | A check pins its policy by value + hash; slider exploration stays free on the lens. |
| **Finding** | Atomic provenance-carrying issue (geometry refs, scope, evidence). | Always **derived** from (result, policy, scope) — never hand-authored. |
| **Disposition** | Human judgment on a finding (accepted deviation, customer approval…), who/when/why. | Always **authored**, append-only. |
| **Plan** | Decisions (material/stock: provisional → selected → locked), ordered operations, checks, revision counter. | A *Step* in the UI is the projection of an operation + its nested checks. |

Status is four independent axes, never one field:
**execution** (not-run/queued/running/current/stale/error) · **verdict** (pass/review/fail/na)
· **disposition** (open/accepted/customer-approval/resolved) · **visibility**
(internal/customer/report). "Computed" is not "good".

## What already exists (build on it, don't duplicate it)

1. **Selective invalidation** — `processes/resolver.py:cache_key` = own declared params +
   schema + transitive prep fingerprints (mesh/directions/aag) + opt-in salts (`"splits"`) +
   `key_extra`. Stale results orphan to new hashes; undo re-validates old ones; the manifest
   computes per-result `stale`. The plan layer adds **no parallel cache**.
2. **Study → check precedent (CNC)** — `prep/directions` → `accessibility.npy bool(D,F)`;
   `zmap.DirectionCache` holds per-(direction, tip, clearance) reach fields lazily;
   `machining.machine_cover` cone-slices it; `cnc/setup_verdict` re-interprets a ranked plan
   against a real tool library (`key_extra={"verdict":1}`).
3. **Decision persistence** — `face_splits.json` (fingerprint salted into dependent keys) and
   per-result `<hash>_overrides.json`. The plan sidecars follow this pattern.
4. **v2 already runs on the shared engine** — same store/controller/plugins as the old
   frontend; every ViewMode is launchable by setting `{processId, modeId}`.

## Keying rules (the two footguns)

- **Scope splits two ways.** Scope that changes *what is computed* (direction, tools, option)
  is materialized into **declared** analysis params — `resolver.cache_key` filters submitted
  params down to declared ones, so an undeclared `scope` key would silently collide caches.
  Scope that changes *only interpretation* (threshold, face subset, aggregation) lives in the
  check instance and the findings-derivation key, never forking the results cache.
- **No whole-plan "decisions" salt.** Decision values reach computations as materialized
  params, so `params_hash` keys them with exact per-analysis granularity; a plan-wide salt
  would over-invalidate (a material change would orphan reachability results).
- **Numbers canonicalize before hashing** (`params_hash` folds integral floats to ints):
  params round-tripping through JavaScript lose the float-ness of `1.0`, and `1` vs `1.0`
  hashed differently — client-submitted runs landed on different keys than the server
  derived from the same logical values.
- **Analysis param names must not collide with prep salt fields** (`mesh`, `directions`,
  `accessibility`, `aag`, `splits`): the salt would silently overwrite the declared param
  in the cache key, collapsing distinct runs onto one hash. `resolver.cache_key` now raises
  on a clash (bit `cnc/reach_study`'s original `directions` param → renamed
  `direction_indices`).

## Storage — per-part sidecars in the workdir

```
<workdir>/
  plan.json                # current plan (schema-versioned, revision counter)
  plan_history.jsonl       # append-only full snapshot per revision
  dispositions.jsonl       # append-only {finding_id, state, by, at, why, evidence}
  plan_assets/machines/<sha>.yaml       # content-addressed template snapshots (copied in)
  findings/<check_id>.<eval_hash>.json  # derived-findings cache (snapshotted at publish)
  reports/<report_id>/report.json       # immutable published bundles (evidence BY COPY)
```

`plan.json`: `decisions`, ordered `operations` (kind, config, machine `{template, sha}`,
declarative workpiece-state annotations, structured quotation-input fields), `checks`. Each
check: target `analysis` id, **`param_bindings`** (declarative decision/plan → analysis-param
mapping — the single bridge driving keying, staleness and impact preview), `scope`, pinned
`policy` (value + hash), preferred `lens`, `aggregation` policy name, `visible`.

Cross-part libraries (machine templates, materials) live in a repo-level
`catalogue/` (generalizing `pressbrake/catalogue/`); assignment content-addresses and
**copies** the file into `plan_assets/` so plans stay self-contained.

**Workpiece states stay declarative** over the final-part face index space (stock primitives
+ allowances, datums/clamp faces, features done/remaining). Hard rule 3 makes intermediate
meshes toxic to the cache/lens infrastructure — simulation is explicitly deferred.

**Finding identity ≠ eval key.** Eval key (derivation cache) =
`params_hash({result cache-key, policy hash, scope, evaluator schema})` — re-derivable,
deletable. Finding id (disposition anchor) = `sha1(check_id + canonical geometry ref + kind)`
— excludes the result hash so a disposition survives a re-run reproducing the same issue.
Geometry refs use BREP/effective-face ids; fine-face lists are evidence only.

## API

New `api/plan.py` router: `GET/PUT /api/parts/{id}/plan` (PUT with `If-Match: revision` →
409), `GET /plan/history`, `POST /plan/impact`, `GET/POST /dispositions`, `GET/POST /reports`.
Manifest gains a `plan` section: per check the server materializes `param_bindings` →
`resolver.cache_key` → `{expected_hash, exists, stale}` (pure fingerprint arithmetic).

**Impact preview** (`POST /plan/impact` with a decision patch): re-key every check under
current vs patched plan; report per check `unchanged` | `revalidates` (new hash, result file
already on disk — reverting is free) | `recomputes` (+ cost tier). Never enqueues a job
(single worker, one job per part).

## Frontend

- **Lens registry** `frontend/src/v2/lenses.ts`: derived from `PROCESS_PLUGINS` + a curation
  overlay (label/icon/category/pinned/hidden). `ProcessPlugin.modes` stays the single source
  of truth; the hardcoded v2 `VIEWS`/`ANALYSES` arrays retire. Ribbon: 5–7 pinned/recent +
  grouped menus + search, not 44 icons.
- **Field lenses self-materialize** (spike, adopted): the scalar-field lenses (thickness,
  gaps, ray variants, thin span — `v2/fieldLenses.ts`) are pinned to the ribbon and clicking
  one runs the backing analysis with plain defaults when nothing current is cached; the
  paint is the un-thresholded heatmap over the real data range (edge artifacts visible).
  All interpretation lives in the side panel (`FieldLensRail`): a **highlight band** —
  faces whose value falls inside it paint COL.band magenta ON TOP of the unchanged
  heatmap — defined by two open-ended bounds, each a number in its own unit (field units,
  % of mean, % of median, or percentile), so "≥ 5 mm", "the bottom p5" (to 5 percentile)
  and "70–130 % of the mean" are each one or two number+dropdown gestures. Band edits
  recolor instantly; the re-run button only arms when a compute knob actually changed;
  **"Save band as check"** is the moment exploration becomes a pinned policy on the plan.
  Lens = data, band = interpretation, check = saved interpretation.
- **Check → lens**: selecting a check activates its preferred lens with scope bound into
  `viewerParams`; selecting a finding also flies the camera. The engineer can switch to any
  lens at any time.
- **Controls panels**: host the existing `ProcessPlugin.Controls` verbatim in a right-rail
  Configure tab under a `.v1-controls` scoping wrapper; restyle panel-by-panel later.
- **Verdict evaluation** runs client-side over cached fields against the pinned policy
  (the codebase's native pattern; deterministic because inputs are content-addressed).
  Evaluators live in one module (`v2/checks/evaluators.ts`) so a Python mirror is a port.
  Publishing snapshots derived findings server-side under `findings/` with their eval keys.

## First vertical slice — CNC exploration flow

One new `AnalysisDef cnc/reach_study` (`processes/cnc.py`, modeled on `run_setup_verdict`):
params `directions: int_list`, `tools: tool_list`, `tollerance`, `pixel`, `window`; drives
precompute + per-(direction, tool) verdicts from `DirectionCache`; output a packed
`reach bool(D_sel, T, F)` cube + stats, `REACH_STUDY_SCHEMA=1`. Then on `testpart_42`:
candidates (hole axes + manual) → reach study over ~10 directions × 3 tools → plan with
OP10/OP20 binding `direction_index` → per-op checks + a plan-scope `geometry-union` aggregate
(unreachable-in-all-ops → findings) → flip OP20's direction → impact preview → re-slice with
zero recompute → disposition one finding → publish a report.

The mixed sheet route (laser → CNC → press brake) is deliberately slice 2: CNC-on-flat-blank
is a different mesh/face space, exactly the workpiece-state question we defer.

## Phase 2 plan (locked 2026-07-21)

Scope decisions: findings stay at face/area granularity (the cnc/features join is a
follow-up); operations UI is a seeded template + per-op direction dropdown (no
add/remove/reorder yet).

1. **`cnc/reach_study`** (`processes/cnc.py` + `pipeline.reach_study`, REACH_STUDY_SCHEMA=1):
   `setup_verdict`'s inner loop hoisted — per (direction, tool): `DirectionCache` fields
   (lazy, persistent) → `zmap.tool_face_verdict` AND `accessibility[d]`. Params
   `directions: int_list` (empty = all) + `tools: tool_list` + `tollerance` /
   `wall_tollerance` / `pixel` / `window`; `requires=["prep/directions"]`. Stores
   **per-pair face masks** `reach_<d>_<t>` (manifest-native) + per-pair area stats.
2. **Plan wiring, no schema change**: operations `{kind: "cnc_setup", config:
   {direction_index, tilt}}`; a study check owning execution plus per-op and aggregate
   checks referencing the *same analysis+params* — shared expected hash, so direction
   changes are interpretation-only (never recompute); only toolset/candidate edits re-key.
3. **Client-side evaluation**: per-op verdict = union of masks over the op's tilt-cone
   members (cone membership mirrored in TS from `machining.cone_members`); union ≠ sum, so
   `v2/checks/evaluators.ts` grows an async path cached by (result hash, policy hash) —
   the eval-key design lands here. Precedent: `frontend/src/processes/cnc/compose.ts`.
4. **Lenses** in the cnc plugin (registry seam): `reach_study` (one d×t mask), `reach_op`
   (unreachable by any tool in the op cone), `reach_aggregate` (unreachable in every op).
5. **v2 UI**: operation cards in the Steps rail (direction dropdown from
   `manifest.directions` + source labels, tilt field), a "CNC exploration" template
   button, and the impact-preview modal on plan edits (POST /plan/impact → show
   unchanged/revalidates/recomputes → apply).
6. **Verify**: `test_reach.py` plain script (coarse fixture, 2 dirs × 2 tools; masks ⊆
   accessibility row; one pair cross-checked against `pipeline.compose_tool`); extend the
   Playwright walk to the full exploration flow on `testpart_42`.

## Sequencing

| Phase | Scope |
|---|---|
| 0. Investigate surface + honest status (frontend-only) | `v2/lenses.ts` derived registry; all modes reachable; 4-axis status (verdict provisional); Configure tab hosting old Controls; retire VIEWS/ANALYSES; drop TopBar process tabs + duplicate part picker |
| 1. Plan substrate | `api/plan.py`; plan/history/dispositions sidecars; manifest `plan` section; pinned policies; evaluators + findings derivation; PipelineRail → Steps rail; quotation-input fields on operations |
| 2. Study + CNC slice | `cnc/reach_study`; per-op/aggregate lenses; impact preview; slice end-to-end |
| 3. Reports | publish flow (findings snapshot + evidence: result hash, params, camera, lens, legend), immutable bundles, read-only customer view |
| 4. Libraries + mixed sheet route | `catalogue/machines/`; declarative workpiece-state annotations; laser→CNC→brake plan |
| Deferred | intermediate-geometry simulation, automated route proposal, quotation generation, cross-part dashboards, results GC (must respect report-referenced hashes) |

## Verification per phase

Backend: new plain-script `test_plan.py` (plan CRUD + revision 409s, impact-preview
classification vs hand-computed cache keys, findings-derivation determinism, disposition
survival across re-runs). Study: cross-check reach-cube slices against `cnc/setup_verdict`
areas on `testpart_42`. Frontend: `npx tsc -b`; extend `frontend/smoke.mjs` to walk every
lens in the v2 shell. Existing suites stay green (`test_zmap.py`, `test_splits.py`,
`test_accessibility.py` — the study reuses their machinery unchanged).
