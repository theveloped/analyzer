"""Per-part production plan: decisions, operations and checks as sidecars.

The plan is the user-authored layer over the content-addressed results
cache (docs/PLAN-ARCHITECTURE.md). It never stores computed data — a check
references an analysis plus the params to run it with, and everything about
its execution state is *derived* by keying those params through
``resolver.cache_key`` against the current workdir fingerprints:

- expected result exists on disk  -> current
- older results exist, not this key -> stale (an input or param moved)
- nothing stored                   -> not run

Files (all in the part workdir, following the ``face_splits.json`` pattern):

- ``plan.json``          current plan; schema-versioned, revision counter
- ``plan_history.jsonl`` append-only full snapshot per revision (undo /
                         provenance are file reads, never reconstruction)
- ``dispositions.jsonl`` append-only human judgments on findings; the
                         latest event per finding wins, history is free

Check shape (validated by ``validate_plan``)::

    {"id": "chk-...", "analysis": "process/analysis",
     "params": {...},              # declared analysis params; values may be
                                   # {"$plan": "decisions.stock.value.x"}
     "policy": {...},              # pinned interpretation thresholds
     "operation": "op10",          # optional owning operation id
     "lens": "cnc:access",         # preferred inspection lens
     "visible": true}

Params are materialized against the plan document (``$plan`` paths), so a
decision change re-keys exactly the checks whose bound params moved — the
substrate of selective invalidation and the impact preview. Interpretation-
only knobs live in ``policy`` and never touch the cache key.

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

PLAN_SCHEMA = 1
PLAN_FILE = "plan.json"
PLAN_HISTORY_FILE = "plan_history.jsonl"
DISPOSITIONS_FILE = "dispositions.jsonl"

DISPOSITION_STATES = ("open", "accepted", "customer_approval", "resolved")


class RevisionConflictError(Exception):
    """The plan was modified since the revision the caller edited."""


def empty_plan():
    return {"schema": PLAN_SCHEMA, "revision": 0,
            "decisions": {}, "operations": [], "checks": []}


def load_plan(workdir):
    """The part's current plan (a default empty plan when none exists)."""
    path = os.path.join(workdir, PLAN_FILE)
    if not os.path.exists(path):
        return empty_plan()
    with open(path) as f:
        return json.load(f)


def validate_plan(plan):
    """Structural validation; raises ValueError with a actionable message."""
    if plan.get("schema") != PLAN_SCHEMA:
        raise ValueError(f"plan schema must be {PLAN_SCHEMA}")
    for key, kind in (("decisions", dict), ("operations", list), ("checks", list)):
        if not isinstance(plan.get(key), kind):
            raise ValueError(f"plan.{key} must be a {kind.__name__}")
    op_ids = [op.get("id") for op in plan["operations"]]
    if len(op_ids) != len(set(op_ids)) or not all(op_ids):
        raise ValueError("operation ids must be unique and non-empty")
    check_ids = [c.get("id") for c in plan["checks"]]
    if len(check_ids) != len(set(check_ids)) or not all(check_ids):
        raise ValueError("check ids must be unique and non-empty")
    for check in plan["checks"]:
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


def save_plan(workdir, plan, expected_revision):
    """Store a new plan revision (optimistic concurrency on the revision).

    The caller sends the revision it edited; a mismatch with the stored
    plan raises RevisionConflictError (the API maps it to 409, mirroring
    the splits stale-mesh pattern). The stored plan gets revision+1 and the
    full snapshot is appended to the history file.
    """
    current = load_plan(workdir)
    if current["revision"] != expected_revision:
        raise RevisionConflictError(
            f"plan is at revision {current['revision']}, "
            f"you edited revision {expected_revision} — reload and retry")
    stored = copy.deepcopy(plan)
    stored["schema"] = PLAN_SCHEMA
    stored["revision"] = current["revision"] + 1
    validate_plan(stored)

    path = os.path.join(workdir, PLAN_FILE)
    with open(path, "w") as f:
        json.dump(stored, f, indent=1)
    with open(os.path.join(workdir, PLAN_HISTORY_FILE), "a") as f:
        f.write(json.dumps({"at": _now(), "plan": stored}) + "\n")
    return stored


def plan_history(workdir):
    return _read_jsonl(os.path.join(workdir, PLAN_HISTORY_FILE))


def append_disposition(workdir, event):
    """Record a human judgment on a finding (append-only).

    ``event``: finding_id, state (one of DISPOSITION_STATES), by, and
    optionally why + evidence (e.g. the result hash / policy hash the
    judgment was made against). The timestamp is stamped here.
    """
    for required in ("finding_id", "state", "by"):
        if not event.get(required):
            raise ValueError(f"disposition needs {required}")
    if event["state"] not in DISPOSITION_STATES:
        raise ValueError(f"state must be one of {DISPOSITION_STATES}")
    stored = {"finding_id": event["finding_id"], "state": event["state"],
              "by": event["by"], "at": _now(),
              "why": event.get("why", ""),
              "evidence": event.get("evidence", {})}
    with open(os.path.join(workdir, DISPOSITIONS_FILE), "a") as f:
        f.write(json.dumps(stored) + "\n")
    return stored


