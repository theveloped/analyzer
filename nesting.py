"""2D contour nesting sandbox — standalone sketch, not wired into the pipeline.

Exploration of sheet nesting for sheet-metal (and CNC plate) work: given the
unfolded 2D outer contour of a part and a rectangular sheet, how many copies
fit and where. The core algorithm is the one SVGNest/Deepnest (MIT) built
around Clipper: collision geometry is precomputed as no-fit polygons (NFPs)
via Minkowski sums, so the placement loop never runs a geometric overlap test
— a part placement is valid iff its reference point lies outside every
placed part's NFP and inside the sheet's inner-fit rectangle.

Scope of this sketch (see docs/NESTING.md for the full exploration notes):

- single contour, one rectangular sheet, greedy gravity placement over a
  discrete rotation set (Deepnest's placement worker without the genetic
  algorithm — with identical parts there is no ordering to evolve, only
  per-placement rotation choice, which the greedy handles directly);
- `find_tiling`: minimum-area periodic patterns (a motif of a few parts plus
  two lattice vectors) — nest a handful of parts once, then `TilePattern.count`
  estimates any rectangular sheet size in milliseconds;
- exact pairwise `spacing` (round-join inflation of the placed contour before
  the Minkowski sum) and sheet `margin`;
- part holes are ignored for collision (parts are never nested inside other
  parts' cutouts — valid, just conservative).

Coordinates are float millimetres in and out; Clipper works on int64 at
`SCALE` units per mm. All contours are simple closed polygons as (N, 2)
arrays, no repeated end vertex required.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from itertools import combinations_with_replacement

import numpy as np
import pyclipper

SCALE = 10**4  # Clipper integer units per mm (0.1 um resolution)
_MAX_PARTS = 10_000  # runaway guard for fill-the-sheet mode


# ---------------------------------------------------------------------------
# contour helpers (mm floats <-> scaled Clipper paths)


def _clean_contour(contour: np.ndarray) -> np.ndarray:
    """Return the contour as float64 (N,2), CCW, without a repeated end vertex."""
    pts = np.asarray(contour, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 3:
        raise ValueError("contour must be an (N>=3, 2) array")
    if np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    if _signed_area(pts) < 0:
        pts = pts[::-1]
    return pts


def _signed_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def contour_area(contour: np.ndarray) -> float:
    """Absolute area of a simple polygon (mm^2)."""
    return abs(_signed_area(_clean_contour(contour)))


def rotate_contour(contour: np.ndarray, degrees: float) -> np.ndarray:
    a = np.deg2rad(degrees)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    return np.asarray(contour, dtype=np.float64) @ rot.T


def _scale_path(pts: np.ndarray) -> list[tuple[int, int]]:
    ints = np.rint(np.asarray(pts) * SCALE).astype(np.int64)
    return [(int(x), int(y)) for x, y in ints]


def _negate_path(path) -> list[tuple[int, int]]:
    return [(-x, -y) for x, y in path]


def _translate_paths(paths, dx: int, dy: int):
    return [[(x + dx, y + dy) for x, y in p] for p in paths]


def _inflate(path, delta_mm: float):
    """Offset a scaled path outward with round joins (exact clearance discs)."""
    off = pyclipper.PyclipperOffset(arc_tolerance=0.005 * SCALE)
    off.AddPath(path, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return off.Execute(delta_mm * SCALE)


def _nfp(a_paths, b_path):
    """No-fit polygon(s): positions of B's local origin where B overlaps A.

    NFP(A, B) = A (+) (-B), the Minkowski sum with B negated — the same trick
    Deepnest implements. Clipper's MinkowskiSum sweeps the pattern along the
    path *outline*, so the raw result is an annular band whose inner contours
    are NOT real holes (a solid part fully inside another's outline still
    collides). Keeping only positive contours fills the region — Deepnest's
    "largest area child" trick. Cost: positions where a part would float
    inside an enclosed cavity of another part are treated as collisions
    (conservative; irrelevant while part holes are not modelled anyway).
    """
    result = []
    neg_b = _negate_path(b_path)
    for a in a_paths:
        result.extend(pyclipper.MinkowskiSum(a, neg_b, True))
    # Output paths are not always strictly simple; point-in-polygon tests
    # need clean winding, so simplify before filtering orientations.
    result = pyclipper.SimplifyPolygons(result, pyclipper.PFT_NONZERO)
    return [p for p in result if len(p) >= 3 and pyclipper.Orientation(p)]


def _intersection(paths_a, paths_b):
    pc = pyclipper.Pyclipper()
    pc.AddPaths(paths_a, pyclipper.PT_SUBJECT, True)
    pc.AddPaths(paths_b, pyclipper.PT_CLIP, True)
    return pc.Execute(
        pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO
    )


def _paths_bbox(paths) -> tuple[int, int, int, int]:
    pts = np.concatenate([np.asarray(p) for p in paths])
    (minx, miny), (maxx, maxy) = pts.min(axis=0), pts.max(axis=0)
    return int(minx), int(miny), int(maxx), int(maxy)


class _Nfp:
    """One placed part's no-fit polygon: translated Clipper paths (disjoint
    positive contours, see _nfp) plus a bbox for cheap rejection."""

    __slots__ = ("paths", "bbox")

    def __init__(self, paths):
        self.paths = paths
        self.bbox = _paths_bbox(paths)

    def strictly_inside(self, pt) -> bool:
        """True when pt is in the interior; the boundary counts as outside
        (touching a placed part is a valid contact placement)."""
        x, y = pt
        minx, miny, maxx, maxy = self.bbox
        if x < minx or x > maxx or y < miny or y > maxy:
            return False
        for path in self.paths:
            if pyclipper.PointInPolygon(pt, path) == 1:
                return True
        return False


# ---------------------------------------------------------------------------
# nesting


@dataclass(frozen=True)
class Placement:
    rotation: float  # degrees
    x: float  # translation of the contour's own origin, mm
    y: float


@dataclass
class NestResult:
    contour: np.ndarray  # cleaned input contour (CCW, mm)
    sheet: tuple[float, float]
    spacing: float
    margin: float
    placements: list[Placement] = field(default_factory=list)
    runtime: float = 0.0

    @property
    def count(self) -> int:
        return len(self.placements)

    @property
    def part_area(self) -> float:
        return abs(_signed_area(self.contour))

    @property
    def utilization(self) -> float:
        sheet_area = self.sheet[0] * self.sheet[1]
        return self.count * self.part_area / sheet_area if sheet_area else 0.0

    def polygons(self) -> list[np.ndarray]:
        """Placed contours in sheet coordinates (mm)."""
        return [
            rotate_contour(self.contour, p.rotation) + (p.x, p.y)
            for p in self.placements
        ]


def _pair_nfps(poly, angles, spacing):
    """Per-rotation geometry and the rotation-pair NFP cache.

    The placed-part source geometry is inflated by the full spacing so the
    NFP boundary sits exactly `spacing` away from contact (dist(A,B) >= s iff
    B misses A (+) disc_s) — round joins keep that exact at corners.
    """
    rot_poly = {a: rotate_contour(poly, a) for a in angles}
    rot_path = {a: _scale_path(rot_poly[a]) for a in angles}
    rot_bbox = {
        a: (rot_poly[a].min(axis=0), rot_poly[a].max(axis=0)) for a in angles
    }
    src = {
        a: _inflate(rot_path[a], spacing) if spacing > 0 else [rot_path[a]]
        for a in angles
    }
    nfps = {(a, b): _nfp(src[a], rot_path[b]) for a in angles for b in angles}
    return rot_poly, rot_path, rot_bbox, nfps


def _rotation_angles(rotations) -> list[float]:
    if np.isscalar(rotations):
        n = int(rotations)
        if n < 1:
            raise ValueError("rotations must be >= 1")
        return [k * 360.0 / n for k in range(n)]
    return [float(a) for a in rotations]


def nest_single(
    contour: np.ndarray,
    sheet_width: float,
    sheet_height: float,
    *,
    spacing: float = 0.0,
    margin: float = 0.0,
    rotations=4,
    count: int | None = None,
    strategy: str = "gravity",
) -> NestResult:
    """Nest copies of one contour on a rectangular sheet.

    rotations: int N for N evenly spaced angles, or an iterable of degrees.
    count: stop after this many parts (None = fill the sheet).
    strategy: 'gravity' (Deepnest default, compacts along x first) or 'bbox'
    (minimize combined bounding-box area).

    Greedy loop, one part per iteration: candidate positions are the live
    vertices of the constraint arrangement (NFP vertices, NFP-NFP and
    NFP-sheet crossings), evaluated per candidate rotation; the vertex
    minimizing the strategy metric wins.
    """
    poly = _clean_contour(contour)
    angles = _rotation_angles(rotations)
    if strategy not in ("gravity", "bbox"):
        raise ValueError(f"unknown strategy {strategy!r}")

    t0 = time.perf_counter()
    rot_poly, rot_path, rot_bbox, nfps = _pair_nfps(poly, angles, spacing)

    # Inner-fit rectangle per rotation: containment in an axis-aligned sheet
    # only depends on the part's bbox, so the IFP is exact and free.
    ifp = {}
    for a in angles:
        (minx, miny), (maxx, maxy) = rot_bbox[a]
        lo = (margin - minx, margin - miny)
        hi = (sheet_width - margin - maxx, sheet_height - margin - maxy)
        ifp[a] = (lo, hi) if lo[0] <= hi[0] and lo[1] <= hi[1] else None

    # A valid tight placement always sits at a vertex of the "arrangement" of
    # constraint boundaries: an NFP vertex, a crossing of two NFP boundaries,
    # or a crossing with the inner-fit rectangle. Deepnest instead evaluates
    # vertices of difference(IFP, union of NFPs), which silently loses valid
    # touching positions once parts tile exactly (the free region between
    # them has zero area and the boolean difference drops it). We maintain
    # the candidate vertex pool incrementally per rotation instead: points
    # die when a new NFP strictly swallows them, new points come from the
    # freshly placed part's NFP (clipped to the IFP) and its crossings with
    # bbox-overlapping older NFPs.
    pool: dict[float, list] = {}  # candidate points, int Clipper units
    alive: dict[float, list] = {}
    seen: dict[float, set] = {}
    placed_nfps: dict[float, list[_Nfp]] = {a: [] for a in angles}
    ifp_rect_path = {}
    for a in angles:
        if ifp[a] is None:
            pool[a], alive[a], seen[a] = [], [], set()
            continue
        lo, hi = ifp[a]
        corners = [
            (int(round(x * SCALE)), int(round(y * SCALE)))
            for x, y in (lo, (hi[0], lo[1]), hi, (lo[0], hi[1]))
        ]
        ifp_rect_path[a] = corners
        pool[a] = list(dict.fromkeys(corners))
        alive[a] = [True] * len(pool[a])
        seen[a] = set(pool[a])

    result = NestResult(poly, (sheet_width, sheet_height), spacing, margin)
    placed_lo = np.full(2, np.inf)
    placed_hi = np.full(2, -np.inf)

    limit = _MAX_PARTS if count is None else min(count, _MAX_PARTS)
    while result.count < limit:
        best = None  # ((metric, x, y), x, y, angle)
        for a in angles:
            if ifp[a] is None:
                continue
            pts = [p for p, ok in zip(pool[a], alive[a]) if ok]
            if not pts:
                continue
            arr = np.asarray(pts, dtype=np.float64) / SCALE
            (minx, miny), (maxx, maxy) = rot_bbox[a]
            w = np.maximum(placed_hi[0], arr[:, 0] + maxx) - np.minimum(
                placed_lo[0], arr[:, 0] + minx
            )
            h = np.maximum(placed_hi[1], arr[:, 1] + maxy) - np.minimum(
                placed_lo[1], arr[:, 1] + miny
            )
            m = w * 5 + h if strategy == "gravity" else w * h
            i0 = np.lexsort((arr[:, 1], arr[:, 0], m))[0]
            key = (m[i0], arr[i0, 0], arr[i0, 1])
            if best is None or key < best[0]:
                best = (key, arr[i0, 0], arr[i0, 1], a)
        if best is None:
            break
        _, x, y, a = best
        result.placements.append(Placement(a, float(x), float(y)))
        placed_lo = np.minimum(placed_lo, rot_bbox[a][0] + (x, y))
        placed_hi = np.maximum(placed_hi, rot_bbox[a][1] + (x, y))
        dx, dy = int(round(x * SCALE)), int(round(y * SCALE))

        for b in angles:
            if ifp[b] is None:
                continue
            new = _Nfp(_translate_paths(nfps[(a, b)], dx, dy))
            for i, pt in enumerate(pool[b]):
                if alive[b][i] and new.strictly_inside(pt):
                    alive[b][i] = False
            cand = [
                tuple(pt)
                for p in _intersection(new.paths, [ifp_rect_path[b]])
                for pt in p
            ]
            for old in placed_nfps[b]:
                if (
                    old.bbox[0] <= new.bbox[2]
                    and new.bbox[0] <= old.bbox[2]
                    and old.bbox[1] <= new.bbox[3]
                    and new.bbox[1] <= old.bbox[3]
                ):
                    cand += [
                        tuple(pt)
                        for p in _intersection(new.paths, old.paths)
                        for pt in p
                    ]
            placed_nfps[b].append(new)
            (lox, loy), (hix, hiy) = ifp_rect_path[b][0], ifp_rect_path[b][2]
            for pt in cand:
                if pt in seen[b]:
                    continue
                seen[b].add(pt)
                if not (lox <= pt[0] <= hix and loy <= pt[1] <= hiy):
                    continue  # NFP-NFP crossing outside the sheet window
                if any(nf.strictly_inside(pt) for nf in placed_nfps[b]):
                    continue
                pool[b].append(pt)
                alive[b].append(True)

    result.runtime = time.perf_counter() - t0
    return result


# ---------------------------------------------------------------------------
# periodic tiling patterns (minimum-area lattice packing)
#
# A tiling pattern is a motif of k <= max_parts placed parts plus two lattice
# vectors v1, v2: the motif repeats at every m*v1 + n*v2. It is valid iff no
# lattice translation lands strictly inside the motif's self-NFP, and optimal
# when the cell area |v1 x v2| / k is minimal. Finding one is a small search
# over the NFP machinery above; once found, counting parts on any rectangular
# sheet is closed-form interval arithmetic per lattice row — microseconds,
# which is the point: nest a handful of parts once, then estimate arbitrary
# sheet sizes instantly (a lower bound: edge slivers that greedy nesting
# could still exploit are not counted).

_COUNT_EPS = 1e-3  # mm of slack when counting lattice cells against the sheet


def _hull_area(pts: np.ndarray) -> float:
    """Convex hull area (monotone chain) — motif pre-score for beam pruning."""
    pts = np.unique(np.round(pts, 6), axis=0)
    if len(pts) < 3:
        return 0.0
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def half(points):
        h = []
        for p in points:
            while len(h) >= 2 and np.cross(h[-1] - h[-2], p - h[-2]) <= 0:
                h.pop()
            h.append(p)
        return h

    hull = np.array(half(pts)[:-1] + half(pts[::-1])[:-1])
    return abs(_signed_area(hull)) if len(hull) >= 3 else 0.0


def _boundary_samples(paths, step: int, cap: int = 600) -> np.ndarray:
    """Candidate lattice generators on the self-NFP boundary: path vertices,
    points interpolated along edges every `step` units, and the boundary's
    exact axis crossings.

    Optimal generators touch the boundary but need not sit at a vertex.
    Vertices cover interlock optima (part-corner contacts); the axis
    crossings cover grid/brick optima exactly (e.g. squares need generator
    (pitch, 0), the midpoint of an NFP edge); edge interpolation approximates
    everything in between.
    """
    out, crossings = [], []
    for p in paths:
        arr = np.asarray(p, dtype=np.float64)
        nxt = np.roll(arr, -1, axis=0)
        for a, b in zip(arr, nxt):
            out.append(a)
            # samples at exact arc-length multiples of `step` from the edge
            # start, so rectilinear contact offsets land on-grid
            length = np.linalg.norm(b - a)
            for i in range(1, int(length // step) + 1):
                out.append(a + (b - a) * (i * step / length))
            for ax in (0, 1):  # edge crosses the other axis' zero line
                if (a[ax] > 0) != (b[ax] > 0) and a[ax] != b[ax]:
                    t = a[ax] / (a[ax] - b[ax])
                    crossings.append(a + t * (b - a))
    samples = np.unique(np.rint(out).astype(np.int64), axis=0)
    if len(samples) > cap:
        samples = samples[:: len(samples) // cap + 1]
    if crossings:
        samples = np.unique(
            np.vstack([samples, np.rint(crossings).astype(np.int64)]), axis=0
        )
    return samples


_NEAR_COMBOS = [(1, 0), (0, 1), (1, 1), (1, -1), (2, 0), (0, 2), (2, 1), (1, 2)]


def _valid_lattice(v1, v2, nfp: _Nfp) -> bool:
    """No lattice translation m*v1 + n*v2 (except 0) strictly inside the
    self-NFP. Nearest neighbours are probed first so invalid candidates die
    on the first point test; the self-NFP is centrally symmetric, so (m, n)
    covers (-m, -n) too. Only finitely many combos reach the bounded NFP."""
    for m, n in _NEAR_COMBOS:
        pt = (m * v1[0] + n * v2[0], m * v1[1] + n * v2[1])
        if nfp.strictly_inside(pt):
            return False
    det = v1[0] * v2[1] - v1[1] * v2[0]
    minx, miny, maxx, maxy = nfp.bbox
    ms, ns = [], []
    for tx, ty in ((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)):
        ms.append((tx * v2[1] - ty * v2[0]) / det)
        ns.append((v1[0] * ty - v1[1] * tx) / det)
    near = set(_NEAR_COMBOS)
    for n in range(math.floor(min(ns)), math.ceil(max(ns)) + 1):
        for m in range(math.floor(min(ms)), math.ceil(max(ms)) + 1):
            if (m, n) == (0, 0) or (m, n) in near or (-m, -n) in near:
                continue
            if n < 0 or (n == 0 and m < 0):  # central symmetry
                continue
            pt = (m * v1[0] + n * v2[0], m * v1[1] + n * v2[1])
            if nfp.strictly_inside(pt):
                return False
    return True


def _lattice_search(
    self_nfp_paths, min_cell: int, step: int, max_checks: int,
    slack: float = 1.08, keep: int = 6,
):
    """Valid lattice bases with generators on the self-NFP boundary, sorted
    by cell area. Candidates are scanned smallest-cell first, so the first
    valid basis is optimal (up to boundary sampling); scanning continues
    through `slack` to also collect near-optimal alternates — an
    asymptotically slightly-denser skewed lattice can lose to an axis-
    aligned one on a finite sheet, so the caller keeps both and picks per
    sheet size. Returns a list of (v1, v2, det) in Clipper units."""
    nfp = _Nfp(self_nfp_paths)
    verts = _boundary_samples(self_nfp_paths, step)
    x, y = verts[:, 0], verts[:, 1]
    det = np.outer(x, y) - np.outer(y, x)  # det[i, j] = cross(v_i, v_j)
    ii, jj = np.nonzero(det >= min_cell)
    if not len(ii):
        return []
    # Equal-area bases tie-break toward axis-aligned generators: those lose
    # the least to edge effects on rectangular sheets, and this is what puts
    # the plain grid ahead of its many equal-area sheared cousins.
    pen = np.minimum(np.abs(x), np.abs(y))
    order = np.lexsort((np.add.outer(pen, pen)[ii, jj], det[ii, jj]))
    found, seen = [], set()
    for k in order[:max_checks]:
        i, j = ii[k], jj[k]
        d = int(det[i, j])
        if found and d > found[0][2] * slack:
            break
        v1 = (int(x[i]), int(y[i]))
        v2 = (int(x[j]), int(y[j]))
        canon = frozenset(max(v, (-v[0], -v[1])) for v in (v1, v2))
        if (d, canon) in seen:
            continue
        if _valid_lattice(v1, v2, nfp):
            seen.add((d, canon))
            found.append((v1, v2, d))
            if len(found) >= keep:
                break
    return found


def _self_nfp(motif, nfps):
    """Self-NFP of a motif: translations where the motif overlaps a copy of
    itself — the union of pairwise NFPs offset by the parts' relative
    positions (spacing is already inside the pairwise NFPs)."""
    paths = []
    for pi in motif:
        for pj in motif:
            dx = int(round((pi.x - pj.x) * SCALE))
            dy = int(round((pi.y - pj.y) * SCALE))
            paths += _translate_paths(nfps[(pi.rotation, pj.rotation)], dx, dy)
    paths = pyclipper.SimplifyPolygons(paths, pyclipper.PFT_NONZERO)
    return [p for p in paths if len(p) >= 3 and pyclipper.Orientation(p)]


def _motif_candidates(motif, rot_next, nfps):
    """Touching positions for the next motif part: arrangement vertices of
    the existing parts' NFPs (vertices + pairwise crossings), like the
    placement loop in nest_single but without a sheet."""
    objs = [
        _Nfp(
            _translate_paths(
                nfps[(p.rotation, rot_next)],
                int(round(p.x * SCALE)),
                int(round(p.y * SCALE)),
            )
        )
        for p in motif
    ]
    pts = [tuple(pt) for o in objs for path in o.paths for pt in path]
    for i, a in enumerate(objs):
        for b in objs[i + 1 :]:
            if (
                a.bbox[0] <= b.bbox[2]
                and b.bbox[0] <= a.bbox[2]
                and a.bbox[1] <= b.bbox[3]
                and b.bbox[1] <= a.bbox[3]
            ):
                pts += [
                    tuple(pt)
                    for path in _intersection(a.paths, b.paths)
                    for pt in path
                ]
    return [
        pt
        for pt in dict.fromkeys(pts)
        if not any(o.strictly_inside(pt) for o in objs)
    ]


@dataclass
class TilePattern:
    """A periodic nest: motif placements repeated at m*v1 + n*v2 (mm).

    `alternates` are near-optimal patterns from the same search (within a few
    % of the primary's cell area): a skewed lattice that wins asymptotically
    can lose to an axis-aligned one on a finite sheet, so `count`/`realize`
    evaluate the primary and every alternate and use whichever fits most.
    """

    contour: np.ndarray
    spacing: float
    motif: list[Placement]
    v1: tuple[float, float]
    v2: tuple[float, float]
    runtime: float = 0.0
    alternates: list["TilePattern"] = field(default_factory=list)

    @property
    def cell_area(self) -> float:
        return abs(self.v1[0] * self.v2[1] - self.v1[1] * self.v2[0])

    @property
    def area_per_part(self) -> float:
        return self.cell_area / len(self.motif)

    @property
    def utilization(self) -> float:
        """Asymptotic (infinite sheet) material utilization."""
        return abs(_signed_area(self.contour)) / self.area_per_part

    def _enumerate(self, w, h, margin, phase):
        placements = []
        for p in self.motif:
            box = rotate_contour(self.contour, p.rotation)
            (minx, miny), (maxx, maxy) = box.min(axis=0), box.max(axis=0)
            xlo = margin - minx - p.x - phase[0]
            xhi = w - margin - maxx - p.x - phase[0]
            ylo = margin - miny - p.y - phase[1]
            yhi = h - margin - maxy - p.y - phase[1]
            if xlo > xhi + _COUNT_EPS or ylo > yhi + _COUNT_EPS:
                continue
            det = self.v1[0] * self.v2[1] - self.v1[1] * self.v2[0]
            ns = [
                (self.v1[0] * ty - self.v1[1] * tx) / det
                for tx, ty in ((xlo, ylo), (xhi, ylo), (xhi, yhi), (xlo, yhi))
            ]
            for n in range(
                math.ceil(min(ns) - _COUNT_EPS), math.floor(max(ns) + _COUNT_EPS) + 1
            ):
                mi = _interval(self.v1[0], n * self.v2[0], xlo, xhi)
                if mi is None:
                    continue
                m2 = _interval(self.v1[1], n * self.v2[1], ylo, yhi)
                if m2 is None:
                    continue
                for m in range(
                    math.ceil(max(mi[0], m2[0]) - _COUNT_EPS),
                    math.floor(min(mi[1], m2[1]) + _COUNT_EPS) + 1,
                ):
                    placements.append(
                        Placement(
                            p.rotation,
                            p.x + phase[0] + m * self.v1[0] + n * self.v2[0],
                            p.y + phase[1] + m * self.v1[1] + n * self.v2[1],
                        )
                    )
        return placements

    def _best_phase(self, w, h, margin, grid=8):
        box = rotate_contour(self.contour, self.motif[0].rotation)
        anchor = (
            margin - box[:, 0].min() - self.motif[0].x,
            margin - box[:, 1].min() - self.motif[0].y,
        )
        best = (anchor, self._enumerate(w, h, margin, anchor))
        for fx in range(grid):
            for fy in range(grid):
                phase = (
                    anchor[0] + (fx / grid) * self.v1[0] + (fy / grid) * self.v2[0],
                    anchor[1] + (fx / grid) * self.v1[1] + (fy / grid) * self.v2[1],
                )
                got = self._enumerate(w, h, margin, phase)
                if len(got) > len(best[1]):
                    best = (phase, got)
        return best

    def _best_variant(self, sheet_width, sheet_height, margin):
        variants = [self] + self.alternates
        got = [
            v._best_phase(sheet_width, sheet_height, margin)[1] for v in variants
        ]
        return max(got, key=len)

    def count(self, sheet_width, sheet_height, margin=0.0) -> int:
        """Parts on a WxH sheet (best of primary + alternates, lattice phase
        optimized over a small grid). A conservative estimate: edge slivers a
        greedy nest could still fill are not counted."""
        return len(self._best_variant(sheet_width, sheet_height, margin))

    def realize(self, sheet_width, sheet_height, margin=0.0) -> NestResult:
        """Materialize the pattern on a sheet as a NestResult (for SVG dumps
        and the same invariant checks nest_single results go through)."""
        result = NestResult(
            self.contour, (sheet_width, sheet_height), self.spacing, margin
        )
        result.placements = self._best_variant(sheet_width, sheet_height, margin)
        return result


def _interval(coeff, offset, lo, hi):
    """Solve lo <= m*coeff + offset <= hi for m; None if infeasible."""
    if abs(coeff) < 1e-12:
        return (-np.inf, np.inf) if lo - _COUNT_EPS <= offset <= hi + _COUNT_EPS else None
    a, b = (lo - offset) / coeff, (hi - offset) / coeff
    return (a, b) if a <= b else (b, a)


def find_tiling(
    contour: np.ndarray,
    *,
    max_parts: int = 2,
    rotations=4,
    spacing: float = 0.0,
    beam: int = 16,
    max_checks: int = 20000,
) -> TilePattern:
    """Best periodic tiling pattern of up to `max_parts` copies of a contour.

    Searches motifs per rotation multiset (touching positions from the NFP
    arrangement, beam-pruned by convex-hull area), then a minimum-area valid
    lattice per motif; returns the pattern with the lowest area per part,
    carrying near-optimal alternates for finite-sheet counting.
    Always succeeds: the axis-aligned bbox lattice is the fallback.

    max_parts=2 is the practical sweet spot: translation lattices already
    alternate rotations *between* lattice rows via the motif, and 3+ part
    motifs grow the search combinatorially for rarely any density gain.
    """
    poly = _clean_contour(contour)
    angles = _rotation_angles(rotations)
    t0 = time.perf_counter()
    rot_poly, rot_path, rot_bbox, nfps = _pair_nfps(poly, angles, spacing)
    part_area = abs(_signed_area(poly))

    # Cell-area lower bound: parts inflated by spacing/2 are pairwise
    # disjoint in any valid nest, so a cell can never be smaller than that
    # inflated area. Lets the sorted candidate scan start at feasible sizes.
    if spacing > 0:
        grown = _inflate(rot_path[angles[0]], spacing / 2)
        min_area = sum(abs(pyclipper.Area(p)) for p in grown)
    else:
        min_area = part_area * SCALE**2
    step = max(
        int(min(b[1][i] - b[0][i] for b in rot_bbox.values() for i in (0, 1))
            * SCALE) // 8,
        int(0.5 * SCALE),
    )

    candidates = []  # (area_per_part, motif, v1, v2)
    for k in range(1, max_parts + 1):
        if k == 1:
            motifs = [[Placement(a, 0.0, 0.0)] for a in angles]
        else:
            motifs = []
            for multiset in combinations_with_replacement(angles, k):
                partial = [[Placement(multiset[0], 0.0, 0.0)]]
                for rot in multiset[1:]:
                    ext = [
                        m + [Placement(rot, pt[0] / SCALE, pt[1] / SCALE)]
                        for m in partial
                        for pt in _motif_candidates(m, rot, nfps)
                    ]
                    ext.sort(
                        key=lambda m: _hull_area(
                            np.vstack(
                                [
                                    rot_poly[p.rotation] + (p.x, p.y)
                                    for p in m
                                ]
                            )
                        )
                    )
                    partial = ext[:beam]
                motifs += partial
            motifs.sort(
                key=lambda m: _hull_area(
                    np.vstack([rot_poly[p.rotation] + (p.x, p.y) for p in m])
                )
            )
            motifs = motifs[:beam]
        for motif in motifs:
            for v1, v2, det in _lattice_search(
                _self_nfp(motif, nfps), int(min_area * k * 0.995), step, max_checks
            ):
                candidates.append(
                    (
                        det / SCALE**2 / k,
                        motif,
                        (v1[0] / SCALE, v1[1] / SCALE),
                        (v2[0] / SCALE, v2[1] / SCALE),
                    )
                )

    best = min(candidates, key=lambda c: c[0]) if candidates else None
    if best is None:  # bbox-grid fallback, always valid
        a = min(
            angles,
            key=lambda a: (rot_bbox[a][1][0] - rot_bbox[a][0][0] + spacing)
            * (rot_bbox[a][1][1] - rot_bbox[a][0][1] + spacing),
        )
        (minx, miny), (maxx, maxy) = rot_bbox[a]
        best = (
            (maxx - minx + spacing) * (maxy - miny + spacing),
            [Placement(a, 0.0, 0.0)],
            (maxx - minx + spacing, 0.0),
            (0.0, maxy - miny + spacing),
        )

    alternates = [
        TilePattern(poly, spacing, m, v1, v2)
        for app, m, v1, v2 in sorted(candidates, key=lambda c: c[0])[1:]
        if app <= best[0] * 1.08
    ][:6]
    return TilePattern(
        poly, spacing, best[1], best[2], best[3],
        time.perf_counter() - t0, alternates,
    )


# ---------------------------------------------------------------------------
# debug output


def write_svg(result: NestResult, path: str) -> None:
    """Write the nest as a standalone SVG (sheet outline + placed parts)."""
    w, h = result.sheet
    colors = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#76b7b2"]
    angle_ids = {
        a: i for i, a in enumerate(sorted({p.rotation for p in result.placements}))
    }
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 {w + 2} {h + 2}" '
        f'width="{(w + 2) * 3}" height="{(h + 2) * 3}">',
        f'<g transform="translate(0 {h}) scale(1 -1)">',
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#f4f1ea" '
        'stroke="#444" stroke-width="0.5"/>',
    ]
    for placement, poly in zip(result.placements, result.polygons()):
        pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in poly)
        color = colors[angle_ids[placement.rotation] % len(colors)]
        lines.append(
            f'<polygon points="{pts}" fill="{color}" fill-opacity="0.65" '
            f'stroke="#222" stroke-width="0.3"/>'
        )
    lines += ["</g>", "</svg>"]
    with open(path, "w") as fh:
        fh.write("\n".join(lines))
