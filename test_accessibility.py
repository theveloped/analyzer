"""Analytic checks of the visibility-based accessibility computation.

Synthetic pocket + slot part (20x20x10 block, top at z=0, pocket 8x8x5,
slot 3 wide x 3 deep). From +Z:
- top face, pocket floor and slot floor are fully visible
- the pocket's vertical walls classify UNIFORMLY accessible — the speckle
  regression this module exists for (a hard front/back verdict flips on
  tangent faces; our visibility test must not)
- the part's bottom face is back-facing and inaccessible

Run from the repo root: python test_accessibility.py
"""
import os
import sys

import numpy as np
from meshlib import mrmeshpy as mm

from analysis import compute_accessibility, get_mesh_data


def make_part():
    block = mm.makeCube(mm.Vector3f(20, 20, 10), mm.Vector3f(-10, -10, -10))
    pocket = mm.makeCube(mm.Vector3f(8, 8, 6), mm.Vector3f(-4, -4, -5))
    part = mm.boolean(block, pocket, mm.BooleanOperation.DifferenceAB).mesh
    slot = mm.makeCube(mm.Vector3f(3, 22, 4), mm.Vector3f(5, -11, -3))
    part = mm.boolean(part, slot, mm.BooleanOperation.DifferenceAB).mesh

    # refine so faces are small enough to localize results
    subdiv = mm.SubdivideSettings()
    subdiv.maxEdgeLen = 0.8
    subdiv.maxEdgeSplits = 10_000_000
    subdiv.maxDeviationAfterFlip = 0.0
    mm.subdivideMesh(part, subdiv)
    return part


def build_regions(verts, faces):
    centroids = verts[faces].mean(axis=1)
    return {
        "top face": (np.abs(centroids[:, 2]) < 0.1)
                    & (centroids[:, 0] < 4.5) & (centroids[:, 0] > -9.0)
                    & (np.abs(centroids[:, 1]) > 5.0),
        "pocket floor": (np.abs(centroids[:, 2] + 5.0) < 0.1)
                        & (np.abs(centroids[:, 0]) < 3.0)
                        & (np.abs(centroids[:, 1]) < 3.0),
        "slot floor": (np.abs(centroids[:, 2] + 3.0) < 0.1)
                      & (centroids[:, 0] > 5.5) & (centroids[:, 0] < 7.5)
                      & (np.abs(centroids[:, 1]) < 8.0),
        # all four pocket walls at mid height, away from corners and edges
        "pocket walls": ((np.abs(np.abs(centroids[:, 0]) - 4.0) < 0.05)
                         & (np.abs(centroids[:, 1]) < 3.0)
                         | (np.abs(np.abs(centroids[:, 1]) - 4.0) < 0.05)
                         & (np.abs(centroids[:, 0]) < 3.0))
                        & (centroids[:, 2] > -4.0) & (centroids[:, 2] < -1.0),
        "bottom face": np.abs(centroids[:, 2] + 10.0) < 0.05,
    }


def write_step(path, shape):
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    writer.Write(path)
    return path


def wall_angles(workdir, kind, direction):
    """Angles (deg) between the stored normals of `kind`-type BREP faces and
    `direction` — exact BREP normals via pipeline.load_face_normals."""
    import json
    import pipeline

    brep_ids = np.load(os.path.join(workdir, "brep_faces.npy"))
    with open(os.path.join(workdir, "brep_meta.json")) as f:
        types = json.load(f)["surface_types"]
    normals = pipeline.load_face_normals(workdir).astype(np.float64)
    wall = np.isin(brep_ids, [i for i, t in enumerate(types) if t == kind])
    dots = normals[wall] @ np.asarray(direction, dtype=np.float64)
    return np.degrees(np.arccos(np.clip(dots, -1, 1))), wall


