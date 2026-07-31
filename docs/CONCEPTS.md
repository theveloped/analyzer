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
| **Judgement** | what it means | check · policy · scope · finding · status axes |
| **Intent** | what we will do | route · operation · assignment · machine profile |

The rules that hold the layers apart:

- **An analysis is the only runnable unit.** A prep stage is an analysis with a
  currency gate; there is no second kind. A *study* is not a computation.
- **A lens is never a verdict. A check never computes.** A lens shows data; a check
  interprets a stored result against a pinned policy.
- **An operation is the only place a choice is recorded.** Lenses and studies
  persist nothing; adding an operation is the act of deciding. There is no second
  record of the same choice to drift away from it.
- **What a computation was asked about is a declared param; which answer you read is
  not.** This is why browsing candidates costs nothing and changing a selection
  re-keys nothing.
- **A field's `association` names its index space.** It is the only thing standing
  between the coarse preview and a silently wrong paint.

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
| **check** | A scoped interpretation of one stored result against a **pinned policy**, producing a verdict and findings. A check computes nothing; it may *trigger* the analysis it reads. **Always authored** — nothing seeds checks, so a check on the route is one somebody meant. | `route.py` module docstring (shape); `frontend/src/v2/checks/catalog.ts` `CheckView` | modelled |
| **policy** | The interpretation knobs pinned on a check — threshold, band, scope, aggregation. Never in the cache key. | `route.py` module docstring; `frontend/src/v2/checks/evaluators.ts` | modelled |
| **source** | One result a check reads, as `{id, analysis, params}`. A check carries either one `analysis` or a list of sources — never both. Params are computation and key the cache. | `route.py` `validate_route`, `check_status` | modelled |
| **term** | (field, rule) — one field turned into a yes/no region. Three rules, one per role that carries a per-element value: a **band** on a scalar, set/not-set on a **mask**, a value set on a **category**. Interpretation only: editing a rule recomputes nothing. | `frontend/src/fields/expression.ts` `ExprTerm` | modelled |
| **expression** | Terms folded left to right with `and` / `or` / `andNot`, verdicted on the composed mask's **area share** against a pinned limit. The general form the other check kinds are special cases of. | `frontend/src/fields/expression.ts` `buildMask` | modelled |
| **scope** | What a check interprets *over*: the whole part, one operation, or the route. Lives in `policy.scope`. | `frontend/src/v2/checks/catalog.ts` `CheckView` | convention |
| **finding** | An atomic issue derived from (result, policy, scope). Never hand-authored, and never acknowledged — there is no disposition layer, so a finding is purely derived and lives only as long as its evaluation. Identity is `check id + finding code`. | `frontend/src/v2/checks/evaluators.ts` `Finding` | modelled |
| **severity** | A per-finding axis (`review` / `fail`), distinct from the check's verdict. | `frontend/src/v2/checks/evaluators.ts` `Finding.severity` | modelled |
| **eval key** | The memo key for a derived evaluation: expected result hash + policy + scope. In-memory today, not a stored derivation cache. | `frontend/src/v2/checks/catalog.ts` | convention |

**Status is two independent axes, never one field. "Computed" is not "good" — and
"no checks" is not "passing."**

| Axis | Values | Anchor | Status |
|---|---|---|---|
| **execution** | `not_run` · `queued` · `running` · `current` · `stale` · `error` | `frontend/src/v2/checks/status.ts` `ExecutionState` | modelled |
| **verdict** | `pass` · `review` · `fail` · `na` · `unknown` — `unknown` is load-bearing: a verdict only counts when execution is current or stale. | `frontend/src/v2/checks/status.ts` `VerdictState` | modelled |

There were four. **disposition** (an authored human judgement on a finding) and
**audience** (who a check is for) both belonged to the report/publish flow and
were cut with it — see §6. A route with no checks reads as *unassessed*, and the
rail says so rather than showing a clean slate.

A part also carries its own execution-ish state — `raw` (nothing built) · `preview`
(the first-load bundle landed, renderable and inspectable) · `meshed` (the fine mesh
exists). Not one of the axes; it describes the part, not a check.

### Intent

| Term | Definition | Anchor | Status |
|---|---|---|---|
| **route** | The per-part production route: ordered operations, checks, a revision counter. Authored; it never stores computed data. Was called *plan*, which meant three things — see §5. | `route.py` `save_route`, `validate_route` | modelled |
| **revision** | The route's optimistic-concurrency counter. A write sends the revision it edited; a mismatch is a 409. | `route.py` `save_route` | modelled |
| **operation** | One ordered manufacturing step: `kind`, `config`, an optional machine name, and the checks that name it. **Atomic** — one approach direction, one bend, one turning axis; a tilt cone is a property of a *grouping*, not of an operation. This is the only word for it — see §4 on *step*. | `route.py` `OPERATION_KINDS`; `frontend/src/api/types.ts` `Operation` | modelled |
| **candidate** | One option a study lays out — a direction, an axis, a ranked setup plan. Free: generated client-side or computed by an analysis that takes a *set*. Comparing them persists nothing; committing to one means adding an operation. | `frontend/src/processes/directions/build.ts` `GeneratedDir`; `frontend/src/v2/table/columns.ts` | convention |
| **assignment** | The partition induced over the part: face → chosen operation. Exists as `membership_k` / `brep_default_k`, indexed by **effective face id**, with `254` = conflict (wants a user cut) and `255` = unreachable. | `molding.py` `brep_defaults`, `machining.py` `setup_defaults` | convention |
| **machine profile** | A YAML machine definition in `catalogue/machines/`. A plain reference library: an operation stores the **name**, nothing is copied into the workdir. Not the tilt-cone "machine" of a setup search — see §5. | `catalogue/machines/*.yaml`; `route.py` `list_machines` | modelled |
| **grouping** | *(not built)* The object that says "these two milling ops share one 3+2 fixturing" or "these three bends share brake tooling". An inference over the operation list, never authored per operation. | — | aspiration |

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
| operation kind, **stats rule** | `route.py` `OPERATION_KINDS`, `STATS_RULES` | `validate_route` — the one boundary every stored route passes through |
| stats rule (frontend half) | `frontend/src/v2/checks/evaluators.ts` `StatsRule` | TS: the evaluator table and the card table are both `Record<StatsRule, …>`, so a rule with logic but no card will not compile |
| lens curation keys, and the lens keys named in code | derived from `ProcessPlugin.modes` | `frontend/src/v2/lenses.test.ts` |

