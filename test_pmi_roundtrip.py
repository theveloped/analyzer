"""Round-trip checks for the AP242 GD&T exporter (step_export) against the
reader (step_import): author PMI, export AP242, re-import, and assert the
supported subset survives while every lossy construct is *warned*, never
silently dropped.

Two fixtures:
  * a synthetic box whose pmi.json we control, covering the representative
    support matrix (form/orientation/location symbols, Ø zone, MMC, a three-
    datum reference frame, ± and angular dimensions);
  * the NIST CTC-01 AP242 part (skipped if its Git-LFS object isn't present),
    as a real-world integration check that tolerances (type/value/datum frame)
    round-trip and that dimension losses are all accounted for by warnings.

Run from the repo root: python test_pmi_roundtrip.py
"""
import collections
import json
import os
import tempfile

import step_export
import step_import

NIST = "tests/nist/nist_ctc_01_asme1_ap242.stp"


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:52s} {detail}")
        if not condition:
            failures.append(name)
    return check


def _box_workdir(dx=40.0, dy=30.0, dz=20.0):
    """A temp part workdir holding a box source.stp (6 faces, iter_faces 0..5)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType
    wd = tempfile.mkdtemp(prefix="rt_")
    shape = BRepPrimAPI_MakeBox(dx, dy, dz).Shape()
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    writer.Write(os.path.join(wd, "source.stp"))
    return wd


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
        # a reader-dropped qualifier: must be WARNED, not silently lost
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


def _reimport_pmi(step_path):
    root = tempfile.mkdtemp(prefix="rtre_")
    manifest = step_import.import_step(step_path, root=root)
    wd = os.path.join(root, "parts", manifest["parts"][0]["part"])
    with open(os.path.join(wd, "pmi.json")) as f:
        return json.load(f)


def _tol_sig(t):
    return (t["type"], round(t["value"] or 0.0, 3), tuple(t.get("datum_names", [])))


def fixture_synthetic(check):
    wd = _box_workdir()
    with open(os.path.join(wd, "pmi.json"), "w") as f:
        json.dump(_synthetic_pmi(), f)
    src = _synthetic_pmi()

    report = step_export.export_step(wd, out_path=os.path.join(wd, "out.stp"))
    with open(report.out_path, encoding="utf-8", errors="replace") as f:
        header = f.read(4000)
    check("exported file is AP242", "AP242" in header, report.schema)
    check("no unresolved faces", not report.unresolved_faces)

    warn_text = " ".join(report.warnings).lower()
    check("qualifier loss is warned", "qualifier" in warn_text)
    check("name non-preservation is warned", "semantic name" in warn_text)

    out = _reimport_pmi(report.out_path)

    # every authored tolerance (type, value, datum frame) survives
    want = collections.Counter(_tol_sig(t) for t in src["tolerances"])
    got = collections.Counter(_tol_sig(t) for t in out["tolerances"])
    check("all tolerance types+values+datum-frames round-trip", want == got,
          f"{sum(want.values())} tolerances")

    pos = next((t for t in out["tolerances"] if t["type"] == "Position"), None)
    check("Position keeps Ø zone", bool(pos) and pos["type_of_value"] == "Diameter")
    check("Position keeps MMC modifier", bool(pos) and pos["material_modifier"] == "M")
    check("three-datum frame A|B|C preserved",
          bool(pos) and pos["datum_names"] == ["A", "B", "C"])
    prof = next((t for t in out["tolerances"] if t["type"] == "ProfileOfSurface"), None)
    check("free-state modifier preserved",
          bool(prof) and "Free_State" in prof["modifiers"])

    # dimensions: linear ±, diameter, angular survive (values within tolerance)
    dims = {d["type"]: d for d in out["dimensions"]}
    lin = dims.get("Location_LinearDistance")
    check("linear distance value round-trips",
          bool(lin) and abs(lin["value"] - 40.0) < 1e-3, lin and lin["value"])
    check("linear ± magnitudes round-trip",
          bool(lin) and abs(abs(lin["upper_tolerance"] or 0) - 0.1) < 1e-3
          and abs(abs(lin["lower_tolerance"] or 0) - 0.2) < 1e-3)
    dia = dims.get("Size_Diameter")
    check("diameter value round-trips", bool(dia) and abs(dia["value"] - 12.0) < 1e-3
          or any(abs(d["value"] - 12.0) < 1e-3 for d in out["dimensions"]
                 if d["type"] == "Size_Diameter"))
    ang = dims.get("Location_Angular")
    check("angular value round-trips (degrees)",
          bool(ang) and abs(ang["value"] - 90.0) < 1e-2, ang and ang["value"])


def fixture_nist(check):
    if not os.path.exists(NIST):
        print("  [SKIP] NIST fixture absent"); return
    with open(NIST, encoding="utf-8", errors="replace") as f:
        if not f.read(20).startswith("ISO-10303"):
            print("  [SKIP] NIST fixture is a Git-LFS pointer (run git lfs pull)")
            return

    root = tempfile.mkdtemp(prefix="rtn_")
    manifest = step_import.import_step(NIST, root=root)
    wd = os.path.join(root, "parts", manifest["parts"][0]["part"])
    with open(os.path.join(wd, "pmi.json")) as f:
        pmi_in = json.load(f)
    report = step_export.export_step(wd, out_path=os.path.join(root, "out.stp"))
    pmi_out = _reimport_pmi(report.out_path)

    want = collections.Counter(_tol_sig(t) for t in pmi_in["tolerances"])
    got = collections.Counter(_tol_sig(t) for t in pmi_out["tolerances"])
    check("NIST tolerances (type/value/datum-frame) round-trip", want == got,
          f"{sum(want.values())} tolerances")

    # any dropped dimension must be accounted for by a warning (nothing silent)
    in_dims = collections.Counter((d["type"], round(d["value"] or 0, 3))
                                  for d in pmi_in["dimensions"])
    out_dims = collections.Counter((d["type"], round(d["value"] or 0, 3))
                                   for d in pmi_out["dimensions"])
    lost = sum(in_dims.values()) - sum(out_dims.values())
    warned_loc = sum("location dimension" in w for w in report.warnings)
    check("every dropped dimension is warned", lost <= warned_loc,
          f"{lost} lost, {warned_loc} location-loss warnings")


def main():
    failures = []
    check = check_factory(failures)
    print("=== synthetic box: support-matrix round-trip ===")
    fixture_synthetic(check)
    print("=== NIST CTC-01: real-world round-trip ===")
    fixture_nist(check)
    if failures:
        print(f"\nFAILED: {len(failures)} check(s): {failures}")
        raise SystemExit(1)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
