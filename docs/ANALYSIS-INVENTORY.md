# Analysis inventory — what exists today

A complete audit of the analysis surface as of 2026-07-29, written to drive the
"keep / clean up / how to visualize" pass over the v2 UI. Vocabulary comes from
docs/CONCEPTS.md, plan-layer rules from docs/PLAN-ARCHITECTURE.md; contracts from
docs/CODEMAP.md. When code and this
file disagree, the code wins.

UI companion: [V2 button and icon inventory](V2-BUTTON-ICON-INVENTORY.md) —
current button styling, all 40 lens icons, and the viewer/action icon review sheet.

## The layers

Vocabulary is defined once, in **[CONCEPTS.md](CONCEPTS.md)**. This file is the census:
what exists today and how much of it, as of the date above.

```
prep stages        artifacts in the workdir, auto-run by the resolver
   ↓ requires
analyses           results/<proc>/<an>/<hash>.json[.npz], self-caching
   ↓ read by
lenses             ProcessPlugin.modes + v2/lenses.ts curation — never a verdict
   ↓ interpreted by
checks             pinned policy over one analysis result → verdict + findings
```

The arrows are "reads", not "produces": a field lens *runs* the analysis it paints when
nothing is cached, so the dependency points both ways in practice.

Counts as of 2026-07-29: **7 prep stages + 24 results-tier analyses**, surfaced by
**41 lenses**, of which **7 are self-materializing field lenses**; **9 analyses can
currently become a check**.

---

## A. prep stages (`processes/prep.py`)

Auto-run as prerequisites (they declare `is_current`). They overwrite fixed-name
artifacts; they have no result cache of their own except `voxels`.


| id            | requires      | params                                                                                    | writes                                                                                                                    | contributes salt              |
| ------------- | ------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| `mesh`        | —             | `resolution`, `heal`, `subdivide`, `deflection`                                           | `fine_verts/faces.npy`, `normals.npy`, `brep_faces.npy`, `brep_edges(+_pairs).npy`, `brep_meta.json`, `mesh_meta.json`    | `mesh`                        |
| `mesh_coarse` | —             | `resolution`, `deflection`                                                                | `coarse_verts/faces/normals/brep_faces.npy`, `coarse_meta.json` (display-only preview)                                    | —                             |
| `attributes`  | —             | —                                                                                         | `face_attrs.json`, `pmi.json` (STEP only)                                                                                 | —                             |
| `aag`         | `mesh_coarse` | `smooth_angle`, `tollerance`, `deflection`                                                | `aag.npz` + `aag.json`                                                                                                    | `aag`                         |
| `directions`  | `mesh`        | `count`, `axes`, `bbox_axes`, `hole_axes`, `manual`, `face_groups`, `tollerance`, `pixel` | `directions.npy (D,3)`, `accessibility.npy bool(D,F)`, `directions_meta.json`                                             | `directions`, `accessibility` |
| `voxels`      | `mesh`        | `voxel`                                                                                   | result npz `voxel_index`, `voxel_dist`, `vert_voxel`, `vert_half_thickness`; stats `grid`, `cells`, `interior_volume_mm3` | — (results-tier storage)      |
| `bundle`      | —             | —                                                                                         | orchestrator only: runs `mesh_coarse`+`aag`+`attributes` on STEP upload                                                   | —                             |


Also non-registry but part of the currency: `face_splits.json` → `subfaces.npy`
(+ `subface_edges/_edge_pairs/_meta`), the `splits` salt.

---

## B. Results-tier analyses

### B1. CNC (`processes/cnc.py`) — 9