def fixture_brep_curved(check):
    """Curved STEP faces classify by their exact surface normal: a vertical
    cylinder wall is exactly 90 deg (uniformly accessible from +-Z) and a
    drafted cone wall sits exactly at 90 - draft — the facet normals the
    coarse tessellation froze into the mesh are off by degrees."""
    import tempfile

    import pipeline
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder

    z = [0.0, 0.0, 1.0]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_step(os.path.join(tmp, "cylinder.stp"),
                          BRepPrimAPI_MakeCylinder(6.0, 20.0).Shape())
        wd = os.path.join(tmp, "cyl")
        os.makedirs(wd)
        pipeline.mesh_part(path, wd, resolution=0.5)

        angles, wall = wall_angles(wd, "cylinder", z)
        check("cylinder wall exactly 90 deg",
              float(np.abs(angles - 90.0).max()) < 1e-3,
              f"max |angle-90| = {np.abs(angles - 90.0).max():.2e} deg "
              f"over {int(wall.sum())} faces")

        pipeline.compute_directions(wd, count=2, axes=True)
        access = np.load(os.path.join(wd, "accessibility.npy"))
        up, down = access[4][wall], access[5][wall]
        check("cylinder wall uniformly visible from +-Z",
              bool(up.all() and down.all()),
              f"+Z {up.mean() * 100:.1f}%  -Z {down.mean() * 100:.1f}%")

    with tempfile.TemporaryDirectory() as tmp:
        # base R=8 at z=0, top R=6 at z=20 -> draft atan(0.1), outward
        # normals tilt up: angle to +Z is exactly 90 - draft
        draft = np.degrees(np.arctan((8.0 - 6.0) / 20.0))
        path = write_step(os.path.join(tmp, "cone.stp"),
                          BRepPrimAPI_MakeCone(8.0, 6.0, 20.0).Shape())
        wd = os.path.join(tmp, "cone")
        os.makedirs(wd)
        pipeline.mesh_part(path, wd, resolution=0.5)

        angles, wall = wall_angles(wd, "cone", z)
        check("cone wall exactly at 90 - draft",
              float(np.abs(angles - (90.0 - draft)).max()) < 1e-3,
              f"draft {draft:.3f} deg, max err "
              f"{np.abs(angles - (90.0 - draft)).max():.2e} deg "
              f"over {int(wall.sum())} faces")


def fixture_fillet_coarse(check):
    """Convex outer fillets must be fully accessible from the pull axis at
    the default coarse resolution (0.5 mm). Regression for chord-recession
    self-shadowing: the zmap height map is rendered from chord facets that
    sit up to `deflection` inside the true surface, so near-tangent fillet
    triangles read occluded at 0.5 mm (and only meshing at 0.1 mm hid it).
    Geometry: box 20x20x10 (top at z=0) with r=2 fillets on the four top
    edges — four cylinder faces plus four sphere corner patches."""
    import json
    import tempfile

    import molding
    import pipeline
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    box = BRepPrimAPI_MakeBox(gp_Pnt(-10.0, -10.0, -10.0), 20.0, 20.0,
                              10.0).Shape()
    edges = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(box, TopAbs_EDGE, edges)
    fillet = BRepFilletAPI_MakeFillet(box)
    added = 0
    for i in range(1, edges.Extent() + 1):
        edge = TopoDS.Edge_s(edges.FindKey(i))
        curve = BRepAdaptor_Curve(edge)
        mid = curve.Value(0.5 * (curve.FirstParameter()
                                 + curve.LastParameter()))
        if mid.Z() > -0.5:  # the four edges of the top face
            fillet.Add(2.0, edge)
            added += 1
    check("fillet fixture: four top edges rounded", added == 4,
          f"{added} edges")

    with tempfile.TemporaryDirectory() as tmp:
        path = write_step(os.path.join(tmp, "rounded.stp"), fillet.Shape())
        wd = os.path.join(tmp, "wd")
        os.makedirs(wd)
        pipeline.mesh_part(path, wd, resolution=0.5)
        pipeline.compute_directions(wd, count=2, axes=True)

        brep_ids = np.load(os.path.join(wd, "brep_faces.npy"))
        with open(os.path.join(wd, "brep_meta.json")) as f:
            types = json.load(f)["surface_types"]
        rounded = np.isin(brep_ids, [i for i, t in enumerate(types)
                                     if t in ("cylinder", "sphere")])
        access = np.load(os.path.join(wd, "accessibility.npy"))

        frac = access[4][rounded].mean()
        check("fillets fully visible from +Z at 0.5 mm", frac == 1.0,
              f"accessible {frac * 100:6.2f}%  "
              f"faces {int(rounded.sum())}")

        # the mold amplification: one bad triangle flips a whole BREP face
        membership = molding.membership_field([4, 5], [], access)
        valid = molding.brep_validity(membership, brep_ids, 2)
        default = molding.brep_defaults(membership, valid, brep_ids)
        bad = np.flatnonzero(default >= molding.DEFAULT_CONFLICT)
        check("no conflict/internal BREP faces for the +-Z pull",
              bad.size == 0,
              f"{bad.size} of {len(default)} faces flipped "
              f"({[types[b] for b in bad[:6]]})")