Adding a vocabulary means adding both halves, and the halves are compared by a
test rather than by a comment: **`test_vocab.py`** parses each named TS union
(`ParamType`, `FieldAssociation`, `FieldRole`, `FieldDtype`, `OperationKind`,
`StatsRule`) and asserts it equals the Python set. Two consequences worth knowing before you add one:

- **Declare the mirror as a named exported union**, never inline in an interface
  member. `association: 'vertex' | 'face' | …` inside `FieldDescriptor` cannot be
  found by name, so it cannot be checked; `export type FieldAssociation = …` can.
- **A vocabulary that lives only on one side still needs its own test.** Lens keys
  exist only in TS, so `lenses.test.ts` owns them — including the keys
  `fieldLenses.ts` and `analyses.ts` name, which no Python check can see.

`FieldRole` was missing `fold` for an entire schema version before this existed.

## 4. Where a new thing goes

Adding something? It is one of these, or it does not exist yet:

- It **computes** → an analysis. Not a check, not a lens, not a study.
- It **shows** one thing over the model → a lens.
- It **compares** many candidates → a study (a surface), reading candidate-indexed
  analyses (computations).
- It **judges** a stored result against a pinned threshold → a check.
- It **records a choice** → an **operation**. There is no other place. If the
  choice does not yet correspond to something you will run, it is not recorded —
  it is a selection in a study, and studies persist nothing.

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
| **visibility** — line-of-sight only | `zmap.py` `face_visibility`, `accessibility.npy` — the analyzer's central primitive — plus UI show/hide. The publication sense (*audience*) was cut with the report flow. |
| **stage** — prep only | *Prep stage* is the only stage. Pipeline narratives name their commands (`mesh`, `directions`, `setups`) rather than numbering stages. |
| **segment** — struck | The word had four unrelated uses and no definition. The concept it kept being reached for is **assignment**. Tooling *sections*, cut *paths* and line *segments* keep their own names. |
| **plan** — retired for the per-part document | Say **route**: the part's ordered operations, which is what routing means in a shop. *Plan* survives only where it is qualified and means something else — a *setup plan* (one ranked option out of `cnc/setups`) and a *bend plan* (`sheet_metal/bend_plan`). It used to mean all three at once. |
| **route** — the part's own, not a template | A route is THIS part's operations. It used to also mean a YAML template that instantiated operations and checks; templates are gone, so the word is free. |
| **machine / bundle / catalogue** | Kept, always qualified: *machine profile* (the YAML) vs the tilt-cone *machine* of a setup option; the *first-load bundle* prep stage; the YAML *catalogue* vs a *tool library*. |
| **`Analysis` in `v2/analyses.ts`** | A misnomer — it is a **check preset**: its `id` is a viewer modeId and it carries a field called `analysis` pointing at the real one. Renaming is out of scope; treat the name as wrong when reading it. |

---

## 6. Not built yet

Defined now so the next flow does not re-decide the vocabulary. All **aspiration** —
nothing implements them.

| Term | Definition |
|---|---|
| **stock / workpiece state** | Stock primitives (bounding box, PCA box, bar, tube) held **declaratively** over the final-part face space — stock plus allowances, never intermediate geometry. Hard rule 3 makes intermediate meshes toxic to the cache. Likely an operation's config rather than an object of its own. |
| **material** | Materializes into analysis params (and later, cost). |
| **grouping** | See §2 — the object that assigns several operations to one machine setup. `machining.cone_members` and `pressbrake/tooling.solve_setup` are the engines; the input is the authored operation list. |
| **quotation input** | A structured field on an operation feeding a price. Nothing declares one — the shape should be decided by whatever first needs to read it. |
| **restricted view** | The replacement for report bundles: the same app with lenses and analyses hidden, so an outside reader sees only checks. Consequence to respect now — results GC must never delete a result some check's `expected_hash` names. **disposition** (acknowledging a known finding) comes back with it. |

---

## Related

- [ROUTE-ARCHITECTURE.md](ROUTE-ARCHITECTURE.md) — how the route layer is built:
  the model, keying rules, storage.
- [ANALYSIS-INVENTORY.md](ANALYSIS-INVENTORY.md) — what exists today, with counts.
- [CODEMAP.md](CODEMAP.md) — file map and on-disk contracts.
