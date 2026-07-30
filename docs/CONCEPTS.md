# Concepts — the canonical vocabulary

This file is the single place that says what a word means. It exists because the
vocabulary grew faster than its definitions: `verdict` had picked up six meanings,
`step` five, `study` three, and `view mode` and `lens` were two names for one object
with no doc recording the rename.

**How to read it.** On *behaviour*, the code wins — if this file describes something
the code does not do, the entry is marked **aspiration** and the code is right. On
*naming*, this file wins: new code and new prose use these words this way, and the
rulings in §5 are decisions, not suggestions.

Deliberately no counts. "40 lenses" rots the moment someone adds one, and the
contradictions this file was written to fix were mostly stale numbers.
Counts live in [ANALYSIS-INVENTORY.md](ANALYSIS-INVENTORY.md), dated.

Anchors name a **file and a symbol**, never a line number, for the same reason —
`graphify explain "<symbol>"` resolves one and no edit above it can rot it.

---

## 1. The five layers

Every concept sits in exactly one layer, and each layer has one job.

| Layer | Job | Concepts |
|---|---|---|
| **Data** | what is true about the part | artifact · result · field · mask · array · association · effective face id · fingerprint |
| **Computation** | how it is produced | process · analysis · prep stage · param · cache key · salt · job · manifest |
| **Presentation** | how it is seen | lens · study · viewer param |
| **Judgement** | what it means | check · policy · scope · finding · disposition · status axes |
| **Intent** | what we will do | plan · operation · decision · candidate · assignment · route · machine template · report |

The rules that hold the layers apart:

- **An analysis is the only runnable unit.** A prep stage is an analysis with a
  currency gate; there is no second kind. A *study* is not a computation.
- **A lens is never a verdict. A check never computes.** A lens shows data; a check
  interprets a stored result against a pinned policy.
- **What a computation was asked about is a declared param; which answer you read is
  not.** This is why browsing candidates costs nothing and changing a selection
  re-keys nothing.
- **A field's `association` names its index space.** It is the only thing standing
  between the coarse preview and a silently wrong paint.
- **A decision's `value` is derived, never authored.**

---

## 2. Glossary

Status column: **modelled** (a type or validator enforces it) · **convention**
(consistent but nothing enforces it) · **aspiration** (documented, not built).

### Data

| Term | Definition | Anchor | Status |
|---|---|---|---|
| **artifact** | A fixed-name file in the part workdir (`fine_faces.npy`, `aag.npz`, `directions.npy`). Written by a prep stage, overwritten in place, never params-keyed. | [CODEMAP.md](CODEMAP.md) workdir table | convention |
| **result** | One stored answer from an analysis: JSON `stats` plus an optional npz of `arrays`, addressed by `(process, analysis, cache key)`. Immutable; a new key means a new file. | `processes/base.py` `AnalysisResult`, `store_result` | modelled |
| **stats** | The JSON-safe half of a result — scalars, ranked option lists, per-candidate rows. What a check reads. Untyped: each analysis decides its own keys. | `processes/base.py` `AnalysisResult` | convention |
| **arrays** | The npz half of a result — the per-element data a lens paints. | `processes/base.py` `store_result` | convention |
| **field** | One array plus its descriptor, as the manifest publishes it: id, dtype, role, length, url, and an **association**. | `api/manifest.py` `_result_entries`; `frontend/src/api/types.ts` `FieldDescriptor` | modelled |
| **mask** | A boolean/`u1` field — a selection. Contrast **field** used loosely for a scalar. Say *mask* when it is a yes/no and *scalar field* when it is a number. | — | convention |
| **association** | Which index space a field lives in: `vertex`/`face` = the **fine** mesh, `brep_face` = the BREP (valid on the coarse preview too), `graph` = skeleton nodes, `none` = unindexed geometry. Before the fine mesh exists the manifest publishes only `brep_face` and `none`. | `processes/base.py` `FIELD_ASSOCIATIONS` (required at write time); `frontend/src/api/types.ts` `FieldAssociation` | modelled |
| **effective face id** | The face id space that includes user splits: BREP ids below `n_brep`, sub-face ids above. What assignment arrays and finding geometry refs are indexed by. | `splits.py` `effective_face_ids` | modelled |
| **fingerprint** | A content hash of an artifact, used both as a currency gate and as a cache-key salt. Distinct from **hash** (of a param dict) and **sha** (of a file copied into a plan). | `pipeline.py` `mesh_fingerprint`, `splits_fingerprint` | modelled |

### Computation