def fixture_flush_ledge(check):
    """A tangent wall next to a flush overhanging ledge must stay visible.

    The height map samples rays at pixel centers, so an edge-on wall is
    invisible to its own raster column — the column reads whatever surface
    lies at that pixel center instead. When a down-facing ledge starts
    (sub-pixel) flush with the wall, the one-pixel lateral push lands the
    occlusion sample under the ledge and falsely occludes the wall, even
    though the wall's own column proves the corridor is open (regression:
    tangent slivers on outer corner rounds flipping whole BREP faces in the
    mold view at coarse resolution). A wall under a genuinely wide overhang
    must still read occluded.
    """
    from meshlib import mrmeshnumpy as mn

    from zmap import face_visibility

    pixel = 0.1

    verts = []
    faces = []
    normals = []

    def quad(p0, p1, p2, p3, normal):
        base = len(verts)
        verts.extend([p0, p1, p2, p3])
        faces.append([base, base + 1, base + 2])
        faces.append([base, base + 2, base + 3])
        normals.extend([normal, normal])
        return [len(faces) - 2, len(faces) - 1]

    # back plate at z=0 behind the walls (their own columns read this)
    quad([-5, -5, 0], [5, -5, 0], [5, 0, 0], [-5, 0, 0], [0, 0, -1])
    # flush ledge in front of wall 1 (z=-3, closer to the -Z viewer),
    # starting 0.03 mm outward of the wall plane — sub-pixel flush
    quad([-5, 0.03, -3], [0, 0.03, -3], [0, 5, -3], [-5, 5, -3], [0, 0, -1])
    # wide overhang covering wall 2's own column and both pushes
    quad([1, -1, -3], [5, -1, -3], [5, 5, -3], [1, 5, -3], [0, 0, -1])

    # wall 1 (flush case, must be visible): tangent to -Z, at y=0
    w1 = quad([-4, 0, -2], [-1, 0, -2], [-1, 0, 0], [-4, 0, 0], [0, 1, 0])
    # wall 2 (genuinely occluded, must stay occluded)
    w2 = quad([1.5, 0, -2], [3.5, 0, -2], [3.5, 0, 0], [1.5, 0, 0], [0, 1, 0])

    verts = np.asarray(verts, dtype=np.float32)
    faces = np.asarray(faces, dtype=np.int32)
    normals = np.asarray(normals, dtype=np.float64)
    mesh = mn.meshFromFacesVerts(faces, verts)

    vis = face_visibility(mesh, verts, faces, np.array([0.0, 0.0, -1.0]),
                          pixel=pixel, normals=normals)
    check("flush-ledge wall visible from -Z",
          bool(vis[w1].all()), f"visible {vis[w1].tolist()}")
    check("wall under wide overhang stays occluded",
          bool(~vis[w2].any()), f"visible {vis[w2].tolist()}")


def main():
    failures = []
    part = make_part()
    verts, faces = get_mesh_data(part)
    regions = build_regions(verts, faces)
    directions = np.array([[0.0, 0.0, 1.0]])

    access = compute_accessibility(part, directions, len(faces), pixel=0.1)[0]

    def check(name, condition, detail):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:28s} {detail}")
        if not condition:
            failures.append(name)

    for region, expected in [("top face", True), ("pocket floor", True),
                             ("slot floor", True), ("bottom face", False)]:
        mask = regions[region]
        frac = access[mask].mean()
        check(f"{region} {'accessible' if expected else 'inaccessible'}",
              frac == (1.0 if expected else 0.0),
              f"accessible {frac * 100:5.1f}%  faces {int(mask.sum())}")

    # the speckle regression: vertical walls must be uniform (and accessible,
    # nothing overhangs an open pocket)
    walls = regions["pocket walls"]
    frac = access[walls].mean()
    check("pocket walls uniform", frac in (0.0, 1.0),
          f"accessible {frac * 100:5.1f}%  faces {int(walls.sum())}")
    check("pocket walls accessible", frac == 1.0, "")

    print("=== exact BREP normals on curved STEP faces ===")
    fixture_brep_curved(check)

    print("=== coarse-resolution fillet accessibility ===")
    fixture_fillet_coarse(check)

    print("=== flush ledge / tangent wall ===")
    fixture_flush_ledge(check)

    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
