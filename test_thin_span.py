"""Thin-span ladder vs analytic expectations.

Hand-built strip meshes with injected thickness fields (no meshlib): each
vertex must read the geodesic distance to the nearest material at least
`contrast` x its own thickness, divided by its own thickness. A long thin
bridge between thick pads reads (L/2)/t at mid-span, a short bridge and a
stubby rib read low, a uniform thin sheet grades away from its single
thick boss and saturates at max_span, the nearest adequate support wins
over farther bulk, and near-bulk / uniform material reads 0 (no support
contrast to measure against).

Plain script, not pytest: python test_thin_span.py
"""
import sys

import numpy as np

from pipeline import span_ladder

SPACING = 0.5
THIN, THICK = 1.0, 6.0

failures = []


def check(name, ok, detail):
    status = "OK " if ok else "FAIL"
    print(f"  [{status}] {name}: {detail}")
    if not ok:
        failures.append(f"{name}: {detail}")


def strip_mesh(length, width=1.0):
    """Two-row triangulated strip along x; geodesic distance ~ |dx|."""
    xs = np.arange(0.0, length + SPACING / 2, SPACING)
    n = len(xs)
    verts = np.zeros((2 * n, 3))
    verts[:n, 0] = xs
    verts[n:, 0] = xs
    verts[n:, 1] = width
    faces = []
    for i in range(n - 1):
        faces.append([i, i + 1, n + i])
        faces.append([i + 1, n + i + 1, n + i])
    return verts, np.asarray(faces, dtype=np.int64), xs


def field(xs, *regions, default=THIN):
    """Per-vertex thickness from (predicate, value) regions over x."""
    t = np.full(len(xs), default, dtype=np.float64)
    for where, value in regions:
        t[where(xs)] = value
    return np.tile(t, 2)


def at_x(xs, x):
    return int(np.argmin(np.abs(xs - x)))


def run(verts, faces, thickness, **kw):
    kw.setdefault("ladder", 1.5)
    kw.setdefault("contrast", 1.5)
    kw.setdefault("max_thickness", THICK)
    kw.setdefault("max_span", 1000.0)
    return span_ladder(verts, faces, thickness, **kw)


def main():
    # --- long thin bridge (L=20) between thick pads
    verts, faces, xs = strip_mesh(40.0)
    thickness = field(xs, (lambda x: (x <= 10) | (x >= 30), THICK))
    ratio, critical, scales = run(verts, faces, thickness)
    mid = at_x(xs, 20.0)
    check("long bridge mid-span ratio", abs(ratio[mid] - 10.0) < 0.05,
          f"{ratio[mid]:.2f} (expected {10.0 / THIN:.1f})")
    t_first = min(s for s in scales if s >= 1.5 * THIN)
    check("long bridge support scale",
          abs(critical[mid] - t_first) < 1e-6,
          f"{critical[mid]:.2f} (expected {t_first:g})")
    check("thick pads read zero (no thicker support exists)",
          ratio[at_x(xs, 5.0)] == 0.0, f"{ratio[at_x(xs, 5.0)]:.2f}")

    # --- short thin bridge (L=3): well below a 5x threshold
    verts, faces, xs = strip_mesh(23.0)
    thickness = field(xs, (lambda x: (x <= 10) | (x >= 13), THICK))
    ratio, _, _ = run(verts, faces, thickness)
    r = ratio[at_x(xs, 11.5)]
    check("short bridge stays ok", abs(r - 1.5) < 0.05, f"{r:.2f}")

    # --- stubby thin rib (h=2) off a thick pad
    verts, faces, xs = strip_mesh(12.0)
    thickness = field(xs, (lambda x: x <= 10, THICK))
    ratio, _, _ = run(verts, faces, thickness)
    r = ratio[at_x(xs, 12.0)]
    check("stubby rib stays ok", abs(r - 2.0) < 0.05, f"{r:.2f}")

    # --- uniform thin sheet with one thick boss: grades away, saturates
    verts, faces, xs = strip_mesh(100.0)
    thickness = field(xs, (lambda x: x <= 2, THICK))
    ratio, _, _ = run(verts, faces, thickness, max_span=50.0)
    samples = [ratio[at_x(xs, x)] for x in (10, 30, 50, 70)]
    check("sheet grades away from boss",
          all(a < b for a, b in zip(samples, samples[1:])),
          " -> ".join(f"{v:.1f}" for v in samples))
    far = ratio[at_x(xs, 100.0)]
    check("sheet saturates at max_span", abs(far - 50.0) < 0.05,
          f"{far:.2f} (cap {50.0 / THIN:.1f})")

    # --- nearest ADEQUATE support wins over farther bulk: a modest t=2
    # column mid-bridge supports its neighborhood; the column itself is
    # measured against the thick pads
    verts, faces, xs = strip_mesh(40.0)
    thickness = field(xs, (lambda x: (x <= 10) | (x >= 30), THICK),
                      (lambda x: (x >= 18) & (x <= 19), 2.0))
    ratio, critical, scales = run(verts, faces, thickness)
    r = ratio[at_x(xs, 15.0)]
    check("bridge vertex uses nearest support", abs(r - 3.0) < 0.05,
          f"{r:.2f} (3 to the t=2 column, not 5 to the pad)")
    col = at_x(xs, 18.5)
    t_req = min(s for s in scales if s >= 1.5 * 2.0)
    check("support column measured against the pads",
          abs(ratio[col] - 8.5 / 2.0) < 0.05
          and abs(critical[col] - t_req) < 1e-6,
          f"{ratio[col]:.2f} at scale {critical[col]:.2f}")

    # --- uniform bulk: nothing meaningfully thicker exists anywhere
    verts, faces, xs = strip_mesh(40.0)
    thickness = field(xs, default=THICK)
    ratio, _, _ = run(verts, faces, thickness)
    check("uniform bulk reads zero", float(ratio.max()) == 0.0,
          f"max {float(ratio.max()):.2f}")

    print(f"\n{len(failures)} failure(s)" if failures
          else "\nall assertions passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