| Term | Definition | Anchor | Status |
|---|---|---|---|
| **process** | A grouping of analyses for one manufacturing route (`prep`, `cnc`, `injection_molding`, `sheet_metal`, `tube_laser`). A namespace, not a behaviour. | `processes/base.py` `ProcessDef` | modelled |
| **analysis** | **The only runnable unit.** Declared params + `requires` + a `run(workdir, params, progress)`. Everything computed is one. | `processes/base.py` `AnalysisDef` | modelled |
| **prep stage** | An analysis that declares `is_current`, so the resolver may run it as a prerequisite. It writes artifacts, not results. Not a separate type — the gate *is* the definition. | `processes/prep.py`; `processes/resolver.py` `ensure` | convention |
| **candidate-indexed analysis** | An analysis whose params take a *set* (`direction_indices`, `axis_vectors`) and whose result carries one row or array per member. Reading one member is free; asking about a different set is a different result. Replaces the old backend sense of "study". | `cnc/reach_study`, `cnc/turning_scan` | convention |
| **param** | A declared, typed input that **keys the cache**. Anything that changes *what is computed* belongs here. | `processes/base.py` `Param` | modelled |
| **cache key** | `resolver.cache_key` = declared params + `schema` + transitive prep fingerprints + opt-in salts + `key_extra`, hashed by `params_hash`. The single builder; runners never hand-salt. | `processes/resolver.py` `cache_key`; `processes/base.py` `params_hash` | modelled |
| **salt** | A fingerprint folded into a cache key that is not a declared param. Auto for prep prerequisites; opt-in via `salts=("splits",)` / `("mesh",)`. | `processes/resolver.py` `_OPT_IN_SALTS` | modelled |
| **job** | One queued analysis run. **One worker thread for the whole server** (meshlib is not concurrency-safe) and one queued/running job per part. | `api/jobs.py` `JobManager.submit` | modelled |
| **manifest** | The per-part index the viewer reads: mesh levels, fields, results, directions, plan section. Rebuilt from disk on every request, so CLI and UI see the same thing. | `api/manifest.py` `build_manifest` | modelled |

### Presentation

| Term | Definition | Anchor | Status |
|---|---|---|---|
| **lens** | Anything paintable or activatable over the model. The user-facing word for a `ViewMode`. **A lens is never a verdict.** | `frontend/src/registry/types.ts` `ViewMode`; `frontend/src/v2/lenses.ts` `Lens` | modelled |
| **field lens** | A lens over one scalar field that *materializes itself* — clicking it runs the backing analysis with plain defaults. Membership in `FIELD_LENSES` is the only marker; `Lens` carries no discriminant. | `frontend/src/v2/fieldLenses.ts` `FIELD_LENSES` | convention |
| **study** | A comparison surface over many candidates at once, opened from the left rail into the right one. A lens paints one thing, a check judges one thing, a study lays candidates side by side so you can pick. Exploration — opening one persists nothing. | `frontend/src/v2/studies.ts` `Study` | modelled |
| **viewer param** | Client-side display state per process (`viewerParams[processId]`): thresholds, which candidate is shown, which result hash is pinned. Never touches a cache key. | `frontend/src/registry/types.ts` `ViewCtx.params` | modelled |

### Judgement

| Term | Definition | Anchor | Status |
|---|---|---|---|
| **check** | A scoped interpretation of one stored result against a **pinned policy**, producing a verdict and findings. A check computes nothing; it may *trigger* the analysis it reads. | `plans.py` module docstring (shape); `frontend/src/v2/checks/catalog.ts` `CheckView` | modelled |
| **policy** | The interpretation knobs pinned on a check — threshold, band, scope, aggregation. Never in the cache key. | `plans.py` module docstring; `frontend/src/v2/checks/evaluators.ts` | modelled |
| **scope** | What a check interprets *over*: the whole part, one operation's cone, or the route. Lives in `policy.scope`. | `frontend/src/v2/checks/catalog.ts` `CheckView` | convention |
| **finding** | An atomic issue derived from (result, policy, scope). Never hand-authored. Identity is `check id + finding code` — deliberately excluding the result hash, so a disposition survives a re-run that reproduces the same issue. | `frontend/src/v2/checks/evaluators.ts` `Finding` | modelled |
| **severity** | A per-finding axis (`review` / `fail`), distinct from the check's verdict. | `frontend/src/v2/checks/evaluators.ts` `Finding.severity` | modelled |
| **disposition** | An authored human judgement on a finding — who, when, why. Append-only; latest per finding wins. | `plans.py` `DISPOSITION_STATES` | modelled |
| **eval key** | The memo key for a derived evaluation: expected result hash + policy + scope. In-memory today, not a stored derivation cache. | `frontend/src/v2/checks/catalog.ts` | convention |

**Status is four independent axes, never one field. "Computed" is not "good."**

