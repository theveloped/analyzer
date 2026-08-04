"""Route sidecar tests: CRUD/revisions, validation and derived check status
(docs/ROUTE-ARCHITECTURE.md).

Runs on empty temp workdirs — check keying goes through resolver.cache_key
whose prep fingerprints are None-safe, so no meshing is needed; results are
simulated with store_result under the exact key the runner would use.

    python test_route.py
"""

import tempfile

import route as route_mod
from processes import get_analysis
from processes import resolver
from processes.base import apply_defaults, store_result

PASSED = 0
FAILED = 0


def check(name, condition):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"[OK ] {name}")
    else:
        FAILED += 1
        print(f"[FAIL] {name}")


def expect_raises(name, exc, fn):
    try:
        fn()
    except exc:
        check(name, True)
    except Exception as error:  # noqa: BLE001 - report the wrong exception
        print(f"      unexpected: {error!r}")
        check(name, False)
    else:
        check(name, False)


ANALYSIS_ID = "injection_molding/thickness"
_analysis = get_analysis(*ANALYSIS_ID.split("/"))
NUM_PARAM = next(p.name for p in _analysis.params if p.type == "number")


def store_for(workdir, params):
    """Store a fake result under exactly the key the runner would use."""
    merged = apply_defaults(_analysis, params)
    key = resolver.cache_key(workdir, ANALYSIS_ID, merged)
    process_id, name = ANALYSIS_ID.split("/")
    store_result(workdir, process_id, name, key, {"min": 1.23})


def test_crud_and_history(workdir):
    route = route_mod.load_route(workdir)
    check("empty route defaults",
          route["revision"] == 0 and route["checks"] == []
          and route["operations"] == [])

    route["operations"] = [{"id": "op10", "kind": "milling", "label": "OP10",
                            "config": {"direction_index": 4}}]
    expect_raises("revision conflict raises", route_mod.RevisionConflictError,
                  lambda: route_mod.save_route(workdir, route,
                                               expected_revision=7))
    stored = route_mod.save_route(workdir, route, expected_revision=0)
    check("revision bumps on save", stored["revision"] == 1)
    check("reload returns stored",
          route_mod.load_route(workdir)["operations"][0]["id"] == "op10")
    route_mod.save_route(workdir, stored, expected_revision=1)
    history = route_mod.route_history(workdir)
    check("history appends full snapshots",
          len(history) == 2 and history[0]["route"]["revision"] == 1)


def test_validation(workdir):
    route_mod.save_route(workdir, route_mod.empty_route(), expected_revision=0)

    bad = route_mod.empty_route()
    bad["checks"] = [{"id": "a", "analysis": "x/y"},
                     {"id": "a", "analysis": "x/y"}]
    expect_raises("duplicate check ids rejected", ValueError,
                  lambda: route_mod.save_route(workdir, bad,
                                               expected_revision=1))
    bad["checks"] = [{"id": "a", "analysis": "x/y", "operation": "nope"}]
    expect_raises("unknown operation ref rejected", ValueError,
                  lambda: route_mod.save_route(workdir, bad,
                                               expected_revision=1))
    bad["checks"] = [{"id": "a", "analysis": "no-slash"}]
    expect_raises("analysis id must be process/analysis", ValueError,
                  lambda: route_mod.save_route(workdir, bad,
                                               expected_revision=1))
    # a stats rule is only ever read by the frontend evaluator table, so an
    # unrecognized one would evaluate to `unknown` forever — reject it here
    bad["checks"] = [{"id": "a", "analysis": "x/y",
                      "policy": {"kind": "stats", "rule": "sheet_detekt"}}]
    expect_raises("unknown stats rule rejected", ValueError,
                  lambda: route_mod.save_route(workdir, bad,
                                               expected_revision=1))

    bad["checks"] = []
    bad["operations"] = [{"id": "op", "kind": "cnc_setup"}]
    expect_raises("retired operation kind rejected", ValueError,
                  lambda: route_mod.save_route(workdir, bad,
                                               expected_revision=1))
    bad["operations"] = [{"id": "op", "kind": "milling"},
                         {"id": "op", "kind": "turning"}]
    expect_raises("duplicate operation ids rejected", ValueError,
                  lambda: route_mod.save_route(workdir, bad,
                                               expected_revision=1))

    good = route_mod.empty_route()
    good["operations"] = [{"id": "op", "kind": "turning"}]
    good["checks"] = [{"id": "a", "analysis": "x/y", "operation": "op",
                       "policy": {"kind": "stats", "rule": "sheet_detect"}}]
    route_mod.save_route(workdir, good, expected_revision=1)
    check("legal route accepted", True)


