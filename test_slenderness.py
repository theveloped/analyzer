"""Pocket slenderness ladder vs analytic expectations.

Synthetic height map with a deep narrow pocket (2 mm wide, 8 mm deep ->
true depth/width ratio 4) and a wide shallow pocket (10 mm wide, 2 mm deep
-> ratio 0.2). slenderness_ladder must read each pocket's depth divided by
the first ladder diameter that no longer fits it (the geometric-ladder
approximation of the true ratio), pocket walls must ramp with depth, and
open surfaces / rims must stay at zero.

Plain script, not pytest: python test_slenderness.py
"""
import sys
import time

import numpy as np

from zmap import close_heightmap, slenderness_ladder

PIXEL = 0.1
TOP = 10.0
DEEP_W, DEEP_H = 2.0, 8.0   # px 80..99
WIDE_W, WIDE_H = 10.0, 2.0  # px 240..339
MAX_DIAMETER = 12.0

failures = []


def check(name, ok, detail):
    status = "OK " if ok else "FAIL"
    print(f"  [{status}] {name}: {detail}")
    if not ok:
        failures.append(f"{name}: {detail}")


def first_blocked(diameters, width_px):
    """First ladder diameter whose discrete disk no longer fits a pocket of
    width_px pixels (footprint 2n+1 px vs the pocket's width)."""
    for d in diameters:
        if 2 * int(round(0.5 * d / PIXEL)) + 1 > width_px:
            return d
    return None


def main():
    heights = np.full((400, 400), TOP, dtype=np.float32)
    heights[80:100, 80:100] = TOP - DEEP_H
    heights[240:340, 240:340] = TOP - WIDE_H

    verts = [
        # (name, fx, fy, height)
        ("deep floor center", 90.0, 90.0, TOP - DEEP_H),
        ("deep wall 1mm", 80.0, 90.0, TOP - 1.0),
        ("deep wall 4mm", 80.0, 90.0, TOP - 4.0),
        ("deep wall 6mm", 80.0, 90.0, TOP - 6.0),
        ("deep wall 8mm", 80.0, 90.0, TOP - DEEP_H),
        ("wide floor center", 290.0, 290.0, TOP - WIDE_H),
        ("top face", 150.0, 350.0, TOP),
        ("deep rim corner", 78.0, 78.0, TOP),
    ]
    fx = np.array([v[1] for v in verts])
    fy = np.array([v[2] for v in verts])
    vh = np.array([v[3] for v in verts])

    t0 = time.time()
    ratio, width, diameters = slenderness_ladder(heights, fx, fy, vh,
                                                 PIXEL, MAX_DIAMETER)
    ladder_s = time.time() - t0
    t0 = time.time()
    close_heightmap(heights, MAX_DIAMETER, 0.0, PIXEL)
    single_s = time.time() - t0
    print(f"=== ladder of {len(diameters)} diameters "
          f"({', '.join(f'{d:g}' for d in diameters)}) in {ladder_s:.2f}s "
          f"vs one D{MAX_DIAMETER:g} closing in {single_s:.2f}s ===")

    values = {name: (float(ratio[i]), float(width[i]))
              for i, (name, *_rest) in enumerate(verts)}

    d_deep = first_blocked(diameters, 20)
    d_wide = first_blocked(diameters, 100)
    check("ladder covers deep pocket", d_deep is not None
          and d_deep <= DEEP_W * 1.5 + 2 * PIXEL,
          f"first blocked diameter {d_deep} for W={DEEP_W}")
    check("ladder covers wide pocket", d_wide is not None
          and d_wide <= WIDE_W * 1.5 + 2 * PIXEL,
          f"first blocked diameter {d_wide} for W={WIDE_W}")

    r, w = values["deep floor center"]
    expected = DEEP_H / d_deep
    check("deep floor ratio", abs(r - expected) < 0.02,
          f"{r:.3f} (expected {expected:.3f}, true {DEEP_H / DEEP_W:.2f})")
    check("deep floor ratio >= ladder bound",
          r >= (DEEP_H / DEEP_W) / 1.5 - 1e-6,
          f"{r:.3f} >= {(DEEP_H / DEEP_W) / 1.5:.3f}")
    check("deep floor critical width", abs(w - d_deep) < 1e-6,
          f"{w:.2f} (expected {d_deep:g})")

    r, w = values["wide floor center"]
    expected = WIDE_H / d_wide
    check("wide floor ratio", abs(r - expected) < 0.02,
          f"{r:.3f} (expected {expected:.3f}, true {WIDE_H / WIDE_W:.2f})")
    check("wide pocket not slender", r < 0.5, f"{r:.3f} < 0.5")

    wall = [values[f"deep wall {d}mm"][0] for d in (1, 4, 6, 8)]
    check("wall ratio ramps with depth",
          all(a < b for a, b in zip(wall, wall[1:])),
          " -> ".join(f"{v:.3f}" for v in wall))
    for depth, r in zip((1, 4, 6, 8), wall):
        expected = depth / d_deep
        check(f"wall ratio at {depth}mm", abs(r - expected) < 0.05,
              f"{r:.3f} (expected {expected:.3f})")

    r, _ = values["top face"]
    check("open top face", r == 0.0, f"{r:.3f}")
    r, _ = values["deep rim corner"]
    check("pocket rim stays clear", r == 0.0, f"{r:.3f}")

    # a finer ladder must converge on the true ratio (less quantization)
    fine, fine_w, fine_d = slenderness_ladder(heights, fx, fy, vh, PIXEL,
                                              MAX_DIAMETER, ladder=1.1)
    coarse = float(ratio[0])
    r = float(fine[0])
    d_fine = first_blocked(fine_d, 20)
    check("finer ladder converges",
          coarse < r <= DEEP_H / DEEP_W + 1e-6
          and abs(r - DEEP_H / d_fine) < 0.02,
          f"ladder 1.5 -> {coarse:.3f}, ladder 1.1 -> {r:.3f} "
          f"(true {DEEP_H / DEEP_W:.2f}, {len(fine_d)} scales)")

    print(f"\n{len(failures)} failure(s)" if failures else "\nall assertions passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
