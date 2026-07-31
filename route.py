"""Per-part route: the operations we will run and the checks that judge them.

The route is the user-authored layer over the content-addressed results
cache (docs/ROUTE-ARCHITECTURE.md). It never stores computed data — a check
references an analysis plus the params to run it with, and everything about
its execution state is *derived* by keying those params through
``resolver.cache_key`` against the current workdir fingerprints:

- expected result exists on disk  -> current
- older results exist, not this key -> stale (an input or param moved)
- nothing stored                   -> not run

**An operation is the only place a choice is recorded.** Lenses and studies
are exploration and persist nothing; adding an operation is what fixes a
direction, an axis or a machine. Checks are authored deliberately (from a
lens band or a study aggregate) — nothing seeds them, so a check on the
route is one somebody meant.

Operations are ATOMIC: one approach direction, one bend, one turning axis.
Grouping several of them onto one machine setup (two milling ops sharing a
3+2 fixturing, three bends sharing brake tooling) is a later inference over
the operation list, not something authored per operation.

Files (all in the part workdir, following the ``face_splits.json`` pattern):

- ``route.json``          current route; schema-versioned, revision counter
- ``route_history.jsonl`` append-only full snapshot per revision (undo /
                          provenance are file reads, never reconstruction)

Check shape (validated by ``validate_route``)::

    {"id": "chk-...", "analysis": "process/analysis",
     "params": {...},              # declared analysis params, literal
     "policy": {...},              # pinned interpretation thresholds
     "operation": "op10",          # optional owning operation id
     "lens": "cnc:access"}         # preferred inspection lens

Params are literal: what a computation is asked about is a declared param,
so the check's params ARE the cache key's input. Interpretation-only knobs
(bands, thresholds, scope) live in ``policy`` and never touch the key.

Framework-free on purpose: the API routes wrap these functions; tests and
the CLI import them directly.
"""

import copy
import json
import os
from datetime import datetime, timezone

from processes import get_analysis
from processes import resolver
from processes.base import apply_defaults, params_hash, result_paths

ROUTE_SCHEMA = 2
ROUTE_FILE = "route.json"
ROUTE_HISTORY_FILE = "route_history.jsonl"

# repo-level machine library — a plain reference library, not a plan
# mechanism: an operation names a machine, nothing is copied into the workdir
CATALOGUE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "catalogue")

# What kind of thing an operation is. Dispatches the card icon and the
# operation's configurable fields. Atomic: `milling` is ONE approach
# direction, not a setup that might carry several.
# Mirrored as OperationKind in frontend/src/api/types.ts (test_vocab.py).
OPERATION_KINDS = ("laser", "milling", "turning", "press_brake")

# A stats check names the RULE that reads its analysis's stats. The rule is
# authored by the client and evaluated only on the frontend (the StatsRule
# union in v2/checks/evaluators.ts mirrors this tuple, and test_vocab.py
# compares them), so a typo here has no backend consequence at all — it
# silently produces a check that evaluates to `unknown` for the life of the
# route. Validated where the route enters instead.
STATS_RULES = ("sheet_detect", "flat_pattern", "bend_plan", "features")


class RevisionConflictError(Exception):
    """The route was modified since the revision the caller edited."""


def empty_route():
    return {"schema": ROUTE_SCHEMA, "revision": 0,
            "operations": [], "checks": []}


def load_route(workdir):
    """The part's current route (an empty route when none exists).

    A document written under an older schema is discarded rather than
    migrated — the route is authored, cheap to rebuild, and a half-migrated
    one would key checks against params that no longer mean the same thing.
    """
    path = os.path.join(workdir, ROUTE_FILE)
    if not os.path.exists(path):
        return empty_route()
    with open(path) as f:
        stored = json.load(f)
    if stored.get("schema") != ROUTE_SCHEMA:
        return empty_route()
    return stored