| analysis        | requires                                               | params (default)                                                                                                                | stats out                                                                                                                      | arrays out                                                                                                                         | schema / salts                                                                               |
| --------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `features`      | `prep/mesh_coarse`, `prep/aag`                         | `axis_angle_tol` 1°, `axis_dist_tol` 0.01 mm, `include_pockets` true                                                            | `counts` per type, `features[]` (type, faces, diameter, depth, axis)                                                           | `feature_category` u1, `feature_id` u4 — per **BREP face**, so no fine mesh is needed and the lens paints on the coarse preview | FEATURES_SCHEMA 2                                                                            |
| `turning`       | `prep/mesh` *(deliberately not aag — must run on STL)* | `tollerance` (auto 1°/5°), `profile_bins` 512, `refine_rounds` 3, `max_candidates` 12, `sample_faces` 100k, `axis_override`     | verdict, axis, `profile` (axial z/r), `inner_profiles[]`, `milled_regions`, `bores`, `needs_split(+_area)`, turned/swept share | `turn_role` u1/face, `turn_residual` f4/face, `milled_region` u4/face, `brep_valid` u4, `brep_default` u1 (effective-face indexed) | TURNING_SCHEMA 2, salt `splits`                                                              |
| `turning_scan`  | `prep/mesh`                                            | `axis_vectors` (candidate directions), `tollerance` (auto) | per candidate axis: `inlier_fraction` (revolution-compatible area), `radial_fraction` (swept area), `qualified`, the fitted axis line; plus `min_radial_fraction`, `sample_faces`, `total_area` | `axis_role_<k>` u1/face per scanned axis (off-axis / compatible / swept) | TURNING_SCAN_SCHEMA 2 |
| `hull`          | `prep/mesh`                                            | `tollerance` (auto from deflection)                                                                                             | face/area counts, `area_fraction`, `hull_area`                                                                                 | `on_hull` mask, `hull_gap` f4/face, `hull_edges` lines                                                                             | HULL_SCHEMA 1                                                                                |
| `setups`        | `prep/directions`                                      | `indexed` true, `tilt` 90°, `max_setups` 4, `min_setup_area` (auto 0.1 %), `count` 10, `field_options` 3                        | ranked `options[]` (machine, setups, coverage), `total_area`, `field_options`                                                  | per option k: `membership_k` u4/face, `internal_region_k` u4/face, `brep_valid_k`, `brep_default_k`                                | SETUPS_SCHEMA 3, salt `splits`                                                               |
| `setup_verdict` | `cnc/setups`                                           | all of `setups` + `option` 0, `tools` (5-tool default lib), `tollerance` 0.1, `wall_tollerance` 1°, `pixel`, `window` 0.3       | same shape, `verdict: true`, `options[0].verdict{tools, base_coverage, lost}`                                                  | one option's membership set (k=0)                                                                                                  | SETUPS_SCHEMA 3, `key_extra {verdict:1}`, salt `splits` — **stored in the `cnc/setups` dir** |
| `reach_study`   | `prep/directions`                                      | `direction_indices` (blank = all), `tools`, `tollerance` 0.1, `wall_tollerance` 1°, `pixel`, `window` 0.3                       | `directions`, `tools`, per-pair `reachable_faces/area`                                                                         | `reach_<d>_<t>` u1 face mask per (direction × tool)                                                                                | REACH_STUDY_SCHEMA 1                                                                         |
| `precompute`    | `prep/directions`                                      | `directions` [4], `pixel`, `tips`, `clearances`, `window`                                                                       | timing/counts only                                                                                                             | **none** — writes `zcache/dir_*.npz` (`tip_*`, `clear_*`, `sreq_*`)                                                                | — (no result cache)                                                                          |
| `compose`       | `cnc/precompute`                                       | `direction` 4, `diameter` 2, `corner_radius`, `tollerance`, `stickout`, `holder`, `sweep`, `wall_tollerance`, `pixel`, `window` | `unreachable`, `accessible`, `sweep`                                                                                           | **none** — writes `highlights.json`                                                                                                | — (no result cache)                                                                          |


### B2. Injection molding (`processes/injection_molding.py`) — 12


