"""Compare the zmap and voxel engines on a mold-like part with draft walls.

Part (top at z=0, approach +Z):
- pocket A: 10x10x6 with vertical 90-degree walls (2D-milled style)
- pocket B: 10x10x6 with 1-degree draft (91-degree walls, mold cavity style)
- both floors blend sharp into the walls

The point of the comparison: near-vertical walls that a tool sweeps with its
side must NOT be flagged by either engine (this is where a vertical-distance
gap metric would over-flag), while the floor-wall fillet bands and floor
corners must be flagged consistently by both. Also reports per-field wall
times for both engines.

Run from the repo root: python benchmark_engines.py [--part PATH.stl]
"""
import os
import sys
import tempfile
import time

import numpy as np
from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn

from analysis import compute_accessibility, get_mesh_data, relax_accessibility
from zmap import DirectionCache

REPO = os.path.dirname(os.path.abspath(__file__))
PIXEL = 0.1


def frustum_pocket(width_bottom, depth, draft_deg, cx, cy):
    """Cutting solid for a pocket with drafted walls (wider at the top)."""
    width_top = width_bottom + 2 * depth * np.tan(np.radians(draft_deg))
    # build as a convex hull of two squares via meshlib boolean-friendly mesh
    wb, wt, d = width_bottom / 2, width_top / 2, depth
    verts = np.array([
        [cx - wb, cy - wb, -d], [cx + wb, cy - wb, -d], [cx + wb, cy + wb, -d], [cx - wb, cy + wb, -d],
        [cx - wt, cy - wt, 1.0], [cx + wt, cy - wt, 1.0], [cx + wt, cy + wt, 1.0], [cx - wt, cy + wt, 1.0],
    ])
    faces = np.array([
        [0, 2, 1], [0, 3, 2],              # bottom
        [4, 5, 6], [4, 6, 7],              # top
        [0, 1, 5], [0, 5, 4],              # sides
        [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6],
        [3, 0, 4], [3, 4, 7],
    ])
    return mn.meshFromFacesVerts(faces, verts)


def make_part():
    block = mm.makeCube(mm.Vector3f(36, 20, 10), mm.Vector3f(-18, -10, -10))
    part = mm.boolean(block, frustum_pocket(10, 6, 0.0, -9, 0), mm.BooleanOperation.DifferenceAB).mesh
    part = mm.boolean(part, frustum_pocket(10, 6, 1.0, 9, 0), mm.BooleanOperation.DifferenceAB).mesh
    subdiv = mm.SubdivideSettings()
    subdiv.maxEdgeLen = 0.6
    subdiv.maxEdgeSplits = 10_000_000
    subdiv.maxDeviationAfterFlip = 0.0
    mm.subdivideMesh(part, subdiv)
    return part


def build_regions(verts, faces):
    centroids = verts[faces].mean(axis=1)
    x, y, z = centroids.T

    def wall_band(cx, draft_deg):
        # mid-height band of the four pocket walls, central parts only: the
        # vertical corner strips are correctly unreachable for any tool whose
        # silhouette is a disk and are checked separately by other tests
        half_mid = 5.0 + 3.0 * np.tan(np.radians(draft_deg))  # half width at z=-3
        lx, ly = np.abs(x - cx), np.abs(y)
        on_wall = (np.abs(np.maximum(lx, ly) - half_mid) < 0.35) & (np.maximum(lx, ly) < half_mid + 0.35)
        away_from_corners = np.minimum(lx, ly) < 2.5
        return np.where(on_wall & away_from_corners & (z > -4.0) & (z < -2.0))[0]

    def floor_edge(cx):
        # floor band along the walls, away from the pocket corners
        lx, ly = np.abs(x - cx), np.abs(y)
        near_one_wall = ((np.abs(lx - 5.0) < 0.4) & (ly < 3.0)) | ((np.abs(ly - 5.0) < 0.4) & (lx < 3.0))
        return np.where((np.abs(z + 6.0) < 0.3) & near_one_wall)[0]

    def floor_center(cx):
        return np.where((np.abs(z + 6.0) < 0.1) & (np.abs(x - cx) < 2.0) & (np.abs(y) < 2.0))[0]

    return {
        "90deg walls": wall_band(-9, 0.0),
        "91deg walls": wall_band(9, 1.0),
        "floor edge 90": floor_edge(-9),
        "floor edge 91": floor_edge(9),
        "floor center 90": floor_center(-9),
        "floor center 91": floor_center(9),
    }


