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


if __name__ == "__main__":
    test_nfp_square()
    test_square_grid()
    test_rect_grid()
    result = test_lshape_interlock()
    test_spacing()
    test_margin()
    test_count_cap()

    import sys

    if "--svg" in sys.argv:
        out = sys.argv[sys.argv.index("--svg") + 1]
        write_svg(result, out)
        print(f"wrote {out}")

    print("all nesting assertions passed")
