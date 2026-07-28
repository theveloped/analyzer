"""Convex-hull face-mask checks on synthetic parts.

Geometry, meshed through the BREP at resolution 0.5:

- 20x20x10 solid box: every facet lies on its own hull (fraction 1.0),
  the hull has exactly the 8 corners, and crease edges exist
- the test_reach pocket part (20x20x10 block, centered 8x8x5 pocket):
  outer faces + top rim on-hull, pocket floor and walls off-hull, the
  floor's support gap is the pocket depth (5 mm), and the explicit
  ``tollerance`` knob swallows the floor (gap 5 <= 6) but not the walls
- cylinder r=10 h=20: a convex curved part is entirely on its hull with
  the default deflection-derived eps
- the registry runner stores under the resolver cache key and returns
  the cached result on a second run

Run from the repo root: python test_hull.py
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


def make_cylinder_step(tmp):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    shape = BRepPrimAPI_MakeCylinder(10.0, 20.0).Shape()
    return write_step(shape, os.path.join(tmp, "cylinder.stp"))


def main():
    import machining

    with tempfile.TemporaryDirectory() as tmp:
        # --- solid box: the part is its own hull -------------------------
        box_wd = os.path.join(tmp, "box_wd")
        os.makedirs(box_wd)
        pipeline.mesh_part(make_box_step(tmp), box_wd, resolution=0.5)
        result = pipeline.convex_hull_faces(box_wd)
        arrays, stats = result["arrays"], result["stats"]
        mask = arrays["on_hull"].astype(bool)
        check("box: mask dtype and length",
              arrays["on_hull"].dtype == np.uint8
              and len(mask) == stats["face_count"])
        check("box: every face on the hull", bool(mask.all()),
              f"{int(mask.sum())}/{len(mask)}")
        check("box: area fraction is 1",
              abs(stats["area_fraction"] - 1.0) < 1e-6,
              f"{stats['area_fraction']}")
        check("box: hull has the 8 corners",
              stats["hull_vertex_count"] == 8,
              f"{stats['hull_vertex_count']} hull vertices")
        edges = arrays["hull_edges"]
        check("box: crease edges present with shape (K, 2, 3)",
              edges.ndim == 3 and edges.shape[1:] == (2, 3)
              and len(edges) > 0
              and stats["edge_segments"] == len(edges))
        check("box: gaps are non-negative and within eps on-hull",
              bool((arrays["hull_gap"] >= 0).all()
                   and (arrays["hull_gap"][mask] <= stats["tollerance"]).all()))

        # --- pocket part: cavity is off-hull -----------------------------
        wd = os.path.join(tmp, "pocket_wd")
        os.makedirs(wd)
        pipeline.mesh_part(make_pocket_step(tmp), wd, resolution=0.5)
        result = pipeline.convex_hull_faces(wd)
        arrays, stats = result["arrays"], result["stats"]
        mask = arrays["on_hull"].astype(bool)
        gap = arrays["hull_gap"]

        verts, faces = pipeline.load_mesh_arrays(wd)
        centroids = verts[faces].mean(axis=1)
        floor = ((np.abs(centroids[:, 2] + 5.0) < 0.1)
                 & (np.abs(centroids[:, 0]) < 3.0)
                 & (np.abs(centroids[:, 1]) < 3.0))
        walls = ((centroids[:, 2] > -4.9) & (centroids[:, 2] < -0.1)
                 & (np.maximum(np.abs(centroids[:, 0]),
                               np.abs(centroids[:, 1])) < 4.1))
        top = ((np.abs(centroids[:, 2]) < 0.1)
               & (np.maximum(np.abs(centroids[:, 0]),
                             np.abs(centroids[:, 1])) > 5.0))
        bottom = np.abs(centroids[:, 2] + 10.0) < 0.1

        check("pocket: floor off-hull", bool(~mask[floor].any()),
              f"{int(mask[floor].sum())}/{int(floor.sum())} flagged")
        check("pocket: walls off-hull", bool(~mask[walls].any()),
              f"{int(mask[walls].sum())}/{int(walls.sum())} flagged")
        check("pocket: top rim on-hull", bool(mask[top].all()),
              f"{int(mask[top].sum())}/{int(top.sum())}")
        check("pocket: bottom on-hull", bool(mask[bottom].all()),
              f"{int(mask[bottom].sum())}/{int(bottom.sum())}")
        check("pocket: floor gap is the pocket depth",
              bool(np.allclose(gap[floor], 5.0, atol=1e-3)),
              f"gap {gap[floor].min():.4f}..{gap[floor].max():.4f}")
        areas = machining.face_areas(verts, faces)
        check("pocket: on_hull_area consistent with the mask",
              abs(stats["on_hull_area"] - float(areas[mask].sum())) < 1e-2,
              f"{stats['on_hull_area']} vs {areas[mask].sum():.3f}")
        check("pocket: hull is the uncut box",
              stats["hull_vertex_count"] == 8
              and abs(stats["hull_volume"] - 4000.0) < 1.0,
              f"volume {stats['hull_volume']}")

        # the tollerance knob: 6 mm swallows the 5 mm floor, not the walls
        wide = pipeline.convex_hull_faces(wd, tollerance=6.0)
        wide_mask = wide["arrays"]["on_hull"].astype(bool)
        check("pocket: tollerance=6 flags the floor",
              bool(wide_mask[floor].all()))
        check("pocket: tollerance=6 keeps the walls off",
              bool(~wide_mask[walls].any()))
        check("pocket: tollerance echoed in stats",
              wide["stats"]["tollerance"] == 6.0)

        # --- cylinder: convex curved part is all hull --------------------
        cyl_wd = os.path.join(tmp, "cyl_wd")
        os.makedirs(cyl_wd)
        pipeline.mesh_part(make_cylinder_step(tmp), cyl_wd, resolution=0.5)
        result = pipeline.convex_hull_faces(cyl_wd)
        cyl_mask = result["arrays"]["on_hull"].astype(bool)
        check("cylinder: every face on the hull with default eps",
              bool(cyl_mask.all()),
              f"{int(cyl_mask.sum())}/{len(cyl_mask)}, "
              f"eps {result['stats']['tollerance']:.4g}")

        # --- registry runner: stores under the resolver key, caches ------
        analysis = get_analysis("cnc", "hull")
        params = apply_defaults(analysis, {})
        first = analysis.run(wd, params, None)
        key = resolver.cache_key(wd, "cnc/hull", params)
        json_path, _ = result_paths(wd, "cnc", "hull", key)
        check("runner stores under the resolver cache key",
              os.path.exists(json_path))
        second = analysis.run(wd, params, None)
        check("second run returns the cached result",
              second.stats == first.stats
              and sorted(second.fields) == sorted(first.fields))
        check("runner fields cover mask, gap and edges",
              set(first.fields) == {"on_hull", "hull_gap", "hull_edges"})

    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED == 0:
        print("ALL CHECKS PASSED")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