| analysis            | requires                               | params (default)                                                                                                                                                                                            | stats out                                                                                | arrays out                                                                                            | schema                       |
| ------------------- | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ---------------------------- |
| `mold_orientation`  | `prep/directions`                      | `max_slides` 2, `slide_tollerance` 2°, `count` 10, `min_slide_faces` 50                                                                                                                                     | ranked `options[]` (pull pair, slides), `face_count`, `brep`                             | per option k: `membership_k`, `internal_region_k`, `brep_valid_k`, `brep_default_k`                   | MOLD_SCHEMA 4, salt `splits` |
| `thickness`         | `prep/mesh`                            | `max_radius` (auto), `sharp_deg` 25°, `contact_angles` false                                                                                                                                                | min/mean/p05/p50/p95, `cap`, `saturated_fraction`, `excluded_fraction`, `edge_floor/tol` | `thickness` f4/vertex, `limit`, `band_lo`, `band_hi`, `suspect` u1, opt. `contact_angle`              | — (params-only key)          |
| `gaps`              | `prep/mesh`                            | same as thickness                                                                                                                                                                                           | same                                                                                     | `gap` + same companions                                                                               | —                            |
| `ray_thickness`     | `prep/mesh`                            | `max_distance` (auto bbox)                                                                                                                                                                                  | min/mean/percentiles, `saturated_fraction`                                               | `ray_thickness` f4/vertex                                                                             | RAY_SCHEMA 1                 |
| `ray_gap`           | `prep/mesh`                            | `max_distance`                                                                                                                                                                                              | same                                                                                     | `ray_gap` f4/vertex                                                                                   | RAY_SCHEMA 1                 |
| `thin_span`         | `prep/mesh` (sub-runs `thickness`)     | `max_radius`, `max_thickness` (auto p99), `ladder` 1.5, `contrast` 1.5, `max_span`                                                                                                                          | span stats                                                                               | `span_ratio` f4/vertex, `critical_thickness` f4/vertex                                                | SPAN_SCHEMA 1                |
| `slenderness`       | `prep/directions`                      | `direction` 4, `max_diameter` (auto), `ladder` 1.5                                                                                                                                                          | ratio stats                                                                              | `slenderness` f4/vertex, `critical_width` f4/vertex                                                   | SLENDER_SCHEMA 1             |
| `wall_skeleton`     | `prep/mesh`                            | `max_radius` 5, `min_radius` 0.1, `cluster_factor` 1.0, `absorb_factor` 0.5                                                                                                                                 | node/edge counts, `mesh` resolution spec (`edge_thickness_ratio`)                        | `thickness`, `raw_nodes/radii/edges/vert_node`, `cluster_nodes/radii/edges/vert_node`                 | SKELETON_SCHEMA 5            |
| `sprue_proposals`   | `prep/mesh` (sub-runs `wall_skeleton`) | 4 skeleton knobs + `min_gate_thickness` 0.8, `max_candidates` 400, `thick_percentile` 85, `pack_factor` 0.5, `edge_gate_distance` 5, `forbid_side`, `orientation_option` 0, `top_n` 10, **7 score weights** | ranked proposals + subscores                                                             | `candidate_points/node/vertex/face/score/subscores`, `proposal_index`, `best_fill`, `weld_edges_best` | SPRUE_SCHEMA 2               |
| `ejection_sticking` | `prep/mesh` (sub-runs `wall_skeleton`) | 4 skeleton knobs + `grip_deg` 15°, `mu` 0.5, `p_shrink` 0.5 MPa, `orientation_option` 0                                                                                                                     | total force, `pull`, `orientation`                                                       | `draft_deg` f4/face, `grip_faces` u1/face, `vert_force` f4/vertex, `node_load` f4/graph-node          | EJECTION_SCHEMA 2            |
| `flow_fill`         | `prep/mesh` (sub-runs `prep/voxels`)   | `voxel`, `gate` [x,y,z] **required**, `delta0` 0, `skin_coef` 0.12, `fill_time` 2 s, `iterations` 3, `neighborhood` 26                                                                                      | `gate` (point/voxel/snap), `grid`, `voxels_hash`                                         | `arrival` f4/voxel, `frozen` u1/voxel, `vert_arrival`, `vert_frozen`                                  | FLOW_SCHEMA (= 1)            |


