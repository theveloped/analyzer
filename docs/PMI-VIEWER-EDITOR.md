# PMI viewer + editor — implementation plan

Consolidates backlog items **11** (PMI inspect panel) and **19** (PMI / GD&T
editor) into one program of work, driven by the `3a` "Full annotation model"
mockup (Claude Design project *CAD GD&T visualization concepts*). Read
docs/CODEMAP.md (frontend seam, artifact contracts) and AGENTS.md hard rule 4
(schema versioning, both sides of the seam) before touching this.

## Where things stand (2026-07)

The **read** and **export** halves already shipped; only the richer *viewer*
and the *editing* path are outstanding.

Shipped (do not rebuild):
- `step_import.py` → `pmi.json` (`PMI_SCHEMA = 4`) + `face_attrs.json`, with
  0-based `brep.iter_faces` face ids bridged to the workdir's re-read
  `source.stp` by (area, centroid) / (length, centroid) signatures. Ambiguous
  symmetric matches are **dropped, never guessed**.
- `step_export.py::export_step` authors `pmi.json` → AP242 via XCAF
  (`_LabelResolver` inverts the same id contract), self-calibrates the metre/mm
  unit, returns an `ExportReport`, never raises on unsupported PMI.
- `pmi_support.py` — the single-source lossy matrix + `roundtrip_warnings(pmi)`.
- API: `GET /api/parts/{id}/pmi`, `/face_attrs`, `/export/step`,
  `/export/step/report`; manifest `pmi` block (counts/warnings/degraded/urls),
  rebuilt from disk every request.
- Frontend: `PmiRail.tsx` (flat lists + click-to-highlight + datum chips +
  Export button + round-trip caveats), `pmiMode` painter (`colorizers/core.ts`,
  amber toleranced / teal datum faces), `ControlFrame.tsx` glyph rendering
  (`GDT_SYMBOL` = the 14 characteristic symbols; `MAT_/MOD_/ZONE_SYMBOL`).
- Tests: `test_pmi.py` (read), `test_pmi_roundtrip.py` (export→re-import).

Gap to the `3a` mockup and to item 19:
- The rail is flat lists; `3a` wants **scope chips** (All / per-datum / Pattern /
  No-datum) and **grouped sections** with a toggleable **dimensions layer**.
- The `pmiMode` painter tints faces only; `3a` wants **feature-control-frame
  callouts floating in the 3D scene** with leader lines / datum triangles.
  **The viewer has no HTML-anchored-to-3D overlay primitive today** — every
  overlay is an in-scene THREE mesh, there is no `CSS2DRenderer`, and measure
  labels are not anchored to a tracked 3D point. This overlay layer is net-new.
- No mutation path at all: no `PUT /pmi`, no editable store slice, no editor
  forms, no face-pick-into-form, no `test_pmi_edit.py`.

## Decisions (agreed)

1. **Viewer `3a` first, then the editor.** Ship the designed visual before the
   authoring path.
2. **3D callouts: selected/hovered frame only in the first cut.** Prove the
   screen-projection overlay infra on one frame at a time before scaling it to
   render every frame at once (the full `3a` "print" look is a follow-up).
3. Plan is written down (this file); implementation proceeds on
   `claude/pmi-viewer-editor-plan-6bo0mx`.

## Phase 0 — foundations (do first, tiny)

- Correct the stale `frontend/src/api/types.ts` "schema 2" doc-comment on
  `PmiData` (backend is `PMI_SCHEMA = 4`; fields already match).
- Single vocabulary source. Item 19 says "build pickers from `pmi_support`," but
  the symbol glyphs live in `ControlFrame.tsx` `GDT_SYMBOL` and the authored
  string vocabulary lives in `step_export._enum_map` (OCP enum names). Introduce
  a shared `pmiVocab` (frontend module) enumerating characteristic types +
  modifiers + dimension kinds, each tagged with its glyph and a `lossy` flag
  cross-checked against `pmi_support`'s `WRITER_UNSUPPORTED_*` / reader-drop
  sets, so the (later) editor cannot author a construct the exporter drops. No
  schema bump — this is a frontend-only derived table plus, optionally, a
  `SUPPORTED_*` enumeration added to `pmi_support.py` for backend validation.

## Phase 1 — viewer `3a`: rail + layers (no new 3D infra)

Rework `PmiRail.tsx` from flat lists into the `3a` structure, all client-side
over the already-cached `pmi.json`:
- **Scope chips**: All / one-per-datum / `⌖ Pattern` / `∅ No datum`. Selecting a
  scope drives the existing `pmiFaces` / `pmiDatumFaces` viewer params plus a new
  `pmiScope` filter param so `pmiMode` dims out-of-scope faces.
