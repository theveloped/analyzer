"""Analytic checks of sheet-metal detection (sheet.py).

Fixtures:
A. Flat plate 40x30x2 — sheet, thickness 2, base/opposite are the large
   faces, four walls, no bends.
B. L-bracket (legs 40/20, thickness 2, inner bend radius 3, width 25) —
   sheet, one bend (two cylindrical faces: inner r3, outer r5), roles on
   both skins, walls on caps and cut edges.
C. Cube 20^3 with max_thickness=5 — not_sheet (thickness beyond limit).

Run from the repo root: python test_sheet.py
"""
import math
import os
import sys
import tempfile

import numpy as np

import pipeline
import processes
import sheet
from processes.base import apply_defaults


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:40s} {detail}")
        if not condition:
            failures.append(name)
    return check


def write_step(tmp, shape, name):
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    path = os.path.join(tmp, name)
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(path)
    return path


def make_l_bracket(a=40.0, b=20.0, t=2.0, r=3.0, w=25.0):
    """L-profile in the XZ plane (bend arcs included), extruded along Y.

    Bottom leg along X (outer surface z=0), vertical leg along Z (outer
    surface x=0); inner bend radius r centered at (t+r, t+r).
    """
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,
                                    BRepBuilderAPI_MakeFace,
                                    BRepBuilderAPI_MakeWire)
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Pnt, gp_Vec

    c = t + r
    s = 1.0 / math.sqrt(2.0)

    def pnt(x, z):
        return gp_Pnt(x, 0.0, z)

    def line(p1, p2):
        return BRepBuilderAPI_MakeEdge(pnt(*p1), pnt(*p2)).Edge()

    def arc(p1, mid, p2):
        maker = GC_MakeArcOfCircle(pnt(*p1), pnt(*mid), pnt(*p2))
        return BRepBuilderAPI_MakeEdge(maker.Value()).Edge()

    outer_mid = (c - (r + t) * s, c - (r + t) * s)
    inner_mid = (c - r * s, c - r * s)
    edges = [
        line((a, 0), (c, 0)),
        arc((c, 0), outer_mid, (0, c)),
        line((0, c), (0, b)),
        line((0, b), (t, b)),
        line((t, b), (t, c)),
        arc((t, c), inner_mid, (c, t)),
        line((c, t), (a, t)),
        line((a, t), (a, 0)),
    ]
    wire_maker = BRepBuilderAPI_MakeWire()
    for edge in edges:
        wire_maker.Add(edge)
    face = BRepBuilderAPI_MakeFace(wire_maker.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, w, 0)).Shape()


def _run_detect(tmp, shape, name, **params):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: F401 (fixture use)

    path = write_step(tmp, shape, name)
    workdir = os.path.join(tmp, name.replace(".step", ""))
    os.makedirs(workdir)
    pipeline.mesh_part(path, workdir, resolution=2.0, subdivide=2.0)
    pipeline.compute_aag(workdir)
    analysis = processes.get_analysis("sheet_metal", "detect")
    merged = apply_defaults(analysis, params)
    result = analysis.run(workdir, merged, None)
    return workdir, merged, result.stats


def fixture_plate(check, tmp):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    plate = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 2).Shape()
    workdir, merged, stats = _run_detect(tmp, plate, "plate.step")

    check("plate: verdict sheet, thickness 2",
          stats["verdict"] == "sheet"
          and np.isclose(stats["thickness"], 2.0, atol=1e-6),
          f"{stats['verdict']} t={stats['thickness']:.2f}")
    counts = stats["role_counts"]
    check("plate: 1 base, 1 opposite, 4 walls, 0 bends",
          counts["base"] == 1 and counts["opposite"] == 1
          and counts["wall"] == 4 and counts["bend"] == 0,
          f"{counts}")


