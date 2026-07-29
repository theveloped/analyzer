"""Hull-roughing pocket checks on synthetic parts.

What is left after roughing a part down to its own convex hull is exactly
``hull - part``, and that residual splits into disjoint pockets. Geometry,
meshed through the BREP at resolution 0.5 and voxelized at 0.25 mm:

- 20x20x10 solid box: the part IS its hull, so no residual and no pockets
- 20x20x10 block with a centered 8x8x5 pocket: exactly one pocket of 320 mm3,
  every pocket face carries the same id, every on-hull face carries 0
- the same block with TWO separated pockets: exactly two pockets. Without the
  erode/label/regrow step the zero-thickness film where the part touches its
  hull shorts them into one, so this is the check that step is doing its job
- a block with a through hole: one pocket of pi*r^2*h
- tool assignment on the 8x8 pocket: a D4 endmill reaches all of it, a D16
  cannot fit, so the best tool is the D4
- the registry runner stores under the resolver cache key and caches

Run from the repo root: python test_roughing.py
"""

import math
import os
import tempfile

import numpy as np

import pipeline
from processes import get_analysis
from processes import resolver
from processes.base import apply_defaults, result_paths

PASSED = 0
FAILED = 0

VOXEL = 0.25  # fine enough that a 320 mm3 pocket lands within a few percent


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"[OK ] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED += 1
        print(f"[FAIL] {name}" + (f"  ({detail})" if detail else ""))


def write_step(shape, path):
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    writer.Write(path)
    return path


def make_box_step(tmp):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    shape = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -10), gp_Pnt(10, 10, 0)).Shape()
    return write_step(shape, os.path.join(tmp, "box.stp"))


def make_pocket_step(tmp):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    block = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -10), gp_Pnt(10, 10, 0)).Shape()
    pocket = BRepPrimAPI_MakeBox(gp_Pnt(-4, -4, -5), gp_Pnt(4, 4, 1)).Shape()
    shape = BRepAlgoAPI_Cut(block, pocket).Shape()
    return write_step(shape, os.path.join(tmp, "pocket.stp"))


def make_two_pockets_step(tmp):
    """Two 4x4x4 pockets in the top face, separated by 4 mm of material."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    shape = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -10), gp_Pnt(10, 10, 0)).Shape()
    for x0 in (-6.0, 2.0):
        cut = BRepPrimAPI_MakeBox(gp_Pnt(x0, -2, -4), gp_Pnt(x0 + 4, 2, 1)).Shape()
        shape = BRepAlgoAPI_Cut(shape, cut).Shape()
    return write_step(shape, os.path.join(tmp, "two_pockets.stp"))


def make_round_pocket_step(tmp):
    """A blind r=5 bore 5 mm deep — no sharp internal corner, so a small flat
    endmill can genuinely clear all of it."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    block = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -10), gp_Pnt(10, 10, 0)).Shape()
    axis = gp_Ax2(gp_Pnt(0, 0, -5), gp_Dir(0, 0, 1))
    bore = BRepPrimAPI_MakeCylinder(axis, 5.0, 6.0).Shape()
    shape = BRepAlgoAPI_Cut(block, bore).Shape()
    return write_step(shape, os.path.join(tmp, "round_pocket.stp"))


