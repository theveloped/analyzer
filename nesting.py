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
- exact pairwise `spacing` (round-join inflation of the placed contour before
  the Minkowski sum) and sheet `margin`;
- part holes are ignored for collision (parts are never nested inside other
  parts' cutouts — valid, just conservative).

Coordinates are float millimetres in and out; Clipper works on int64 at
`SCALE` units per mm. All contours are simple closed polygons as (N, 2)
arrays, no repeated end vertex required.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

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
    rot_poly = {a: rotate_contour(poly, a) for a in angles}
    rot_path = {a: _scale_path(rot_poly[a]) for a in angles}
    rot_bbox = {
        a: (rot_poly[a].min(axis=0), rot_poly[a].max(axis=0)) for a in angles
    }
    # Placed-part source geometry, inflated by the full spacing so the NFP
    # boundary sits exactly `spacing` away from contact (dist(A,B) >= s iff
    # B misses A (+) disc_s) — round joins keep that exact at corners.
    src = {
        a: _inflate(rot_path[a], spacing) if spacing > 0 else [rot_path[a]]
        for a in angles
    }
    nfps = {(a, b): _nfp(src[a], rot_path[b]) for a in angles for b in angles}

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
