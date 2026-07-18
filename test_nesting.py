"""Self-checking script for the nesting sandbox (nesting.py) — run directly:

    python test_nesting.py

Synthetic contours with known-correct packings: grid counts for squares and
rectangles, L-shape interlocking that must beat naive bounding-box packing,
exact spacing/margin behaviour, and no-overlap/containment invariants on
every result.
"""

import numpy as np
import pyclipper

from nesting import (
    SCALE,
    _inflate,
    _nfp,
    _scale_path,
    contour_area,
    find_tiling,
    nest_single,
    write_svg,
)

SQUARE = np.array([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
RECT = np.array([(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)])
LSHAPE = np.array(
    [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (10.0, 10.0), (10.0, 20.0), (0.0, 20.0)]
)


def overlap_area(pa, pb) -> float:
    """Intersection area (mm^2) of two placed contours."""
    pc = pyclipper.Pyclipper()
    pc.AddPath(_scale_path(pa), pyclipper.PT_SUBJECT, True)
    pc.AddPath(_scale_path(pb), pyclipper.PT_CLIP, True)
    sol = pc.Execute(
        pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO
    )
    return sum(abs(pyclipper.Area(p)) for p in sol) / SCALE**2


def check_invariants(result, tol=1e-6):
    """No pairwise overlap beyond tol, every part inside sheet minus margin."""
    polys = result.polygons()
    w, h = result.sheet
    m = result.margin
    for poly in polys:
        assert poly[:, 0].min() >= m - tol and poly[:, 0].max() <= w - m + tol
        assert poly[:, 1].min() >= m - tol and poly[:, 1].max() <= h - m + tol
    boxes = [(p.min(axis=0), p.max(axis=0)) for p in polys]
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            (ilo, ihi), (jlo, jhi) = boxes[i], boxes[j]
            if (ihi < jlo).any() or (jhi < ilo).any():
                continue  # disjoint bboxes cannot overlap
            area = overlap_area(polys[i], polys[j])
            assert area <= tol, f"parts {i},{j} overlap by {area} mm^2"


def check_clearance(result, tol=0.01):
    """Every pair of parts at least `spacing` apart (within tol)."""
    polys = result.polygons()
    s = result.spacing
    grown = [_inflate(_scale_path(p), s - tol) for p in polys]
    for i in range(len(polys)):
        for j in range(len(polys)):
            if i == j:
                continue
            pc = pyclipper.Pyclipper()
            pc.AddPaths(grown[i], pyclipper.PT_SUBJECT, True)
            pc.AddPath(_scale_path(polys[j]), pyclipper.PT_CLIP, True)
            sol = pc.Execute(
                pyclipper.CT_INTERSECTION,
                pyclipper.PFT_NONZERO,
                pyclipper.PFT_NONZERO,
            )
            area = sum(abs(pyclipper.Area(p)) for p in sol) / SCALE**2
            assert area <= 1e-6, f"parts {i},{j} closer than spacing ({area})"


def test_nfp_square():
    nfp = _nfp([_scale_path(SQUARE)], _scale_path(SQUARE))
    pts = np.vstack([np.asarray(p) for p in nfp]) / SCALE
    assert np.allclose(pts.min(axis=0), (-10, -10))
    assert np.allclose(pts.max(axis=0), (10, 10))
    print("nfp square-square bounds: OK")


def test_square_grid():
    r = nest_single(SQUARE, 100, 100, rotations=1)
    assert r.count == 100, f"expected 100 squares, placed {r.count}"
    assert abs(r.utilization - 1.0) < 1e-9
    check_invariants(r)
    print(f"square grid 10x10 on 100x100: {r.count} parts, "
          f"util {r.utilization:.0%}, {r.runtime:.2f}s: OK")


def test_rect_grid():
    r = nest_single(RECT, 100, 100, rotations=2)
    assert r.count == 50, f"expected 50 rects, placed {r.count}"
    check_invariants(r)
    print(f"rect grid 20x10 on 100x100: {r.count} parts, {r.runtime:.2f}s: OK")


def test_lshape_interlock():
    # Naive bounding-box packing puts 5x5 = 25 of the 20x20-bbox L on a
    # 100x100 sheet. Two Ls (one rotated 180 deg) tile a 20x30 rectangle
    # exactly, so 30 must fit; the greedy has to beat the bbox grid.
    assert contour_area(LSHAPE) == 300.0
    r = nest_single(LSHAPE, 100, 100, rotations=4)
    assert r.count > 25, f"L-shapes did not interlock: {r.count} <= 25"
    check_invariants(r)
    rots = sorted({p.rotation for p in r.placements})
    print(f"L-shape on 100x100: {r.count} parts (bbox grid = 25), "
          f"util {r.utilization:.0%}, rotations used {rots}, "
          f"{r.runtime:.2f}s: OK")
    return r


def test_spacing():
    # pitch 12 on a 46-wide sheet: origins 0,12,24,36 -> 4 per axis
    r = nest_single(SQUARE, 46, 46, spacing=2, rotations=1)
    assert r.count == 16, f"expected 16 spaced squares, placed {r.count}"
    check_invariants(r)
    check_clearance(r)
    print(f"spacing 2mm on 46x46: {r.count} parts: OK")


def test_margin():
    # inner window 90x90 -> 9x9 grid
    r = nest_single(SQUARE, 100, 100, margin=5, rotations=1)
    assert r.count == 81, f"expected 81 squares with margin, placed {r.count}"
    check_invariants(r)
    print(f"margin 5mm on 100x100: {r.count} parts: OK")


def test_count_cap():
    r = nest_single(SQUARE, 100, 100, rotations=1, count=7)
    assert r.count == 7
    print("count cap: OK")


def test_tiling_square():
    t = find_tiling(SQUARE, max_parts=1, rotations=1)
    assert abs(t.area_per_part - 100.0) < 1e-6, t.area_per_part
    assert abs(t.utilization - 1.0) < 1e-6
    assert t.count(100, 100) == 100
    assert t.count(100, 100, margin=5) == 81
    check_invariants(t.realize(100, 100))
    print(f"tiling square: area/part {t.area_per_part:.1f}, "
          f"{t.runtime:.2f}s: OK")


def test_tiling_square_spacing():
    # The 12mm grid gives 144 mm^2/part; the rounded spacing corners allow a
    # slightly denser sheared lattice asymptotically, but on a small sheet
    # the axis-aligned alternate must win and recover the full 4x4 grid.
    t = find_tiling(SQUARE, max_parts=1, rotations=1, spacing=2)
    assert t.area_per_part <= 144.0 + 1e-6, t.area_per_part
    assert t.count(46, 46) == 16
    r = t.realize(46, 46)
    check_invariants(r)
    check_clearance(r)
    print(f"tiling square spacing 2: area/part {t.area_per_part:.2f} "
          f"({len(t.alternates)} alternates): OK")


def test_tiling_rect():
    t = find_tiling(RECT, max_parts=1, rotations=2)
    assert abs(t.area_per_part - 200.0) < 1e-6, t.area_per_part
    assert t.count(100, 100) == 50
    print(f"tiling rect: area/part {t.area_per_part:.1f}: OK")


def test_tiling_lshape():
    # The L is a rep-tile: translations alone tile the plane at 100%
    # utilization (offset column packing), so even max_parts=1 with no
    # rotations must find area/part == 300.
    t1 = find_tiling(LSHAPE, max_parts=1, rotations=1)
    assert abs(t1.area_per_part - 300.0) < 1e-6, t1.area_per_part
    # A 2-part motif with rotations must not do worse.
    t2 = find_tiling(LSHAPE, max_parts=2, rotations=4)
    assert t2.area_per_part <= 300.0 + 1e-6, t2.area_per_part
    n = t2.count(100, 100)
    check_invariants(t2.realize(100, 100))
    print(f"tiling L: 1-part {t1.area_per_part:.1f}, "
          f"2-part {t2.area_per_part:.1f} mm^2/part, "
          f"{n} on 100x100 (greedy found 30): OK")


def test_tiling_estimates_match_greedy():
    # Estimates must be internally consistent (count == len(realize)) and
    # land in the same ballpark as the full greedy nest, fast.
    import time

    t = find_tiling(LSHAPE, max_parts=2, rotations=4)
    t0 = time.perf_counter()
    counts = {
        (w, h): t.count(w, h)
        for w in (100, 150, 200, 250, 300)
        for h in (100, 150, 200)
    }
    dt = time.perf_counter() - t0
    for (w, h), c in counts.items():
        assert c == len(t.realize(w, h).placements)
    greedy = nest_single(LSHAPE, 200, 200, rotations=4).count
    est = counts[(200, 200)]
    assert est >= 0.8 * greedy, f"estimate {est} vs greedy {greedy}"
    print(f"tiling estimates: 15 sheet sizes in {dt * 1000:.1f} ms, "
          f"200x200 estimate {est} vs greedy {greedy}: OK")


if __name__ == "__main__":
    test_nfp_square()
    test_square_grid()
    test_rect_grid()
    result = test_lshape_interlock()
    test_spacing()
    test_margin()
    test_count_cap()
    test_tiling_square()
    test_tiling_square_spacing()
    test_tiling_rect()
    test_tiling_lshape()
    test_tiling_estimates_match_greedy()

    import sys

    if "--svg" in sys.argv:
        out = sys.argv[sys.argv.index("--svg") + 1]
        write_svg(result, out)
        print(f"wrote {out}")

    print("all nesting assertions passed")
