"""Analytic checks of the BREP attributed adjacency graph (aag.py).

Fixtures:
A. Thin plate: all faces planar, all interior edges convex and sharp;
   get_sheet_base finds the two large faces and thickness 2.
B. Pocketed block: pocket floor edges concave (positive dihedral), pocket
   rim edges convex — the sign convention every consumer relies on.
C. Filleted block: the fillet face is convex and connects tangentially
   (smooth edges) to its two neighbours; C1 groups reflect it.
D. Drilled plate vs solid cylinder: hole wall concave, shaft wall convex;
   the cylinder seam edge is smooth with both sides the same face.
E. Determinism: two independent builds from the same STEP bytes produce
   byte-identical tables; save/load round-trips; the prep/aag analysis
   runs on a meshed workdir and agrees with brep_meta.json.

Run from the repo root: python test_aag.py
"""
import math
import os
import sys
import tempfile

import numpy as np

import aag
import brep


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


def make_plate():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 2).Shape()


def make_pocketed_block():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    block = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 20).Shape()
    pocket = BRepPrimAPI_MakeBox(gp_Pnt(10, 10, 12), 15, 10, 20).Shape()
    return BRepAlgoAPI_Cut(block, pocket).Shape()


def make_filleted_block(radius=3.0):
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    box = BRepPrimAPI_MakeBox(20, 20, 20).Shape()
    fillet = BRepFilletAPI_MakeFillet(box)
    explorer = TopExp_Explorer(box, TopAbs_EDGE)
    fillet.Add(radius, TopoDS.Edge_s(explorer.Current()))
    return fillet.Shape()


def make_drilled_plate():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    plate = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 5).Shape()
    axis = gp_Ax2(gp_Pnt(20, 15, -1), gp_Dir(0, 0, 1))
    drill = BRepPrimAPI_MakeCylinder(axis, 4.0, 7.0).Shape()
    return BRepAlgoAPI_Cut(plate, drill).Shape()


def make_cylinder():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    return BRepPrimAPI_MakeCylinder(5.0, 20.0).Shape()


def fixture_plate(check):
    shape = make_plate()
    graph = aag.build_aag(shape)

    check("plate: 6 planar faces",
          graph.face_count == 6
          and bool(np.all(graph.face_convexity == aag.FACE_PLANAR)),
          f"{graph.face_count} faces")

    interior = graph.interior_edges()
    check("plate: 12 interior edges, all sharp convex",
          int(interior.sum()) == 12
          and bool(np.all(graph.edge_convexity[interior] == aag.EDGE_CONVEX))
          and bool(np.all(graph.edge_continuity[interior] == 0)),
          f"{int(interior.sum())} edges")
    check("plate: convex dihedrals are -90 deg",
          bool(np.allclose(np.abs(graph.edge_angle[interior]), np.pi / 2,
                           atol=1e-6))
          and bool(np.all(graph.edge_angle[interior] < 0)), "")
    check("plate: edge lengths match the box",
          bool(np.isclose(sorted(graph.edge_length[interior])[-1], 40.0)), "")

    faces = list(brep.iter_faces(shape))
    base, opposite, thickness = aag.get_sheet_base(graph, faces,
                                                   min_thickness=0.1)
    areas = graph.face_area
    check("plate: sheet base is a 40x30 face",
          bool(np.isclose(areas[base], 1200.0)), f"area {areas[base]:.1f}")
    check("plate: opposite found with thickness 2",
          opposite is not None and np.isclose(thickness, 2.0)
          and bool(np.isclose(areas[opposite], 1200.0)),
          f"thickness {thickness:.3f}")


def fixture_pocket(check):
    shape = make_pocketed_block()
    graph = aag.build_aag(shape)
    interior = graph.interior_edges()

    concave = interior & (graph.edge_convexity == aag.EDGE_CONCAVE)
    convex = interior & (graph.edge_convexity == aag.EDGE_CONVEX)
    check("pocket: has concave edges (floor + walls)",
          int(concave.sum()) == 8, f"{int(concave.sum())} concave")
    check("pocket: concave dihedrals are +90 deg",
          bool(np.allclose(graph.edge_angle[concave], np.pi / 2, atol=1e-6)),
          "")
    check("pocket: rim edges stay convex",
          int(convex.sum()) == 12 + 4, f"{int(convex.sum())} convex")


