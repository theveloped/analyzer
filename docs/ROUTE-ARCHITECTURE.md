# Route architecture — the v2 workbench

Status: leaned down 2026-07-31. This file is canon for **how the route layer is
built**; [CONCEPTS.md](CONCEPTS.md) is canon for **what a word means**.
Background: AGENTS.md (hard rules), [CODEMAP.md](CODEMAP.md) (cache/data
contracts).

## The model

Three things, and nothing between them:

| | What it is | Persists? |
|---|---|---|
| **lens** | paints one thing over the model | no |
| **study** | compares many candidates side by side | no |
| **operation** | one step we will actually run | **yes** — this is the record |
| **check** | judges one stored result against a pinned policy | yes |

**An operation is the only place a choice is recorded.** Everything before it is
exploration; everything after it is judgement. You look at candidate directions
in a study, decide, and *add an operation* — that act is the decision. There is
no second record of the same choice to drift away from it.

**Checks are authored deliberately.** Nothing seeds them: no per-kind default
set, no route template. A check on the route is one somebody meant, which is
what makes it worth reading.

**One check kind, configured — not many kinds.** An *expression* check turns
any stored field into a yes/no region and combines regions with and · or ·
and-not. That is the general form the others are special cases of, and the
intent is that it REPLACES them once it has proven itself on real parts (see
*The general form* below).

**Operations are atomic.** One approach direction, one bend, one turning axis —
no tilt cone. Grouping several onto one machine setup (two milling ops sharing a
3+2 fixturing, three bends sharing brake tooling) is a later *inference over the
operation list*, not something authored per operation. Crediting an operation
with everything inside a ±90° cone counted coverage from directions nobody
chose.

### What this replaced

The layer had grown a second and third way to record the same choice: decision
slots with candidate sets and derived values, `$plan` param bindings, route
templates that instantiated operations *and* their checks, per-kind default
check sets, and seeding buttons. Two authors for an operation's checks
(`defaultChecksFor` and the route YAML) had already drifted. Reports/dispositions
went with them — see *Deferred* below.

## The general form: expression checks

Every field-shaped question has the same shape — take a field, decide which of
its values mean "yes", combine that region with another. So there is one
configurable check rather than a kind per question.

**A term** is (field, rule). One rule per `FieldRole` that carries a
per-element value:

| role | rule | reads |
|---|---|---|
| `scalar` | **band** — two open-ended bounds | `≥ 50 % of mean`, `10 – 60 °`, `bottom p5` |
| `mask` | **set / not set** | `on_hull`, `reach_3_0` |
| `category` | **a set of values**, optionally inverted | `feature_category in {1, 2}` |

Bounds resolve in field units, % of mean, % of median or percentile
(`fields/stats.ts`), **always against the whole field** — never against the
region an earlier term scoped to. "Above 50 % of the mean thickness" is then
one number regardless of what it is combined with; a scoped reference would
silently move every threshold downstream of an edit.

**Terms fold left to right** with `and` / `or` / `andNot`. The first term's
operator is ignored, so the same list cannot mean two things depending on
order. Fields in any index space work: `face` directly, `vertex` by the
faceValues convention (mean of three corners for a scalar; all three must
agree for a mask or category, since a mean of category ids is meaningless),
`brep_face` broadcast through the id map.

**The verdict** is the composed mask's AREA share against a pinned limit —
area, not a face count, so a dense corner cannot outvote a large flat wall.
A limit of 0 means "any selected face is a finding".

`fields/expression.ts` owns the model and the mask math; the lens
(`colorizers/expression.ts`) and the evaluator both call `buildMask`, so the
number and the picture are one computation. `fields/expression.test.ts` pins
the semantics, including the worked example
*"faces where the contact angle is 10–60° and the thickness is above 50 % of
the mean"*.

### What it already subsumes

A threshold check is one band term. A band check is one band term. "Faces no
tool reaches" is `visible and not reachable`. When the expression form has
proven itself, those kinds collapse into it and their evaluators are deleted
rather than rewritten. The one thing NOT expressible today is a `stats` check,
which reads stored scalars rather than arrays — the obvious extension is a
`stat` term kind, deliberately not built yet.

Seeding a common expression from an operation or a lens is also deliberately
out of scope: seeding is what made checks unreadable before.

## What already exists (build on it, don't duplicate it)