- **Grouped sections**: *Control frames · datum-referenced* (tolerances with
  `datum_refs`), *Patterns*, *No datum reference* (form tolerances, slate), and
  *Dimensions* with a "Show on model" checkbox.
- **Pattern collapse** (`8X`): `pmi.json` carries **no explicit pattern
  identity**, so derive it — group tolerances sharing identical
  (type, value, datum-frame, modifiers) whose `face_ids` are sibling features;
  render one `N×` card that highlights every instance. Keep the heuristic in one
  helper with a unit test against the NIST fixtures (a mis-group must degrade to
  separate frames, never merge unrelated tols).
- Extend `pmiMode` (`colorizers/core.ts:658`): honor `pmiScope`, add optional
  per-class / per-datum-network coloring (the `1b`/`1c` ideas), and a
  dimensions-visible flag. No new geometry pass — pure mask logic over
  `loadBrepFaceIds`.

Verify: `cd frontend && npx tsc -b && npm test`; manual against
`tests/nist/nist_ctc_01_asme1_ap242.stp` (datums A/B/C, a pattern, ± dims).

## Phase 2 — 3D floating callout, selected/hovered only (net-new overlay)

Build the projection overlay minimally, for the one frame the user
clicks/hovers in the rail:
- Add a screen-projection hook: either three's `CSS2DRenderer`, or a manual
  per-frame `Vector3.project(camera)` loop exposed from `viewer/scene.ts`, that
  maps a 3D **anchor** (toleranced face centroid — from the fine mesh or
  `brep_meta.json`) to viewport pixels and survives camera moves.
- A React overlay layer in `Workspace.tsx` renders a single `ControlFrame` box +
  SVG leader line (+ datum triangle when the selected entity is a datum) at the
  projected anchor. Reuse `ControlFrame` for the glyphs.
- Wire it to the rail's existing selection (`highlight(...)`), so clicking a
  frame both tints its faces (today) and floats its callout (new).

This deliberately stops short of rendering all frames at once; scaling to the
full `3a` print view is a follow-up once the projection layer is proven.

Verify: `npx tsc -b`; manual — click each frame, callout tracks orbit/zoom and
clears on deselect.

## Phase 3 — editor backend (item 19-A)

- `PUT /api/parts/{id}/pmi` mirroring `api/plan.py:28 put_plan`: a
  `PmiPutRequest` pydantic model in `api/schemas.py` + a jsonschema for
  `pmi.json`; validate, reject/warn constructs outside `pmi_support`'s supported
  set, write the file, then re-derive
  `pmi["warnings"] = pmi_support.roundtrip_warnings(pmi)` (the exact logic at
  `step_import.py:855`). Synchronous, OCP-free, no jobs queue (same class as the
  export route). Manifest auto-reflects because it rebuilds from disk.
- `test_pmi_edit.py` (plain script): PUT `test_pmi_roundtrip._synthetic_pmi` →
  GET → `export_step` → re-import → assert round-trip and that losses are warned.

## Phase 4 — editor frontend (item 19-B/C)

- New editable zustand slice holding the working `pmi` document + dirty flag
  (today's store only carries transient highlight params).
- `PmiEditor` (extend `PmiRail` or a sibling rail): add/edit/delete forms per
  entity type; type/modifier pickers from the Phase-0 `pmiVocab` with lossy
  entries greyed + inline `pmi_support` warning; datum-letter management (A/B/C…).
- Face/edge pick into forms: reuse `onPick` → `loadBrepFaceIds`
  (`colorizers/core.ts:558`) → 0-based BREP id capture (the ids
  `step_export._LabelResolver` inverts). A "pick target faces" / "pick datum
  feature" mode toggle.
- Save → `PUT /pmi` → refetch manifest → rail/viewer update.

## Phase 5 — close the loop + verify

- Manual acceptance (item 19): import `tests/nist/nist_ctc_01_asme1_ap203.stp`
  (no PMI) → author a Position ⌀0.2 |A|B|C on picked faces → Export AP242 →
  re-import → confirm `pmi.json` carries it.
- `python test_pmi.py`, `test_pmi_roundtrip.py`, `test_pmi_edit.py`;
  `cd frontend && npx tsc -b && npm test`.

## Watch-outs (carried from item 19 + this recon)

- **Schema, both sides.** Bump `PMI_SCHEMA` (`step_import.py`), the `types.ts`
  `PmiData` mirror, and `pmi_support` together only if you add editor-only fields
  (e.g. an `authored` provenance flag). AGENTS.md hard rule 4.
- **`pmi_support` stays the single source** so the editor cannot author what the
  exporter silently drops; surface the warning inline at author time.
- **Semantic names are not preserved** by OCCT on AP242 export (datum
  identifiers are). Say so at author time, or add a sidecar if names must persist.
- **Ambiguous symmetric faces are dropped at import** — an authored id set the
  user picks on the *current* geometry avoids that bridging entirely.