def fixture_fillet(check):
    shape = make_filleted_block()
    graph = aag.build_aag(shape)

    convex_faces = np.flatnonzero(graph.face_convexity == aag.FACE_CONVEX)
    check("fillet: one convex face (the fillet)",
          len(convex_faces) == 1 and graph.face_count == 7,
          f"{len(convex_faces)} convex / {graph.face_count} faces")

    interior = graph.interior_edges()
    smooth = interior & (np.abs(graph.edge_continuity) >= 1)
    fillet_face = int(convex_faces[0])
    smooth_touch_fillet = smooth & np.any(graph.edge_faces == fillet_face,
                                          axis=1)
    check("fillet: two tangent connections",
          int(smooth_touch_fillet.sum()) == 2
          and int(smooth.sum()) == int(smooth_touch_fillet.sum()),
          f"{int(smooth.sum())} smooth edges")
    check("fillet: smooth edges carry angle 0",
          bool(np.all(graph.edge_angle[smooth] == 0.0)), "")

    # C1: fillet + its two tangent planes in one group, 4 singletons
    groups = graph.c1_group
    fillet_group = groups[fillet_face]
    check("fillet: C1 group of 3 faces",
          int(np.sum(groups == fillet_group)) == 3
          and len(np.unique(groups)) == 5,
          f"{len(np.unique(groups))} groups")

    curvature = graph.face_curvature[fillet_face]
    check("fillet: curvature ~ 1/(2r) mean",
          bool(np.isclose(abs(curvature), 0.5 * (1 / 3.0), rtol=1e-3)),
          f"curvature {curvature:.4f}")


def fixture_cylinders(check):
    drilled = aag.build_aag(make_drilled_plate())
    hole_wall = np.flatnonzero(drilled.face_convexity == aag.FACE_CONCAVE)
    check("drill: hole wall is concave",
          len(hole_wall) == 1, f"{len(hole_wall)} concave faces")

    solid = aag.build_aag(make_cylinder())
    shaft = np.flatnonzero(solid.face_convexity == aag.FACE_CONVEX)
    check("cylinder: shaft wall is convex",
          len(shaft) == 1 and solid.face_count == 3,
          f"{len(shaft)} convex / {solid.face_count} faces")

    seam = ((solid.edge_faces[:, 0] >= 0)
            & (solid.edge_faces[:, 0] == solid.edge_faces[:, 1]))
    check("cylinder: seam edge is smooth on one face",
          int(seam.sum()) == 1
          and bool(np.all(np.abs(solid.edge_continuity[seam]) == 2)),
          f"{int(seam.sum())} seams")

    rims = solid.interior_edges() & ~seam
    check("cylinder: cap rims are convex",
          int(rims.sum()) == 2
          and bool(np.all(solid.edge_convexity[rims] == aag.EDGE_CONVEX)), "")


def fixture_determinism(check):
    import json
    import pipeline
    import processes
    from processes.base import apply_defaults

    with tempfile.TemporaryDirectory() as tmp:
        path = write_step(tmp, make_pocketed_block(), "pocket.step")

        builds = []
        for _ in range(2):
            shape = brep.load_step_shape(path)
            builds.append(aag.build_aag(shape))
        identical = all(
            np.array_equal(getattr(builds[0], name), getattr(builds[1], name),
                           equal_nan=np.issubdtype(
                               getattr(builds[0], name).dtype, np.floating))
            for name in aag._ARRAY_FIELDS)
        check("determinism: independent builds identical", identical, "")

        # workdir integration: mesh + prep/aag analysis + save/load round-trip
        workdir = os.path.join(tmp, "wd")
        os.makedirs(workdir)
        pipeline.mesh_part(path, workdir, subdivide=2.0)
        analysis = processes.get_analysis("prep", "aag")
        merged = apply_defaults(analysis, {})
        result = analysis.run(workdir, merged, None)
        check("prep/aag: analysis runs and reports stats",
              result.stats["faces"] == builds[0].face_count,
              f"{result.stats['faces']} faces")

        meta = json.load(open(os.path.join(workdir, "aag.json")))
        brep_meta = json.load(open(os.path.join(workdir, "brep_meta.json")))
        check("prep/aag: face count matches brep_meta.json",
              meta["face_count"] == brep_meta["face_count"], "")
        check("prep/aag: source_sha and mesh fingerprint recorded",
              bool(meta["source_sha"])
              and meta["mesh_fingerprint"] == pipeline.mesh_fingerprint(workdir),
              "")

        loaded = aag.load_aag(workdir)
        round_trip = all(
            np.array_equal(getattr(builds[0], name), getattr(loaded, name),
                           equal_nan=np.issubdtype(
                               getattr(builds[0], name).dtype, np.floating))
            for name in aag._ARRAY_FIELDS)
        check("save/load round-trips every table", round_trip, "")
        check("aag fingerprint present",
              pipeline.aag_fingerprint(workdir) is not None, "")

        # graphs come up from the loaded artifact alone
        c1 = loaded.C1_faces
        check("loaded artifact builds networkx graphs",
              c1.number_of_nodes() == loaded.face_count, "")


def main():
    failures = []
    check = check_factory(failures)

    print("=== fixture A: thin plate ===")
    fixture_plate(check)
    print("=== fixture B: pocketed block (dihedral signs) ===")
    fixture_pocket(check)
    print("=== fixture C: filleted block (tangency) ===")
    fixture_fillet(check)
    print("=== fixture D: drilled plate / cylinder ===")
    fixture_cylinders(check)
    print("=== fixture E: determinism + workdir integration ===")
    fixture_determinism(check)

    if failures:
        print(f"{len(failures)} CHECKS FAILED: {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
