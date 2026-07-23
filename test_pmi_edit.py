"""Checks for the PMI editor write-path (pmi_edit) and the PUT /pmi endpoint.

Four fixtures, degrading gracefully by what's installed:
  * validate  — the jsonschema + vocabulary gate accepts a well-formed payload
    and rejects structural/vocabulary nonsense (OCP-free, always runs);
  * save      — save_pmi persists pmi.json atomically, re-derives warnings, and
    strips the degraded stub (OCP-free, always runs);
  * roundtrip — author onto a box via save_pmi, export AP242, re-import, and
    assert the supported subset survives (needs OpenCASCADE — skipped if absent);
  * endpoint  — PUT /api/parts/{id}/pmi validates + writes, even on a part that
    arrived without PMI; a bad payload is a 400 (needs the FastAPI app import
    chain — skipped if absent).

Run from the repo root: python test_pmi_edit.py
"""
import json
import os
import tempfile

import pmi_edit
import pmi_support


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:52s} {detail}")
        if not condition:
            failures.append(name)
    return check


def _tol(**kw):
    base = {"kind": "tolerance", "value": None, "type_of_value": None,
            "modifiers": [], "material_modifier": None, "zone_modifier": None,
            "zone_value": None, "max_value": None, "datum_refs": [],
            "datum_names": [], "name": None, "face_ids": [], "edge_ids": []}
    base.update(kw)
    return base


def _dim(**kw):
    base = {"kind": "dimension", "value": None, "upper_tolerance": None,
            "lower_tolerance": None, "qualifier": None, "modifiers": [],
            "angular": False, "name": None, "face_ids": [],
            "secondary_face_ids": [], "edge_ids": []}
    base.update(kw)
    return base


def _synthetic_pmi():
    """A controlled payload exercising the support matrix (mirrors
    test_pmi_roundtrip._synthetic_pmi): a datum-free form control, a single- and
    a three-datum frame, a Ø/MMC position, a free-state profile, ± / diameter /
    angular dims, and a Max-qualifier dim that must be *warned*."""
    dref = lambda n, p: {"name": n, "position": p, "modifiers": []}
    tolerances = [
        _tol(id=1, name="Flat.1", type="Flatness", value=0.05, face_ids=[0]),
        _tol(id=2, name="Perp.1", type="Perpendicularity", value=0.1,
             face_ids=[1], datum_refs=[dref("A", 1)], datum_names=["A"]),
        _tol(id=3, name="Pos.1", type="Position", value=0.25,
             type_of_value="Diameter", material_modifier="M", face_ids=[2],
             datum_refs=[dref("A", 1), dref("B", 2), dref("C", 3)],
             datum_names=["A", "B", "C"]),
        _tol(id=4, name="Prof.1", type="ProfileOfSurface", value=0.3,
             modifiers=["Free_State"], face_ids=[1],
             datum_refs=[dref("A", 1)], datum_names=["A"]),
    ]
    dimensions = [
        _dim(id=5, name="L.1", type="Location_LinearDistance", value=40.0,
             upper_tolerance=0.1, lower_tolerance=-0.2,
             face_ids=[0], secondary_face_ids=[1]),
        _dim(id=6, name="Dia.1", type="Size_Diameter", value=12.0,
             upper_tolerance=0.05, lower_tolerance=-0.05, face_ids=[2]),
        _dim(id=7, name="Ang.1", type="Location_Angular", value=90.0,
             angular=True, face_ids=[0], secondary_face_ids=[3]),
        _dim(id=8, name="Dia.max", type="Size_Diameter", value=8.0,
             qualifier="Max", face_ids=[4]),
    ]
    datums = [
        {"id": 9, "kind": "datum", "name": "A", "face_ids": [4], "edge_ids": []},
        {"id": 10, "kind": "datum", "name": "B", "face_ids": [5], "edge_ids": []},
        {"id": 11, "kind": "datum", "name": "C", "face_ids": [3], "edge_ids": []},
    ]
    return {"schema": 4, "dimensions": dimensions,
            "tolerances": tolerances, "datums": datums}


