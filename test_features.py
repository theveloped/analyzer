"""Analytic checks of machining-feature recognition (machining_features.py).

Fixture: a 60x40x20 block with four features of known dimensions —
- D8 through hole,
- D6 blind hole (depth 10),
- counterbore: D6 through + coaxial D12 x 5 from the top,
- countersink: D6 through + coaxial 45-degree cone entry (R3 -> R6 x 3).

Asserts type, diameter, counterbore diameter, cone angle and depth of every
recognized feature, the per-fine-face category/id fields, and the result
cache round-trip through the cnc/features analysis.

Run from the repo root: python test_features.py
"""
import os
import sys
import tempfile

import numpy as np

import aag
import pipeline
import processes
from processes.base import apply_defaults


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:40s} {detail}")
        if not condition:
            failures.append(name)
    return check


def make_feature_block(tmp):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import (BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone,
                                 BRepPrimAPI_MakeCylinder)
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    def up(x, y, z):
        return gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1))

    shape = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 60, 40, 20).Shape()

    def cut(tool):
        nonlocal shape
        shape = BRepAlgoAPI_Cut(shape, tool).Shape()

    # D8 through hole
    cut(BRepPrimAPI_MakeCylinder(up(15, 10, -1), 4.0, 22.0).Shape())
    # D6 blind hole, depth 10 (floor at z = 10)
    cut(BRepPrimAPI_MakeCylinder(up(45, 10, 10), 3.0, 11.0).Shape())
    # counterbore: D6 through + D12 x 5 from the top
    cut(BRepPrimAPI_MakeCylinder(up(15, 30, -1), 3.0, 22.0).Shape())
    cut(BRepPrimAPI_MakeCylinder(up(15, 30, 15), 6.0, 6.0).Shape())
    # countersink: D6 through + 45-degree cone entry (R3 at z17 -> R6 at z20)
    cut(BRepPrimAPI_MakeCylinder(up(45, 30, -1), 3.0, 22.0).Shape())
    cut(BRepPrimAPI_MakeCone(up(45, 30, 17), 3.0, 6.0, 3.0).Shape())

    path = os.path.join(tmp, "features.step")
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(path)
    return path


def main():
    failures = []
    check = check_factory(failures)

    with tempfile.TemporaryDirectory() as tmp:
        path = make_feature_block(tmp)
        workdir = os.path.join(tmp, "wd")
        os.makedirs(workdir)

        pipeline.mesh_part(path, workdir, resolution=2.0, subdivide=2.0)
        pipeline.compute_aag(workdir)

        analysis = processes.get_analysis("cnc", "features")
        merged = apply_defaults(analysis, {})
        result = analysis.run(workdir, merged, None)
        stats = result.stats

        print("=== feature classification ===")
        counts = stats["counts"]
        check("one of each hole type",
              counts.get("through_hole") == 1
              and counts.get("blind_hole") == 1
              and counts.get("counterbore") == 1
              and counts.get("countersink") == 1,
              f"{counts}")

        by_type = {f["type"]: f for f in stats["features"]}

        through = by_type.get("through_hole", {})
        check("through hole: D8, depth 20",
              np.isclose(through.get("diameter", 0), 8.0, atol=1e-3)
              and np.isclose(through.get("depth", 0), 20.0, atol=1e-3),
              f"D{through.get('diameter')} depth {through.get('depth')}")

        blind = by_type.get("blind_hole", {})
        check("blind hole: D6, depth 10",
              np.isclose(blind.get("diameter", 0), 6.0, atol=1e-3)
              and np.isclose(blind.get("depth", 0), 10.0, atol=1e-3),
              f"D{blind.get('diameter')} depth {blind.get('depth')}")

        bore = by_type.get("counterbore", {})
        check("counterbore: D6 bore, D12 counterbore, depth 20",
              np.isclose(bore.get("diameter", 0), 6.0, atol=1e-3)
              and np.isclose(bore.get("counterbore_diameter", 0), 12.0,
                             atol=1e-3)
              and np.isclose(bore.get("depth", 0), 20.0, atol=1e-3),
              f"D{bore.get('diameter')} cb D{bore.get('counterbore_diameter')}")

        sink = by_type.get("countersink", {})
        check("countersink: D6, 45 degree cone",
              np.isclose(sink.get("diameter", 0), 6.0, atol=1e-3)
              and np.isclose(sink.get("angle", 0), 45.0, atol=0.5),
              f"D{sink.get('diameter')} angle {sink.get('angle')}")

        check("hole axes along Z",
              all(np.isclose(abs(f["axis"][2]), 1.0, atol=1e-6)
                  for f in stats["features"]), "")

        print("=== fields ===")
        from processes.base import load_cached_result, load_result_arrays
        cache_params = {**merged, "schema": 1,
                        "mesh": pipeline.mesh_fingerprint(workdir),
                        "aag": pipeline.aag_fingerprint(workdir)}
        arrays = load_result_arrays(workdir, "cnc", "features", cache_params)
        category = arrays["feature_category"]
        feature_id = arrays["feature_id"]
        fine_count = len(np.load(os.path.join(workdir, "fine_faces.npy")))
        check("per-face fields cover the fine mesh",
              len(category) == fine_count and len(feature_id) == fine_count,
              f"{fine_count} faces")
        check("categories match the feature list",
              set(np.unique(category)) == {0, 1, 2, 3, 4},
              f"codes {sorted(set(int(c) for c in np.unique(category)))}")
        check("ids and categories agree",
              bool(np.all((category > 0) == (feature_id > 0))), "")

        # cache round-trip: second run must not recompute
        calls = []
        analysis.run(workdir, merged, lambda f, m: calls.append(m))
        check("cache round-trip (no recompute)", len(calls) == 0,
              f"{len(calls)} progress calls")

    if failures:
        print(f"{len(failures)} CHECKS FAILED: {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
