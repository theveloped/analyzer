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


# 1x1 transparent PNG
TINY_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def test_reports(workdir):
    import os
    store_for(workdir, {NUM_PARAM: 3.0})
    plan = plans.empty_plan()
    plans.save_plan(workdir, plan, expected_revision=0)
    plans.append_disposition(workdir, {
        "finding_id": "chk-a:in_band", "state": "accepted", "by": "tobias"})

    expect_raises("empty report rejected", ValueError,
                  lambda: plans.publish_report(workdir, {"checks": []}))

    merged = apply_defaults(_analysis, {NUM_PARAM: 3.0})
    key = resolver.cache_key(workdir, ANALYSIS_ID, merged)
    from processes.base import params_hash
    result_hash = params_hash(key)
    payload = {"title": "DFM review", "part": "testpart", "checks": [{
        "id": "chk-a", "label": "Wall thickness", "verdict": "review",
        "findings": [{"id": "chk-a:in_band", "detail": "12 faces"}],
        "evidence": {"process": "injection_molding", "analysis": "thickness",
                     "result_hash": result_hash, "policy": {"band": [1, 3]}},
        "shot": TINY_PNG,
    }]}
    report = plans.publish_report(workdir, payload)
    bundle = os.path.join(workdir, plans.REPORTS_DIR, report["rid"])
    check("bundle stores report + shot + evidence copy",
          os.path.exists(os.path.join(bundle, "report.json"))
          and os.path.exists(os.path.join(bundle, "shot_chk-a.png"))
          and os.path.exists(os.path.join(
              bundle, "evidence",
              f"injection_molding.thickness.{result_hash}.json")))
    check("report snapshots dispositions",
          report["dispositions"]["chk-a:in_band"]["state"] == "accepted")
    check("report freezes the plan revision", report["plan_revision"] == 1)

    loaded = plans.load_report(workdir, report["rid"])
    check("load_report roundtrips",
          loaded is not None and loaded["checks"][0]["shot"] == "shot_chk-a.png"
          and loaded["checks"][0]["evidence"]["result_hash"] == result_hash)

    first_json = open(os.path.join(bundle, "report.json")).read()
    second = plans.publish_report(workdir, payload)
    check("republish mints a new rid, first bundle untouched",
          second["rid"] != report["rid"]
          and open(os.path.join(bundle, "report.json")).read() == first_json)
    check("list_reports sees both",
          [r["rid"] for r in plans.list_reports(workdir)]
          == sorted([report["rid"], second["rid"]]))
    check("shot path validated",
          plans.report_shot_path(workdir, report["rid"], "shot_chk-a.png")
          is not None
          and plans.report_shot_path(workdir, "../evil", "shot_chk-a.png")
          is None
          and plans.report_shot_path(workdir, report["rid"], "report.json")
          is None)


def test_routes(workdir):
    import hashlib
    import os

    routes = plans.list_routes()
    check("catalogue lists the mixed route",
          any(r["name"] == "laser_cnc_brake" and r["operations"] == 3
              for r in routes))

    expect_raises("unknown route rejected", ValueError,
                  lambda: plans.instantiate_route(workdir, "nope"))
    expect_raises("path-escaping route name rejected", ValueError,
                  lambda: plans.instantiate_route(workdir, "../evil"))

    plan = plans.instantiate_route(workdir, "laser_cnc_brake")
    check("route appends operations + checks",
          [op["id"] for op in plan["operations"]] == ["laser", "cnc10", "brake"]
          and len(plan["checks"]) == 5 and plan["revision"] == 1)

    brake = next(op for op in plan["operations"] if op["id"] == "brake")
    source = open(os.path.join(plans.CATALOGUE_DIR, "machines",
                               "pressbrake_135t.yaml"), "rb").read()
    sha = hashlib.sha1(source).hexdigest()[:12]
    snapshot = os.path.join(workdir, "plan_assets", "machines", f"{sha}.yaml")
    check("machine snapshot is content-addressed",
          brake["machine"]["sha"] == sha and os.path.exists(snapshot)
          and open(snapshot, "rb").read() == source)

    bend = next(c for c in plan["checks"] if c["id"] == "chk-bend-plan")
    check("$machine_snapshot binds machine_path to the snapshot",
          bend["params"]["machine_path"] == f"plan_assets/machines/{sha}.yaml")

    reach = next(c for c in plan["checks"] if c["id"] == "chk-reach-cnc10")
    check("$machine:tools resolves the CNC tool library",
          isinstance(reach["params"]["tools"], list)
          and reach["params"]["tools"][0]["diameter"] == 16.0)

    cnc = next(op for op in plan["operations"] if op["id"] == "cnc10")
    check("machine config merges under step config",
          cnc["config"] == {"tilt": 0, "direction_index": 4}
          and cnc["produces"] == {"features": "holes"})

    expect_raises("op-id clash rejected", ValueError,
                  lambda: plans.instantiate_route(workdir, "laser_cnc_brake"))
    check("failed re-instantiation left the plan untouched",
          plans.load_plan(workdir)["revision"] == 1)

    # the derived status machinery accepts the route checks (params are
    # declared-valid; bend_plan's machine_path is part of its cache key)
    section = plans.plan_section(workdir)
    check("route checks key cleanly through the resolver",
          all(section["checks"][c["id"]]["error"] is None
              for c in plan["checks"]))


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
    with tempfile.TemporaryDirectory() as workdir:
        test_reports(workdir)
    with tempfile.TemporaryDirectory() as workdir:
        test_routes(workdir)

    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED == 0:
        print("ALL CHECKS PASSED")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