1. **Selective invalidation** — `processes/resolver.py:cache_key` = own declared
   params + schema + transitive prep fingerprints (mesh/directions/aag) + opt-in
   salts (`"splits"`) + `key_extra`. Stale results orphan to new hashes; undo
   re-validates old ones; the manifest computes per-result `stale`. The route
   layer adds **no parallel cache**.
2. **Study → check precedent (CNC)** — `prep/directions` →
   `accessibility.npy bool(D,F)`; `zmap.DirectionCache` holds per-(direction,
   tip, clearance) reach fields lazily; `cnc/reach_study` stores per-(direction,
   tool) masks that every check slices without recomputing.
3. **Search as inspiration** — `cnc/setups` ranks setup combinations and
   `cnc/setup_verdict` re-verdicts one against a real tool library. They are
   *studies*: things to read before authoring operations. Neither writes a route.
4. **Decision persistence precedent** — `face_splits.json` (fingerprint salted
   into dependent keys) and per-result `<hash>_overrides.json`.

## Keying rules (the footguns)

- **What a computation was ASKED about is a declared param; which answer you read
  is not.** A candidate-indexed analysis (`cnc/reach_study`, `cnc/turning_scan`)
  declares the candidates it covers and emits one row per candidate, so changing
  which candidate you are *looking at* is interpretation and costs nothing. Since
  `resolver.cache_key` filters submitted params down to declared ones, a
  `candidate_id` bolted onto the params dict is silently dropped. Such an
  analysis is keyed by whatever candidates it was ASKED about — one, or all of
  them — and a comparison surface merges every non-stale result rather than
  demanding one that covers exactly the current set. That is what lets a table
  fill in a cell at a time, and it means candidates cost nothing to define:
  `cnc/turning_scan` takes vectors and needs only the mesh. The exception is
  genuinely set-shaped artifacts — `accessibility.npy` is one array over all
  directions, so computing it necessarily covers the whole set, and the UI should
  say so rather than pretend otherwise.
- **Scope splits two ways.** Scope that changes *what is computed* (direction,
  tools) is materialized into **declared** analysis params —
  `resolver.cache_key` filters submitted params down to declared ones, so an
  undeclared `scope` key would silently collide caches. Scope that changes *only
  interpretation* (threshold, face subset, aggregation) lives in the check's
  `policy` and never forks the results cache.
- **Check params are literal.** There is no `$plan` indirection: what the check
  says is what gets keyed. An operation's direction reaches a check as *scope*
  (the check names its operation; the evaluator reads `op.config`), so changing a
  direction re-slices and never recomputes.
- **Numbers canonicalize before hashing** (`params_hash` folds integral floats to
  ints): params round-tripping through JavaScript lose the float-ness of `1.0`,
  and `1` vs `1.0` hashed differently — client-submitted runs landed on different
  keys than the server derived from the same logical values.
- **Analysis param names must not collide with prep salt fields** (`mesh`,
  `directions`, `accessibility`, `aag`, `splits`): the salt would silently
  overwrite the declared param in the cache key, collapsing distinct runs onto
  one hash. `resolver.cache_key` raises on a clash (this bit `cnc/reach_study`'s
  original `directions` param → renamed `direction_indices`).

## Storage — per-part sidecars in the workdir

```
<workdir>/
  route.json           current route (schema-versioned, revision counter)
  route_history.jsonl  append-only full snapshot per revision
```

`route.json`: ordered `operations` (id, kind, label, config, machine name,
declarative `produces` annotations) and `checks`. Each check: literal `params`
plus either one `analysis` id **or** a list of `sources`
(`{id, analysis, params}`) when it interprets several results at once;
`operation` (optional owner), pinned `policy`, preferred `lens`.

A multi-source check has no single `expected_hash` — it has one per source, and
`check_status` rolls them up: `exists` only when EVERY source is on disk,
because an expression over two fields cannot be evaluated from one of them.
Running it submits each source that is not already cached.

The split matters for keying: a source's **params** are computation and land in
the cache key; a term's **rule** is interpretation and lives in `policy`. That
is what lets you re-band an expression without recomputing anything.

A document written under an older `ROUTE_SCHEMA` is **discarded, not migrated**
(`route.load_route`): the route is authored, cheap to rebuild, and a
half-migrated one would key checks against params that no longer mean the same
thing.

