"""Analytic checks of BREP meshing and the mold membership model.

Fixtures:
A. BREP meshing: an OCC box minus a through-slot round-tripped through STEP
   — exact face counts, weld conformity, tag-preserving subdivision, and the
   BREP edge geometry exported by mesh_part.
B. Side hole (mesh-only): membership bitmasks — slot walls carry only the
   slide bit, outer walls both side bits; a feasible option has no internal
   region.
B2. Side hole (BREP end-to-end): whole-BREP-face validity and defaults, and
   the client parting-line filter replicated over brep_edge_pairs.
C. Internal undercut: a laterally occluded notch has membership 0 and forms
   a numbered internal region.
D. Unit checks of brep_validity / brep_defaults on hand-built arrays.

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
from processes.injection_molding import MOLD_SCHEMA


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


def make_slotted_step(tmp):
    """OCC box minus a through-slot along X, written to a STEP file."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    block = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -5), 20, 20, 10).Shape()
    slot = BRepPrimAPI_MakeBox(gp_Pnt(-12, -2, -2), 24, 4, 4).Shape()
    shape = BRepAlgoAPI_Cut(block, slot).Shape()

    path = os.path.join(tmp, "fixture.step")
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(path)
    return path, shape


def edge_counts(faces):
    e = np.stack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]],
                 axis=1).reshape(-1, 2)
    e = np.sort(e, axis=1)
    _, counts = np.unique(e, axis=0, return_counts=True)
    return counts


def fixture_brep(check):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    with tempfile.TemporaryDirectory() as tmp:
        path, shape = make_slotted_step(tmp)

        n_brep = 0
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            n_brep += 1
            explorer.Next()

        verts, faces, ids, types = brep.mesh_step(path, deflection=0.5)
        check("brep face count matches TopExp",
              len(types) == n_brep and len(np.unique(ids)) == n_brep,
              f"{len(np.unique(ids))} ids / {n_brep} BREP faces")
        check("welded mesh is conformal (watertight)",
              bool(np.all(edge_counts(faces.astype(np.int64)) == 2)), "")

        fine_verts, fine_faces, fine_ids = brep.subdivide_tagged(
            verts, faces, ids, 1.0)
        check("subdivision keeps every BREP id",
              set(np.unique(fine_ids)) == set(np.unique(ids)),
              f"{len(fine_faces)} fine faces")
        check("subdivided mesh stays conformal",
              bool(np.all(edge_counts(fine_faces.astype(np.int64)) == 2)), "")

        # mesh_part exports the BREP edge geometry
        workdir = os.path.join(tmp, "wd")
        os.makedirs(workdir)
        pipeline.mesh_part(path, workdir, subdivide=1.0)
        edges_ok = os.path.exists(os.path.join(workdir, "brep_edges.npy"))
        pairs_ok = os.path.exists(os.path.join(workdir, "brep_edge_pairs.npy"))
        segments = np.load(os.path.join(workdir, "brep_edges.npy"))
        id_pairs = np.load(os.path.join(workdir, "brep_edge_pairs.npy"))
        check("brep edge geometry exported",
              edges_ok and pairs_ok and len(segments) > 0
              and len(segments) == len(id_pairs)
              and bool(np.all(id_pairs[:, 0] != id_pairs[:, 1]))
              and int(id_pairs.max()) < n_brep,
              f"{len(segments)} segments")

    # real STEP: id count matches OCC's face count
    shape = brep.load_step_shape("tests/testpart_42.stp")
    n = sum(1 for _ in brep.iter_faces(shape))
    _, _, ids, types = brep.mesh_step("tests/testpart_42.stp", deflection=0.5)
    check("testpart_42.stp face count", len(types) == n, f"{len(types)} / {n}")


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

        slide_dirs = [s["direction"] for s in z_pair["slides"]]
        membership = molding.membership_field(z_pair["pair"], slide_dirs,
                                              accessibility)
        centroids = verts[faces].mean(axis=1)
        slot_walls = (np.abs(np.abs(centroids[:, 1]) - 2.0) < 0.05) \
            & (np.abs(centroids[:, 2]) < 1.9) & (np.abs(centroids[:, 0]) < 9.5)
        outer_walls = (np.abs(np.abs(centroids[:, 0]) - 10.0) < 0.05) \
            & (np.abs(centroids[:, 2]) < 4.0) & (np.abs(centroids[:, 1]) < 8.0) \
            & (np.abs(centroids[:, 2]) > -4.0)
        check("slot walls carry only the slide bit",
              np.all(membership[slot_walls] == (1 << molding.FEAT_SLIDE_BASE)),
              f"{int(slot_walls.sum())} faces")
        check("outer walls reachable by both sides",
              np.all((membership[outer_walls] & 3) == 3),
              f"{int(outer_walls.sum())} faces")

        pairs, _ = molding.face_adjacency(faces)
        region, counts = molding.internal_regions(membership, pairs, len(faces))
        check("feasible option has no internal region",
              not counts and int(region.max()) == 0, "")


