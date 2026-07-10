"""Analytic checks of BREP meshing and the mold orientation analysis.

Fixtures:
A. BREP meshing: an OCC box minus a through-slot — face count matches the
   BREP exactly, the welded mesh is conformal (every edge shared by exactly
   two faces), and the tag-preserving subdivision keeps every id.
B. Side hole: a block with a through slot along X — the ±Z plate pair is
   infeasible alone and feasible with exactly one ±X slide; slot walls are
   categorized slide, outer walls 'either', and the parting line sits
   strictly inside the wall span.
C. Internal undercut: a lateral blind notch inside a top pocket — occluded
   from ±Z and from every perpendicular direction, so it must classify as
   internal undercut, not slide.

Run from the repo root: python test_mold.py
"""
import os
import sys
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm

import brep
import molding
import pipeline
import processes
from analysis import get_mesh_data, subdivide_mesh
from processes.base import apply_defaults


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:40s} {detail}")
        if not condition:
            failures.append(name)
    return check


def save_workdir(workdir, part, subdivide=1.0):
    part = subdivide_mesh(part, subdivide)
    verts, faces = get_mesh_data(part)
    np.save(os.path.join(workdir, "fine_verts.npy"), verts)
    np.save(os.path.join(workdir, "fine_faces.npy"), faces)
    return verts, faces


def fixture_brep(check):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    with tempfile.TemporaryDirectory() as tmp:
        block = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -5), 20, 20, 10).Shape()
        slot = BRepPrimAPI_MakeBox(gp_Pnt(-12, -2, -2), 24, 4, 4).Shape()
        shape = BRepAlgoAPI_Cut(block, slot).Shape()

        n_brep = 0
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            n_brep += 1
            explorer.Next()

        from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
        path = os.path.join(tmp, "fixture.step")
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        writer.Write(path)

        verts, faces, ids, types = brep.mesh_step(path, deflection=0.5)
        check("brep face count matches TopExp",
              len(types) == n_brep and len(np.unique(ids)) == n_brep,
              f"{len(np.unique(ids))} ids / {n_brep} BREP faces")

        def edge_counts(f):
            e = np.stack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]],
                         axis=1).reshape(-1, 2)
            e = np.sort(e, axis=1)
            _, counts = np.unique(e, axis=0, return_counts=True)
            return counts

        check("welded mesh is conformal (watertight)",
              bool(np.all(edge_counts(faces.astype(np.int64)) == 2)), "")

        fine_verts, fine_faces, fine_ids = brep.subdivide_tagged(
            verts, faces, ids, 1.0)
        check("subdivision keeps every BREP id",
              set(np.unique(fine_ids)) == set(np.unique(ids)),
              f"{len(fine_faces)} fine faces")
        check("subdivided mesh stays conformal",
              bool(np.all(edge_counts(fine_faces.astype(np.int64)) == 2)), "")
        lengths = np.linalg.norm(
            fine_verts[fine_faces[:, 0]] - fine_verts[fine_faces[:, 1]], axis=1)
        check("edge length target respected", float(lengths.max()) <= 1.0 + 1e-9,
              f"max {lengths.max():.3f}")

    # real STEP: id count matches OCC's face count
    shape = brep.load_step_shape("tests/testpart_42.stp")
    n = sum(1 for _ in brep.iter_faces(shape))
    _, _, ids, types = brep.mesh_step("tests/testpart_42.stp", deflection=0.5)
    check("testpart_42.stp face count", len(types) == n,
          f"{len(types)} / {n}")


