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

    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
