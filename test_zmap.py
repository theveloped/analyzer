"""Validation of the height-map (Z-map) engine against known geometry.

Synthetic part (20x20x10 block, top at z=0, pocket 8x8x5, slot 3 wide x 3
deep); checks, for a D=4 tool from +Z:

- per-region expectations by tip type (ball flags pocket floor edges, flat
  reaches them, everyone flags the slot and the vertical pocket corners)
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
from meshlib import mrmeshpy as mm

from analysis import compute_accessibility, get_mesh_data
from zmap import DirectionCache, compose_unreachable

REPO = os.path.dirname(os.path.abspath(__file__))
PIXEL = 0.05


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

    # the visibility test's angular tolerance makes exactly-vertical walls
    # deterministically front-facing — no cone relaxation needed
    accessibility = compute_accessibility(part, directions, len(faces))
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
# (the bull corner radius is chosen so its fillet band is wider than a face)
CASES = {
    "ball": 2.0,
    "bull": 1.0,
    "flat": 0.0,
}
EXPECTATIONS = {
    "ball": {"pocket floor edge": True, "pocket floor center": False, "pocket vertical corner": True, "slot floor": True, "top face": False},
    "bull": {"pocket floor edge": True, "pocket floor center": False, "pocket vertical corner": True, "slot floor": True, "top face": False},
    "flat": {"pocket floor edge": False, "pocket floor center": False, "pocket vertical corner": True, "slot floor": True, "top face": False},
}


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

        # --- tip fields through the CLI (same expectations as the 3D engine)
        for name, corner_radius in CASES.items():
            cmd = [
                sys.executable, "main.py", "compose", workdir, "0",
                "--diameter", "4.0", "--corner_radius", str(corner_radius),
                "--pixel", str(PIXEL), "--tollerance", "0.1",
            ]
            t0 = time.time()
            res = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=600)
            print(f"=== {name} (corner radius {corner_radius}) via CLI [{time.time() - t0:.1f}s] ===")
            if res.returncode != 0:
                print(res.stderr[-2000:])
                failures.append(f"{name}: CLI failed")
                continue

            with open(os.path.join(workdir, "highlights.json")) as f:
                flagged = set(json.load(f)["faces"])

            for region, should_flag in EXPECTATIONS[name].items():
                idx = regions[region]
                frac = np.mean([i in flagged for i in idx])
                # bull rc=1 leaves a 0.54 mm blocked band at the floor edge -
                # thinner than the 0.8 mm test faces, so only a partial face
                # row can flag (the exact-gap probes cover the band itself)
                lo = 0.2 if (name, region) == ("bull", "pocket floor edge") else 0.5
                ok = frac > lo if should_flag else frac < 0.2
                check(f"{name}/{region}", ok, f"flagged {frac * 100:.1f}% (expected {'flagged' if should_flag else 'clear'})")

        # --- Euclidean gaps against theory at deterministic probe points:
        # distance from a floor point at delta from the wall to the fillet
        # arc a tip of corner radius rc leaves: hypot(delta - rc, rc) - rc
        print("=== Euclidean gaps on the pocket floor ===")
        cache = DirectionCache(workdir, 0, verts=verts, faces=faces, pixel=PIXEL)
        from zmap import close_heightmap, euclidean_gap, project_vertices_float

        probes = np.array([
            [0.0, -3.0, -5.0],   # floor, 1.0 from the wall: inside the ball fillet only
            [0.0, -3.9, -5.0],   # floor, 0.1 from the wall: inside every fillet
            [-3.9, -3.9, -5.0],  # floor at the vertical corner: nothing reaches below the rim
            [0.0, -4.0, -2.5],   # mid-height on the vertical wall: swept by every tool side
        ])
        fx, fy, ph = project_vertices_float(probes, cache.frame)

        def fillet_gap(rc, delta):
            if rc <= 0 or delta >= rc:
                return 0.0
            return float(np.hypot(delta - rc, rc) - rc)

        window_px = max(2, int(np.ceil(cache.window / PIXEL)))
        for name, corner_radius in [("ball", 2.0), ("bull", 1.0), ("flat", 0.0)]:
            closed = close_heightmap(cache.heights, 4.0, corner_radius, PIXEL)
            gaps = euclidean_gap(closed, fx, fy, ph, PIXEL, window_px)
            expected = [fillet_gap(corner_radius, 1.0), fillet_gap(corner_radius, 0.1), 4.5, 0.0]
            for label, gap, want in zip(["floor d=1.0", "floor d=0.1", "corner", "wall mid"], gaps, expected):
                ok = gap > want - 0.15 if label == "corner" else abs(gap - want) < 0.15
                check(f"gap/{name}/{label}", ok, f"gap {gap:.3f} expected {'>' if label == 'corner' else ''}{want:.3f}")

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

        # --- tip-aware stickout: the holder must be evaluated at the tool
        # AXIS, with the tip at its true depth. A ball touching a wall does so
        # with its flank: the tip sits corner_radius below the contact, so the
        # required stickout is depth + rc, not the vertex-centred depth.
        print("=== tip-aware stickout (ball D4 rc2, holder radius 6) ===")
        sreq_ball = cache.min_stickout([(6.0, 0.0)], tip=(4.0, 2.0))
        for label, point, expected in [
            ("pocket wall mid-height", (0.0, -4.0, -2.5), 4.5),  # 2.5 depth + 2.0 rc
            ("pocket floor center", (0.0, 0.0, -5.0), 5.0),      # bottom contact, unchanged
            ("top face", (-8.0, 8.0, 0.0), 0.0),
        ]:
            value = sreq_ball[nearest_vertex(verts, point)]
            ok = abs(value - expected) < 0.2
            check(f"sreq ball/{label}", ok, f"{value:.3f} expected {expected:.3f}")

        naive = cache.min_stickout([(6.0, 0.0)])
        value = naive[nearest_vertex(verts, (0.0, -4.0, -2.5))]
        check("vertex-centred wall estimate (for contrast)", abs(value - 2.5) < 0.2,
              f"{value:.3f} expected 2.5 (underestimates flank contact by rc)")

        # --- the chunked fast path must match the reference offset loop to
        # float32 noise, on the real fixture and on randomized micro maps
        # (borders, brackets outside the map, all-infeasible fallback)
        print("=== tip-aware stickout: fast path vs reference ===")
        from zmap import (FREE_SPACE, _tip_aware_min_stickout_ref,
                          tip_aware_min_stickout)

        worst = 0.0
        clear_map = cache._clearance_map(6.0)
        for tip_d, tip_rc in [(4.0, 2.0), (4.0, 1.0), (4.0, 0.0)]:
            tip_map = cache._tip_map(tip_d, tip_rc)
            ref = _tip_aware_min_stickout_ref(
                tip_map, clear_map, tip_d, tip_rc, PIXEL,
                cache._fx, cache._fy, cache._vheight)
            fast = tip_aware_min_stickout(
                tip_map, clear_map, tip_d, tip_rc, PIXEL,
                cache._fx, cache._fy, cache._vheight)
            worst = max(worst, float(np.abs(ref - fast).max()))
        check("fast stickout matches reference", worst <= 1e-3,
              f"max |delta| = {worst:.2e}")

        rng = np.random.default_rng(11)
        micro_worst = 0.0
        for _ in range(20):
            heights_r = rng.uniform(-30.0, 30.0, size=(36, 48))
            heights_r[rng.random((36, 48)) < 0.15] = FREE_SPACE
            tip_r = heights_r + rng.uniform(0.0, 3.0, size=(36, 48))
            clear_r = tip_r + rng.uniform(0.0, 20.0, size=(36, 48))
            fx_r = rng.uniform(-2.0, 50.0, size=400)
            fy_r = rng.uniform(-2.0, 38.0, size=400)
            h_r = rng.uniform(-32.0, 32.0, size=400)
            for rc in (0.0, 1.0, 2.5):
                ref = _tip_aware_min_stickout_ref(
                    tip_r, clear_r, 5.0, rc, 0.1, fx_r, fy_r, h_r)
                fast = tip_aware_min_stickout(
                    tip_r, clear_r, 5.0, rc, 0.1, fx_r, fy_r, h_r)
                scale = np.maximum(np.abs(ref), 1.0)  # FREE_SPACE magnitudes
                micro_worst = max(
                    micro_worst, float((np.abs(ref - fast) / scale).max()))
        check("fast stickout micro A/B (60 cases)", micro_worst <= 1e-4,
              f"max relative delta = {micro_worst:.2e}")

        # --- a tool assembled from cached fields (new stickout, new holder
        # stack) must be near-instant: pure numpy over stored scalars. The
        # per-(tip, radius) stickout fields are part of the cache, so warm
        # them first (precompute does this) and time the composition itself.
        cache.min_stickout([(6.0, 0.0), (9.0, 15.0)], tip=(4.0, 1.0))
        t0 = time.time()
        compose_unreachable(cache, faces, 4.0, 1.0, 0.1, stickout=25.0,
                            cylinders=[(6.0, 0.0), (9.0, 15.0)])
        dt = time.time() - t0
        check("cached compose speed", dt < 1.0, f"{dt * 1000:.0f} ms")

        # --- coarse-pixel wall guarantee: the exact-gap window must cover
        # the wall threshold (2.5 px) at ANY pixel size, so wall verdicts
        # stay bimodal (reachable ~ 0, blocked whole millimetres) instead of
        # speckling when the analysis resolution is coarse. This goes last:
        # the coarse cache overwrites the tempdir's zcache file.
        print("=== coarse-pixel wall verdicts (pixel 0.25) ===")
        coarse_pixel = 0.25
        coarse = DirectionCache(workdir, 0, verts=verts, faces=faces,
                                pixel=coarse_pixel)
        check("window floored to cover the wall threshold",
              coarse.window >= 3.0 * coarse_pixel - 1e-12,
              f"window {coarse.window:.3f} >= {3.0 * coarse_pixel:.3f}")
        gap_coarse = coarse.tip_gap(4.0, 0.0)
        wall_thr = max(0.1, 2.5 * coarse_pixel)
        wall_gap = gap_coarse[nearest_vertex(verts, (0.0, -4.0, -2.5))]
        check("coarse: open pocket wall reads reachable",
              wall_gap <= wall_thr,
              f"gap {wall_gap:.3f} <= wall threshold {wall_thr:.3f}")
        slot_gap = gap_coarse[nearest_vertex(verts, (6.5, 0.0, -3.0))]
        check("coarse: too-narrow slot stays blocked",
              slot_gap > wall_thr,
              f"gap {slot_gap:.3f} > wall threshold {wall_thr:.3f}")

    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