def fixture_side_hole_brep(check):
    with tempfile.TemporaryDirectory() as tmp:
        path, _ = make_slotted_step(tmp)
        workdir = os.path.join(tmp, "wd")
        os.makedirs(workdir)
        pipeline.mesh_part(path, workdir, subdivide=1.0)
        pipeline.compute_directions(workdir, count=4, axes=True)

        result = pipeline.mold_orientation(workdir, max_slides=2, count=10,
                                           field_options=3)
        check("stats carry schema 2", result["stats"]["schema"] == 2
              and result["stats"]["brep"], "")

        options = result["stats"]["options"]
        k, option = next((k, o) for k, o in enumerate(options[:3])
                         if o["pair"] == [4, 5])
        valid = result["arrays"][f"brep_valid_{k}"]
        default = result["arrays"][f"brep_default_{k}"]
        brep_ids = np.load(os.path.join(workdir, "brep_faces.npy"))
        verts = np.load(os.path.join(workdir, "fine_verts.npy"))
        faces = np.load(os.path.join(workdir, "fine_faces.npy"))
        centroids = verts[faces].mean(axis=1)

        def brep_of(mask):
            return np.unique(brep_ids[mask])

        top = brep_of(np.abs(centroids[:, 2] - 5.0) < 0.01)
        bottom = brep_of(np.abs(centroids[:, 2] + 5.0) < 0.01)
        outer = brep_of((np.abs(np.abs(centroids[:, 0]) - 10.0) < 0.01)
                        & (np.abs(centroids[:, 2]) < 4.0))
        slot = brep_of((np.abs(np.abs(centroids[:, 1]) - 2.0) < 0.05)
                       & (np.abs(centroids[:, 2]) < 1.9)
                       & (np.abs(centroids[:, 0]) < 9.5))

        one_side = [bool(bin(int(valid[b]) & 3).count("1") == 1)
                    for b in np.concatenate([top, bottom])]
        check("top/bottom faces valid for exactly one side", all(one_side),
              f"{len(one_side)} faces")
        check("outer walls valid for both sides",
              all((int(valid[b]) & 3) == 3 for b in outer),
              f"{len(outer)} faces")
        check("slot walls valid only for the slide",
              all(int(valid[b]) == (1 << molding.FEAT_SLIDE_BASE) for b in slot)
              and all(default[b] == molding.FEAT_SLIDE_BASE for b in slot),
              f"{len(slot)} faces")
        check("no conflict or internal faces",
              not np.any(default >= molding.DEFAULT_CONFLICT), "")

        # replicate the client parting-line filter over the BREP edges
        id_pairs = np.load(os.path.join(workdir, "brep_edge_pairs.npy"))
        cur = default
        a, b = cur[id_pairs[:, 0]], cur[id_pairs[:, 1]]
        keep = (a != b) & (a < molding.DEFAULT_CONFLICT) & (b < molding.DEFAULT_CONFLICT)
        segments = np.load(os.path.join(workdir, "brep_edges.npy"))[keep]
        check("parting line lands on BREP edges", len(segments) > 0,
              f"{len(segments)} segments")


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

        slide_dirs = [s["direction"] for s in z_pair["slides"]]
        membership = molding.membership_field(z_pair["pair"], slide_dirs,
                                              accessibility)
        centroids = verts[faces].mean(axis=1)
        notch_back = (np.abs(centroids[:, 0] - 7.0) < 0.05) \
            & (np.abs(centroids[:, 1]) < 1.9) & (centroids[:, 2] > -4.9) \
            & (centroids[:, 2] < -3.1)
        check("notch back wall has empty membership",
              int(notch_back.sum()) > 0
              and np.all(membership[notch_back] == 0),
              f"{int(notch_back.sum())} faces")

        pairs, _ = molding.face_adjacency(faces)
        region, counts = molding.internal_regions(membership, pairs, len(faces))
        check("internal region numbered and complete",
              len(counts) >= 1
              and np.all(region[notch_back] == region[notch_back][0])
              and int(region[notch_back][0]) > 0
              and sum(counts) == int((membership == 0).sum()),
              f"{len(counts)} region(s), counts {counts[:4]}")