### B3. Sheet metal (`processes/sheet_metal.py`) — 3


| analysis       | requires                | params (default)                                                                                                                                                                      | stats out                                                                                                                                                                                              | arrays out                                                                                                                                                                   | schema            |
| -------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| `detect`       | `prep/mesh_coarse`, `prep/aag` | `min_thickness` 0.1, `max_thickness`                                                                                                                                                  | `verdict`, `reasons`, `warnings`, `thickness`, `base_face`, `opposite_face`, `bend_count`, `role_counts`, `features`                                                                                   | `face_role` u1/face (labels), `bend_radius` f4/face                                                                                                                          | SHEET_SCHEMA 3    |
| `flat_pattern` | `prep/mesh_coarse`, `prep/aag` | `k_factor` 0.5, `combine_bends` true, `min_thickness` 0.1, `volume_tolerance` 0.025, `tollerance` 0.1                                                                                 | `developable`, `open_wires`, `flat_area`, `flat_size`, `volume_error_pct`, `volume_ok`, `hole_count`, `bends[]`, `entities`                                                                            | `outline_lines`, `hole_lines`, `bend_lines`, `engraving_lines`, `face_role`                                                                                                  | SHEET_SCHEMA 3    |
| `bend_plan`    | `prep/mesh_coarse`, `prep/aag` (salt `mesh`) | `k_factor` 0.5, `margin` 2 mm, `springback_deg` 2°, `punch_id`/`die_id`, `machine_path`/`punches_path`/`dies_path`, `search` true, `solutions` 4, `mesh_check` false, `min_thickness` | `feasible`, `mode`, `machine`, `thickness`, `panel/bend/sister counts`, `graph`, `actions[]`, `plans[]` (with per-step machine pose), `search_stats`, `tooling`, `fold_mesh`, `mesh_check`, `warnings` | `outline_lines`, `bend_axis_lines`, `required_lines`, `forbidden_lines`, `panel_id`; schema-2: `flat_verts`, `vertex_panel`, `vertex_bend`, `bend_t`; opt. `collision_faces` | BENDPLAN_SCHEMA 3 |


### B4. Tube / profile laser (`processes/tube_laser.py`) — 1


| analysis  | requires                | params                        | stats out                                                                                      | arrays out                                                       | schema        |
| --------- | ----------------------- | ----------------------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------- |
| `profile` | `prep/mesh_coarse`, `prep/aag` | `unroll` true, `k_factor` 0.5 | `verdict`, `reasons`, section dims, `thickness`, length, `entities`, `flat_size`, `hole_count` | `face_role` u1/face; when unrolled `outline_lines`, `hole_lines` | TUBE_SCHEMA 3 |


---

## C. Lens registry (40) → backing analysis

Built by `v2/lenses.ts` from `ProcessPlugin.modes` + a curation overlay. Shared
modes (`brep_faces`, `face_attrs`, `pmi`, `highlights`) are hosted once under
`injection_molding`.


