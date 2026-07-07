"""End-to-end test of the unified `endmill` command on synthetic geometry.

Part (20x20x10 block, top at z=0):
- pocket 8x8x5 centered at origin -> a D=4 tool fits; a ball nose rounds the
  floor edges while a flat endmill reaches them; every tip type rounds the
  vertical pocket corners (the silhouette is a disk for all of them)
- slot 3 wide, full length in y, 3 deep -> a D=4 tool cannot enter, so every
  tip type must flag the slot floor

Run from the repo root: python test_endmill.py
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm

from analysis import compute_accessibility, get_mesh_data, relax_accessibility

REPO = os.path.dirname(os.path.abspath(__file__))


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


def prepare_workdir(workdir):
    part = make_part()
    verts, faces = get_mesh_data(part)
    np.save(os.path.join(workdir, "fine_verts.npy"), verts)
    np.save(os.path.join(workdir, "fine_faces.npy"), faces)

    directions = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, -1.0]])
    np.save(os.path.join(workdir, "directions.npy"), directions)

    accessibility = compute_accessibility(part, directions, len(faces))

    # vertical walls are tangent to the approach direction and flip between
    # accessible/undercut numerically; relax with a small cone (like
    # `directions --relax`) so they count as accessible
    for i in range(directions.shape[0]):
        accessibility[i, :] = relax_accessibility(part, accessibility[i, :], directions[i], tolerance_degrees=1.0, n=8)
    np.save(os.path.join(workdir, "accessibility.npy"), accessibility)

    return verts, faces


def build_regions(verts, faces):
    centroids = verts[faces].mean(axis=1)
    return {
        # midpoints of the pocket floor/wall edges, away from the corners
        "pocket floor edge": np.where(
            (np.abs(centroids[:, 2] + 5.0) < 0.4)
            & (np.abs(np.abs(centroids[:, 1]) - 4.0) < 0.4)
            & (np.abs(centroids[:, 0]) < 2.0)
        )[0],
        "pocket floor center": np.where(
            (np.abs(centroids[:, 2] + 5.0) < 0.1)
            & (np.abs(centroids[:, 0]) < 2.0)
            & (np.abs(centroids[:, 1]) < 2.0)
        )[0],
        # vertical corner edge of the pocket at mid height
        "pocket vertical corner": np.where(
            (np.abs(centroids[:, 0] + 4.0) < 0.5)
            & (np.abs(centroids[:, 1] + 4.0) < 0.5)
            & (centroids[:, 2] > -4.0)
            & (centroids[:, 2] < -1.0)
        )[0],
        "slot floor": np.where(
            (np.abs(centroids[:, 2] + 3.0) < 0.1)
            & (centroids[:, 0] > 5.5)
            & (centroids[:, 0] < 7.5)
            & (np.abs(centroids[:, 1]) < 8.0)
        )[0],
        "top face": np.where(
            (np.abs(centroids[:, 2]) < 0.1)
            & (centroids[:, 0] < 4.5)
            & (centroids[:, 0] > -9.0)
            & (np.abs(centroids[:, 1]) > 5.0)
        )[0],
    }


# tool tip type -> corner radius, region -> should be flagged
CASES = {
    "ball": 2.0,
    "bull": 0.5,
    "flat": 0.0,
}
EXPECTATIONS = {
    "ball": {"pocket floor edge": True, "pocket floor center": False, "pocket vertical corner": True, "slot floor": True, "top face": False},
    "bull": {"pocket floor edge": True, "pocket floor center": False, "pocket vertical corner": True, "slot floor": True, "top face": False},
    "flat": {"pocket floor edge": False, "pocket floor center": False, "pocket vertical corner": True, "slot floor": True, "top face": False},
}


def main():
    failures = []
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir)
        regions = build_regions(verts, faces)

        for name, corner_radius in CASES.items():
            cmd = [
                sys.executable, "main.py", "endmill", workdir, "0",
                "--diameter", "4.0", "--corner_radius", str(corner_radius), "--tollerance", "0.1",
            ]
            print(f"=== {name} (corner radius {corner_radius}) ===")
            res = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=1200)
            if res.returncode != 0:
                print(res.stderr[-2000:])
                failures.append(f"{name}: CLI failed")
                continue

            with open(os.path.join(workdir, "highlights.json")) as f:
                flagged = set(json.load(f)["faces"])

            for region, should_flag in EXPECTATIONS[name].items():
                idx = regions[region]
                frac = np.mean([i in flagged for i in idx])
                ok = frac > 0.5 if should_flag else frac < 0.2
                status = "OK " if ok else "FAIL"
                print(f"  [{status}] {region:24s} flagged {frac * 100:5.1f}%  (expected {'flagged' if should_flag else 'clear'})")
                if not ok:
                    failures.append(f"{name}/{region}: {frac:.2f} expected {'>0.5' if should_flag else '<0.2'}")

    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