Cross-part libraries live in a repo-level `catalogue/` — today
`catalogue/machines/*.yaml`. An operation stores the machine **name**; nothing is
copied into the workdir. (Content-addressed snapshots existed so a route stayed
self-contained as the library evolved. That matters when something freezes a
route for an outside reader; it is deferred with reports.)

**Workpiece states stay declarative** over the final-part face index space (stock
primitives + allowances, datums/clamp faces, features done/remaining). Hard rule
3 makes intermediate meshes toxic to the cache/lens infrastructure — simulation
is explicitly deferred.

## API

`api/route.py`: `GET/PUT /api/parts/{id}/route` (PUT sends the revision it
edited → 409 on mismatch), `GET /route/history`, `GET /api/catalogue/machines`.
The manifest gains a `route` section: per check the server keys `params` through
`resolver.cache_key` → `{expected_hash, exists, stale, error}` (pure fingerprint
arithmetic, no geometry, no jobs).

## Frontend

- **Lens registry** `frontend/src/v2/lenses.ts`: derived from `PROCESS_PLUGINS`
  plus a curation overlay (label/icon/category/pinned/hidden).
  `ProcessPlugin.modes` stays the single source of truth.
- **Field lenses self-materialize**: the scalar-field lenses (`v2/fieldLenses.ts`)
  are pinned to the ribbon and clicking one runs the backing analysis with plain
  defaults when nothing current is cached; the paint is the un-thresholded
  heatmap over the real data range. All interpretation lives in the side panel
  (`FieldLensRail`): a **highlight band** — faces whose value falls inside it
  paint COL.band magenta ON TOP of the unchanged heatmap — defined by two
  open-ended bounds, each a number in its own unit (field units, % of mean, % of
  median, or percentile). Band edits recolor instantly; the re-run button only
  arms when a compute knob actually changed; **"Save band as check"** is the
  moment exploration becomes a pinned policy on the route.
  *Lens = data, band = interpretation, check = saved interpretation.*
- **Studies** (`v2/studies.ts`) open from the left rail into the right one and
  persist nothing. The directions study's row selection lives in the v2 store
  (`v2/table/selection.ts`) and never reaches the server.
- **Check → lens**: selecting a check activates its preferred lens with its scope
  bound into `viewerParams`; selecting a finding also flies the camera.
- **Verdict evaluation** runs client-side over cached fields against the pinned
  policy (deterministic because the inputs are content-addressed). Evaluators
  live in one module (`v2/checks/evaluators.ts`) so a Python mirror is a port.
- **Status is two axes now**: execution (`not_run`/`queued`/`running`/`current`/
  `stale`/`error`) and verdict (`pass`/`review`/`fail`/`na`/`unknown`).
  A route with no checks reads as **unassessed**, never as passing — the rail
  says so.

## Deferred

- **Reports and dispositions.** Cut 2026-07-31. The replacement is not a frozen
  bundle but *the app itself in a restricted mode* — same objects, same
  evaluation, with lenses and analyses hidden so an outside reader sees only
  checks. One consequence to respect now: **results GC must never delete a result
  some check's `expected_hash` names**, because there is no evidence-by-copy to
  fall back on. Dispositions ("known, accepted") come back with that viewer;
  until then every finding stays open.
- **Grouping operations into machine setups** — the object that says "these two
  milling ops share one 3+2 fixturing" or "these three bends share brake
  tooling". `machining.cone_members` and `pressbrake/tooling.solve_setup` are the
  engines; the input is the authored operation list.
- **Impact preview.** `POST /plan/impact` classified checks as
  unchanged/revalidates/recomputes under a hypothetical edit. With literal check
  params and interpretation-only operation edits, nothing in the UI could change
  a hash, so it always answered "unchanged". Re-add it when check params become
  editable (a tool-library editor is the obvious trigger).
- Intermediate-geometry simulation, automated route proposal, quotation
  generation, cross-part dashboards.

## Verification

- `python test_route.py` — route CRUD + revision 409s, validation, schema-
  mismatch discard, derived check status, the machine catalogue.
- `python test_vocab.py` — `OPERATION_KINDS`/`STATS_RULES` against their TS
  mirrors.
- `cd frontend && npx tsc -b && npm test` — types plus the lens-registry suite
  (which now checks the lens keys named in `fieldLenses.ts` and `analyses.ts`,
  the job route templates used to do).
- `frontend/v2-smoke.mjs` walks the shell against a running server.
