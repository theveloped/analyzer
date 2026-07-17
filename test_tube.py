"""Analytic checks of tube/profile classification (tube.py).

Fixtures:
A. Round tube (outer r10, wall 2, length 80): radii, thickness, length,
   axis; unroll circumference = 2*pi*(r_inner + k*t) at k=0.5 = neutral.
B. Rectangular tube 60x40 wall 3, corner radius 6 outer / 3 inner,
   length 120: section dims, corner radii, length.
C. Square tube 50x50: square verdict.
D. L-bracket (bent sheet): verdict none with a sheet_metal pointer.

Run from the repo root: python test_tube.py
"""
import math
import os
import sys
import tempfile

import numpy as np

import pipeline
import processes
from processes.base import apply_defaults
from test_sheet import make_l_bracket, write_step


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:40s} {detail}")
        if not condition:
            failures.append(name)
    return check


def make_round_tube(outer=10.0, wall=2.0, length=80.0):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    axis = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    solid = BRepPrimAPI_MakeCylinder(axis, outer, length).Shape()
    bore_axis = gp_Ax2(gp_Pnt(0, 0, -1), gp_Dir(0, 0, 1))
    bore = BRepPrimAPI_MakeCylinder(bore_axis, outer - wall,
                                    length + 2).Shape()
    return BRepAlgoAPI_Cut(solid, bore).Shape()


def make_rect_tube(width=60.0, height=40.0, wall=3.0, radius=6.0,
                   length=120.0):
    """Rounded-rectangle profile tube: outer corner radius ``radius``,
    inner ``radius - wall``, extruded along Z."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,
                                    BRepBuilderAPI_MakeFace,
                                    BRepBuilderAPI_MakeWire)
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Pnt, gp_Vec

    def rounded_rect(w, h, r, z):
        s = 1.0 / math.sqrt(2.0)
        x0, y0 = -w / 2, -h / 2
        x1, y1 = w / 2, h / 2

        def pnt(x, y):
            return gp_Pnt(x, y, z)

        def line(p1, p2):
            return BRepBuilderAPI_MakeEdge(pnt(*p1), pnt(*p2)).Edge()

        def arc(p1, mid, p2):
            maker = GC_MakeArcOfCircle(pnt(*p1), pnt(*mid), pnt(*p2))
            return BRepBuilderAPI_MakeEdge(maker.Value()).Edge()

        d = r * (1 - s)
        edges = [
            line((x0 + r, y0), (x1 - r, y0)),
            arc((x1 - r, y0), (x1 - d, y0 + d), (x1, y0 + r)),
            line((x1, y0 + r), (x1, y1 - r)),
            arc((x1, y1 - r), (x1 - d, y1 - d), (x1 - r, y1)),
            line((x1 - r, y1), (x0 + r, y1)),
            arc((x0 + r, y1), (x0 + d, y1 - d), (x0, y1 - r)),
            line((x0, y1 - r), (x0, y0 + r)),
            arc((x0, y0 + r), (x0 + d, y0 + d), (x0 + r, y0)),
        ]
        wire_maker = BRepBuilderAPI_MakeWire()
        for edge in edges:
            wire_maker.Add(edge)
        return BRepBuilderAPI_MakeFace(wire_maker.Wire()).Face()

    outer = BRepPrimAPI_MakePrism(
        rounded_rect(width, height, radius, 0.0),
        gp_Vec(0, 0, length)).Shape()
    inner = BRepPrimAPI_MakePrism(
        rounded_rect(width - 2 * wall, height - 2 * wall, radius - wall,
                     -1.0), gp_Vec(0, 0, length + 2)).Shape()
    return BRepAlgoAPI_Cut(outer, inner).Shape()


def _run_profile(tmp, shape, name, **params):
    path = write_step(tmp, shape, name)
    workdir = os.path.join(tmp, name.replace(".step", ""))
    os.makedirs(workdir)
    pipeline.mesh_part(path, workdir, resolution=2.0, subdivide=2.0)
    pipeline.compute_aag(workdir)
    analysis = processes.get_analysis("tube_laser", "profile")
    merged = apply_defaults(analysis, params)
    return analysis.run(workdir, merged, None).stats


def main():
    failures = []
    check = check_factory(failures)

    with tempfile.TemporaryDirectory() as tmp:
        print("=== fixture A: round tube ===")
        stats = _run_profile(tmp, make_round_tube(), "round.step")
        check("round: verdict + radii 8/10, wall 2",
              stats["verdict"] == "round"
              and np.isclose(stats["inner_radius"], 8.0, atol=1e-3)
              and np.isclose(stats["outer_radius"], 10.0, atol=1e-3)
              and np.isclose(stats["thickness"], 2.0, atol=1e-3),
              f"{stats['verdict']} r{stats.get('inner_radius')}/"
              f"{stats.get('outer_radius')}")
        check("round: length 80 along Z",
              np.isclose(stats["length"], 80.0, atol=1e-3)
              and np.isclose(abs(stats["axis"][2]), 1.0, atol=1e-6),
              f"L{stats.get('length')}")
        # unroll: neutral circumference x length
        neutral = 2 * math.pi * (8.0 + 0.5 * 2.0)
        size = sorted(stats.get("flat_size", [0, 0]))
        check("round: unroll is neutral circumference x length",
              np.isclose(max(size), max(neutral, 80.0), rtol=1e-3)
              and np.isclose(min(size), min(neutral, 80.0), rtol=1e-3),
              f"{size} vs [{min(neutral, 80.0):.2f}, {max(neutral, 80.0):.2f}]")

        print("=== fixture B: rectangular tube ===")
        stats = _run_profile(tmp, make_rect_tube(), "rect.step")
        check("rect: verdict + 60 x 40 wall 3",
              stats["verdict"] == "rectangular"
              and np.isclose(stats["width"], 60.0, atol=1e-3)
              and np.isclose(stats["height"], 40.0, atol=1e-3)
              and np.isclose(stats["thickness"], 3.0, atol=1e-3),
              f"{stats['verdict']} {stats.get('width')}x{stats.get('height')}"
              f" t{stats.get('thickness')}")
        check("rect: corner radii 3 (inner) / 6 (outer), length 120",
              np.isclose(stats["inner_radius"], 3.0, atol=1e-3)
              and np.isclose(stats["outer_radius"], 6.0, atol=1e-3)
              and np.isclose(stats["length"], 120.0, atol=1e-3),
              f"r{stats.get('inner_radius')}/{stats.get('outer_radius')} "
              f"L{stats.get('length')}")

        print("=== fixture C: square tube ===")
        stats = _run_profile(tmp, make_rect_tube(width=50.0, height=50.0),
                             "square.step", unroll=False)
        check("square: verdict square 50 x 50",
              stats["verdict"] == "square"
              and np.isclose(stats["width"], 50.0, atol=1e-3)
              and np.isclose(stats["height"], 50.0, atol=1e-3),
              f"{stats['verdict']}")

        print("=== fixture D: bent sheet rejected ===")
        stats = _run_profile(tmp, make_l_bracket(), "bent.step",
                             unroll=False)
        check("bent sheet: verdict none, sheet pointer",
              stats["verdict"] == "none"
              and any("sheet_metal" in reason for reason in stats["reasons"]),
              f"{stats['verdict']}: {stats['reasons']}")

    if failures:
        print(f"{len(failures)} CHECKS FAILED: {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