def fixture_bracket(check, tmp):
    bracket = make_l_bracket()
    workdir, merged, stats = _run_detect(tmp, bracket, "bracket.step")

    check("bracket: verdict sheet, thickness 2",
          stats["verdict"] == "sheet"
          and np.isclose(stats["thickness"], 2.0, atol=1e-3),
          f"{stats['verdict']} t={stats['thickness']:.2f}")
    check("bracket: one bend (two cylindrical faces)",
          stats["bend_count"] == 1 and stats["role_counts"]["bend"] == 2,
          f"{stats['bend_count']} bends / {stats['role_counts']}")
    counts = stats["role_counts"]
    check("bracket: two flats per skin, four walls",
          counts["base"] == 2 and counts["opposite"] == 2
          and counts["wall"] == 4, f"{counts}")
    check("bracket: no not-sheet reasons", not stats["reasons"],
          f"{stats['reasons']}")

    # bend radii: inner r3 (opposite skin), outer r5 (base skin), read off
    # the per-fine-face field
    from processes.base import load_result_arrays
    from processes.sheet_metal import SHEET_SCHEMA
    cache_params = {**merged, "schema": SHEET_SCHEMA,
                    "mesh": pipeline.mesh_fingerprint(workdir),
                    "aag": pipeline.aag_fingerprint(workdir)}
    arrays = load_result_arrays(workdir, "sheet_metal", "detect",
                                cache_params)
    radius = arrays["bend_radius"]
    role = arrays["face_role"]
    bend_radii = np.unique(np.round(radius[role == sheet.ROLE_BEND], 3))
    check("bracket: bend radii are 3 (inner) and 5 (outer)",
          len(bend_radii) == 2
          and np.allclose(sorted(bend_radii), [3.0, 5.0], atol=1e-3),
          f"{bend_radii}")
    check("bracket: radius NaN off bends",
          bool(np.all(np.isnan(radius[role != sheet.ROLE_BEND]))), "")


def make_u_channel(width=40.0, height=15.0, t=2.0, r=3.0, w=25.0):
    """U-profile (two bends) in the XZ plane, extruded along Y."""
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,
                                    BRepBuilderAPI_MakeFace,
                                    BRepBuilderAPI_MakeWire)
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Pnt, gp_Vec

    c = t + r
    s = 1.0 / math.sqrt(2.0)
    W, H = width, height

    def pnt(x, z):
        return gp_Pnt(x, 0.0, z)

    def line(p1, p2):
        return BRepBuilderAPI_MakeEdge(pnt(*p1), pnt(*p2)).Edge()

    def arc(p1, mid, p2):
        maker = GC_MakeArcOfCircle(pnt(*p1), pnt(*mid), pnt(*p2))
        return BRepBuilderAPI_MakeEdge(maker.Value()).Edge()

    edges = [
        line((c, 0), (W - c, 0)),
        arc((W - c, 0), (W - c + (r + t) * s, c - (r + t) * s), (W, c)),
        line((W, c), (W, H)),
        line((W, H), (W - t, H)),
        line((W - t, H), (W - t, c)),
        arc((W - t, c), (W - c + r * s, c - r * s), (W - c, t)),
        line((W - c, t), (c, t)),
        arc((c, t), (c - r * s, c - r * s), (t, c)),
        line((t, c), (t, H)),
        line((t, H), (0, H)),
        line((0, H), (0, c)),
        arc((0, c), (c - (r + t) * s, c - (r + t) * s), (c, 0)),
    ]
    wire_maker = BRepBuilderAPI_MakeWire()
    for edge in edges:
        wire_maker.Add(edge)
    face = BRepBuilderAPI_MakeFace(wire_maker.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, w, 0)).Shape()


def _run_pattern(workdir, k_factor=0.5):
    analysis = processes.get_analysis("sheet_metal", "flat_pattern")
    merged = apply_defaults(analysis, {"k_factor": k_factor})
    return analysis.run(workdir, merged, None).stats


