"""Validation of the height-map (Z-map) engine against known geometry.

Reuses the synthetic part from test_endmill.py (pocket + narrow slot) and
checks, for a D=4 tool from +Z:

- the same per-region expectations as the 3D voxel engine (ball flags pocket
  floor edges, flat reaches them, everyone flags the slot and the vertical
  pocket corners)
- gap magnitudes against theory: ball leaves r*(sqrt(2)-1) = 0.83 at the
  floor edge, bull rc=0.5 leaves 0.21, flat leaves ~0
- holder clearance: a cylinder of radius 6 above the tool needs stickout >= 5
  over the pocket floor (pocket depth) and >= 3 over the slot floor

Run from the repo root: python test_zmap.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

from test_endmill import CASES, build_regions, prepare_workdir
from zmap import DirectionCache, compose_unreachable

REPO = os.path.dirname(os.path.abspath(__file__))
PIXEL = 0.05


def nearest_vertex(verts, point):
    return int(np.argmin(np.linalg.norm(verts - np.asarray(point), axis=1)))


def main():
    failures = []

    def check(name, ok, detail):
        status = "OK " if ok else "FAIL"
        print(f"  [{status}] {name}: {detail}")
        if not ok:
            failures.append(f"{name}: {detail}")

    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir)
        regions = build_regions(verts, faces)
        accessibility = np.load(os.path.join(workdir, "accessibility.npy"))

        # --- tip fields through the CLI (same expectations as the 3D engine);
        # the voxel engine's --length/--holder_diameter cases translate to
        # compose's --stickout/--holder (one cylinder from the tip)
        for name, (corner_radius, extra_args, expectations) in CASES.items():
            cmd = [
                sys.executable, "main.py", "compose", workdir, "0",
                "--diameter", "4.0", "--corner_radius", str(corner_radius),
                "--pixel", str(PIXEL), "--tollerance", "0.1",
            ]
            if "--length" in extra_args:
                stickout = extra_args[extra_args.index("--length") + 1]
                holder_radius = float(extra_args[extra_args.index("--holder_diameter") + 1]) / 2.0
                cmd += ["--stickout", stickout, "--holder", f"{holder_radius:g}:0"]
            t0 = time.time()
            res = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=600)
            print(f"=== {name} (corner radius {corner_radius}) via CLI [{time.time() - t0:.1f}s] ===")
            if res.returncode != 0:
                print(res.stderr[-2000:])
                failures.append(f"{name}: CLI failed")
                continue

            with open(os.path.join(workdir, "highlights.json")) as f:
                flagged = set(json.load(f)["faces"])

            for region, should_flag in expectations.items():
                idx = regions[region]
                frac = np.mean([i in flagged for i in idx])
                ok = frac > 0.5 if should_flag else frac < 0.2
                check(f"{name}/{region}", ok, f"flagged {frac * 100:.1f}% (expected {'flagged' if should_flag else 'clear'})")

        # --- Euclidean gaps against theory at deterministic probe points:
        # distance from a floor point at delta from the wall to the fillet
        # arc a tip of corner radius rc leaves: hypot(delta - rc, rc) - rc
        print("=== Euclidean gaps on the pocket floor ===")
        cache = DirectionCache(workdir, 0, verts=verts, faces=faces, pixel=PIXEL)
        from zmap import close_heightmap, euclidean_gap, project_vertices, tip_profile

        probes = np.array([
            [0.0, -3.0, -5.0],   # floor, 1.0 from the wall: inside the ball fillet only
            [0.0, -3.9, -5.0],   # floor, 0.1 from the wall: inside every fillet
            [-3.9, -3.9, -5.0],  # floor at the vertical corner: inside the corner wedge
            [0.0, -4.0, -2.5],   # mid-height on the vertical wall: swept by every tool side
        ])
        fx, fy, ph = project_vertices(probes, cache.frame)

        def fillet_gap(rc, delta):
            if rc <= 0 or delta >= rc:
                return 0.0
            return float(np.hypot(delta - rc, rc) - rc)

        # the window covers the tool radius so gaps are true distances to the
        # nearest tool-reachable surface even deep inside unreachable wedges:
        # the corner probe reads its LATERAL distance to the machined arc
        # (sqrt(2)*3.9 - (sqrt(2) - 1)*rc - 2 for the wedge geometry, always
        # well below the pocket depth), not the vertical distance to the rim
        window_px = max(2, int(np.ceil(2.0 / PIXEL)) + 2)
        for name, corner_radius in [("ball", 2.0), ("bull", 1.0), ("flat", 0.0)]:
            footprint, profile = tip_profile(4.0, corner_radius, PIXEL)
            closed = close_heightmap(cache.heights, footprint, profile)
            gaps = euclidean_gap(closed, fx, fy, ph, PIXEL, window_px)
            expected = [fillet_gap(corner_radius, 1.0), fillet_gap(corner_radius, 0.1), None, 0.0]
            for label, gap, want in zip(["floor d=1.0", "floor d=0.1", "corner", "wall mid"], gaps, expected):
                if label == "corner":
                    ok = 0.2 < gap < 1.6
                    check(f"gap/{name}/{label}", ok, f"gap {gap:.3f} expected in (0.2, 1.6): wedge distance, far below the pocket depth")
                else:
                    ok = abs(gap - want) < 0.15
                    check(f"gap/{name}/{label}", ok, f"gap {gap:.3f} expected {want:.3f}")

        # --- holder clearance and stickout sweep (pure numpy on cached fields)
        print("=== holder clearance (cylinder radius 6 from the tip) ===")
        min_stick = cache.min_stickout([(6.0, 0.0)])
        for label, point, expected in [
            ("pocket floor", (0.0, 0.0, -5.0), 5.0),
            ("slot floor", (6.5, 0.0, -3.0), 3.0),
            ("top face", (-8.0, 8.0, 0.0), 0.0),
        ]:
            value = min_stick[nearest_vertex(verts, point)]
            ok = abs(value - expected) < 0.15
            check(f"min_stickout/{label}", ok, f"{value:.3f} expected {expected:.3f}")

        floor_faces = regions["pocket floor center"]
        for stickout, should_flag in [(4.0, True), (6.0, False)]:
            unreachable, _, _ = compose_unreachable(
                cache, faces, 4.0, 0.0, 0.1,
                stickout=stickout, cylinders=[(6.0, 0.0)],
            )
            unreachable = unreachable[accessibility[0, unreachable]]
            flagged = set(unreachable.tolist())
            frac = np.mean([i in flagged for i in floor_faces])
            ok = frac > 0.8 if should_flag else frac < 0.2
            check(f"stickout {stickout}", ok, f"pocket floor flagged {frac * 100:.1f}% (expected {'flagged' if should_flag else 'clear'})")

        # --- a tool assembled from cached fields (new stickout, new holder
        # stack) must be near-instant: pure numpy over stored scalars
        t0 = time.time()
        compose_unreachable(cache, faces, 4.0, 1.0, 0.1, stickout=25.0,
                            cylinders=[(6.0, 0.0), (9.0, 15.0)])
        dt = time.time() - t0
        check("cached compose speed", dt < 1.0, f"{dt * 1000:.0f} ms")

    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
