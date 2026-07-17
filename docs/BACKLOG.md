# Backlog — self-contained work items

One item per section, written to be picked up cold in a fresh session:
problem, evidence, where to start, suggested approach, verification.
Delete a section when it lands (git history keeps the record). Ordering
within a tier is the recommended attack order. Background reading:
AGENTS.md first, then docs/CODEMAP.md for the artifacts referenced here.

Shared context for the sheet/tube items: the instapart port (branch
`claude/instapart-port`) added `aag.py` (BREP adjacency graph, `prep/aag`
stage artifact `aag.npz`), `sheet.py`/`unfold.py` (sheet_metal detect +
flat_pattern), `tube.py` (tube_laser/profile), `machining_features.py`
(cnc/features), `step_import.py` (assemblies/colors/PMI) and
`benchmark/sheet_corpus.py`, which scores the pipeline against the
instapart examples corpus (sibling checkout
`..\..\instapart\benchmarks\manifest.yaml`, ~119 fast entries). Current
score: 106/119 files fully passing, 119/119 processed, thickness 82/82.
`python benchmark/sheet_corpus.py --smoke` is the quick regression;
failures land in `<workroot>/sheet_corpus.csv`.

## Tier 1 — port tail (quality gaps with known reproducers)

### 1. Flat-pattern layout debugger + the multi-bend volume tail

**Problem.** Branched multi-bend brackets unfold with zero open wires but
lose ~10% of skin area — some flange still lands mirrored or overlapping
without creating a chainable gap. Reproducer:
`instapart/examples/benchmark/test_1/BenchMark_07.stp` (identical to
`examples/parts/SmartPart_07.stp`): volume error 15.5%, flat area 157.5k
mm² vs skin area 180.8k (neutral-adjusted expectation ~170k). The corpus
expects ≤ 2.5%.

**Where to start.** Build the debug tool first — it pays for itself on
every later sheet bug: a function (CLI flag on `main.py sheet`, e.g.
`--debug_svg out.svg`) that, inside `sheet.flat_pattern`, dumps every
face of the unfolded skin as its own outline in the flat layout (color
per face, face id label at the centroid). Implementation sketch: in
`unfold.Unfolder`, transform each face's full edge set (reuse
`transformed_edge` per edge with the face's scale/chain — the debug
script from the port session did exactly this, see
`_face_wire_edges`-style iteration) and write an SVG polyline per face.
Overlaps and mirrored branches become visually obvious.

**Then fix.** The suspect is the empirical mirror test in
`unfold.Unfolder.unfold` (material-side samples via
`_material_side_point` against the fold line at the edge midpoint).
Known blind spots: faces whose classifier sample fails (falls back to
UV-bbox midpoints), and multi-face bend chains where the local fold
direction differs from the shared-edge midpoint tangent. With the SVG
you can see WHICH link flips and instrument that specific side() call.

**Verify.** `python test_sheet.py` stays green (32 checks);
`python benchmark/sheet_corpus.py --file BenchMark_07` volume ≤ 2.5%;
full corpus run should lift several of the current 13 failures
(SmartPart_33 46.9%→?, `1051830-00_Default` 12%).

### 2. `flat_with_curves` volume errors (3–8%)

**Problem.** `instapart/examples/3dhubs/flat_with_curves_{1,2,3}.step`
are flat plates with large-radius curved (rolled) regions; volume errors
are 3.1–7.8% against the ≤ 2.5% invariant. The manifest notes the legacy
goldens for these were themselves failed unfolds ("port result is
superior"), so the bar is only the volume invariant.

**Hypotheses to check** (in order): (a) large-radius single-curvature
faces in the skin get the bend-allowance scale, which subtracts
thickness from the radius — for a gentle roll on the OUTER skin with
radius ≫ t that is correct, but check the convexity classification of
these faces in `aag.face_convexity` (mid-UV curvature sampling on big
gentle bsplines can misclassify); (b) the thickness ray in
`aag.get_sheet_base` may hit a curved region and report a slightly wrong
t — volume error scales linearly with t; (c) genuinely non-developable
doubly-curved patches excluded from the skin (`ignore_complex`) whose
area then goes missing — if so the right answer is including their area
estimate in `flat_area` or relaxing the invariant with an explicit
`approximate` flag. Use the item-1 SVG tool.

**Verify.** `python benchmark/sheet_corpus.py --file flat_with_curves`
→ volume ok on all three.

### 3. Rolled parts detected with zero bends

**Problem.** `benchmark/test_1/BenchMark_17.stp` (=
`examples/parts/SmartPart_17.stp`): expected 6 bends, we emit 0, volume
error 0.0 (so the unfold thinks it's already flat or the skin excludes
the rolls). SmartPart_17's manifest entry carries code 3 (volume
difference expected) — the bend COUNT is the real target.