def fixture_patterns(check, tmp):
    # analytic constants for the fixtures (t=2, r=3, k=0.5, 90 deg bends)
    t, r, k = 2.0, 3.0, 0.5
    allowance = (math.pi / 2) * (r + k * t)   # 2*pi ~ 6.2832

    # flat plate: trivial unfold
    workdir = os.path.join(tmp, "plate")
    stats = _run_pattern(workdir)
    check("plate pattern: 40 x 30, no bends, developable",
          np.allclose(sorted(stats["flat_size"]), [30.0, 40.0], atol=1e-3)
          and len(stats["bends"]) == 0 and stats["developable"]
          and stats["volume_error_pct"] < 0.1,
          f"{stats['flat_size']} err {stats['volume_error_pct']:.3f}%")

    # L-bracket: flat length = (a-c) + BA + (b-c) with c = t + r
    workdir = os.path.join(tmp, "bracket")
    stats = _run_pattern(workdir)
    expected_length = 35.0 + allowance + 15.0
    size = sorted(stats["flat_size"])
    check("bracket pattern: analytic flat length",
          np.isclose(max(size), expected_length, atol=1e-2)
          and np.isclose(min(size), 25.0, atol=1e-2),
          f"{max(size):.3f} vs {expected_length:.3f}")
    check("bracket pattern: volume conserved (k=0.5)",
          stats["volume_error_pct"] < 0.1 and stats["volume_ok"]
          and stats["developable"],
          f"err {stats['volume_error_pct']:.3f}%")
    check("bracket pattern: one 90 deg bend line",
          len(stats["bends"]) == 1
          and np.isclose(stats["bends"][0]["angle_deg"], 90.0, atol=0.1)
          and np.isclose(stats["bends"][0]["inner_radius"], r, atol=1e-3)
          and np.isclose(stats["bends"][0]["length"], 25.0, atol=0.1),
          f"{stats['bends']}")

    # U-channel: two bends, flat length = (W-2c) + 2 BA + 2 (H-c)
    channel = make_u_channel()
    workdir, _, detect_stats = _run_detect(tmp, channel, "channel.step")
    check("channel: detected as sheet with 2 bends",
          detect_stats["verdict"] == "sheet"
          and detect_stats["bend_count"] == 2, f"{detect_stats['verdict']}")
    stats = _run_pattern(workdir)
    expected_length = 30.0 + 2 * allowance + 2 * 10.0
    size = sorted(stats["flat_size"])
    check("channel pattern: analytic flat length",
          np.isclose(max(size), expected_length, atol=1e-2),
          f"{max(size):.3f} vs {expected_length:.3f}")
    check("channel pattern: two bends, volume conserved",
          len(stats["bends"]) == 2 and stats["volume_error_pct"] < 0.1
          and stats["developable"],
          f"{len(stats['bends'])} bends, err {stats['volume_error_pct']:.3f}%")

    # hole in the vertical flange survives into the pattern
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    bracket = make_l_bracket()
    drill = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(-1, 12.5, 12), gp_Dir(1, 0, 0)), 4.0, 4.0).Shape()
    holed = BRepAlgoAPI_Cut(bracket, drill).Shape()
    workdir, _, _ = _run_detect(tmp, holed, "holed.step")
    stats = _run_pattern(workdir)
    check("holed bracket: hole survives the unfold",
          stats["hole_count"] == 1 and stats["developable"],
          f"{stats['hole_count']} holes")
    hole = stats["entities"]["holes"][0]["path"]
    points = np.array([entry[:2] for entry in hole])
    span = points.max(axis=0) - points.min(axis=0)
    check("holed bracket: hole is a D8 circle in the flat frame",
          np.allclose(span, [8.0, 8.0], atol=0.05), f"span {span}")
    check("holed bracket: volume conserved",
          stats["volume_error_pct"] < 0.15, f"{stats['volume_error_pct']:.3f}%")


def fixture_features(check, tmp):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, \
        BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    def up(x, y, z):
        return gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1))

    # boss on top: extrusion feature, height 6
    plate = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 5).Shape()
    boss = BRepPrimAPI_MakeCylinder(up(12, 15, 5), 5.0, 6.0).Shape()
    bossed = BRepAlgoAPI_Fuse(plate, boss).Shape()
    workdir, _, stats = _run_detect(tmp, bossed, "bossed.step")
    features = stats["features"]
    check("boss: one extrusion feature of height 6",
          stats["verdict"] == "sheet" and len(features) == 1
          and features[0]["type"] == "extrusion"
          and np.isclose(features[0]["value"], 6.0, atol=1e-3),
          f"{features}")
    check("boss: feature faces get the feature role",
          stats["role_counts"]["feature"] == 2, f"{stats['role_counts']}")

    # recess in the top: embossing feature, depth 2
    plate = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 5).Shape()
    recess = BRepPrimAPI_MakeCylinder(up(25, 15, 3), 4.0, 3.0).Shape()
    recessed = BRepAlgoAPI_Cut(plate, recess).Shape()
    workdir, _, stats = _run_detect(tmp, recessed, "recessed.step")
    features = stats["features"]
    check("recess: one embossing feature of depth 2",
          len(features) == 1 and features[0]["type"] == "embossing"
          and np.isclose(features[0]["value"], 2.0, atol=1e-3),
          f"{features}")

    # countersunk through hole: chamfer feature + projected engraving ring
    plate = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 5).Shape()
    sunk = BRepAlgoAPI_Cut(plate, BRepPrimAPI_MakeCylinder(
        up(20, 15, -1), 3.0, 7.0).Shape()).Shape()
    sunk = BRepAlgoAPI_Cut(sunk, BRepPrimAPI_MakeCone(
        up(20, 15, 2), 3.0, 6.0, 3.0).Shape()).Shape()
    workdir, _, stats = _run_detect(tmp, sunk, "sunk.step")
    features = stats["features"]
    check("countersink: recognized as a chamfer feature",
          len(features) == 1 and features[0]["type"] == "chamfer",
          f"{features}")
    pattern = _run_pattern(workdir)
    check("countersink: pattern shows the countersunk side",
          pattern["unfolded_side"] == "bottom"
          and pattern["hole_count"] == 1, f"{pattern['unfolded_side']}")
    hole = pattern["entities"]["holes"][0]
    check("countersink: hole annotated with the feature",
          hole.get("feature_type") == "chamfer", f"{hole.keys()}")
    check("countersink: engraving ring projected (D6 bore)",
          len(pattern["entities"]["engravings"]) == 1, "")
    ring = np.array([e[:2] for e in pattern["entities"]["engravings"][0]])
    span = ring.max(axis=0) - ring.min(axis=0)
    check("countersink: engraving is the D6 bore circle",
          np.allclose(span, [6.0, 6.0], atol=0.05), f"span {span}")

    # features on both sides: warning + unfold side by majority
    both = BRepAlgoAPI_Fuse(
        BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 5).Shape(),
        BRepPrimAPI_MakeCylinder(up(12, 15, 5), 5.0, 6.0).Shape()).Shape()
    both = BRepAlgoAPI_Cut(both, BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(30, 15, 2), gp_Dir(0, 0, -1)), 4.0, 3.0).Shape()).Shape()
    workdir, _, stats = _run_detect(tmp, both, "both_sides.step")
    check("both sides: two features + warning",
          len(stats["features"]) == 2 and len(stats["warnings"]) == 1,
          f"{len(stats['features'])} features, {stats['warnings']}")


