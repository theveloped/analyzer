"""Analytic probes for the gap/stickout fields on known feature geometry.

Covers the failure modes found while validating on testpart_42:

- map border: features near the part silhouette must NOT read as machinable
  (unpadded erosion used to pull border columns to -inf)
- hole narrower than the tool: walls blocked from the first row below the rim
- vertical internal corner: wall gap follows sqrt((s-R)^2 + R^2) - R
- wall/floor fillet band: wall blocked below h*(rc) with
  gap(h) = sqrt(rc^2 + (rc-h)^2) - rc  ->  band is wider for larger rc
- smooth doubly-curved pocket: no quantization speckle; flat tool gap =
  r_tool^2 / (2 R_surface), ball gap ~ 0

Run from the repo root: python test_gap_probes.py
"""
import os
import sys
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm

from analysis import get_mesh_data, subdivide_mesh
from zmap import DirectionCache

PIXEL = 0.05
WALL_THR = 2.5 * PIXEL  # viewer wall threshold
TOL = 0.1


def make_part():
    # block 40 x 30 x 10, top at z = 0
    part = mm.makeCube(mm.Vector3f(40, 30, 10), mm.Vector3f(-20, -15, -10))

    # narrow slot 2 wide, 4 deep, 1 mm from the x = -20 border
    slot = mm.makeCube(mm.Vector3f(2, 24, 5), mm.Vector3f(-19, -12, -4))
    part = mm.boolean(part, slot, mm.BooleanOperation.DifferenceAB).mesh

    # blind hole d8, 5 deep at (12, -8)
    hole = mm.makeCylinderAdvanced(4.0, 4.0, 0.0, 2 * np.pi, 6.0, 96)
    hole.transform(mm.AffineXf3f.translation(mm.Vector3f(12.0, -8.0, -5.0)))
    part = mm.boolean(part, hole, mm.BooleanOperation.DifferenceAB).mesh

    # pocket 16 x 16, 6 deep -> vertical internal corners + wall/floor bands
    pocket = mm.makeCube(mm.Vector3f(16, 16, 7), mm.Vector3f(-8, -3, -6))
    part = mm.boolean(part, pocket, mm.BooleanOperation.DifferenceAB).mesh

    # doubly-curved pocket: sphere R=12 sunk 2 deep, centred at (12, 7)
    ball = mm.makeUVSphere(12.0, 128, 128)
    ball.transform(mm.AffineXf3f.translation(mm.Vector3f(12.0, 7.0, 10.0)))
    part = mm.boolean(part, ball, mm.BooleanOperation.DifferenceAB).mesh

    subdivide_mesh(part, 0.5)
    return part


def main():
    failures = []

    def check(name, ok, detail):
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            failures.append(f"{name}: {detail}")

    with tempfile.TemporaryDirectory() as workdir:
        part = make_part()
        verts, faces = get_mesh_data(part)
        np.save(os.path.join(workdir, "fine_verts.npy"), verts)
        np.save(os.path.join(workdir, "fine_faces.npy"), faces)
        np.save(os.path.join(workdir, "directions.npy"), np.array([[0.0, 0.0, 1.0]]))

        cache = DirectionCache(workdir, 0, verts=verts, faces=faces, pixel=PIXEL)

        def nearest(point):
            return int(np.argmin(np.linalg.norm(verts - np.asarray(point), axis=1)))

        def gap_at(gap, point):
            return float(gap[nearest(point)])

        # ---- D10 flat --------------------------------------------------
        gap = cache.tip_gap(10.0, 0.0)
        print("=== D10 flat ===")
        for name, point, blocked, thr in [
            # border slot: everything in it is unreachable, border or not
            ("border slot floor", (-18.0, 0.0, -4.0), True, TOL),
            ("border slot outer wall mid", (-19.0, 0.0, -2.0), True, WALL_THR),
            # d8 hole: filled to the top -> wall gap = depth below rim
            ("hole wall depth 0.9", (16.0, -8.0, -0.9), True, WALL_THR),
            ("hole wall depth 2.5", (16.0, -8.0, -2.5), True, WALL_THR),
            ("hole floor", (12.0, -8.0, -5.0), True, TOL),
            ("top face at hole rim", (16.9, -8.0, 0.0), False, TOL),
            # vertical internal corner at (-8, -3): gap = sqrt((s-5)^2+25)-5
            ("corner wall s=2", (-6.0, -3.0, -3.0), True, WALL_THR),      # 0.83
            ("corner wall s=4.5", (-3.5, -3.0, -3.0), False, WALL_THR),   # 0.025
            ("pocket floor centre", (0.0, 5.0, -6.0), False, TOL),
        ]:
            g = gap_at(gap, point)
            ok = (g > thr) == blocked
            check(f"D10/{name}", ok, f"gap {g:.3f} thr {thr} expected {'blocked' if blocked else 'clear'}")

        # ---- wall/floor fillet band: blocked below h*(rc) ---------------
        # wall point h above the floor: gap = sqrt(rc^2 + (rc-h)^2) - rc
        # NB: the rc1 band (h* = 0.48) is thinner than the 0.5 vertex spacing,
        # so probe its lower edge; the first vertex ROW above (h = 0.5) is
        # correctly clear - a band thinner than the face size cannot resolve
        # to more than one face row
        for D, rc, cases in [
            (6.0, 1.0, [("h~0 (edge)", -5.97, True), ("h=0.5", -5.5, False)]),  # h* = 0.48
            (6.0, 3.0, [("h=1.5", -4.5, True), ("h=2.7", -3.3, False)]),        # h* = 2.12
        ]:
            gap = cache.tip_gap(D, rc)
            print(f"=== D{D:g} rc{rc:g} wall band (h* = {rc - np.sqrt(0.25 * rc + 0.0156):.2f}) ===")
            for name, z, blocked in cases:
                g = gap_at(gap, (0.0, -3.0, z))
                ok = (g > WALL_THR) == blocked
                check(f"D{D:g}rc{rc:g}/{name}", ok, f"gap {g:.3f} thr {WALL_THR} expected {'blocked' if blocked else 'clear'}")

        # ---- doubly-curved pocket: no speckle ---------------------------
        cap = np.where(
            (np.linalg.norm(verts[:, :2] - np.array([12.0, 7.0]), axis=1) < 4.5)
            & (verts[:, 2] > -2.05) & (verts[:, 2] < -0.4)
        )[0]
        print(f"=== spherical pocket R12 ({len(cap)} cap vertices) ===")

        gap = cache.tip_gap(2.0, 0.0)  # flat D2: theory r^2/2R = 0.042
        centre = gap_at(gap, (12.0, 7.0, -2.0))
        check("D2 flat/cap centre", abs(centre - 0.042) < 0.05, f"gap {centre:.3f} theory 0.042")
        frac = float(np.mean(gap[cap] > TOL))
        check("D2 flat/cap speckle", frac < 0.02, f"{frac * 100:.1f}% of cap vertices over tolerance (want ~0)")

        gap = cache.tip_gap(6.0, 3.0)  # ball D6 fits R12 everywhere
        worst = float(np.max(gap[cap]))
        check("D6 ball/cap fits", worst < 0.08, f"max cap gap {worst:.3f} (want ~0)")

    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