**Where to start.** `python benchmark/sheet_corpus.py --file BenchMark_17`
then inspect the workdir's `aag.json` stats and
`sheet_metal/detect` role counts: are the curved faces classified
COMPLEX (`aag.face_convexity == 2`) and dropped from the skin by
`ignore_complex`? The COMPLEX test in `aag.face_convexity` fires when
BOTH |d2u| and |d2v| exceed tolerance at the mid-UV sample — bspline
cylinders (single-curvature geometrically) can trip it because their
parametrization has second-derivative components in both directions.
A more geometric test (principal curvature ratio via
`GeomLProp_SLProps.MinCurvature/MaxCurvature` instead of D2 magnitudes)
would classify bspline rolls as single-curvature CONVEX/CONCAVE.
Touching this reclassifies faces ⇒ bump `AAG_SCHEMA` in `aag.py` and
re-run everything (aag artifacts orphan via the schema check).

**Verify.** `python test_aag.py` green; BenchMark_17/SmartPart_17 bend
counts match; corpus run does not regress (COMPLEX reclassification can
shift skins on other parts — watch the sheets/volume columns).

### 4. Rectangular-tube classifier over-matches machined parts

**Problem.** `benchmark/test_2/P015731_4020_004_A_12.stp` is expected to
be neither sheet nor tube (a machined block), but
`tube.analyse_profile` returns a rectangular verdict.

**Fix sketch.** In `tube._rect_parameters` / `analyse_profile`, add
sanity gates before accepting a rect/square verdict: wall thickness
(|width_a − width_b|/2) must be > 0 and < min(width, height)/2; both
shells must have 4 planar clusters AND the shells' total area should
dominate the part (e.g. > 50% of `graph.face_area.sum()` — a pocketed
block has most area elsewhere). Instapart had no such gates; expecting
`none` here means being stricter than the legacy tool, which the
manifest already encodes.

**Verify.** `python test_tube.py` green (round/rect/square fixtures
still classify); `--file P015731` → tubes:ok; `--file "tube/"` still
12/12.

### 5. Corpus runner hardening + the slow assemblies

**Problem.** `benchmark/sheet_corpus.py` runs everything in-process: one
OCCT hard crash (they exist — see the `FindAttribute` access-violation
class) would kill a whole sweep, and there is no per-file timeout. Also
the six `timeout_s: 900` manifest entries (assemblies up to 285 parts,
e.g. `examples/assy/BUITENPONTON.stp`) have never been scored — run
them once.

