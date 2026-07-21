"""Plan sidecar tests: CRUD/revisions, dispositions, param materialization,
derived check status and the impact preview (docs/PLAN-ARCHITECTURE.md).

Runs on empty temp workdirs — check keying goes through resolver.cache_key
whose prep fingerprints are None-safe, so no meshing is needed; results are
simulated with store_result under the exact key the runner would use.
"""

import tempfile

import plans
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
    plan = plans.load_plan(workdir)
    check("empty plan defaults", plan["revision"] == 0 and plan["checks"] == [])

    plan["decisions"]["material"] = {"value": "AlMg3", "state": "provisional"}
    expect_raises("revision conflict raises", plans.RevisionConflictError,
                  lambda: plans.save_plan(workdir, plan, expected_revision=7))
    stored = plans.save_plan(workdir, plan, expected_revision=0)
    check("revision bumps on save", stored["revision"] == 1)
    check("reload returns stored", plans.load_plan(workdir)["decisions"]
          ["material"]["value"] == "AlMg3")
    plans.save_plan(workdir, stored, expected_revision=1)
    history = plans.plan_history(workdir)
    check("history appends full snapshots",
          len(history) == 2 and history[0]["plan"]["revision"] == 1)

    bad = plans.empty_plan()
    bad["checks"] = [{"id": "a", "analysis": "x/y"},
                     {"id": "a", "analysis": "x/y"}]
    expect_raises("duplicate check ids rejected", ValueError,
                  lambda: plans.save_plan(workdir, bad, expected_revision=2))
    bad["checks"] = [{"id": "a", "analysis": "x/y", "operation": "nope"}]
    expect_raises("unknown operation ref rejected", ValueError,
                  lambda: plans.save_plan(workdir, bad, expected_revision=2))


def test_dispositions(workdir):
    expect_raises("disposition needs a state", ValueError,
                  lambda: plans.append_disposition(
                      workdir, {"finding_id": "f1", "by": "tobias"}))
    plans.append_disposition(workdir, {
        "finding_id": "f1", "state": "accepted", "by": "tobias",
        "why": "EDM secondary op"})
    plans.append_disposition(workdir, {
        "finding_id": "f1", "state": "resolved", "by": "tobias"})
    events = plans.load_dispositions(workdir)
    latest = plans.latest_dispositions(workdir)
    check("dispositions append-only", len(events) == 2)
    check("latest disposition wins", latest["f1"]["state"] == "resolved"
          and events[0]["why"] == "EDM secondary op")


def test_materialize():
    plan = plans.empty_plan()
    plan["decisions"]["stock"] = {"value": {"xyz": [120, 80, 40]}}
    good = {"id": "c1", "analysis": ANALYSIS_ID,
            "params": {"a": {"$plan": "decisions.stock.value.xyz.1"},
                       "b": 7}}
    params = plans.materialize_params(plan, good)
    check("$plan paths resolve (incl. list index)",
          params == {"a": 80, "b": 7})
    dangling = {"id": "c2", "analysis": ANALYSIS_ID,
                "params": {"a": {"$plan": "decisions.missing.value"}}}
    expect_raises("dangling $plan path raises", ValueError,
                  lambda: plans.materialize_params(plan, dangling))


def test_check_status(workdir):
    plan = plans.empty_plan()
    plan["checks"] = [
        {"id": "chk", "analysis": ANALYSIS_ID, "params": {NUM_PARAM: 3.0}}]
    status = plans.check_status(workdir, plan, plan["checks"][0])
    check("not run: hash derived, nothing stored",
          status["expected_hash"] and not status["exists"]
          and not status["stale"] and status["error"] is None)
    check("materialized params include defaults",
          status["params"][NUM_PARAM] == 3.0
          and len(status["params"]) == len(_analysis.params))

    store_for(workdir, {NUM_PARAM: 3.0})
    status = plans.check_status(workdir, plan, plan["checks"][0])
    check("stored result is found under the expected hash", status["exists"])

    plan["checks"][0]["params"][NUM_PARAM] = 5.0
    status = plans.check_status(workdir, plan, plan["checks"][0])
    check("param change -> stale (older results exist)",
          not status["exists"] and status["stale"])

    bogus = {"id": "b", "analysis": ANALYSIS_ID, "params": {"nope": 1}}
    status = plans.check_status(workdir, plan, bogus)
    check("unknown param surfaces as error", status["error"] is not None)

    plans.save_plan(workdir, plan, expected_revision=0)
    section = plans.plan_section(workdir)
    check("plan_section carries per-check status",
          "chk" in section["checks"]
          and section["checks"]["chk"]["stale"])


def test_impact(workdir):
    plan = plans.empty_plan()
    plan["decisions"] = {"stock": {"value": 3.0, "state": "provisional"},
                         "material": {"value": "AlMg3"}}
    plan["checks"] = [
        {"id": "bound", "analysis": ANALYSIS_ID,
         "params": {NUM_PARAM: {"$plan": "decisions.stock.value"}}},
        {"id": "fixed", "analysis": ANALYSIS_ID,
         "params": {NUM_PARAM: 9.0}}]
    plans.save_plan(workdir, plan, expected_revision=0)
    store_for(workdir, {NUM_PARAM: 3.0})

    report = plans.impact_preview(
        workdir, {"decisions": {"stock": {"value": 5.0}}})
    check("bound check recomputes on decision change",
          report["bound"]["outcome"] == "recomputes")
    check("unbound check unchanged", report["fixed"]["outcome"] == "unchanged")

    store_for(workdir, {NUM_PARAM: 5.0})
    report = plans.impact_preview(
        workdir, {"decisions": {"stock": {"value": 5.0}}})
    check("existing result -> revalidates",
          report["bound"]["outcome"] == "revalidates")

    report = plans.impact_preview(
        workdir, {"decisions": {"material": {"value": "S235"}}})
    check("unrelated decision -> all unchanged",
          all(entry["outcome"] == "unchanged" for entry in report.values()))


def main():
    with tempfile.TemporaryDirectory() as workdir:
        test_crud_and_history(workdir)
    with tempfile.TemporaryDirectory() as workdir:
        test_dispositions(workdir)
    test_materialize()
    with tempfile.TemporaryDirectory() as workdir:
        test_check_status(workdir)
    with tempfile.TemporaryDirectory() as workdir:
        test_impact(workdir)

    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED == 0:
        print("ALL CHECKS PASSED")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