def validate_route(route):
    """Structural validation; raises ValueError with an actionable message."""
    if route.get("schema") != ROUTE_SCHEMA:
        raise ValueError(f"route schema must be {ROUTE_SCHEMA}")
    for key in ("operations", "checks"):
        if not isinstance(route.get(key), list):
            raise ValueError(f"route.{key} must be a list")
    op_ids = [op.get("id") for op in route["operations"]]
    if len(op_ids) != len(set(op_ids)) or not all(op_ids):
        raise ValueError("operation ids must be unique and non-empty")
    for op in route["operations"]:
        kind = op.get("kind")
        if kind is not None and kind not in OPERATION_KINDS:
            raise ValueError(
                f"operation {op['id']}: unknown kind {kind!r} — "
                f"known kinds: {', '.join(OPERATION_KINDS)}")
    check_ids = [c.get("id") for c in route["checks"]]
    if len(check_ids) != len(set(check_ids)) or not all(check_ids):
        raise ValueError("check ids must be unique and non-empty")
    for check in route["checks"]:
        analysis = check.get("analysis", "")
        if "/" not in analysis:
            raise ValueError(
                f"check {check['id']}: analysis must be 'process/analysis'")
        if not isinstance(check.get("params", {}), dict):
            raise ValueError(f"check {check['id']}: params must be a dict")
        operation = check.get("operation")
        if operation is not None and operation not in op_ids:
            raise ValueError(
                f"check {check['id']}: unknown operation {operation!r}")
        policy = check.get("policy") or {}
        if isinstance(policy, dict) and policy.get("kind") == "stats":
            rule = policy.get("rule")
            if rule not in STATS_RULES:
                raise ValueError(
                    f"check {check['id']}: unknown stats rule {rule!r} — "
                    f"known rules: {', '.join(STATS_RULES)}")


def save_route(workdir, route, expected_revision):
    """Store a new route revision (optimistic concurrency on the revision).

    The caller sends the revision it edited; a mismatch with the stored
    route raises RevisionConflictError (the API maps it to 409, mirroring
    the splits stale-mesh pattern). The stored route gets revision+1 and the
    full snapshot is appended to the history file.
    """
    current = load_route(workdir)
    if current["revision"] != expected_revision:
        raise RevisionConflictError(
            f"route is at revision {current['revision']}, "
            f"you edited revision {expected_revision} — reload and retry")
    stored = copy.deepcopy(route)
    stored["schema"] = ROUTE_SCHEMA
    stored["revision"] = current["revision"] + 1
    validate_route(stored)

    path = os.path.join(workdir, ROUTE_FILE)
    with open(path, "w") as f:
        json.dump(stored, f, indent=1)
    with open(os.path.join(workdir, ROUTE_HISTORY_FILE), "a") as f:
        f.write(json.dumps({"at": _now(), "route": stored}) + "\n")
    return stored


def route_history(workdir):
    return _read_jsonl(os.path.join(workdir, ROUTE_HISTORY_FILE))


def check_status(workdir, check):
    """Derived execution facts for one check (no geometry, no jobs).

    Keys the check's params through resolver.cache_key (declared params +
    schema + prep fingerprints + salts — identical to what the runner will
    store under), and reports:

    - expected_hash  where this check's result lives / will land
    - params         the merged dict to submit when running it
    - exists         the expected result is on disk (execution: current)
    - stale          not exists, but older results for the analysis exist
    - error          params failed to validate (fix the check)
    """
    try:
        analysis_id = check["analysis"]
        process_id, name = analysis_id.split("/", 1)
        analysis = get_analysis(process_id, name)
        merged = apply_defaults(analysis, check.get("params", {}))
        key = resolver.cache_key(workdir, analysis_id, merged)
    except (KeyError, ValueError) as error:
        return {"expected_hash": None, "params": None, "exists": False,
                "stale": False, "error": str(error)}
    json_path, _ = result_paths(workdir, process_id, name, key)
    exists = os.path.exists(json_path)
    stale = not exists and bool(_stored_results(workdir, process_id, name))
    return {"expected_hash": params_hash(key), "params": merged,
            "exists": exists, "stale": stale, "error": None}


def route_section(workdir):
    """The manifest's ``route`` section: the route + per-check status."""
    route = load_route(workdir)
    return {
        "route": route,
        "checks": {check["id"]: check_status(workdir, check)
                   for check in route["checks"]},
    }


def list_machines():
    """Available machine profiles: name, label and kind.

    A reference library the user picks from when adding an operation. The
    operation stores the NAME; nothing is copied into the workdir.
    """
    import yaml

    base = os.path.join(CATALOGUE_DIR, "machines")
    if not os.path.isdir(base):
        return []
    machines = []
    for filename in sorted(os.listdir(base)):
        if not filename.endswith(".yaml"):
            continue
        with open(os.path.join(base, filename)) as f:
            data = yaml.safe_load(f)
        machines.append({
            "name": filename[:-len(".yaml")],
            "label": str(data.get("label", filename)),
            "kind": data.get("kind"),
            "path": os.path.join("catalogue", "machines", filename)
                       .replace(os.sep, "/"),
        })
    return machines


def _stored_results(workdir, process_id, analysis_id):
    base = os.path.join(workdir, "results", process_id, analysis_id)
    if not os.path.isdir(base):
        return []
    return [name for name in os.listdir(base)
            if name.endswith(".json") and not name.endswith("_overrides.json")]


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _now():
    return datetime.now(timezone.utc).isoformat()