def fixture_unit(check):
    # 5 brep faces x 2 triangles each; features: A(bit0), B(bit1), slide(bit2)
    brep_ids = np.repeat(np.arange(5, dtype=np.int32), 2)
    membership = np.array([
        1, 1,    # face 0: fully A            -> valid A, default A
        1, 2,    # face 1: half A, half B     -> conflict (no full cover)
        0, 0,    # face 2: unreachable        -> internal
        3, 3,    # face 3: A and B everywhere -> both valid; exclusive tie -> A
        4, 4,    # face 4: slide only         -> default slide
    ], dtype=np.uint32)
    valid = molding.brep_validity(membership, brep_ids, 3)
    default = molding.brep_defaults(membership, valid, brep_ids)

    check("unit: full-A face", valid[0] == 1 and default[0] == molding.FEAT_A, "")
    check("unit: split face is conflict",
          valid[1] == 0 and default[1] == molding.DEFAULT_CONFLICT, "")
    check("unit: unreachable face is internal",
          valid[2] == 0 and default[2] == molding.DEFAULT_INTERNAL, "")
    check("unit: A|B tie goes to A",
          valid[3] == 3 and default[3] == molding.FEAT_A, "")
    check("unit: slide-only face",
          valid[4] == 4 and default[4] == molding.FEAT_SLIDE_BASE, "")

    # B-majority exclusive coverage wins over A
    membership2 = np.array([3, 3, 3, 2], dtype=np.uint32)
    brep_ids2 = np.zeros(4, dtype=np.int32)
    valid2 = molding.brep_validity(membership2, brep_ids2, 2)
    default2 = molding.brep_defaults(membership2, valid2, brep_ids2)
    check("unit: B-exclusive majority wins",
          valid2[0] == 2 and default2[0] == molding.FEAT_B,
          f"valid {valid2[0]} default {default2[0]}")


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
        check("schema and membership fields present",
              first.stats["schema"] == MOLD_SCHEMA
              and "membership_0" in first.fields
              and "internal_region_0" in first.fields, "")
        check("simple block is feasible without slides",
              first.stats["options"][0]["feasible"]
              and not first.stats["options"][0]["slides"], "")


def main():
    failures = []
    check = check_factory(failures)
    print("=== fixture A: BREP meshing ===")
    fixture_brep(check)
    print("=== fixture B: side hole (membership) ===")
    fixture_side_hole(check)
    print("=== fixture B2: side hole (BREP end-to-end) ===")
    fixture_side_hole_brep(check)
    print("=== fixture C: internal undercut ===")
    fixture_internal_undercut(check)
    print("=== fixture D: unit checks ===")
    fixture_unit(check)
    print("=== cache round-trip ===")
    cache_round_trip(check)
    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