def test_schema_mismatch_discards(workdir):
    """An older document is discarded rather than migrated: the route is
    authored and cheap to rebuild, and a half-migrated one would key checks
    against params that no longer mean the same thing."""
    import json
    import os

    path = os.path.join(workdir, route_mod.ROUTE_FILE)
    with open(path, "w") as f:
        json.dump({"schema": 1, "revision": 9, "decisions": {},
                   "operations": [{"id": "old"}], "checks": []}, f)
    loaded = route_mod.load_route(workdir)
    check("stale schema loads as an empty route",
          loaded["revision"] == 0 and loaded["operations"] == []
          and "decisions" not in loaded)


def test_check_status(workdir):
    route = route_mod.empty_route()
    route["checks"] = [
        {"id": "chk", "analysis": ANALYSIS_ID, "params": {NUM_PARAM: 3.0}}]
    status = route_mod.check_status(workdir, route["checks"][0])
    check("not run: hash derived, nothing stored",
          status["expected_hash"] and not status["exists"]
          and not status["stale"] and status["error"] is None)
    check("merged params include defaults",
          status["params"][NUM_PARAM] == 3.0
          and len(status["params"]) == len(_analysis.params))

    store_for(workdir, {NUM_PARAM: 3.0})
    status = route_mod.check_status(workdir, route["checks"][0])
    check("stored result is found under the expected hash", status["exists"])

    route["checks"][0]["params"][NUM_PARAM] = 5.0
    status = route_mod.check_status(workdir, route["checks"][0])
    check("param change -> stale (older results exist)",
          not status["exists"] and status["stale"])

    bogus = {"id": "b", "analysis": ANALYSIS_ID, "params": {"nope": 1}}
    check("unknown param surfaces as error",
          route_mod.check_status(workdir, bogus)["error"] is not None)
    missing = {"id": "c", "analysis": "nosuch/analysis", "params": {}}
    check("unknown analysis surfaces as error",
          route_mod.check_status(workdir, missing)["error"] is not None)

    route_mod.save_route(workdir, route, expected_revision=0)
    section = route_mod.route_section(workdir)
    check("route_section carries per-check status",
          "chk" in section["checks"] and section["checks"]["chk"]["stale"])
    check("route_section carries the route itself",
          section["route"]["revision"] == 1)


def test_machines():
    """The machine catalogue is a plain reference library — an operation
    stores the NAME, nothing is copied into the workdir."""
    machines = route_mod.list_machines()
    names = {m["name"] for m in machines}
    check("catalogue lists the shipped machines",
          {"cnc_3axis", "laser_flatbed", "pressbrake_135t"} <= names)
    brake = next(m for m in machines if m["name"] == "pressbrake_135t")
    check("machines carry a repo-relative catalogue path",
          brake["path"] == "catalogue/machines/pressbrake_135t.yaml")
    # the add-operation form filters the library by kind, so a machine
    # declaring a retired kind would silently never be offered
    kinds = {m["kind"] for m in machines if m["kind"]}
    check("every declared machine kind is a legal operation kind",
          kinds <= set(route_mod.OPERATION_KINDS))


def main():
    with tempfile.TemporaryDirectory() as workdir:
        test_crud_and_history(workdir)
    with tempfile.TemporaryDirectory() as workdir:
        test_validation(workdir)
    with tempfile.TemporaryDirectory() as workdir:
        test_schema_mismatch_discards(workdir)
    with tempfile.TemporaryDirectory() as workdir:
        test_check_status(workdir)
    test_machines()

    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED == 0:
        print("ALL CHECKS PASSED")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