def _rejects(payload):
    """True iff validate_pmi raises ValueError for this payload."""
    try:
        pmi_edit.validate_pmi(payload)
        return False
    except ValueError:
        return True


def fixture_validate(check):
    ok = _synthetic_pmi()
    try:
        pmi_edit.validate_pmi(ok)
        accepted = True
    except ValueError as exc:
        accepted = False
        print("    (unexpected):", exc)
    check("accepts a well-formed synthetic payload", accepted)

    check("rejects a non-object payload", _rejects([1, 2, 3]))
    check("rejects a non-integer face id",
          _rejects({"dimensions": [], "datums": [],
                    "tolerances": [_tol(id=1, type="Flatness", face_ids=["x"])]}))
    check("rejects a negative face id",
          _rejects({"dimensions": [], "datums": [],
                    "tolerances": [_tol(id=1, type="Flatness", face_ids=[-1])]}))
    check("rejects an unknown tolerance type",
          _rejects({"dimensions": [], "datums": [],
                    "tolerances": [_tol(id=1, type="Bogosity", face_ids=[0])]}))
    check("rejects an unknown material modifier",
          _rejects({"dimensions": [], "datums": [],
                    "tolerances": [_tol(id=1, type="Position",
                                        material_modifier="Q", face_ids=[0])]}))
    check("rejects duplicate entity ids across families",
          _rejects({"dimensions": [_dim(id=1, value=5, face_ids=[0])],
                    "datums": [],
                    "tolerances": [_tol(id=1, type="Flatness", face_ids=[0])]}))
    check("rejects a tolerance missing face_ids",
          _rejects({"dimensions": [], "datums": [],
                    "tolerances": [{"id": 1, "type": "Flatness"}]}))
    # a lossy-but-legal construct (Coaxiality) must be ACCEPTED, only warned
    check("accepts a lossy construct (Coaxiality) without blocking",
          not _rejects({"dimensions": [], "datums": [],
                        "tolerances": [_tol(id=1, type="Coaxiality", value=0.1,
                                            face_ids=[0], datum_names=["A"],
                                            datum_refs=[{"name": "A", "position": 1,
                                                         "modifiers": []}])]}))


def fixture_save(check):
    wd = tempfile.mkdtemp(prefix="pmiedit_")
    # authoring onto a dir that never had PMI must just work (AP203 → AP242 loop)
    check("no pmi.json before save", not os.path.exists(os.path.join(wd, "pmi.json")))

    summary = pmi_edit.save_pmi(wd, _synthetic_pmi())
    path = os.path.join(wd, "pmi.json")
    check("save writes pmi.json", os.path.exists(path))
    check("summary schema is PMI_SCHEMA", summary["schema"] == pmi_support.PMI_SCHEMA)
    check("summary counts are correct",
          summary["counts"] == {"tolerances": 4, "dimensions": 4, "datums": 3})

    with open(path) as f:
        written = json.load(f)
    check("written schema stamped", written["schema"] == pmi_support.PMI_SCHEMA)
    warn_text = " ".join(written["warnings"]).lower()
    check("qualifier loss is warned", "qualifier" in warn_text)
    check("semantic-name loss is warned", "semantic name" in warn_text)
    check("save re-derives warnings (matches pmi_support)",
          written["warnings"] == pmi_support.roundtrip_warnings(written))

    # a degraded stub is stripped when the user authors real entities over it
    summary2 = pmi_edit.save_pmi(wd, {**_synthetic_pmi(), "degraded": True})
    with open(path) as f:
        rewritten = json.load(f)
    check("authored payload is not marked degraded", "degraded" not in rewritten)
    check("re-save overwrites cleanly", summary2["counts"]["tolerances"] == 4)

    # an invalid payload must not clobber the good file already on disk
    try:
        pmi_edit.save_pmi(wd, {"tolerances": [_tol(id=1, type="Nope", face_ids=[0])],
                               "dimensions": [], "datums": []})
        raised = False
    except ValueError:
        raised = True
    with open(path) as f:
        after = json.load(f)
    check("invalid save raises", raised)
    check("invalid save leaves the previous pmi.json intact",
          len(after["tolerances"]) == 4)