def load_dispositions(workdir):
    return _read_jsonl(os.path.join(workdir, DISPOSITIONS_FILE))


def latest_dispositions(workdir):
    """finding_id -> most recent disposition event."""
    latest = {}
    for event in load_dispositions(workdir):
        latest[event["finding_id"]] = event
    return latest


def materialize_params(plan, check):
    """Resolve a check's params against the plan document.

    A param value of ``{"$plan": "dotted.path"}`` is replaced by the value
    at that path in the plan (e.g. ``decisions.material.value``); list
    indices are numeric segments. Everything else passes through verbatim.
    Raises ValueError on a dangling path.
    """
    params = {}
    for name, value in check.get("params", {}).items():
        if isinstance(value, dict) and "$plan" in value:
            params[name] = _resolve_path(plan, value["$plan"], check["id"])
        else:
            params[name] = value
    return params


def check_status(workdir, plan, check):
    """Derived execution facts for one check (no geometry, no jobs).

    Materializes the params, keys them through resolver.cache_key (declared
    params + schema + prep fingerprints + salts — identical to what the
    runner will store under), and reports:

    - expected_hash  where this check's result lives / will land
    - params         the materialized dict to submit when running it
    - exists         the expected result is on disk (execution: current)
    - stale          not exists, but older results for the analysis exist
    - error          params failed to materialize/validate (fix the plan)
    """
    try:
        params = materialize_params(plan, check)
        analysis_id = check["analysis"]
        process_id, name = analysis_id.split("/", 1)
        analysis = get_analysis(process_id, name)
        merged = apply_defaults(analysis, params)
        key = resolver.cache_key(workdir, analysis_id, merged)
    except (KeyError, ValueError) as error:
        return {"expected_hash": None, "params": None, "exists": False,
                "stale": False, "error": str(error)}
    json_path, _ = result_paths(workdir, process_id, name, key)
    exists = os.path.exists(json_path)
    stale = not exists and bool(
        _stored_results(workdir, process_id, name))
    return {"expected_hash": params_hash(key), "params": merged,
            "exists": exists, "stale": stale, "error": None}


def plan_section(workdir):
    """The manifest's ``plan`` section: the plan + per-check derived status."""
    plan = load_plan(workdir)
    return {
        "plan": plan,
        "checks": {check["id"]: check_status(workdir, plan, check)
                   for check in plan["checks"]},
        "dispositions": latest_dispositions(workdir),
    }


def impact_preview(workdir, patch):
    """Classify every check under a hypothetical plan edit — pure hash
    arithmetic over fingerprints; never enqueues a job.

    ``patch``: {"decisions": {...deep-merged...}, "operations": [...],
    "checks": [...]} — lists replace, decisions merge recursively. Per
    check: ``unchanged`` (same key), ``revalidates`` (new key but that
    result already exists on disk — e.g. reverting a decision),
    ``recomputes`` (new key, nothing stored), or ``error``.
    """
    plan = load_plan(workdir)
    patched = copy.deepcopy(plan)
    patched["decisions"] = _deep_merge(
        patched["decisions"], patch.get("decisions", {}))
    for key in ("operations", "checks"):
        if key in patch:
            patched[key] = patch[key]
    validate_plan(patched)

    report = {}
    for check in patched["checks"]:
        before = next((c for c in plan["checks"] if c["id"] == check["id"]),
                      None)
        now = (check_status(workdir, plan, before)
               if before is not None else None)
        then = check_status(workdir, patched, check)
        if then["error"]:
            outcome = "error"
        elif now is not None and then["expected_hash"] == now["expected_hash"]:
            outcome = "unchanged"
        elif then["exists"]:
            outcome = "revalidates"
        else:
            outcome = "recomputes"
        report[check["id"]] = {"outcome": outcome,
                               "expected_hash": then["expected_hash"],
                               "error": then["error"]}
    return report


def _stored_results(workdir, process_id, analysis_id):
    base = os.path.join(workdir, "results", process_id, analysis_id)
    if not os.path.isdir(base):
        return []
    return [name for name in os.listdir(base)
            if name.endswith(".json") and not name.endswith("_overrides.json")]


def _resolve_path(plan, path, check_id):
    node = plan
    for segment in path.split("."):
        if isinstance(node, list):
            try:
                node = node[int(segment)]
                continue
            except (ValueError, IndexError):
                node = None
        elif isinstance(node, dict):
            node = node.get(segment)
        else:
            node = None
        if node is None:
            raise ValueError(
                f"check {check_id}: plan path {path!r} not found")
    return node


def _deep_merge(base, patch):
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


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