def make_through_hole_step(tmp):
    """A r=3 hole straight through the 10 mm thickness of the block."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    block = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -10), gp_Pnt(10, 10, 0)).Shape()
    axis = gp_Ax2(gp_Pnt(0, 0, -11), gp_Dir(0, 0, 1))
    drill = BRepPrimAPI_MakeCylinder(axis, 3.0, 12.0).Shape()
    shape = BRepAlgoAPI_Cut(block, drill).Shape()
    return write_step(shape, os.path.join(tmp, "through.stp"))


def roughing(workdir, **kwargs):
    """Run the analysis the way the runner does, on the shared voxel grid."""
    from processes.base import load_result_arrays, params_hash

    voxel = kwargs.pop("voxel", VOXEL)
    result = resolver.ensure(workdir, "prep/voxels", {"voxel": voxel}, None)
    key = resolver.cache_key(workdir, "prep/voxels", {"voxel": voxel})
    voxels = load_result_arrays(workdir, "prep", "voxels", key)
    return pipeline.hull_roughing(
        workdir, voxels=voxels, grid=result.stats["grid"],
        voxels_hash=params_hash(key), **kwargs)


def prepare(step_path, workdir, *, directions=False):
    os.makedirs(workdir, exist_ok=True)
    pipeline.mesh_part(step_path, workdir, resolution=0.5)
    if directions:
        pipeline.compute_directions(workdir, count=0, axes=True)
    return workdir


def main():
    with tempfile.TemporaryDirectory() as tmp:
        # --- solid box: the part is its own hull, nothing to rough ------
        box_wd = prepare(make_box_step(tmp), os.path.join(tmp, "box_wd"))
        stats = roughing(box_wd)["stats"]
        check("box: no pockets", stats["pocket_count"] == 0,
              f"{stats['pocket_count']} pockets")
        check("box: residual is ~zero",
              abs(stats["residual_volume"]) < 1.0,
              f"{stats['residual_volume']} mm3")

        # --- one 8x8x5 pocket -------------------------------------------
        wd = prepare(make_pocket_step(tmp), os.path.join(tmp, "pocket_wd"))
        result = roughing(wd)
        stats = result["stats"]
        pocket_id = result["arrays"]["pocket_id"]

        check("pocket: exactly one pocket", stats["pocket_count"] == 1,
              f"{stats['pocket_count']}")
        check("pocket: exact residual is the pocket volume",
              abs(stats["residual_volume"] - 320.0) < 1.0,
              f"{stats['residual_volume']} mm3")
        if stats["pocket_count"] == 1:
            volume = stats["pockets"][0]["volume"]
            check("pocket: voxel volume within 3% of 320 mm3",
                  abs(volume - 320.0) / 320.0 < 0.03,
                  f"{volume} mm3")
            check("pocket: max_depth is the pocket depth below the hull",
                  abs(stats["pockets"][0]["max_depth"] - 5.0) < 0.3,
                  f"{stats['pockets'][0]['max_depth']} mm")
        check("pocket: volume_error is small",
              stats["volume_error"] < 0.03, f"{stats['volume_error']}")

        hull = pipeline.convex_hull_faces(wd)
        on_hull = hull["arrays"]["on_hull"].astype(bool)
        check("pocket: on-hull faces carry no pocket id",
              bool((pocket_id[on_hull] == 0).all()),
              f"{int((pocket_id[on_hull] != 0).sum())} leaked")
        off = ~on_hull
        check("pocket: every off-hull face is in pocket 1",
              bool((pocket_id[off] == 1).all()),
              f"{int((pocket_id[off] != 1).sum())} of {int(off.sum())} unassigned")
        check("pocket: face count matches the mask",
              stats["pockets"][0]["face_count"] == int(off.sum()))

        # --- two separated pockets: the erode/regrow step under test -----
        two_wd = prepare(make_two_pockets_step(tmp), os.path.join(tmp, "two_wd"))
        result = roughing(two_wd)
        stats = result["stats"]
        check("two pockets: found exactly two", stats["pocket_count"] == 2,
              f"{stats['pocket_count']}")
        if stats["pocket_count"] == 2:
            volumes = sorted(p["volume"] for p in stats["pockets"])
            check("two pockets: both are ~64 mm3",
                  all(abs(v - 64.0) / 64.0 < 0.05 for v in volumes),
                  f"{volumes}")
            ids = set(np.unique(result["arrays"]["pocket_id"]).tolist())
            check("two pockets: both ids appear on faces",
                  {1, 2} <= ids, f"ids {sorted(ids)}")

        # --- through hole -----------------------------------------------
        hole_wd = prepare(make_through_hole_step(tmp), os.path.join(tmp, "hole_wd"))
        stats = roughing(hole_wd)["stats"]
        expected = math.pi * 9.0 * 10.0
        check("through hole: one pocket", stats["pocket_count"] == 1,
              f"{stats['pocket_count']}")
        if stats["pocket_count"] == 1:
            volume = stats["pockets"][0]["volume"]
            check("through hole: volume is pi*r^2*h",
                  abs(volume - expected) / expected < 0.05,
                  f"{volume:.1f} vs {expected:.1f} mm3")

        # --- tool assignment on a round pocket a D4 can actually clear ----
        tools = [
            {"diameter": 16.0, "corner_radius": 0.0, "stickout": 80.0,
             "holder_radius": 8.0},
            {"diameter": 4.0, "corner_radius": 0.0, "stickout": 40.0,
             "holder_radius": 2.0},
        ]
        round_wd = prepare(make_round_pocket_step(tmp),
                           os.path.join(tmp, "round_wd"), directions=True)
        stats = roughing(round_wd, tools=tools, directions=[4])["stats"]
        check("round pocket: one pocket", stats["pocket_count"] == 1,
              f"{stats['pocket_count']}")
        pocket = stats["pockets"][0]
        check("tools: the r=5 bore is fully reachable",
              pocket["fully_reachable"] is True,
              f"coverage {pocket['tool_area_fraction']}")
        check("tools: the D4 is the best tool — the D16 does not fit",
              pocket["best_tool_diameter"] == 4.0,
              f"D{pocket['best_tool_diameter']}, "
              f"D16 covers {pocket['tool_area_fraction']['0']}")
        check("tools: nothing counted unreachable",
              stats["unreachable_volume"] == 0.0,
              f"{stats['unreachable_volume']} mm3")

        # a square pocket is the honest counter-case: a round tool cannot
        # clean a sharp internal corner, so NO flat endmill fully covers it
        pipeline.compute_directions(wd, count=0, axes=True)
        square = roughing(wd, tools=tools, directions=[4])["stats"]
        square_pocket = square["pockets"][0]
        check("tools: sharp corners defeat every flat endmill",
              square_pocket["fully_reachable"] is False,
              f"best {square_pocket['best_tool_diameter']}")
        check("tools: the D4 still reaches most of it, the D16 almost none",
              (square_pocket["tool_area_fraction"]["1"] > 0.7
               > square_pocket["tool_area_fraction"]["0"]),
              f"{square_pocket['tool_area_fraction']}")
        check("tools: its volume lands in unreachable_volume",
              square["unreachable_volume"] == square_pocket["volume"],
              f"{square['unreachable_volume']} mm3")

        # --- registry runner: stores under the resolver key, caches ------
        analysis = get_analysis("cnc", "roughing")
        params = apply_defaults(analysis, {"voxel": VOXEL, "tools": [],
                                           "direction_indices": [4]})
        first = analysis.run(wd, params, None)
        key = resolver.cache_key(wd, "cnc/roughing", params)
        json_path, _ = result_paths(wd, "cnc", "roughing", key)
        check("runner stores under the resolver cache key",
              os.path.exists(json_path))

        calls = []
        second = analysis.run(wd, params, lambda f, m: calls.append(m))
        check("second run returns the cached result",
              second.stats == first.stats and not calls,
              f"{len(calls)} progress calls")
        check("runner field is the pocket mask",
              list(first.fields) == ["pocket_id"], f"{first.fields}")

    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED == 0:
        print("ALL CHECKS PASSED")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