| category | lens                    | backing analysis                                                           | pinned | notes                                  |
| -------- | ----------------------- | -------------------------------------------------------------------------- | ------ | -------------------------------------- |
| model    | `brep_faces`            | prep/mesh (`brep_faces.npy`)                                               | ●      |                                        |
| model    | `face_attrs`            | prep/attributes                                                            | ●      |                                        |
| model    | `pmi`                   | prep/attributes (`pmi.json`)                                               | ●      | full editor rail                       |
| model    | `highlights`            | cnc/compose (`highlights.json`)                                            |        | advanced                               |
| —        | `directions:directions` | prep/directions                                                            |        | **hidden** — own toolbar button        |
| geometry | `thickness`             | injection_molding/thickness                                                | ●      | **field lens**                         |
| geometry | `gaps`                  | injection_molding/gaps                                                     | ●      | **field lens**                         |
| geometry | `rayThickness`          | injection_molding/ray_thickness                                            | ●      | **field lens**                         |
| geometry | `rayGap`                | injection_molding/ray_gap                                                  | ●      | **field lens**                         |
| geometry | `thinSpan`              | injection_molding/thin_span                                                | ●      | **field lens**, no check catalog entry |
| geometry | `thicknessAngle`        | thickness (`contact_angles: true`)                                         |        | advanced, **field lens**               |
| geometry | `gapAngle`              | gaps (`contact_angles: true`)                                              |        | advanced, **field lens**               |
| cnc      | `setups`                | cnc/setups (+ setup_verdict)                                               |        | split-aware assignment UI              |
| cnc      | `features`              | cnc/features                                                               |        |                                        |
| cnc      | `turning`               | cnc/turning                                                                |        |                                        |
| cnc      | `turning_residual`      | cnc/turning                                                                |        | advanced                               |
| cnc      | `hull`                  | cnc/hull                                                                   |        |                                        |
| cnc      | `reach_study`           | cnc/reach_study                                                            |        | one (d × t) mask                       |
| cnc      | `reach_op`              | cnc/reach_study                                                            |        | cone-sliced                            |
| cnc      | `reach_aggregate`       | cnc/reach_study                                                            |        | route verdict                          |
| cnc      | `unified`               | cnc/precompute (zcache)                                                    |        | legacy compose path                    |
| cnc      | `access`                | prep/directions                                                            |        |                                        |
| cnc      | `class`                 | cnc/precompute (zcache)                                                    |        | legacy                                 |
| cnc      | `gap`                   | cnc/precompute (zcache)                                                    |        | legacy                                 |
| cnc      | `stickout`              | cnc/precompute (zcache)                                                    |        | legacy                                 |
| cnc      | `thinSpan`              | injection_molding/thin_span                                                |        | **duplicate of the geometry lens**     |
| molding  | `assignment`            | injection_molding/mold_orientation                                         |        | parting-line optimizer + splits        |
| molding  | `sprue`                 | injection_molding/sprue_proposals                                          |        |                                        |
| molding  | `flowFill`              | injection_molding/flow_fill                                                |        | gate picked in-view                    |
| molding  | `cooling`               | **derived client-side** from prep/voxels `vert_half_thickness` (`coef·t²`) |        | no backing analysis                    |
| molding  | `ejector`               | injection_molding/ejection_sticking                                        |        | interactive pin sim                    |
| molding  | `slenderness`           | injection_molding/slenderness                                              |        | **not a field lens**                   |
| molding  | `skeleton`              | injection_molding/wall_skeleton                                            |        |                                        |
| molding  | `voxelField`            | prep/voxels                                                                |        | advanced                               |
| sheet    | `sheet_roles`           | sheet_metal/detect                                                         |        |                                        |
| sheet    | `bend_radius`           | sheet_metal/detect                                                         |        |                                        |
| sheet    | `flat_pattern`          | sheet_metal/flat_pattern                                                   |        |                                        |
| sheet    | `bend_plan`             | sheet_metal/bend_plan                                                      |        |                                        |
| sheet    | `bend_sequence`         | sheet_metal/bend_plan (fold mesh)                                          |        | animated                               |
| tube     | `tube_roles`            | tube_laser/profile                                                         |        |                                        |
| tube     | `cut_pattern`           | tube_laser/profile                                                         |        |                                        |


---

## D. Checks that exist today

Three evaluator kinds in `v2/checks/evaluators.ts`, dispatched by
`describeCheck` in `v2/checks/catalog.ts`:


| kind                                       | source of truth                                                                    | covers                                                              | verdict rule                                                    |
| ------------------------------------------ | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------- | --------------------------------------------------------------- |
| `threshold`                                | `v2/analyses.ts` `ANALYSES` (**4 entries**: thickness, gaps, rayThickness, rayGap) | 4 analyses                                                          | `stats.min >= threshold` → pass, else review                    |
| `band`                                     | same 4, when `policy.band` is set                                                  | 4 analyses                                                          | per-face mean inside [lo, hi] → review with face count/%        |
| `reach_study` / `reach_op` / `reach_route` | `cnc/reach_study` masks, unioned client-side                                       | 1 analysis                                                          | blocked faces (opt. masked to `feature_id > 0`) → review / fail |
| `stats`                                    | `STATS_VIEWS` rules                                                                | 4 analyses: `sheet_detect`, `flat_pattern`, `bend_plan`, `features` | stored stats predicate; `features` is `na` (exploration only)   |


Route template `catalogue/routes/laser_cnc_brake.yaml` wires: sheet detect →
flat pattern → cnc features → reach (op-scoped, feature-masked) → bend plan.

**So 9 of 25 analyses can become a check. The other 16 are lens-only.**

---

## E. Observations for the keep / clean-up pass

Ordered by how much they cost to leave as-is.

1. `**cnc/precompute` + `cnc/compose` are a parallel, un-cached reach path.**
  They write `zcache` fields and `highlights.json` instead of a result, so they
   are invisible to the manifest, the resolver and the plan layer. Four lenses
   (`unified`, `class`, `gap`, `stickout`) plus `highlights` depend on them.
   `cnc/reach_study` supersedes the verdict half. Decision: retire the lenses and
   keep the pair as a CLI-only interactive path, or port the four lenses onto
   reach_study.
2. `**ANALYSES` (4 entries) is the bottleneck on checks.** `FIELD_LENSES` has 7
  scalar-field lenses but `catalogAnalysisFor` looks up `ANALYSES`, so
   `thin_span` (and the two angle lenses) can be painted and banded on-screen but
   **cannot be saved as a check** — `describeCheck` returns null. Cheapest
   high-value fix in the whole list.
3. **`slenderness` is a scalar field with a threshold param (`maxSlenderness`)
  that never became a field lens.** Same treatment as thin_span would make it
   self-materializing + bandable for free.
4. `**cooling` has no backing analysis** — it is `coef · halfThickness²` computed
  in the painter. Fine as a lens, but it can never carry a check or a finding
   until the rule moves into an analysis (or an explicit "derived lens" concept).
5. `**setup_verdict` rides inside the `cnc/setups` store dir** (`key_extra`),
  distinguished only by `stats.verdict`. Works, but it is the one place where
   "one analysis id = one store dir" does not hold — worth a comment in the UI
   layer if the results list ever becomes user-facing.
6. **Three analyses re-run `wall_skeleton` as a sub-run** (`sprue_proposals`,
  `ejection_sticking`) and one re-runs `thickness` (`thin_span`). Cache-aware, so
   correct — but it means those analyses expose the 4 skeleton knobs in their own
   param forms, which is most of their surface area. Candidate for the same
   `requires`-with-derived-params treatment prep got.
7. **Params that are compute-scope vs interpretation are still mixed in the
  forms.** `slenderness.direction`, `sprue.orientation_option`,
   `setup_verdict.option`, `flow_fill.gate` are all scope-that-recomputes; the
   thresholds are interpretation. The v2 field-lens rail already models this split
   correctly — the other lenses don't.
8. **Coverage gaps by process:** molding has 11 analyses and **zero** checks;
  sheet has 3 analyses and 3 checks; CNC has 9 and 2 (reach + features-as-na).
    If the polishing pass is "finalize lenses and checks one-by-one", molding is
    where the work is.