**Fix sketch.** Wrap `run_entry` in a `subprocess.run([sys.executable,
__file__, '--file', <path>, '--child-json', ...])` mode with
`timeout=entry['timeout_s']`, parent aggregates JSON rows (mirrors
instapart's `benchmarks/worker.py` design). Then
`python benchmark/sheet_corpus.py --slow` overnight; triage whatever the
big assemblies surface (likely: multi-solid parts — a prototype whose
shape is a compound of several solids goes through `get_sheet_base`
whole, which is wrong; instapart iterated `get_shape_solids` per part.
If that bites, split compounds into solids at import or in the runner).

**Verify.** Kill -9 a child mid-run → sweep completes with that file
marked error; `--slow` produces a scored CSV for the 6 assemblies.

### 6. Remaining corpus triage (small singles)

- `examples/xml/EMO-65-13-301.STEP`: expected 1 tube among the parts, we
  find 0; also 2 angle mismatches. Check which child part is the tube
  (likely a bspline profile → see item 4 gates / item 3 classification).
- `benchmark/test_2/P015732_0760_003_A.stp`: 12 sheets all detected,
  angles 35°/55.6°/−73.3° vs expectations — probably one part unfolding
  from the other side or a conical (non-cylindrical) bend; instrument
  with the item-1 SVG.
- `benchmark/test_2/1051830-00_Default.stp`: >180° hem part (−186° bend
  emitted); expected sheets=0 with legacy code... re-check its entry —
  if the legacy failed there, the scorer may just need the hem angle
  reported correctly (domain span is exact for analytic cylinders; −186°
  may be RIGHT and only the sheets-count expectation wrong).
- SmartPart_10-class: a hem modeled as two tangent quarter-cylinders is
  emitted as ONE −180° bend (C2-merged); the manifest expects 2×90°
  because legacy curvature sampling kept them apart. Ours is arguably
  the correct manufacturing answer (one hemming stroke) — consider a
  scorer allowance (two expected angles summing to a found angle at the
  same fold) instead of changing the geometry code.

## Tier 2 — new capability on the seams that now exist

### 7. Sheet DFM checks (hole-to-edge, hole-to-bend, flange length, bend relief)

The original `sheet_metal` stub named these. All are numpy over data the
flat_pattern result already stores: `entities.contour/holes` (bulge
polylines in the flat frame), `entities.bend_lines` (with angle/radius/
direction), `stats.thickness`. Rules of thumb: hole-to-edge ≥ t,
hole-to-bend ≥ 2t + r, min flange ≥ 3t (make them params). Implement as
a `sheet_metal/dfm_checks` AnalysisDef (requires sheet_metal/
flat_pattern; reuse `load_cached_result` chaining like
`cnc/setup_verdict` does over `cnc/setups`) emitting per-fine-face masks
(broadcast via hole→feature faces / bend faces) + a violations list in
stats; frontend mode via `maskMode`/`paintCategory` + legend focus.
Distance math: point-to-polyline distance between discretized hole
paths and contour/bend lines — `scipy.spatial.cKDTree` over the
discretized `points`. Verify with an L-bracket fixture with a hole
placed 1mm from the edge and one 1mm from the bend.

### 8. Draft-angle analysis (injection molding)

APPROACH.md lists it as future work. Per-face draft = angle between
`aag.npz` face normals (or `normals.npy` per fine face — already exact)
and the mold pull direction from the `injection_molding/
mold_orientation` result (`options[i].pair` direction indices into
`directions.npy`). Emit a per-face signed draft angle field + mask below
a `min_draft` param (default 0.5–1°), split by mold side using the
membership field the mold result already stores. Frontend: heatmapMode
with flagDirection below. No new geometry; a day of plumbing.
Salt `directions_fingerprint` + the mold result hash into the cache key.

### 9. Feature-aware CNC setups

`cnc/features` stores per-feature `axis` vectors. Feed them into the
setup search: match feature axes against `directions.npy` (dot ≥ cos
tol) and (a) report per-setup which holes are drillable on that setup,
(b) flag features whose axis matches NO sampled direction (needs an
added direction — `compute_directions` could append feature axes the
way `--axes` prepends principals). Start read-only: extend
`pipeline.cnc_setups` stats with a `features` section; UI chip in
`setups.tsx`. Later: cost model (drilling vs milling).

### 10. Assembly navigation in the viewer

`assembly.json` (instance tree with translation+quaternion per instance,
child part ids, quantities) and `GET /api/parts/{id}/assembly` exist;
the part list ignores them. Add: group child parts under their assembly
record in the sidebar (parts.py `list_parts` already returns all;
frontend groups by scanning manifests or a new lightweight
`/api/assemblies` that lists workdirs containing assembly.json), show
quantity badges, click-through to children. Stretch: an exploded 3D
overview rendering each child's coarse mesh at its instance transform.

### 11. PMI inspect panel

`pmi.json` + `face_attrs.json` are served (`pmi_url`/`face_attrs_url` in
the manifest) with 0-based face ids and AAG-canonical edge ids. Wire
into `inspect`: clicking a face with `pmi_refs` lists its dimensions/
tolerances ("Ø6 H7 · Position ⌀0.2 |A|B|C|") by joining pmi.json
entities on face id (map fine face → BREP id via the brep_faces field,
as `faceAttrsMode` already does). Also a "PMI" view mode painting
annotated faces + legend per datum/tolerance with FocusTracker
click-to-fly. Real test data: `tests/nist/nist_ctc_0*_ap242.stp` (12
dimensions, 6 tolerances, datums A/B/C on ctc_01).

### 12. `prep/classify` process suggestion

Sheet detect, tube profile and cnc/features all emit verdicts. A cheap
`prep/classify` analysis (or pure-frontend composition) that runs/reads
all three and suggests the process tab: sheet if detect==sheet AND
pattern developable AND volume ok; tube if profile verdict != none;
else machined. The corpus runner's `run_part` in
`benchmark/sheet_corpus.py` already implements exactly this dispatch —
lift it into `pipeline.py` and share.

### 13. 2D flat-pattern canvas + tube DXF

The pattern currently renders as translated 3D `setLines` beside the
part. A dedicated 2D SVG/canvas panel (own pan/zoom, dimensions, bend
labels, entity hover) would make it a real drawing. The data is all in
the stored result (`entities` bulge polylines). Also: the DXF route only
covers `sheet_metal/flat_pattern`; `tube_laser/profile` stores the same
`entities` shape — extend `dxfexport.export_dxf` dispatch + a Controls
link in the tubelaser plugin.

### 14. Bent-tube centerlines

Out of scope of the port (instapart never had it). Constant-section
bent tubes: detect via C2 ring groups repeating along a path; centerline
from cylinder axes of segments + torus centers of elbows
(`brep_meta.json` has torus center/axis/major_radius). Deliver:
centerline polyline (viewer graph overlay via `setGraph`), cut length,
bend table (angle, radius, rotation). Sizeable; spec before building.

### 15. Per-material K-factor / bend deduction tables

`k_factor` is a single slider. Real shops use per-material+thickness+
radius tables and sometimes bend deduction instead of allowance. Add an
optional `material` param (select) resolving k from a small JSON table
(`sheet_materials.json`), overridable per bend later (the AAG node
attrs already reserve `bend_radius`/`k_factor` per face in instapart's
model — `unfold.node_scale` takes k_factor as an argument, so plumbing
per-face overrides is straightforward).

## Meta

- The port branch `claude/instapart-port` (10 commits) may still be
  unpushed — push / open a PR before stacking new work.
- When an item changes stored-field semantics, bump the matching schema
  (`SHEET_SCHEMA`/`TUBE_SCHEMA`/`FEATURES_SCHEMA`/`AAG_SCHEMA`) on BOTH
  sides of the seam (processes/*.py ↔ frontend plugin) — see AGENTS.md
  hard rule 4.
