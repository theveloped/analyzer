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

    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
