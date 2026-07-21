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

import base64
import copy
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone

from processes import get_analysis
from processes import resolver
from processes.base import apply_defaults, params_hash, result_paths

PLAN_SCHEMA = 1
PLAN_FILE = "plan.json"
PLAN_HISTORY_FILE = "plan_history.jsonl"
DISPOSITIONS_FILE = "dispositions.jsonl"
REPORTS_DIR = "reports"
REPORT_SCHEMA = 1
_PNG_PREFIX = "data:image/png;base64,"

# repo-level template library (docs/PLAN-ARCHITECTURE.md, Phase 4)
CATALOGUE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "catalogue")
PLAN_ASSETS_DIR = "plan_assets"

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


def list_routes():
    """Available route templates: name, title and step count."""
    base = os.path.join(CATALOGUE_DIR, "routes")
    if not os.path.isdir(base):
        return []
    routes = []
    for filename in sorted(os.listdir(base)):
        if not filename.endswith(".yaml"):
            continue
        import yaml
        with open(os.path.join(base, filename)) as f:
            data = yaml.safe_load(f)
        routes.append({
            "name": filename[:-len(".yaml")],
            "title": str(data.get("title", filename)),
            "operations": len(data.get("operations", [])),
        })
    return routes


def instantiate_route(workdir, name):
    """Instantiate a route template into the part's plan (a new revision).

    Each step's machine template YAML is snapshotted into
    ``plan_assets/machines/<sha12>.yaml`` (content-addressed copy — the
    plan stays self-contained as the library evolves) and the step's
    operations + checks are appended. Check param tokens resolve here:

    - ``$machine_snapshot``      → the snapshot's workdir-relative path
      (results-tier params embed it, so the cache key changes exactly when
      the machine content does — the snapshot name IS its content hash)
    - ``$machine:<dotted.path>`` → a value from the machine template YAML

    Rejected when any of the route's operation ids already exist.
    """
    import yaml

    if _safe_name(name) != name:
        raise ValueError(f"invalid route name {name!r}")
    path = os.path.join(CATALOGUE_DIR, "routes", f"{name}.yaml")
    if not os.path.exists(path):
        raise ValueError(f"unknown route template {name!r}")
    with open(path) as f:
        route = yaml.safe_load(f)

    plan = load_plan(workdir)
    existing_ops = {op["id"] for op in plan["operations"]}
    clash = [step["id"] for step in route.get("operations", [])
             if step["id"] in existing_ops]
    if clash:
        raise ValueError(
            f"route operations already in the plan: {sorted(clash)}")

    patched = copy.deepcopy(plan)
    for step in route.get("operations", []):
        machine_name = step.get("machine")
        machine_data, snapshot_rel = {}, None
        if machine_name:
            machine_data, snapshot_rel = _snapshot_machine(
                workdir, machine_name)
        operation = {
            "id": step["id"],
            "kind": step.get("kind"),
            "label": step.get("label", step["id"]),
            "config": {**machine_data.get("config", {}),
                       **step.get("config", {})},
        }
        if machine_name:
            operation["machine"] = {
                "template": machine_name,
                "sha": os.path.splitext(os.path.basename(snapshot_rel))[0],
            }
        if step.get("produces") is not None:
            operation["produces"] = step["produces"]
        patched["operations"].append(operation)

        for check in step.get("checks", []):
            params = {
                key: _resolve_token(value, machine_data, snapshot_rel,
                                    check.get("id"))
                for key, value in (check.get("params") or {}).items()
            }
            patched["checks"].append({
                "id": check["id"],
                "analysis": check["analysis"],
                "params": params,
                "policy": check.get("policy") or {},
                "operation": step["id"],
                "lens": check.get("lens"),
                "visible": check.get("visible", True),
            })

    return save_plan(workdir, patched, expected_revision=plan["revision"])