| Axis | Values | Anchor | Status |
|---|---|---|---|
| **execution** | `not_run` · `queued` · `running` · `current` · `stale` · `error` | `frontend/src/v2/checks/status.ts` `ExecutionState` | modelled |
| **verdict** | `pass` · `review` · `fail` · `na` · `unknown` — `unknown` is load-bearing: a verdict only counts when execution is current or stale. | `frontend/src/v2/checks/status.ts` `VerdictState` | modelled |
| **disposition** | `open` · `accepted` · `customer_approval` · `resolved` (the UI writes only the first two) | `plans.py` `DISPOSITION_STATES` | modelled |
| **audience** | Who a check is for. Documented historically as `internal/customer/report`; **in code it is a boolean** `visible`, consumed only by the publish flow. Called *audience*, not visibility — see §5. | `frontend/src/api/types.ts` `PlanCheck.visible` | aspiration (as an enum) |

A part also carries its own execution-ish state — `raw` (nothing built) · `preview`
(the first-load bundle landed, renderable and inspectable) · `meshed` (the fine mesh
exists). Not one of the four axes; it describes the part, not a check.

### Intent

| Term | Definition | Anchor | Status |
|---|---|---|---|
| **plan** | The per-part production plan: decisions, ordered operations, checks, a revision counter. Authored; it never stores computed data. Always qualify — a *setup plan* and a *bend plan* are different objects. | `plans.py` `save_plan`, `validate_plan` | modelled |
| **revision** | The plan's optimistic-concurrency counter. A write sends the revision it edited; a mismatch is a 409. A report freezes exactly one. | `plans.py` `save_plan` | modelled |
| **operation** | One ordered manufacturing step in the plan: `kind`, `config`, a machine template, and its checks. This is the only word for it — see §4 on *step*. | `frontend/src/api/types.ts` `PlanOperation` | modelled |
| **decision** | A slot holding a candidate set, a selection and a lifecycle state. `value` is derived from the selection on every save, and is the stable path checks bind to via `{"$plan": …}`. | `plans.py` `DECISION_STATES`, `normalize_decisions`; `frontend/src/api/types.ts` `DecisionSlot` | modelled |
| **slot** | The name a decision is filed under (`decisions.directions`). The addressing unit. | `plans.py` `normalize_decisions` | convention |
| **candidate** | One option in a decision's candidate set, with a stable id. Generated or hand-added, then curated — never implicit. | `frontend/src/api/types.ts` `Candidate` | modelled |
| **assignment** | The partition a decision induces over the part: face → chosen candidate. Exists as `membership_k` / `brep_default_k`, indexed by **effective face id**, with `254` = conflict (wants a user cut) and `255` = unreachable. | `molding.py` `brep_defaults`, `machining.py` `setup_defaults` | convention |
| **route** | A template that instantiates a set of operations and their checks in one go, snapshotting each machine template into the plan. | `catalogue/routes/*.yaml`; `plans.py` `instantiate_route` | modelled |
| **machine template** | A YAML machine definition in `catalogue/machines/`, content-addressed and **copied** into `plan_assets/` on assignment so plans stay self-contained. Not the tilt-cone "machine" of a setup search — see §4. | `catalogue/machines/*.yaml` | modelled |
| **report** | An immutable published bundle freezing one plan revision: per-check verdict, findings, screenshots, and copies of the referenced results. Always *report bundle*, never just "bundle". | `plans.py` `publish_report` | modelled |
| **evidence** | Whatever a report copies in to make a finding checkable later — result JSON, a screenshot, a camera pose. Loose by design; say which kind. | `plans.py` `publish_report` | convention |

---

## 3. Vocabularies are enforced, not documented

Every fixed set of legal strings in this file is a frozenset in code, checked
where the value enters — an unknown value raises rather than being accepted and
then silently doing nothing:

| Vocabulary | Lives in | Checked at |
|---|---|---|
| param type | `processes/base.py` `PARAM_TYPES` | `Param.__post_init__` (import time) |
| field association / role / dtype | `processes/base.py` `FIELD_ASSOCIATIONS`, `FIELD_ROLES`, `FIELD_DTYPES` | `store_result` — the one boundary every result passes through |
| salt name | `processes/base.py` `KNOWN_SALTS` | `AnalysisDef.__post_init__`, plus an assert that `resolver._OPT_IN_SALTS` implements each |
| decision kind, decision state, disposition state, operation kind, **stats rule** | `plans.py` (`DECISION_PROJECTIONS` is the kind vocabulary — a kind exists exactly when its `value` can be derived) | `validate_plan`, `append_disposition`, and a Pydantic `Literal` on the request |
| stats rule (frontend half) | `frontend/src/v2/checks/evaluators.ts` `StatsRule` | TS: the evaluator table and the card table are both `Record<StatsRule, …>`, so a rule with logic but no card will not compile |
| lens curation keys, and the lenses route templates name | derived from `ProcessPlugin.modes` | `frontend/src/v2/lenses.test.ts` |