def fixture_side_hole(check):
    with tempfile.TemporaryDirectory() as workdir:
        block = mm.makeCube(mm.Vector3f(20, 20, 10), mm.Vector3f(-10, -10, -5))
        slot = mm.makeCube(mm.Vector3f(24, 4, 4), mm.Vector3f(-12, -2, -2))
        part = mm.boolean(block, slot, mm.BooleanOperation.DifferenceAB).mesh
        verts, faces = save_workdir(workdir, part)
        pipeline.compute_directions(workdir, count=4, axes=True)

        directions = np.load(os.path.join(workdir, "directions.npy"))
        accessibility = np.load(os.path.join(workdir, "accessibility.npy"))

        blocked = molding.mold_orientation_search(
            directions, accessibility, max_slides=0)
        z_pair = next(o for o in blocked if o["pair"] == [4, 5])
        check("side hole: pair-only infeasible",
              not z_pair["feasible"] and z_pair["counts"]["internal"] > 0,
              f"internal {z_pair['counts']['internal']}")

        options = molding.mold_orientation_search(
            directions, accessibility, max_slides=2)
        z_pair = next(o for o in options if o["pair"] == [4, 5])
        check("side hole: feasible with one +-X slide",
              z_pair["feasible"] and len(z_pair["slides"]) == 1
              and z_pair["slides"][0]["direction"] in (0, 1),
              f"slides {z_pair['slides']}")

        band = molding.assignment_band(
            z_pair["pair"], [s["direction"] for s in z_pair["slides"]],
            accessibility)
        centroids = verts[faces].mean(axis=1)
        slot_walls = (np.abs(np.abs(centroids[:, 1]) - 2.0) < 0.05) \
            & (np.abs(centroids[:, 2]) < 1.9) & (np.abs(centroids[:, 0]) < 9.5)
        outer_walls = (np.abs(np.abs(centroids[:, 0]) - 10.0) < 0.05) \
            & (np.abs(centroids[:, 2]) < 4.0) & (np.abs(centroids[:, 1]) < 8.0) \
            & (np.abs(centroids[:, 2]) > -4.0)
        check("slot walls categorized slide",
              np.all(band[slot_walls] == molding.SLIDE_BASE),
              f"{int(slot_walls.sum())} faces")
        check("outer walls in the either band",
              np.all(band[outer_walls] == molding.EITHER),
              f"{int(outer_walls.sum())} faces")

        pairs, edge_verts = molding.face_adjacency(faces)
        resolved = molding.resolve_either(band, pairs)
        check("resolved has no either code",
              not np.any(resolved == molding.EITHER), "")

        lines = molding.parting_line_segments(resolved, pairs, edge_verts, verts)
        z_vals = lines[:, :, 2]
        check("parting line inside the wall span",
              len(lines) > 0 and z_vals.max() < 5.0 and z_vals.min() > -5.0,
              f"{len(lines)} segments, z in [{z_vals.min():.2f}, {z_vals.max():.2f}]")


def fixture_internal_undercut(check):
    with tempfile.TemporaryDirectory() as workdir:
        block = mm.makeCube(mm.Vector3f(20, 20, 10), mm.Vector3f(-10, -10, -10))
        pocket = mm.makeCube(mm.Vector3f(8, 8, 6), mm.Vector3f(-4, -4, -6))
        part = mm.boolean(block, pocket, mm.BooleanOperation.DifferenceAB).mesh
        notch = mm.makeCube(mm.Vector3f(3, 4, 2), mm.Vector3f(4, -2, -5))
        part = mm.boolean(part, notch, mm.BooleanOperation.DifferenceAB).mesh
        verts, faces = save_workdir(workdir, part)
        pipeline.compute_directions(workdir, count=4, axes=True)

        directions = np.load(os.path.join(workdir, "directions.npy"))
        accessibility = np.load(os.path.join(workdir, "accessibility.npy"))

        options = molding.mold_orientation_search(
            directions, accessibility, max_slides=4, min_slide_faces=10)
        z_pair = next(o for o in options if o["pair"] == [4, 5])
        check("internal undercut keeps option infeasible",
              not z_pair["feasible"] and z_pair["counts"]["internal"] > 0,
              f"internal {z_pair['counts']['internal']}")

        band = molding.assignment_band(
            z_pair["pair"], [s["direction"] for s in z_pair["slides"]],
            accessibility)
        centroids = verts[faces].mean(axis=1)
        notch_back = (np.abs(centroids[:, 0] - 7.0) < 0.05) \
            & (np.abs(centroids[:, 1]) < 1.9) & (centroids[:, 2] > -4.9) \
            & (centroids[:, 2] < -3.1)
        check("notch back wall is internal (not slide)",
              int(notch_back.sum()) > 0
              and np.all(band[notch_back] == molding.INTERNAL),
              f"{int(notch_back.sum())} faces")


def cache_round_trip(check):
    with tempfile.TemporaryDirectory() as workdir:
        block = mm.makeCube(mm.Vector3f(20, 20, 10), mm.Vector3f(-10, -10, -5))
        save_workdir(workdir, block, subdivide=2.0)
        pipeline.compute_directions(workdir, count=4, axes=True)

        analysis = processes.get_analysis("injection_molding", "mold_orientation")
        merged = apply_defaults(analysis, {})
        first = analysis.run(workdir, merged, None)
        calls = []
        second = analysis.run(workdir, merged,
                              lambda fraction, message: calls.append(message))
        check("cache round-trip (no recompute)",
              second.stats == first.stats and not calls,
              f"progress calls on 2nd run: {len(calls)}")
        check("simple block is feasible without slides",
              first.stats["options"][0]["feasible"]
              and not first.stats["options"][0]["slides"], "")


def main():
    failures = []
    check = check_factory(failures)
    print("=== fixture A: BREP meshing ===")
    fixture_brep(check)
    print("=== fixture B: side hole ===")
    fixture_side_hole(check)
    print("=== fixture C: internal undercut ===")
    fixture_internal_undercut(check)
    print("=== cache round-trip ===")
    cache_round_trip(check)
    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