def _snapshot_machine(workdir, machine_name):
    """Copy a machine template into plan_assets by content hash; returns
    (parsed template, workdir-relative snapshot path)."""
    import yaml

    if _safe_name(machine_name) != machine_name:
        raise ValueError(f"invalid machine template {machine_name!r}")
    source = os.path.join(CATALOGUE_DIR, "machines", f"{machine_name}.yaml")
    if not os.path.exists(source):
        raise ValueError(f"unknown machine template {machine_name!r}")
    with open(source, "rb") as f:
        content = f.read()
    sha = hashlib.sha1(content).hexdigest()[:12]
    rel = os.path.join(PLAN_ASSETS_DIR, "machines", f"{sha}.yaml")
    target = os.path.join(workdir, rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if not os.path.exists(target):
        with open(target, "wb") as f:
            f.write(content)
    return yaml.safe_load(content), rel.replace(os.sep, "/")


def _resolve_token(value, machine_data, snapshot_rel, check_id):
    if value == "$machine_snapshot":
        if snapshot_rel is None:
            raise ValueError(
                f"check {check_id}: $machine_snapshot without a machine")
        return snapshot_rel
    if isinstance(value, str) and value.startswith("$machine:"):
        node = machine_data
        for segment in value[len("$machine:"):].split("."):
            node = node.get(segment) if isinstance(node, dict) else None
            if node is None:
                raise ValueError(
                    f"check {check_id}: machine template has no {value!r}")
        return node
    return value


def publish_report(workdir, payload):
    """Publish an immutable report bundle under ``reports/<rid>/``.

    ``payload``: title plus per-check entries — id, label, verdict, findings,
    evidence ({process, analysis, result_hash, params, policy, lens, camera})
    and an optional ``shot`` PNG data URL. The bundle stores report.json, the
    shots as files, COPIES of every referenced result JSON (evidence by copy
    — a later reprocess or cleanup must not orphan what was published) and a
    snapshot of the dispositions at publish time. Bundles are never
    modified; republishing mints a new rid.
    """
    checks = payload.get("checks") or []
    if not checks:
        raise ValueError("a report needs at least one check")
    plan = load_plan(workdir)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rid = f"r{stamp}"
    base = os.path.join(workdir, REPORTS_DIR)
    suffix = 2
    while os.path.exists(os.path.join(base, rid)):
        rid = f"r{stamp}-{suffix}"
        suffix += 1
    bundle = os.path.join(base, rid)
    os.makedirs(os.path.join(bundle, "evidence"))

    stored_checks = []
    for check in checks:
        entry = {key: check.get(key)
                 for key in ("id", "label", "verdict", "findings", "evidence")}
        shot = check.get("shot")
        if shot and shot.startswith(_PNG_PREFIX):
            name = f"shot_{_safe_name(str(check.get('id', 'check')))}.png"
            with open(os.path.join(bundle, name), "wb") as f:
                f.write(base64.b64decode(shot[len(_PNG_PREFIX):]))
            entry["shot"] = name
        evidence = check.get("evidence") or {}
        process_id = _safe_name(str(evidence.get("process", "")))
        analysis_id = _safe_name(str(evidence.get("analysis", "")))
        result_hash = _safe_name(str(evidence.get("result_hash", "")))
        if process_id and analysis_id and result_hash:
            source = os.path.join(workdir, "results", process_id, analysis_id,
                                  f"{result_hash}.json")
            if os.path.exists(source):
                shutil.copyfile(source, os.path.join(
                    bundle, "evidence",
                    f"{process_id}.{analysis_id}.{result_hash}.json"))
        stored_checks.append(entry)

    report = {
        "schema": REPORT_SCHEMA,
        "rid": rid,
        "title": str(payload.get("title") or "DFM report"),
        "part": str(payload.get("part") or os.path.basename(
            os.path.abspath(workdir))),
        "plan_revision": plan["revision"],
        "published_at": _now(),
        "dispositions": latest_dispositions(workdir),
        "checks": stored_checks,
    }
    with open(os.path.join(bundle, "report.json"), "w") as f:
        json.dump(report, f, indent=1)
    return report


def list_reports(workdir):
    """Published bundles, oldest → newest (rid embeds the publish stamp)."""
    base = os.path.join(workdir, REPORTS_DIR)
    if not os.path.isdir(base):
        return []
    entries = []
    for rid in sorted(os.listdir(base)):
        path = os.path.join(base, rid, "report.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            report = json.load(f)
        entries.append({key: report.get(key) for key in
                        ("rid", "title", "part", "plan_revision",
                         "published_at")}
                       | {"check_count": len(report.get("checks", []))})
    return entries


def load_report(workdir, rid):
    """One published bundle's report.json (None when absent)."""
    if _safe_name(rid) != rid:
        return None
    path = os.path.join(workdir, REPORTS_DIR, rid, "report.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def report_shot_path(workdir, rid, name):
    """Validated filesystem path of a bundle shot (None when invalid)."""
    if _safe_name(rid) != rid or not re.fullmatch(r"shot_[A-Za-z0-9_-]+\.png",
                                                  name):
        return None
    path = os.path.join(workdir, REPORTS_DIR, rid, name)
    return path if os.path.exists(path) else None


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)


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
