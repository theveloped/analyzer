"""Reach-study checks on a synthetic pocket part.

Geometry: 20x20x10 block (top at z=0) with a centered 8x8x5 pocket. From
+Z with two flat endmills (no holders):

- D=2 reaches the pocket floor (8 mm opening, 1 mm radius)
- D=12 cannot enter the pocket at all, but still reaches the top face
- every stored mask is a subset of the direction's accessibility row
- the bigger tool's reach is a subset of the smaller tool's
- one (direction, tool) pair cross-checks exactly against
  pipeline.compose_tool (same canonical zmap.tool_face_verdict rule):
  reach count == accessible count - unreachable count
- the registry runner caches: a second run returns the stored result

Run from the repo root: python test_reach.py
"""

import os
import tempfile

import numpy as np

import pipeline
from processes import get_analysis
from processes import resolver
from processes.base import apply_defaults, result_paths

PASSED = 0
FAILED = 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"[OK ] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED += 1
        print(f"[FAIL] {name}" + (f"  ({detail})" if detail else ""))


def make_pocket_step(tmp):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    block = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -10), gp_Pnt(10, 10, 0)).Shape()
    pocket = BRepPrimAPI_MakeBox(gp_Pnt(-4, -4, -5), gp_Pnt(4, 4, 1)).Shape()
    shape = BRepAlgoAPI_Cut(block, pocket).Shape()
    path = os.path.join(tmp, "pocket.stp")
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    writer.Write(path)
    return path


SMALL = {"diameter": 2.0, "corner_radius": 0.0,
         "stickout": None, "holder_radius": None}
BIG = {"diameter": 12.0, "corner_radius": 0.0,
       "stickout": None, "holder_radius": None}
UP = 4  # +Z (compute_directions --axes prepends ±XYZ at indices 0..5)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        wd = os.path.join(tmp, "wd")
        os.makedirs(wd)
        pipeline.mesh_part(make_pocket_step(tmp), wd, resolution=0.5)
        pipeline.compute_directions(wd, count=2, axes=True)

        result = pipeline.reach_study(wd, directions=[UP],
                                      tools=[SMALL, BIG])
        arrays = result["arrays"]
        check("per-pair masks stored",
              set(arrays) == {f"reach_{UP}_0", f"reach_{UP}_1"}
              and all(a.dtype == np.uint8 for a in arrays.values()))

        small = arrays[f"reach_{UP}_0"].astype(bool)
        big = arrays[f"reach_{UP}_1"].astype(bool)
        access = np.load(os.path.join(wd, "accessibility.npy"))[UP]
        verts = np.load(os.path.join(wd, pipeline.FINE_VERTS_FILE))
        faces = np.load(os.path.join(wd, pipeline.FINE_FACES_FILE))
        centroids = verts[faces].mean(axis=1)

        floor = ((np.abs(centroids[:, 2] + 5.0) < 0.1)
                 & (np.abs(centroids[:, 0]) < 3.0)
                 & (np.abs(centroids[:, 1]) < 3.0))
        top = ((np.abs(centroids[:, 2]) < 0.1)
               & (np.maximum(np.abs(centroids[:, 0]),
                             np.abs(centroids[:, 1])) > 5.0))

        check("masks are subsets of the accessibility row",
              bool((~access[small]).sum() == 0 and (~access[big]).sum() == 0))
        check("D=2 reaches the pocket floor", bool(small[floor].all()),
              f"{int(small[floor].sum())}/{int(floor.sum())} floor faces")
        check("D=12 cannot enter the pocket", bool(~big[floor].any()),
              f"{int(big[floor].sum())} floor faces flagged reachable")
        check("D=12 still reaches the top face", bool(big[top].all()),
              f"{int(big[top].sum())}/{int(top.sum())} top faces")
        check("bigger tool reach is a subset of smaller",
              bool(~(big & ~small).any()))

        # exact cross-check against the CLI compose path (same canonical rule)
        compose = pipeline.compose_tool(wd, UP, diameter=SMALL["diameter"],
                                        corner_radius=0.0)
        check("study pair matches compose_tool exactly",
              int(small.sum()) == compose["accessible"] - compose["unreachable"],
              f"reach {int(small.sum())} == accessible {compose['accessible']}"
              f" - unreachable {compose['unreachable']}")

        stats_pairs = {(p["direction"], p["tool"]): p
                       for p in result["stats"]["pairs"]}
        check("stats echo per-pair reach counts",
              stats_pairs[(UP, 0)]["reachable_faces"] == int(small.sum())
              and stats_pairs[(UP, 1)]["reachable_faces"] == int(big.sum()))

        try:
            pipeline.reach_study(wd, directions=[999], tools=[SMALL])
            check("out-of-range direction raises", False)
        except ValueError:
            check("out-of-range direction raises", True)

        # registry runner: stores under resolver.cache_key, second run cached
        analysis = get_analysis("cnc", "reach_study")
        params = apply_defaults(analysis, {
            "direction_indices": [UP], "tools": [SMALL, BIG]})
        first = analysis.run(wd, params, None)
        key = resolver.cache_key(wd, "cnc/reach_study", params)
        json_path, _ = result_paths(wd, "cnc", "reach_study", key)
        check("runner stores under the resolver cache key",
              os.path.exists(json_path))
        second = analysis.run(wd, params, None)
        check("second run returns the cached result",
              second.stats == first.stats
              and sorted(second.fields) == sorted(first.fields))

    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED == 0:
        print("ALL CHECKS PASSED")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