Adding a vocabulary means adding both halves, and the halves are compared by a
test rather than by a comment: **`test_vocab.py`** parses each named TS union
(`ParamType`, `FieldAssociation`, `FieldRole`, `FieldDtype`, `OperationKind`,
`DecisionKind`, `DecisionState`, `DispositionState`, `StatsRule`) and asserts it
equals the Python set. Two consequences worth knowing before you add one:

- **Declare the mirror as a named exported union**, never inline in an interface
  member. `association: 'vertex' | 'face' | …` inside `FieldDescriptor` cannot be
  found by name, so it cannot be checked; `export type FieldAssociation = …` can.
- **A vocabulary that lives only on one side still needs its own test.** Lens keys
  exist only in TS, so `lenses.test.ts` owns them — including the `lens:` keys
  route templates name, which no Python check can see.

`FieldRole` was missing `fold` for an entire schema version before this existed.

## 4. Where a new thing goes

Adding something? It is one of these, or it does not exist yet:

- It **computes** → an analysis. Not a check, not a lens, not a study.
- It **shows** one thing over the model → a lens.
- It **compares** many candidates → a study (a surface), reading candidate-indexed
  analyses (computations).
- It **judges** a stored result against a pinned threshold → a check.
- It **records what we chose** → a decision on the plan.
- It **records what we will do** → an operation.

If it seems to be two of these, it is two things.

---

## 5. Rulings

Words that were being used for more than one thing. These are decisions.

| Word | Ruling |
|---|---|
| **lens**, not *view mode* | *Lens* in prose everywhere; `ViewMode` stays the code type for the object a plugin registers. |
| **step** — retired | Say **operation**. "Step" also means the STEP file format, a bend-plan step, and a route loop variable; the UI "Step" it was competing with never existed in code or on screen. |
| **study** — presentation only | A study is the UI comparison surface. The backend sense is now *candidate-indexed analysis*. Ids like `cnc/reach_study` keep their names — they are ids, not claims. |
| **verdict** — the status axis only | Other senses get their own words: `stats.classification` for a sheet/tube/turning classification string; *setup re-check* for what `cnc/setup_verdict` does; *reach test* for `zmap.py` `tool_face_verdict`; *feasibility* for a mold option. The `cnc:unified` lens must not be called a verdict view. |
| **audience**, not *visibility* | The publication status axis is **audience**. `visibility` is reserved for line-of-sight — `zmap.py` `face_visibility`, `accessibility.npy` — which is the analyzer's central primitive, and for UI show/hide. |
| **stage** — prep only | *Prep stage* is the only stage. Pipeline narratives name their commands (`mesh`, `directions`, `setups`) rather than numbering stages. |
| **segment** — struck | The word had four unrelated uses and no definition. The concept it kept being reached for is **assignment**. Tooling *sections*, cut *paths* and line *segments* keep their own names. |
| **plan / machine / report / bundle / catalogue** | Kept, always qualified: *production plan* vs *setup plan* vs *bend plan*; *machine template* vs the tilt-cone *machine* of a setup option; *report bundle* vs the *first-load bundle* prep stage; the YAML *catalogue* vs a *tool library*. |
| **`Analysis` in `v2/analyses.ts`** | A misnomer — it is a **check preset**: its `id` is a viewer modeId and it carries a field called `analysis` pointing at the real one. Renaming is out of scope; treat the name as wrong when reading it. |

---

## 6. Not built yet

Defined now so the next flow does not re-decide the vocabulary. All **aspiration** —
nothing implements them.

| Term | Definition |
|---|---|
| **stock / workpiece state** | A decision whose candidates are stock primitives (bounding box, PCA box, bar, tube), held **declaratively** over the final-part face space — stock plus allowances, never intermediate geometry. Hard rule 3 makes intermediate meshes toxic to the cache. |
| **material** | A decision whose value materializes into analysis params (and later, cost). |
| **sequence** | A decision whose selection is *ordered* rather than a set — bend order being the first. |
| **quotation input** | A structured field on an operation feeding a price. Nothing declares one: `PlanOperation.outputs` held the slot for a while with no producer or consumer and has been deleted — the shape should be decided by whatever first needs to read it. |

---

## Related

- [PLAN-ARCHITECTURE.md](PLAN-ARCHITECTURE.md) — how the plan layer is built: keying
  rules, storage, phases.
- [ANALYSIS-INVENTORY.md](ANALYSIS-INVENTORY.md) — what exists today, with counts.
- [CODEMAP.md](CODEMAP.md) — file map and on-disk contracts.