def main():
    failures = []

    def check(name, ok, detail):
        status = "OK " if ok else "FAIL"
        print(f"  [{status}] {name}: {detail}")
        if not ok:
            failures.append(f"{name}: {detail}")

    with tempfile.TemporaryDirectory() as workdir:
        part = make_part()
        verts, faces = get_mesh_data(part)
        print(f"part: {len(faces)} faces")
        np.save(os.path.join(workdir, "fine_verts.npy"), verts)
        np.save(os.path.join(workdir, "fine_faces.npy"), faces)
        directions = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, -1.0]])
        np.save(os.path.join(workdir, "directions.npy"), directions)
        accessibility = compute_accessibility(part, directions, len(faces))
        accessibility[0, :] = relax_accessibility(part, accessibility[0, :], directions[0], tolerance_degrees=1.5, n=8)
        regions = build_regions(verts, faces)
        for name, idx in regions.items():
            print(f"  region {name:16s} {len(idx)} faces")

        tips = [("ball", 4.0, 2.0), ("flat", 4.0, 0.0), ("bull", 4.0, 1.0)]
        results = {}
        timings = {}

        for engine in ["zmap", "voxel"]:
            cache = DirectionCache(workdir, 0, verts=verts, faces=faces, pixel=PIXEL, engine=engine)
            for name, diameter, rc in tips:
                t0 = time.time()
                gap = cache.tip_gap(diameter, rc)
                timings[(engine, f"tip {name}")] = time.time() - t0
                results[(engine, name)] = gap
            t0 = time.time()
            cache.clearance(8.0)
            timings[(engine, "clearance r=8")] = time.time() - t0

        # --- correctness: walls swept by the tool side must stay unflagged.
        # The voxel engine's flat/bull disk emulation has a residual of about
        # 0.41 * (D - 2 rc) / scale, so its threshold is raised accordingly
        # (endmill_flag_threshold) - the zmap engine has no such residual.
        from analysis import endmill_flag_threshold

        def flag_threshold(engine, diameter, rc):
            if engine == "voxel":
                return endmill_flag_threshold(diameter, rc, 0.1, 10.0) * 1.2
            return 0.1

        print("\n=== flagging by region (face = all 3 verts over threshold) ===")
        for engine in ["zmap", "voxel"]:
            for name, diameter, rc in tips:
                gap = results[(engine, name)]
                blocked = gap > flag_threshold(engine, diameter, rc)
                flagged_faces = np.where(blocked[faces].all(axis=1))[0]
                flagged_faces = set(flagged_faces[accessibility[0, flagged_faces]].tolist())
                for region, idx in regions.items():
                    frac = np.mean([i in flagged_faces for i in idx])
                    if "walls" in region or "center" in region:
                        ok, expect = frac < 0.15, "clear"
                    elif name == "flat":
                        ok, expect = frac < 0.15, "clear"
                    else:
                        ok, expect = frac > 0.5, "flagged"
                    check(f"{engine}/{name}/{region}", ok, f"{frac * 100:5.1f}% (expected {expect})")

        # --- classification agreement between engines at their thresholds,
        # on vertices of faces visible from the approach direction (the rest
        # is masked by accessibility in any composition and the engines
        # legitimately describe it differently)
        print("\n=== engine classification agreement (accessible vertices) ===")
        relevant = np.zeros(len(verts), dtype=bool)
        relevant[faces[accessibility[0]].ravel()] = True
        for name, diameter, rc in tips:
            a = results[("zmap", name)] > flag_threshold("zmap", diameter, rc)
            b = results[("voxel", name)] > flag_threshold("voxel", diameter, rc)
            match = np.mean(a[relevant] == b[relevant])
            check(f"agreement/{name}", match > 0.97,
                  f"{match * 100:.2f}% of {relevant.sum()} accessible verts classified identically")

        print("\n=== timings per field ===")
        for name in ["tip ball", "tip flat", "tip bull", "clearance r=8"]:
            tz, tv = timings[("zmap", name)], timings[("voxel", name)]
            print(f"  {name:14s} zmap {tz:7.2f}s   voxel {tv:7.2f}s   speedup x{tv / max(tz, 1e-9):.0f}")

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
