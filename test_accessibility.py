"""Analytic checks of the visibility-based accessibility computation.

Reuses the synthetic pocket + slot part from test_endmill (20x20x10 block,
top at z=0, pocket 8x8x5, slot 3 wide x 3 deep). From +Z:
- top face, pocket floor and slot floor are fully visible
- the pocket's vertical walls classify UNIFORMLY accessible — the speckle
  regression this module exists for (meshlib's undercut verdict flips on
  tangent faces; our visibility test must not)
- the part's bottom face is back-facing and inaccessible
- away from tangency (>5 deg), the verdict agrees with the legacy meshlib
  undercut path

Run from the repo root: python test_accessibility.py
"""
import sys

import numpy as np

from analysis import (compute_accessibility, compute_accessibility_meshlib,
                      get_mesh_data)
from test_endmill import make_part


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

    # A/B sanity: away from tangency both methods agree
    legacy = compute_accessibility_meshlib(part, directions, len(faces))[0]
    centroids = verts[faces].mean(axis=1)
    tri = verts[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-30)
    angles = np.degrees(np.arccos(np.clip(normals @ directions[0], -1, 1)))
    off_tangent = np.abs(angles - 90.0) > 5.0
    agree = (access[off_tangent] == legacy[off_tangent]).mean()
    check("agrees with meshlib off-tangent", agree > 0.999,
          f"agreement {agree * 100:6.2f}% over {int(off_tangent.sum())} faces")

    # and the legacy path really does speckle on the walls (documents why
    # this module exists; if meshlib ever fixes it this check tells us)
    legacy_frac = legacy[walls].mean()
    print(f"  [info] legacy meshlib wall verdict: {legacy_frac * 100:5.1f}% accessible")

    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