def _lwpolyline_area(points, closed):
    """Exact enclosed area of a bulge polyline: shoelace over the vertices
    plus the circular-segment correction of every bulged segment."""
    vertices = [(p[0], p[1]) for p in points]
    bulges = [p[2] if len(p) > 2 else 0.0 for p in points]
    if not closed:
        return 0.0
    n = len(vertices)
    area = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        area += 0.5 * (x1 * y2 - x2 * y1)
        b = bulges[i]
        if b:
            theta = 4.0 * math.atan(abs(b))
            chord = math.hypot(x2 - x1, y2 - y1)
            radius = chord / (2.0 * math.sin(theta / 2.0))
            segment = 0.5 * radius * radius * (theta - math.sin(theta))
            area += math.copysign(segment, b)
    return abs(area)


def fixture_dxf(check, tmp):
    import ezdxf

    import dxfexport

    workdir = os.path.join(tmp, "holed")
    stats = _run_pattern(workdir)
    dxf_path = dxfexport.export_dxf(workdir,
                                    out_path=os.path.join(tmp, "holed.dxf"))
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    polylines = [e for e in msp if e.dxftype() == "LWPOLYLINE"
                 and e.dxf.layer == "OUTLINE"]
    bends = [e for e in msp if e.dxftype() == "LINE"
             and e.dxf.layer == "BENDS"]
    texts = [e for e in msp if e.dxftype() == "TEXT"
             and e.dxf.layer == "ENGRAVING"]
    check("dxf: contour + hole on OUTLINE, closed",
          len(polylines) == 2 and all(p.closed for p in polylines),
          f"{len(polylines)} polylines")
    check("dxf: one bend line + annotations",
          len(bends) == 1 and len(texts) == 2,
          f"{len(bends)} bends, {len(texts)} texts")

    areas = sorted(_lwpolyline_area(list(p.get_points("xyb")), p.closed)
                   for p in polylines)
    recomputed = areas[-1] - sum(areas[:-1])
    check("dxf: recomputed area matches flat_area",
          abs(recomputed - stats["flat_area"]) / stats["flat_area"] < 0.005,
          f"{recomputed:.1f} vs {stats['flat_area']:.1f}")
    check("dxf: hole area is a D8 circle",
          np.isclose(areas[0], math.pi * 16.0, rtol=5e-3),
          f"{areas[0]:.2f} vs {math.pi * 16:.2f}")


def fixture_cube(check, tmp):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    cube = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20, 20, 20).Shape()
    workdir, merged, stats = _run_detect(tmp, cube, "cube.step",
                                         max_thickness=5.0)
    check("cube: not_sheet beyond max thickness",
          stats["verdict"] == "not_sheet" and len(stats["reasons"]) == 1,
          f"{stats['verdict']}: {stats['reasons']}")


def main():
    failures = []
    check = check_factory(failures)

    with tempfile.TemporaryDirectory() as tmp:
        print("=== fixture A: flat plate ===")
        fixture_plate(check, tmp)
        print("=== fixture B: L-bracket ===")
        fixture_bracket(check, tmp)
        print("=== fixture C: cube (thickness limit) ===")
        fixture_cube(check, tmp)
        print("=== fixture D: flat patterns (unfold) ===")
        fixture_patterns(check, tmp)
        print("=== fixture E: DXF export ===")
        fixture_dxf(check, tmp)
        print("=== fixture F: skin features ===")
        fixture_features(check, tmp)

    if failures:
        print(f"{len(failures)} CHECKS FAILED: {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