def fixture_roundtrip(check):
    try:
        import step_export
        import step_import
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — env probe
        print(f"  [SKIP] OpenCASCADE unavailable ({type(exc).__name__})")
        return

    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType
    wd = tempfile.mkdtemp(prefix="pmiedit_rt_")
    shape = BRepPrimAPI_MakeBox(40.0, 30.0, 20.0).Shape()
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    writer.Write(os.path.join(wd, "source.stp"))

    # the editor loop: author via save_pmi (no pre-existing pmi.json), export
    pmi_edit.save_pmi(wd, _synthetic_pmi())
    report = step_export.export_step(wd, out_path=os.path.join(wd, "out.stp"))
    check("authored PMI exports as AP242", report.schema == "AP242")
    check("no unresolved faces", not report.unresolved_faces)

    root = tempfile.mkdtemp(prefix="pmiedit_re_")
    manifest = step_import.import_step(report.out_path, root=root)
    rewd = os.path.join(root, "parts", manifest["parts"][0]["part"])
    with open(os.path.join(rewd, "pmi.json")) as f:
        out = json.load(f)

    import collections
    sig = lambda t: (t["type"], round(t["value"] or 0.0, 3),
                     tuple(t.get("datum_names", [])))
    want = collections.Counter(sig(t) for t in _synthetic_pmi()["tolerances"])
    got = collections.Counter(sig(t) for t in out["tolerances"])
    check("authored tolerances round-trip through AP242", want == got,
          f"{sum(want.values())} tolerances")


def fixture_endpoint(check):
    try:
        from fastapi.testclient import TestClient
        from api.app import create_app
    except Exception as exc:  # noqa: BLE001 — env probe
        print(f"  [SKIP] FastAPI app import chain unavailable ({type(exc).__name__})")
        return

    root = tempfile.mkdtemp(prefix="pmiedit_api_")
    pid = "boxpart0001"
    wd = os.path.join(root, pid)
    os.makedirs(wd)
    with open(os.path.join(wd, "part.json"), "w") as f:
        json.dump({"name": "box"}, f)  # a part.json alone makes it a known part

    client = TestClient(create_app(root))

    r = client.put(f"/api/parts/{pid}/pmi", json={"pmi": _synthetic_pmi()})
    check("PUT /pmi on a PMI-less part returns 200", r.status_code == 200,
          r.status_code)
    check("PUT response reports counts",
          r.status_code == 200 and r.json()["counts"]["tolerances"] == 4)
    check("pmi.json written to the workdir", os.path.exists(os.path.join(wd, "pmi.json")))

    g = client.get(f"/api/parts/{pid}/pmi")
    check("GET /pmi now serves the authored payload",
          g.status_code == 200 and len(g.json()["tolerances"]) == 4)

    bad = client.put(f"/api/parts/{pid}/pmi", json={"pmi": {
        "tolerances": [_tol(id=1, type="Bogus", face_ids=[0])],
        "dimensions": [], "datums": []}})
    check("PUT with an invalid payload is a 400", bad.status_code == 400, bad.status_code)

    missing = client.put("/api/parts/nope/pmi", json={"pmi": _synthetic_pmi()})
    check("PUT on an unknown part is a 404", missing.status_code == 404)


def main():
    failures = []
    check = check_factory(failures)
    print("=== validate: the jsonschema + vocabulary gate ===")
    fixture_validate(check)
    print("=== save: persist + warnings + atomicity ===")
    fixture_save(check)
    print("=== roundtrip: author → AP242 → re-import ===")
    fixture_roundtrip(check)
    print("=== endpoint: PUT /api/parts/{id}/pmi ===")
    fixture_endpoint(check)
    if failures:
        print(f"\nFAILED: {len(failures)} check(s): {failures}")
        raise SystemExit(1)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
