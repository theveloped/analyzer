"""Turning (lathe) recognition: the maximal turned state and per-face roles.

A part is *turned* when most of its surface is a body of revolution about one
axis; the rest is milled away afterwards. This module finds that axis, builds
the **maximal turned state** — the smallest solid of revolution containing the
part, i.e. the union of all rotations of the part about the axis — and labels
every face by the turning operation that would produce it (OD/ID turning and
facing) or as milled leftover.

The whole thing rests on one identity. A surface is a surface of revolution
about the axis line ``(p, d)`` **iff its normal everywhere has no azimuthal
component** — equivalently, the normal line is coplanar with the axis (it lies
in the meridian half-plane through its own point)::

    r  =  ((c - p) x n) . d  =  n . (d x (c - p))  =  0

``r`` is a *length*, not an angle: ``r = rho * sin(alpha)`` where ``rho`` is the
distance from the axis and ``alpha`` the tilt off the meridian plane. Two
things fall out of it:

**A per-face test that is one numpy pass**, uniform over every surface kind —
cylinder, cone, torus, sphere centred on the axis, plane perpendicular to it,
and crucially B-splines and surfaces of revolution, which carry
``surface_params: null`` in brep_meta.json and cannot be classified
analytically at all today. It correctly rejects planes containing the axis
(``|r| = rho``), off-axis holes and milled flats.

**A least-squares axis fit in 3x3 blocks.** Expanding the determinant with
``w = d x p`` makes the condition linear in the Plucker coordinates of the axis
line, ``(c x n).d - n.w = 0``, so a global seed comes from the Schur complement
of the 6x6 normal matrix (see ``_plucker_axis``). Refinement then alternates
two exact closed-form blocks (``_fit_direction`` / ``_fit_point``), each a
monotone decrease of ``sum w r^2``. That is cheaper than re-solving the 6x6 and
avoids two traps: a naive 6x6 null vector admits ``d = 0`` solutions (any
extruded prism satisfies ``n.w = 0`` for ``w`` along the extrusion, and that
spurious null often beats the true axis), and the ``w.d`` gauge freedom lets
facing planes be double-counted.

Weighting: the fit uses ``r`` as-is with area weights, so it is implicitly
radius^2-weighted — desirable (a big flange should outvote a small pin) and it
makes the on-axis degeneracy self-healing, since a face at ``rho ~ 0``
contributes a near-zero row whatever its normal. Scoring must divide the radius
back out and threshold the *sine* ``|r| / rho``; normalizing the fit rows by
``1/rho`` instead would let near-axis noise dominate.

Tolerance. ``brep.analytic_face_normals`` evaluates the exact surface normal at
the fine triangle *centroid*, the same point this module uses for ``c``, so on
analytic STEP faces ``r`` is zero to machine precision and the chord error
contributes nothing. Freeform STEP faces need a pure length slack (the
tessellation deflection). STL facet normals are the hard case: the facet normal
is radial at the chord midpoint azimuth while the centroid sits at the 1/3
azimuth, giving ~``phi/6`` of azimuthal error (about 2 degrees at typical
deflections) that no length slack fixes — hence the STL default of 5 degrees.

**The turned state is a REGION, not just a silhouette.** ``outer_profile``
gives its outer boundary and ``inner_profile`` the inner one — the smallest
radius carrying material at each height, which the normals decide (material
lies inside an outward-facing surface and outside an inward-facing one, so the
innermost surface tells a bore from a solid shaft). A lathe only ever cuts the
boundary of that region, so where a face sits relative to it is what says
whether it can be turned at all, and through which side. A bolt-hole
counterbore floor is a perfectly good annulus about its own axis but lies
strictly inside the region, and no tool reaches it.

That the outer boundary is a maximum over azimuth is a FEATURE, not an
approximation to apologise for. A lathe leaves the whole turned state and the
milling comes afterwards, so one rib standing on a flange really does block
the facing cut all the way round, even where the face is wide open. Such a
face is not milled outright — it comes out MIXED, and the cut divides it into
the ring a lathe can face and the annulus a mill has to take.

Roles are decided per EFFECTIVE face, never per triangle. A facing surface
spanning a wide radial band has triangles on both sides of any radius
threshold, so a per-triangle test splits the face and the vote then lands
wherever the tessellation happens to weigh more. Only the *participation* test
is local, and that is what drives the needs-split flag: a face that is part
turned and part not gets ``brep_default = CONFLICT_ROLE`` and paints its two
parts separately, so a user cut can resolve it. A cylinder running on under a
flange — exposed over part of its length, buried over the rest — is the case
that wants a cut, and it is found exactly this way.

Known limitations, all deliberate in this phase:

- A plane *parallel to and offset from* the axis has a hairline turnable stripe
  through its centre (width ~ ``2 h tan(tol)``), and a cross-hole has a turnable
  mid-plane ring. Both are measure-zero. Two guards kill them: the ``COMPAT_FRACTION``
  coverage gate for the role, and the ``SPLIT_STABILITY`` tolerance-halving test
  for the needs-split flag — without the latter every flag on a real part is one
  of these artifacts. An STL part has no BREP faces to aggregate over and will
  show speckle there.
- Reachability is positional only: a face on the boundary of the turned state
  counts as turnable without asking whether a tool of any particular shape
  fits. A deep narrow groove passes.
- Blocking is judged one bin along the normal, so a distant overhang that
  clears the immediate neighbourhood is not seen.
- Boundary membership is measured against a BINNED profile, so a face whose
  radius sweeps faster than the bin resolution leans on the vertex extent
  (outer) and the within-bin sweep (inner) rather than on the profile itself.
- The binned profile over-estimates on steep tapers by up to one bin of taper.
  Conservative in the right direction for a stock envelope; reported as
  ``stats["profile_error"]``.
- "Is a solid of revolution" is not "a lathe can make it": tool reach, recess
  accessibility and minimum groove width are not modelled here.

All effective face ids are ``splits.effective_face_ids`` ids (sub-faces when
user cuts are current, plain BREP face ids otherwise).
"""

import math
from dataclasses import dataclass, field

import numpy as np
from loguru import logger

import machining
import pipeline
import splits
from utils import log_execution_time

# index == category code in the per-face field; mirrored in
# frontend/src/processes/cnc/turning.ts
#
# The role is (side x operation). OPERATION is `face` when the normal is
# parallel to the axis (a facing cut) and `turn` otherwise (a profiling pass —
# cylinder, taper, chamfer, radius, contour, all the same tool motion). SIDE is
# which boundary of the turned section the face lies on: OD outward, ID inward.
#
# For a radial face the side is the sign of the normal's radial component —
# material is inside an OD surface and outside an ID one, so the outward normal
# points away from the axis on the OD and toward it on the ID. That test is
# purely local, which is what makes it right for chamfers and fillets at a
# diameter transition: they are outward-facing whether or not they happen to
# reach the widest radius at their own axial station.
#
# For a facing cut the normal carries no radial information, so the side comes
# from whether the face reaches the outer envelope at its own z.
TURN_ROLES = ["other", "od_face", "od_turn", "id_face", "id_turn", "on_axis"]
(ROLE_OTHER, ROLE_OD_FACE, ROLE_OD_TURN,
 ROLE_ID_FACE, ROLE_ID_TURN, ROLE_ON_AXIS) = range(6)

TOLLERANCE = 1e-9
# |n.d| >= cos(this) makes a face "axial" (a facing surface). Not a param: a
# real facing cut is perpendicular to a fraction of a degree, and the slack is
# only there to absorb tessellation.
FACE_NORMAL_TOL_DEG = 5.0
# Fixed constants rather than params: these are properties of what "turnable"
# means, not knobs a user should have to dial per part.
#   COMPAT_FRACTION   share of a face's area that must be revolution-compatible
#   SWING_TOLLERANCE  how much a facing cut's radial band may vary with azimuth
#                     before it stops being an annulus (a square boss top swings
#                     ~0.5, a real annulus ~0.001)
#   MIN_RADIAL_FRACTION  swept area below which no axis is believable at all —
#                     every plane perpendicular to a candidate axis is trivially
#                     a surface of revolution about it, so a plain box scores
#                     ~55% compatible area on normals alone
COMPAT_FRACTION = 0.9
SWING_TOLLERANCE = 0.1
#   SPLIT_FLOOR       both sides of a mixed face must hold at least this share
#                     of its area before it is worth flagging for a cut
#   SPLIT_STABILITY   how much of a face's compatible area must survive
#                     halving the tolerance before the compatible part counts
#                     as a real turned patch rather than a zero-crossing stripe.
#                     A genuine patch is EXACTLY compatible and scores 1.0, so
#                     the bar is high: off-axis cones are tangential rather than
#                     transversal zeros and still reach 0.80.
#   SPAN_TOLLERANCE   how much of the full turn a facing cut must span. A
#                     counterbore floor on a bolt circle is a perfect annulus
#                     about ITS OWN axis and barely swings over the few
#                     azimuths it occupies, so only this rejects it. Set well
#                     below half a turn: a real facing cut interrupted by
#                     milled slots arrives as several part-annuli (a NIST test
#                     part has one split in two at 0.42 a side) and those are
#                     still turned — the slot is a later operation. Bolt-circle
#                     counterbores sit at 0.08.
SPAN_TOLLERANCE = 0.25
SPLIT_FLOOR = 0.05
SPLIT_STABILITY = 0.85
CONFLICT_ROLE = 254  # brep_default sentinel, as molding.DEFAULT_CONFLICT
MIN_RADIAL_FRACTION = 0.15
TURNED_FRACTION = 0.95
# Trimmed re-fit schedule, loose first: (tolerance multiplier, support
# quantile). The quantile term is what makes a COLD seed usable — a PCA or
# world-axis seed can start tens of degrees off, where a pure tolerance band
# selects nothing and the fit has no support to move on. Keeping the best
# fraction of faces by azimuthal SINE (scale-free; trimming on the raw residual
# would just select whatever sits nearest the axis) always leaves something to
# pull on, and the band tightens to pure tolerance by the last round.
TRIM_SCHEDULE = ((4.0, 0.70), (2.0, 0.40), (1.0, 0.0))


@dataclass
class Axis:
    """A turning axis line, canonicalized (``point`` is the foot of the
    perpendicular from the part centroid, ``direction`` is sign-canonical)."""

    point: np.ndarray
    direction: np.ndarray
    source: str = "unknown"
    detail: dict = field(default_factory=dict)

    def as_dict(self):
        return {"point": [float(v) for v in self.point],
                "direction": [float(v) for v in self.direction],
                "source": self.source, **self.detail}


def _unit(vector):
    vector = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(vector))
    if norm < TOLLERANCE:
        return None
    return vector / norm


def _cross_matrix(fixed):
    """``K`` with ``v @ K == v x fixed`` for a stack of row vectors.

    Crossing an ``(F, 3)`` stack against one fixed vector is a 3x3 gemm, and
    BLAS does that several times faster than ``np.cross``'s generic path — the
    difference is worth roughly a second on a multi-million-face part.
    """
    x, y, z = (float(component) for component in fixed)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def _canonical_axis(point, direction, origin, source="unknown", detail=None):
    """The same line expressed uniquely: direction sign-canonical, point at the
    foot of the perpendicular dropped from ``origin``."""
    direction = _unit(direction)
    if direction is None:
        return None
    if direction[int(np.argmax(np.abs(direction)))] < 0:
        direction = -direction
    point = np.asarray(point, dtype=np.float64)
    origin = np.asarray(origin, dtype=np.float64)
    point = point + float((origin - point) @ direction) * direction
    return Axis(point=point, direction=direction, source=source,
                detail=dict(detail or {}))


def _line_distance(axis_a, axis_b):
    """Perpendicular offset between two (near-parallel) axis lines."""
    delta = axis_b.point - axis_a.point
    return float(np.linalg.norm(delta - (delta @ axis_a.direction)
                                * axis_a.direction))


def merge_axis_lines(axes, *, angle_tol_deg=1.0, dist_tol=1e-2):
    """Coaxially merge candidate axes on the FULL LINE.

    ``analysis.hole_axes_from_geometry`` merges by direction only, which fuses
    every parallel hole of a bolt circle into one bogus axis; the
    ``machining_features._coaxial`` test additionally demands an axial-span
    overlap, which a turning axis must not require (the OD and a bore at the
    far end are coaxial but need not overlap). This is the middle ground:
    parallel to within ``angle_tol_deg`` AND within ``dist_tol`` laterally.

    Weights accumulate onto the surviving representative; the list comes back
    ordered by descending weight.
    """
    cos_tol = math.cos(math.radians(angle_tol_deg))
    merged = []
    for axis in axes:
        if axis is None:
            continue
        for keeper in merged:
            if abs(float(axis.direction @ keeper.direction)) < cos_tol:
                continue
            if _line_distance(keeper, axis) > dist_tol:
                continue
            keeper.detail["weight"] = (keeper.detail.get("weight", 0.0)
                                       + axis.detail.get("weight", 0.0))
            break
        else:
            merged.append(axis)
    merged.sort(key=lambda a: -a.detail.get("weight", 0.0))
    return merged


def azimuthal_residual(centroids, normals, axis, *, cross_cn=None,
                       sq_norms=None):
    """``(r, rho, z)`` for every face against one axis.

    ``r = ((c - p) x n) . d`` is the azimuthal moment — a LENGTH, equal to
    ``rho * sin(tilt off the meridian plane)``. ``rho`` is the distance from
    the axis, ``z`` the axial coordinate measured from ``axis.point``.

    Pass the precomputed ``cross_cn = c x n`` and ``sq_norms = |c|^2`` (both
    axis-independent) to make each call three matrix-vector products instead of
    two cross products — that is what keeps the candidate search cheap.
    """
    point = np.asarray(axis.point, dtype=np.float64)
    direction = np.asarray(axis.direction, dtype=np.float64)

    if cross_cn is None:
        # ((c-p) x n).d == (c-p).(n x d), and n x d against a FIXED d is a gemm
        residual = np.einsum('ij,ij->i', centroids - point,
                             normals @ _cross_matrix(direction))
    else:
        # r = (c x n).d - n.(d x p)
        residual = cross_cn @ direction - normals @ np.cross(direction, point)

    axial = centroids @ direction - float(point @ direction)
    if sq_norms is None:
        radial = centroids - point - np.outer(axial, direction)
        rho = np.linalg.norm(radial, axis=1)
    else:
        rho2 = (sq_norms - 2.0 * (centroids @ point) + float(point @ point)
                - axial * axial)
        rho = np.sqrt(np.maximum(rho2, 0.0))
    return residual, rho, axial


def _fit_direction(centroids, normals, weights, point):
    """Best direction for a fixed point on the axis.

    With ``p`` fixed the residual is linear in ``d`` alone: ``r = g . d`` with
    ``g = (c - p) x n``. So the least-squares direction is the smallest
    eigenvector of ``sum w g g^T`` — exact, scale-free, and ``|d| = 1`` is a
    hard constraint rather than a post-hoc normalization.
    """
    g = np.cross(centroids - point, normals)
    matrix = (g * weights[:, None]).T @ g
    if not np.all(np.isfinite(matrix)):
        return None
    values, vectors = np.linalg.eigh(matrix)
    return vectors[:, 0], float(values[0]), float(values[-1])


def _fit_point(centroids, normals, weights, direction):
    """Best point for a fixed direction.

    With ``d`` fixed the residual is linear in ``p``: ``r = beta - p . h`` with
    ``h = n x d`` and ``beta = (c x n) . d``. ``H = sum w h h^T`` is exactly
    rank 2 (every ``h`` is perpendicular to ``d``), which pins the line but
    leaves the point free to slide along it. In exact arithmetic the
    minimum-norm lstsq solution would already be the foot of the perpendicular;
    in practice lstsq's rcond cutoff lets the numerically-tiny third singular
    value through and a sizeable ``d`` component leaks in, so project it out
    explicitly rather than trusting the cutoff.
    """
    h = normals @ _cross_matrix(direction)
    beta = np.einsum('ij,ij->i', centroids, h)
    matrix = (h * weights[:, None]).T @ h
    rhs = (h * (weights * beta)[:, None]).sum(axis=0)
    if not (np.all(np.isfinite(matrix)) and np.all(np.isfinite(rhs))):
        return None
    point, *_ = np.linalg.lstsq(matrix, rhs, rcond=None)
    return point - float(point @ direction) * direction


def _plucker_axis(centroids, normals, weights, origin, scale, *,
                  source="plucker"):
    """Global best-fit axis from the 6x6 Plucker normal matrix.

    Rows are ``[(c x n)^T, -n^T]``. Reducing by the Schur complement over the
    ``w`` block turns this into a 3x3 eigenproblem in ``d`` with ``|d| = 1``
    enforced, which is what rules out the degenerate ``d = 0`` family. Solved
    in coordinates centred on ``origin`` and scaled by ``scale``, because
    ``c x n`` is length-scaled while ``n`` is dimensionless and mixing them raw
    conditions the 6x6 badly on a part far from the world origin.
    """
    local = (centroids - origin) / scale
    rows = np.empty((len(local), 6), dtype=np.float64)
    rows[:, :3] = np.cross(local, normals)
    rows[:, 3:] = -normals

    # fold sqrt(w) into the rows so the normal matrix is one symmetric gemm
    # instead of a weighted copy plus an asymmetric product
    rows *= np.sqrt(weights)[:, None]
    matrix = rows.T @ rows
    if not np.all(np.isfinite(matrix)):
        return None
    block_dd = matrix[:3, :3]
    block_dw = matrix[:3, 3:]
    block_ww = matrix[3:, 3:]
    # pinv, not inv: block_ww is rank 2 for a capless cylinder (the normals
    # only span the plane perpendicular to the axis)
    inv_ww = np.linalg.pinv(block_ww)
    schur = block_dd - block_dw @ inv_ww @ block_dw.T
    values, vectors = np.linalg.eigh(0.5 * (schur + schur.T))
    direction = vectors[:, 0]

    moment = -inv_ww @ block_dw.T @ direction
    moment = moment - float(moment @ direction) * direction
    # w = d x p, so the foot of the perpendicular is p = w x d
    point = np.cross(moment, direction) * scale + origin
    return _canonical_axis(point, direction, origin, source=source,
                           detail={"eigenvalues": [float(v) for v in values]})


def _normal_null_axis(normals, weights, origin, *, source="normal_null"):
    """Smallest eigenvector of ``sum w n n^T`` — the axis of a freeform shell.

    A shaft whose OD came through as a B-spline has no analytic axis anywhere
    in brep_meta.json, but its normals still all lie perpendicular to the axis,
    so the null direction of the normal bundle recovers it. Same trick
    ``tube._round_parameters`` uses for B-spline tubes.
    """
    matrix = (normals * weights[:, None]).T @ normals
    if not np.all(np.isfinite(matrix)):
        return None
    values, vectors = np.linalg.eigh(matrix)
    return _canonical_axis(origin, vectors[:, 0], origin, source=source,
                           detail={"eigenvalues": [float(v) for v in values]})


def seed_axes(centroids, normals, weights, verts, surface_params, face_ids, *,
              angle_tol_deg=1.0, dist_tol=1e-2, max_candidates=12,
              face_areas=None):
    """Candidate turning axes, best first.

    Sources, in the order they are generated: analytic quadric axes from
    brep_meta.json (area-ranked), all three PCA axes of the vertex cloud, the
    global Plucker fit, the normal-bundle null direction, and the world axes as
    a safety net. Everything is coaxially merged as full lines and capped.

    All three PCA axes, not just the major one: a disc or flange is turned
    about its *minor* principal axis.

    ``centroids``/``normals``/``weights`` may be a subsample — a seed only has
    to land in the refiner's basin. ``face_ids``/``face_areas`` must be the
    FULL arrays, so the analytic axes are ranked by true face area.
    """
    import analysis

    origin = verts.mean(axis=0).astype(np.float64)
    scale = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))) or 1.0
    if face_areas is None:
        face_areas = weights

    candidates = []
    if surface_params is not None and face_ids is not None:
        area_by_face = np.bincount(face_ids, weights=face_areas)
        for index, params in enumerate(surface_params):
            if not params:
                continue
            kind = params.get("type")
            if kind == "cylinder":
                anchor = params.get("point")
            elif kind == "cone":
                anchor = params.get("apex")
            elif kind == "torus":
                anchor = params.get("center")
            else:
                continue
            weight = (float(area_by_face[index])
                      if index < len(area_by_face) else 0.0)
            if weight <= 0.0:
                continue
            candidates.append(_canonical_axis(
                anchor, params.get("axis"), origin, source=kind,
                detail={"weight": weight}))

    total_area = float(face_areas.sum())
    for rank, direction in enumerate(analysis.pca_axes(verts)):
        candidates.append(_canonical_axis(
            origin, direction, origin, source="pca",
            detail={"weight": total_area * (0.30 - 0.05 * rank),
                    "pca_rank": rank}))

    for axis in (_plucker_axis(centroids, normals, weights, origin, scale),
                 _normal_null_axis(normals, weights, origin)):
        if axis is not None:
            axis.detail["weight"] = total_area * 0.25
            candidates.append(axis)

    for direction in np.eye(3):
        candidates.append(_canonical_axis(
            origin, direction, origin, source="world",
            detail={"weight": total_area * 0.01}))

    merged = merge_axis_lines(candidates, angle_tol_deg=angle_tol_deg,
                              dist_tol=dist_tol)
    return merged[:max_candidates]


def refine_axis(centroids, normals, weights, axis, *, sin_tol, slack,
                rho_floor, rounds=3, cross_cn=None, sq_norms=None):
    """Trimmed re-fit of one candidate axis.

    Each round re-selects inliers at a shrinking band and re-solves the whole
    line *jointly* on that subset via the Schur complement.

    Alternating the two closed-form blocks (``_fit_direction`` / ``_fit_point``)
    instead is tempting — each is exact for its own half — but the blocks are
    strongly coupled and block coordinate descent only converges linearly at
    ~0.77 per round, i.e. tens of rounds to land a 5-degree seed. The joint
    solve gets the same data to machine precision in one. The alternating
    blocks stay useful as the final polish: ``_fit_point`` is exact for a fixed
    direction and removes any residual gauge artifact from the joint solve's
    unconstrained ``w`` (whose ``d`` component is not part of any real line and
    can otherwise absorb a share of the axial-normal term).
    """
    point = np.asarray(axis.point, dtype=np.float64)
    direction = np.asarray(axis.direction, dtype=np.float64)
    schedule = TRIM_SCHEDULE[-rounds:] if rounds else ()
    origin = centroids.mean(axis=0)
    scale = float(np.linalg.norm(centroids.max(axis=0)
                                 - centroids.min(axis=0))) or 1.0

    for multiplier, quantile in schedule:
        current = Axis(point=point, direction=direction)
        residual, rho, _ = azimuthal_residual(
            centroids, normals, current, cross_cn=cross_cn, sq_norms=sq_norms)
        usable = rho > rho_floor
        if int(usable.sum()) < 8:
            break
        sine = np.abs(residual) / np.maximum(rho, rho_floor)
        band = multiplier * (sin_tol + slack / np.maximum(rho, rho_floor))
        inliers = usable & (sine <= band)
        if quantile > 0.0:
            # union, not replacement: guarantee support for a cold seed without
            # ever discarding a face the tolerance already accepted
            inliers |= usable & (sine <= float(np.quantile(sine[usable],
                                                           quantile)))
        if int(inliers.sum()) < 8:
            break
        sub_c = centroids[inliers]
        sub_n = normals[inliers]
        sub_w = weights[inliers]

        fitted = _plucker_axis(sub_c, sub_n, sub_w, origin, scale)
        if fitted is None:
            break
        # keep the sign continuous with the seed so the axis does not flip
        # between rounds and confuse the trimming
        candidate = fitted.direction
        direction = candidate if candidate @ direction >= 0 else -candidate
        point = fitted.point

        moved = _fit_point(sub_c, sub_n, sub_w, direction)
        if moved is not None:
            point = moved

    return _canonical_axis(point, direction, origin, source=axis.source,
                           detail=dict(axis.detail))


def score_axis(areas, residual, rho, normals, axis, *, sin_tol, slack,
               rho_floor, face_cos):
    """How well one axis explains the part, always area-weighted.

    ``inlier_fraction`` is the share of area that is revolution-compatible.
    ``radial_fraction`` is the share that is actually *swept* — compatible,
    off-axis, and not perpendicular to the axis. The second number is what
    separates a real turned part from a plate: every plane perpendicular to a
    candidate axis is trivially a surface of revolution about it, so a plain
    box scores ~0.55 on inliers alone and a drilled plate ~0.91.
    """
    inliers = np.abs(residual) <= sin_tol * rho + slack
    inliers &= rho > rho_floor
    total = float(areas.sum()) or 1.0
    radial = inliers & (np.abs(normals @ axis.direction) < face_cos)
    return {"inlier_fraction": float(areas[inliers].sum() / total),
            "radial_fraction": float(areas[radial].sum() / total),
            "inlier_area": float(areas[inliers].sum())}


@log_execution_time
def outer_profile(verts, faces, axis, *, bins=512):
    """The meridian of the maximal turned state.

    ``R_out(z) = max radius`` over the part at that axial station. Revolved,
    this is the union of all rotations of the part about the axis — the
    smallest solid of revolution containing it, which is exactly what turning
    alone can leave behind; milling removes the rest.

    Each bin's value is EXACT for the mesh, not a sample of it, and one fact
    buys that. Along a straight edge ``r(z)`` is convex: writing ``r^2`` as a
    quadratic ``a z^2 + b z + c`` (it is, because the radial vector is linear
    in the parameter), ``r'' = (4ac - b^2) / 4r^3``, and ``4ac - b^2 >= 0``
    because ``r^2`` is a squared length and so never negative. A convex
    function on an interval takes its maximum at an endpoint, so the largest
    radius an edge reaches inside a bin is reached either at a VERTEX or where
    the edge crosses a BIN BOUNDARY. Sampling both is therefore not an
    approximation — it is the per-bin maximum exactly.

    That is what makes the bin width a free choice. The older version sampled
    vertices only, so it had to widen bins to the 99th-percentile triangle
    extent to stop one falling between two rows of vertices, which pinned a
    482 mm part at 259 bins however many were asked for, and it patched the
    leftovers by raising empty bins to a spanning triangle's largest radius.
    That patch could only ever be applied to EMPTY bins: used everywhere it
    smears a shoulder's large diameter one bin into the small-diameter
    section, and since the facing test samples one bin past a surface, every
    shoulder would come back internal. With the crossings sampled there is
    nothing left to patch.

    Returns ``{low, high, step, r_out, widths, gap_bins}``. ``high - low`` is
    the exact axial extent of the part; the bin count is rounded up, so the
    last bin is short and ``widths`` carries the per-bin length that volume
    integration needs.
    """
    verts = verts.astype(np.float64)
    point = np.asarray(axis.point, dtype=np.float64)
    direction = np.asarray(axis.direction, dtype=np.float64)

    local = verts - point
    axial = local @ direction
    radial = np.linalg.norm(local - np.outer(axial, direction), axis=1)

    low, high = float(axial.min()), float(axial.max())
    span = high - low
    if span <= TOLLERANCE:
        return {"low": low, "high": high, "step": 1.0,
                "r_out": np.array([float(radial.max())]),
                "widths": np.array([0.0]), "gap_bins": 0}

    step = max(span / max(int(bins), 1), span * 1e-4)
    count = max(int(math.ceil(span / step)), 1)
    index = np.clip(((axial - low) / step).astype(np.int64), 0, count - 1)

    profile = np.zeros(count, dtype=np.float64)
    np.maximum.at(profile, index, radial)

    # the radial vector per vertex, so a crossing's radius is an interpolation
    # rather than a fresh projection
    perp = local - np.outer(axial, direction)

    # every triangle edge, deduplicated only by ordering — a max does not care
    # about duplicates, and sorting 9M pairs costs more than the extra passes
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]],
                            faces[:, [2, 0]]])
    CHUNK = 1 << 20  # bounds the crossing gather, which is the memory peak
    for begin in range(0, len(edges), CHUNK):
        first, second = edges[begin:begin + CHUNK].T
        z_a, z_b = axial[first], axial[second]
        lo_bin = np.minimum(index[first], index[second])
        hi_bin = np.maximum(index[first], index[second])
        spans = hi_bin - lo_bin  # number of boundaries strictly crossed
        if not spans.any():
            continue
        keep = spans > 0
        first, second = first[keep], second[keep]
        z_a, z_b = z_a[keep], z_b[keep]
        lo_bin, spans = lo_bin[keep], spans[keep]

        total = int(spans.sum())
        starts = np.concatenate([[0], np.cumsum(spans)[:-1]])
        rank = np.arange(total) - np.repeat(starts, spans)
        boundary = np.repeat(lo_bin, spans) + rank + 1  # bin index of the wall
        where = low + boundary * step

        owner = np.repeat(np.arange(len(spans)), spans)
        gap = z_b[owner] - z_a[owner]
        ratio = np.where(np.abs(gap) > TOLLERANCE,
                         (where - z_a[owner]) / np.where(gap == 0, 1.0, gap),
                         0.0)
        crossing = (perp[first[owner]]
                    + ratio[:, None] * (perp[second[owner]]
                                        - perp[first[owner]]))
        reach = np.linalg.norm(crossing, axis=1)
        # a boundary belongs to the bins on both of its sides
        np.maximum.at(profile, np.clip(boundary, 0, count - 1), reach)
        np.maximum.at(profile, np.clip(boundary - 1, 0, count - 1), reach)

    gaps = int((profile <= 0.0).sum())

    widths = np.full(count, step, dtype=np.float64)
    widths[-1] = span - (count - 1) * step
    return {"low": low, "high": high, "step": step, "r_out": profile,
            "widths": widths, "gap_bins": gaps}


@log_execution_time
def inner_profile(rho, radial_dot, lo_bin, hi_bin, count, *, rho_floor):
    """``R_in`` per bin: the innermost radius the turned state reaches.

    The maximal turned state is the union of all rotations of the part, so at
    height ``z`` it covers every radius some azimuth has material at. Its inner
    boundary is therefore the SMALLEST radius carrying material anywhere at
    that height — 0 wherever the axis itself runs through material.

    Which of those two it is, is a local question the normal already answers.
    Material lies inside an outward-facing surface and outside an inward-facing
    one, so if the innermost surface at a height faces outward the material
    continues to the axis and there is no bore; if it faces inward it IS the
    bore wall. That is why this reads the normals rather than taking a plain
    minimum over surface points, which cannot tell a bore from a solid shaft.

    Deliberately independent of the role assignment: the roles are decided by
    comparing against this profile, so deriving it FROM them (the first
    version took a minimum over the faces already called internal) is circular
    — a single misfiled face moved the boundary that judged every other one.
    """
    inward = radial_dot < 0.0
    outward = radial_dot > 0.0

    def smallest(mask):
        """Smallest radius over the triangles that REACH each bin.

        Spread across every bin a triangle spans, not just the one holding
        its centroid. The two sides are compared against each other, so a bin
        that happens to catch one and miss the other invents a bore out of
        nothing: at fine resolution a bin can hold a rib's inward-facing end
        face while the outward-facing shaft beside it lands in the
        neighbouring bin, and the comparison then reads a void that is solid
        metal. Spanning makes the answer independent of the bin width.
        """
        out = np.full(count, np.inf)
        if not mask.any():
            return out
        first, last = lo_bin[mask], hi_bin[mask]
        spans = last - first + 1
        total = int(spans.sum())
        starts = np.concatenate([[0], np.cumsum(spans)[:-1]])
        spread = np.repeat(first, spans) + (np.arange(total)
                                            - np.repeat(starts, spans))
        np.minimum.at(out, spread, np.repeat(rho[mask], spans))
        return out

    inner, solid = smallest(inward), smallest(outward)
    # innermost surface faces outward => material runs to the axis => no bore
    bore = np.isfinite(inner) & (inner < solid) & (inner > rho_floor)
    return close_hairline_breaks(np.where(bore, inner, 0.0),
                                 int(np.percentile(hi_bin - lo_bin, 99.0)) + 2)


def close_hairline_breaks(inner, widest):
    """Rejoin a bore across interruptions too narrow to be real.

    Where a rib crosses a bore, the station it occupies reports material at a
    small radius and the bore test fails for exactly the bins the rib touches.
    On a real part that is one or two bins -- a through cavity came back as
    three, meeting at z -0.66 and -0.57, a 0.09 mm break -- and it splits one
    cavity into several in both the section and the bore list.

    A break NARROWER THAN ONE TRIANGLE cannot separate two cavities, because
    no surface spans it to close the bore off. Wider breaks are left, which is
    what keeps a part bored from both ends with solid between it reading as
    two bores rather than one line drawn through the middle.
    """
    bored = inner > 0.0
    if not bored.any():
        return inner
    out = inner.copy()
    edges = np.flatnonzero(np.diff(bored.astype(np.int8)))
    spans = list(zip(np.r_[0, edges + 1], np.r_[edges + 1, len(bored)]))
    for begin, end in spans:
        if bored[begin] or begin == 0 or end == len(bored):
            continue
        if end - begin > widest:
            continue
        out[begin:end] = np.interp(np.arange(begin, end),
                                   [begin - 1, end], [inner[begin - 1],
                                                      inner[end]])
    return out


def boundary_membership(rho_hi, rho_lo, axial, axial_dot, low, step, outer,
                        inner, margin, face_cos, radial_dot):
    """``(on_outer, on_inner)`` per triangle — is it on the turned boundary?

    A lathe only ever cuts the boundary of the turned state. A face buried
    strictly inside it cannot be turned at all, whatever its normals do: the
    material outboard of it belongs to the part, so no tool reaches it. That
    is what makes a bolt-hole counterbore floor milled rather than internal
    facing, and it is the same test that decides OD from ID — the outer
    boundary is reachable from outside, the inner one only through a bore.

    The two face kinds probe the profile at different heights, and that
    difference is the whole subtlety. A RADIAL face follows the profile, so it
    is on the boundary where its own radius meets it. A FACING cut is a
    VERTICAL segment of the meridian — it spans a band of radii at one height
    — so testing it at its own height asks the wrong question and rejects
    every facing surface but its outermost rim. What makes it turnable is that
    nothing blocks the approach, so the probe steps one bin ALONG ITS OWN
    OUTWARD NORMAL and asks whether the turned region still covers that radius
    there. Past the end of the part the region is empty, which is what makes a
    part's end face turnable.
    """
    count = len(outer)

    def window(profile, combine):
        """The profile over each bin AND its two neighbours, most permissive.

        A single bin routinely straddles a shoulder and keeps only the extreme
        radius, so testing a bin against itself makes every junction fail: the
        bin where a cylinder meets a flange belongs entirely to the flange,
        and the bin holding a bore's floor reports no bore. Widening by one
        bin either way costs nothing — a face genuinely buried inside the
        turned state is buried by far more than one bin of profile.
        """
        shifted = [profile]
        if count > 1:
            shifted.append(np.r_[profile[0], profile[:-1]])
            shifted.append(np.r_[profile[1:], profile[-1]])
        return combine(shifted, axis=0)

    # outer is tested from below and inner from above, so "permissive" is the
    # minimum of the window for one and the maximum for the other
    outer_win = window(outer, np.minimum.reduce)
    inner_win = window(inner, np.maximum.reduce)


    # Sampled at a BIN, never interpolated. Interpolation smooths over exactly
    # the steps this has to detect: a flange top would be measured half-way up
    # the boss standing on it, and a bore wall half-way through its own floor.
    facing = np.abs(axial_dot) >= face_cos
    own = np.clip(np.floor((axial - low) / step), 0, count - 1).astype(np.int64)
    beyond = own + np.where(axial_dot >= 0, 1, -1)
    # a facing cut probes one bin ALONG ITS OWN NORMAL: what makes it turnable
    # is that nothing blocks the approach. Past the end of the part the turned
    # region is empty, which is what makes a part's end face turnable.
    outside = facing & ((beyond < 0) | (beyond >= count))
    sample = np.where(facing, np.clip(beyond, 0, count - 1), own)

    # The two sides need different slack, and the asymmetry is not arbitrary.
    # `outer` stores a per-bin MAXIMUM, which a triangle's outermost vertex
    # can reach, so no slack is needed and none is given — the outer test is
    # what detects blocking, and widening it by the local step would erase
    # exactly that signal. `inner` stores a per-bin MINIMUM, attained
    # somewhere else in the bin, so on a sloping chamfer no vertex of the
    # triangle can reach it; that side gets the within-bin sweep as slack.
    # Safe there because what the inner test must reject — cross-hole walls,
    # counterbore walls — misses the inner boundary by orders more than a bin
    # of taper.
    sweep = np.zeros_like(inner)
    if count > 1:
        drop = np.abs(np.diff(inner))
        sweep[:-1] = np.maximum(sweep[:-1], drop)
        sweep[1:] = np.maximum(sweep[1:], drop)

    r_out = np.where(outside, 0.0, outer_win[sample])
    r_in = np.where(outside, 0.0, inner_win[sample])
    # A steep surface is invisible to its own bin, the same way an edge-on
    # wall is invisible to its own column in the z-map raster, and the fix is
    # the one that solved it there: offer the sample SEVERAL positions and let
    # the face take the best of them. Here the measurement axis is radial, so
    # the lateral direction is the axial one, and a candidate steps along the
    # normal's axial component -- proportional to it, so a cylinder does not
    # move at all and an end round moves nearly a full bin.
    #
    # What it fixes: an end round turning over towards the axis crosses its
    # bin while still widening, so the bin's MAXIMUM radius belongs to the
    # part of it nearest the shoulder and the rest cannot reach it. A thin
    # band down the middle of every such round came back milled. Its own bin
    # still counts, so nothing that passes today stops passing.
    #
    # Facing cuts keep their own probe. They are the extreme of this rule --
    # a full bin along a normal that is all axial -- but they take it as a
    # fixed whole bin rather than a proportional step, and it is the blocking
    # test for the approach rather than a sampling correction.
    on_outer = rho_hi >= r_out - margin
    for reach in (1.0, 2.0):
        moved = axial + reach * step * axial_dot
        index = np.floor((moved - low) / step).astype(np.int64)
        past = (index < 0) | (index >= count)
        beside = np.where(past, 0.0, outer_win[np.clip(index, 0, count - 1)])
        on_outer |= ~facing & (rho_hi >= beside - margin)
    # A radial surface can only be the INNER boundary if it faces the axis.
    # The turned state's bore wall has material outboard of it, so its normal
    # points inward; one pointing away has material on the far side and is an
    # outer surface, buried or not. Without this a cylinder lying 1 mm under a
    # corrugated OD passed as internal turning, because the sweep slack that
    # exists for sloping chamfers is metres wide on a corrugated bore. Facing
    # cuts are exempt: a counterbore floor is axial and its radial component
    # says nothing either way.
    faces_axis = facing | (radial_dot <= 0.0)
    on_inner = (r_in > 0.0) & faces_axis & (rho_lo <= r_in + margin
                                            + sweep[sample])
    return on_outer, on_inner


def simplify_profile(points, tollerance):
    """Douglas-Peucker on a (z, r) polyline, iterative (explicit stack).

    Nothing in the repo simplifies polylines — nesting.py's pyclipper
    ``SimplifyPolygons`` is 2D polygon cleanup, not this — so it lives here.
    """
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 3:
        return points
    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        chord = points[last] - points[first]
        length = float(np.linalg.norm(chord))
        segment = points[first + 1:last]
        if length < TOLLERANCE:
            distance = np.linalg.norm(segment - points[first], axis=1)
        else:
            # the 2-D cross product written out: np.cross on 2-vectors is
            # deprecated in NumPy 2 and slated for removal
            offsets = segment - points[first]
            area = chord[0] * offsets[:, 1] - chord[1] * offsets[:, 0]
            distance = np.abs(area) / length
        offset = int(np.argmax(distance))
        if float(distance[offset]) <= tollerance:
            continue
        split = first + 1 + offset
        keep[split] = True
        stack.append((first, split))
        stack.append((split, last))
    return points[keep]


def mesh_volume(verts, faces):
    """Enclosed volume by the divergence theorem — exact on the mesh.

    Indexed a column at a time rather than as ``verts[faces]``: that gathers a
    float64 ``(F, 3, 3)``, which is 200 MB on a 3M-face part.
    """
    first = verts[faces[:, 0]]
    second = verts[faces[:, 1]]
    third = verts[faces[:, 2]]
    # the scalar triple product written out per component: np.cross would
    # allocate another (F, 3) just to be contracted away immediately
    total = (first[:, 0] * (second[:, 1] * third[:, 2]
                            - second[:, 2] * third[:, 1])
             + first[:, 1] * (second[:, 2] * third[:, 0]
                              - second[:, 0] * third[:, 2])
             + first[:, 2] * (second[:, 0] * third[:, 1]
                              - second[:, 1] * third[:, 0]))
    return float(total.sum() / 6.0)


def face_metrics(centroids, normals, areas, axis, residual, rho, axial,
                 face_ids, n_faces, *, sin_tol, slack, rho_floor, face_cos,
                 on_outer=None, on_inner=None, azimuth_bins=24):
    """Per-effective-face geometry, area-weighted, all by ``np.bincount``.

    Every turning decision is taken at this granularity, never per triangle.
    That is not an optimization — it is required for correctness. A facing
    surface spanning a wide radial band has triangles on both sides of any
    radius threshold, so a per-triangle test splits the face and the vote
    then lands wherever the tessellation happens to weigh more.

    ``theta_span``/``radius_swing`` are the annularity pair: a face is a
    genuine surface of revolution only if its radial band is the same at every
    azimuth it occupies. A plane perpendicular to the axis passes the normal
    test trivially — every such plane is locally a surface of revolution — so
    a milled pocket floor or a square boss top can only be told from a real
    annulus by its radius varying with theta.

    ``azimuth_bins`` is deliberately coarse. The metric reads triangle
    centroids, and near the rim of a tessellated disc those are sparse in
    azimuth; fine buckets then miss the true outer radius and report a genuine
    annulus as non-circular. At 24 buckets the separation is an order of
    magnitude (real annuli land under 0.04, a square boss top at 0.43).
    """
    face_ids = np.asarray(face_ids, dtype=np.int64)
    band = sin_tol * rho + slack
    revolved = (np.abs(residual) <= band) | (rho <= rho_floor)
    if on_outer is None:
        on_outer = np.ones(len(rho), dtype=bool)
        on_inner = np.zeros(len(rho), dtype=bool)

    # A triangle takes part in turning only if it is also on the boundary of
    # the turned state: being a surface of revolution is not enough when the
    # material outboard of it belongs to the part. Two shapes of that:
    #
    #   radial  a cylinder that runs on under a flange — exposed over part of
    #           its length, buried over the rest, wanting a cut between them
    #   facing  a flange top with a boss standing on it. The annulus outside
    #           the boss can be faced; the part under it cannot, because the
    #           lathe leaves the WHOLE turned state and the boss is part of
    #           it. Milling comes afterwards, so the axisymmetric envelope is
    #           the right blocker even though it is a maximum over azimuth.
    #
    # Neither becomes "milled" outright — a face that is partly on the
    # boundary comes out MIXED, and the cut divides it into the piece a lathe
    # makes and the piece a mill has to.
    inlier = revolved & (on_outer | on_inner)
    # the same test at half the tolerance. A genuinely revolved patch keeps
    # essentially all of its area; a measure-zero artifact — the hairline
    # stripe down an offset plane, the mid-plane ring of a cross-hole — is a
    # transversal ZERO CROSSING of the residual, so its width is proportional
    # to the tolerance and it loses about half. That ratio is what separates
    # "this face is part turned and part milled" from "this face is milled and
    # the residual happens to pass through zero along a line".
    inlier_half = ((np.abs(residual) <= 0.5 * band) | (rho <= rho_floor))
    inlier_half &= on_outer | on_inner

    weights = areas * inlier

    direction = np.asarray(axis.direction, dtype=np.float64)
    axial_dot = normals @ direction
    # n.rho_hat without materializing the radial vectors:
    #   rho_hat = (c - p - z d)/rho  =>  n.rho_hat = (n.(c-p) - z (n.d)) / rho
    radial_dot = ((np.einsum('ij,ij->i', normals, centroids - axis.point)
                   - axial * axial_dot) / np.maximum(rho, rho_floor))

    def total(values):
        return np.bincount(face_ids, weights=values, minlength=n_faces)

    def extreme(values, op, fill):
        out = np.full(n_faces, fill, dtype=np.float64)
        op(out, face_ids, values)
        return out

    area_total = np.maximum(total(areas), TOLLERANCE)
    area_inlier = total(weights)
    safe = np.maximum(area_inlier, TOLLERANCE)

    metrics = {
        "area": area_total,
        "inlier_fraction": area_inlier / area_total,
        "stability": total(areas * inlier_half) / safe,
        "outer_share": total(areas * revolved * on_outer) / area_total,
        "inner_share": total(areas * revolved * on_inner) / area_total,
        "axial_dot": total(np.abs(axial_dot) * weights) / safe,
        "radial_dot": total(radial_dot * weights) / safe,
        "r_min": extreme(rho, np.minimum.at, np.inf),
        "r_max": extreme(rho, np.maximum.at, -np.inf),
        "z_min": extreme(axial, np.minimum.at, np.inf),
        "z_max": extreme(axial, np.maximum.at, -np.inf),
    }

    # annularity: bucket by azimuth, then compare the radial band across the
    # occupied buckets
    reference = _unit(np.cross(direction, np.eye(3)[int(np.argmin(
        np.abs(direction)))]))
    other = np.cross(direction, reference)
    relative = centroids - axis.point
    theta = np.arctan2(relative @ other, relative @ reference)
    bucket = np.clip(((theta + np.pi) / (2.0 * np.pi)
                      * azimuth_bins).astype(np.int64), 0, azimuth_bins - 1)
    cell = face_ids * azimuth_bins + bucket
    size = n_faces * azimuth_bins

    occupied = np.bincount(cell, minlength=size).reshape(n_faces, -1) > 0
    per_cell = np.full(size, -np.inf)
    np.maximum.at(per_cell, cell, rho)
    per_cell = per_cell.reshape(n_faces, -1)

    counts = occupied.sum(axis=1)
    metrics["theta_span"] = counts / azimuth_bins
    high = np.where(occupied, per_cell, -np.inf).max(axis=1)
    # a LOW PERCENTILE, not the minimum: on a tessellated disc the triangles
    # near the rim are sparse in azimuth, so a few buckets never reach the true
    # outer radius and the minimum would report a genuine annulus as wildly
    # non-circular (this is what filed every blind-bore floor as milled).
    ordered = np.sort(np.where(occupied, per_cell, np.inf), axis=1)
    rank = np.clip((0.1 * counts).astype(np.int64), 0,
                   max(azimuth_bins - 1, 0))
    low = ordered[np.arange(n_faces), rank]
    scale = np.maximum(metrics["r_max"], TOLLERANCE)
    metrics["radius_swing"] = np.where(
        counts > 0, (high - low) / scale, 0.0)
    return metrics, inlier


def classify_effective_faces(metrics, *, face_cos, rho_floor,
                             swing_tollerance, span_tollerance):
    """``role u1[n_faces]`` — the turning role every effective face would take.

    Computed from the face's TURNABLE area alone (``face_metrics`` weights the
    normal components by it), and deliberately ungated: a face that is only
    partly turnable still has a well-defined role for the part that is.
    Applying the coverage gate here would collapse that to "milled" and throw
    away exactly what the needs-split presentation has to show. The caller
    applies the gate.
    """
    n_faces = len(metrics["area"])
    role = np.full(n_faces, ROLE_OTHER, dtype=np.uint8)

    axialish = metrics["axial_dot"] >= face_cos

    # A facing cut has to be a true annulus, and that takes BOTH tests. The
    # radial band must not vary with azimuth (radius_swing) and it must run
    # all the way round (theta_span). Neither alone is enough: a square boss
    # top spans every azimuth but swings, while a counterbore floor on a bolt
    # circle is a perfect annulus about ITS OWN axis and so barely swings at
    # all over the few azimuths it occupies. Checking only the swing is what
    # filed all eight bolt-hole counterbores on a NIST test part as internal
    # facing.
    annular = ((metrics["radius_swing"] <= swing_tollerance)
               & (metrics["theta_span"] >= span_tollerance))

    # Which boundary of the turned state the face lies on decides the side.
    # For a RADIAL face the sign of the radial normal component says it
    # directly and locally — material is inside an OD surface and outside an
    # ID one — which is what makes it right for a chamfer at a diameter step,
    # outward-facing whether or not it reaches the widest radius there.
    outer, inner = metrics["outer_share"], metrics["inner_share"]
    radial = ~axialish
    role[radial & (metrics["radial_dot"] > 0)] = ROLE_OD_TURN
    role[radial & (metrics["radial_dot"] <= 0)] = ROLE_ID_TURN

    # A FACING cut's normal carries no radial information at all, so the side
    # comes from which boundary its area actually sits on. "Reaches the outer
    # envelope" was too crude: it called every annulus that fell a few mm
    # short — a flange underside against a slightly larger rim — internal.
    facing = axialish & annular
    role[facing & (outer >= inner)] = ROLE_OD_FACE
    role[facing & (outer < inner)] = ROLE_ID_FACE

    # nothing on either boundary is reachable by a lathe, whatever its normals
    # do: the material outboard of it belongs to the part
    role[np.maximum(outer, inner) <= 0.0] = ROLE_OTHER

    role[metrics["r_max"] <= rho_floor] = ROLE_ON_AXIS
    return role


def split_state(role_guess, inlier, grouping, n_faces, share, stability, *,
                compat_fraction, split_floor, split_stability):
    """``(role_face, mixed, brep_valid, brep_default)`` for the split UI.

    A face is MIXED when a real share of it is a surface of revolution and a
    real share is not — a turned cylinder with a flat milled across it, say.
    That is the case a single per-face role cannot describe honestly, and the
    one a user cut resolves: split the face and each piece classifies on its
    own.

    Two guards, and both are load-bearing. ``split_floor`` keeps each side
    substantial, so a few stray triangles at a tangent edge do not flag an
    otherwise perfect cylinder. ``split_stability`` is the one that matters:
    without it EVERY flag on a real part is a false positive, because the
    measure-zero artifacts (a hairline stripe down an offset plane, a
    cross-hole's mid-plane ring) look exactly like a small turned patch by
    area alone. They are told apart by tightening the tolerance — see
    ``face_metrics`` — which on a NIST test part separates them completely:
    ratio 1.00 for every genuine face against 0.57 and below for every
    artifact.
    """
    role_face = np.where(share >= compat_fraction, role_guess,
                         ROLE_OTHER).astype(np.uint8)
    mixed = ((share >= split_floor) & (share <= 1.0 - split_floor)
             & (stability >= split_stability))

    # a uniform face floods its own role; a mixed one shows the truth per
    # triangle, so the milled patch reads as a patch and not as the whole face
    role_fine = role_face[grouping]
    on_mixed = mixed[grouping]
    role_fine = np.where(on_mixed,
                         np.where(inlier, role_guess[grouping], ROLE_OTHER),
                         role_fine).astype(np.uint8)

    # bit r set iff role r covers the whole face; mixed faces set nothing,
    # which is what marks them as needing a cut
    brep_valid = np.where(mixed, 0,
                          (1 << role_face.astype(np.uint32))).astype(np.uint32)
    brep_default = np.where(mixed, CONFLICT_ROLE, role_face).astype(np.uint8)
    return role_fine, role_face, mixed, brep_valid, brep_default


def _report(progress, fraction, message):
    if progress is not None:
        progress(fraction, message)


def _edge_pairs(workdir, n_faces):
    """Effective-face adjacency pairs, splits-aware (mirrors pipeline)."""
    import json
    import os

    pairs_path = os.path.join(workdir, pipeline.BREP_EDGE_PAIRS_FILE)
    meta_path = os.path.join(workdir, pipeline.SUBFACE_META_FILE)
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        if (meta.get("mesh_fingerprint") == pipeline.mesh_fingerprint(workdir)
                and meta.get("n_effective") == n_faces):
            pairs_path = os.path.join(workdir,
                                      pipeline.SUBFACE_EDGE_PAIRS_FILE)
    if not os.path.exists(pairs_path):
        return None
    return np.load(pairs_path).reshape(-1, 2).astype(np.int64)


def face_regions(pairs, selected, n_faces):
    """Connected components of the selected effective faces.

    ``molding.internal_regions`` treats ``membership == 0`` as the set to
    label, so the selector is inverted going in. Returns ``region u4[n_faces]``
    with 0 = not selected.
    """
    import molding

    if pairs is None:
        return np.zeros(n_faces, dtype=np.uint32)
    membership = (~selected).astype(np.int8)
    region, _ = molding.internal_regions(membership, pairs, n_faces)
    return region


def _region_stats(region, face_ids, areas, rho, axial, *, kind, limit=32):
    """Per-region area / axial span / diameter, ordered by descending area.

    ``molding.internal_regions`` ranks by triangle count; area is the number a
    machinist cares about, so re-rank here.
    """
    per_fine = region[face_ids]
    count = int(region.max())
    out = []
    for index in range(1, count + 1):
        mask = per_fine == index
        if not mask.any():
            continue
        entry = {
            "id": index,
            "kind": kind,
            "area": float(areas[mask].sum()),
            "z_min": float(axial[mask].min()),
            "z_max": float(axial[mask].max()),
            "faces": int(np.unique(face_ids[mask]).size),
        }
        if kind == "bore":
            entry["diameter"] = 2.0 * float(np.median(rho[mask]))
        out.append(entry)
    out.sort(key=lambda item: -item["area"])
    return out[:limit]


@log_execution_time
def analyse_turning(workdir, *, tollerance=None, profile_bins=512,
                    refine_rounds=3, max_candidates=12,
                    sample_faces=100000, axis_override=(), progress=None):
    """Recognize the maximal turned state and classify every face.

    Returns the analyzer result triple: stats (verdict, axis, profile, stock,
    volumes, role areas, milled regions), per-fine-face arrays and field_meta.
    """
    import json
    import os

    _report(progress, 0.02, "loading mesh")
    verts, faces = pipeline.load_mesh_arrays(workdir)
    verts = verts.astype(np.float64)
    normals = pipeline.load_face_normals(workdir).astype(np.float64)
    areas = machining.face_areas(verts, faces)
    # a column at a time, not verts[faces].mean(axis=1): the latter gathers a
    # float64 (F, 3, 3) temporary, 200 MB on a 3M-face part
    centroids = (verts[faces[:, 0]] + verts[faces[:, 1]]
                 + verts[faces[:, 2]]) / 3.0
    diagonal = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))

    surface_params = None
    meta_path = os.path.join(workdir, pipeline.BREP_META_FILE)
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            surface_params = json.load(f)["surface_params"]
    face_ids, n_faces, _ = splits.effective_face_ids(workdir)
    brep_path = os.path.join(workdir, pipeline.BREP_FACES_FILE)
    brep_ids = np.load(brep_path) if os.path.exists(brep_path) else None

    # tolerance: analytic STEP normals are evaluated at the very centroids used
    # here, so their residual is exact and needs no slack; freeform STEP faces
    # need a length slack the size of the chord error; STL facet normals carry
    # ~2 degrees of azimuthal error that no length slack can absorb
    deflection = pipeline.part_deflection(workdir)
    if tollerance is None:
        tollerance = 1.0 if deflection > 0 else 5.0
    chord_slack = float(deflection) + 1e-6 * diagonal
    if surface_params is not None and brep_ids is not None:
        analytic = np.array([bool(p) for p in surface_params])
        slack = np.where(analytic[brep_ids], 1e-6 * diagonal, chord_slack)
    else:
        slack = np.full(len(faces), chord_slack)
    sin_tol = math.sin(math.radians(float(tollerance)))
    face_cos = math.cos(math.radians(FACE_NORMAL_TOL_DEG))
    rho_floor = max(1e-4 * diagonal, 2.0 * chord_slack)

    # -- candidate axes ---------------------------------------------------
    # both seeding and scoring run on a deterministic subsample (a seed only
    # has to land in the refiner's basin); the winner is then applied at full
    # resolution. The rng seed is fixed so the result is reproducible and the
    # cache key stays meaningful.
    total_faces = len(faces)
    if sample_faces and 0 < sample_faces < total_faces:
        pick = np.random.default_rng(0).permutation(total_faces)[:sample_faces]
        pick.sort()
    else:
        pick = np.arange(total_faces)
    s_c, s_n, s_w = centroids[pick], normals[pick], areas[pick]
    median_slack = float(np.median(slack[pick]))
    # c x n and |c|^2 are axis-independent, so precomputing them turns each
    # residual evaluation into three matrix-vector products. Only worth it for
    # the SAMPLE, which is evaluated once per candidate per round; the single
    # full-resolution pass at the end is cheaper computed directly than by
    # building full-mesh precomputes it would use once.
    s_cross = np.cross(s_c, s_n)
    s_sq = np.einsum('ij,ij->i', s_c, s_c)

    reasons = []
    override = np.asarray(axis_override, dtype=np.float64).ravel()
    if override.size >= 6:
        forced = _canonical_axis(override[:3], override[3:6],
                                 verts.mean(axis=0), source="manual")
        if forced is None:
            raise ValueError("axis_override direction is degenerate")
        candidates = [forced]
        reasons.append("axis forced by axis_override")
    else:
        _report(progress, 0.10, "seeding candidate axes")
        candidates = seed_axes(s_c, s_n, s_w, verts, surface_params, brep_ids,
                               max_candidates=max_candidates,
                               face_areas=areas)
    if not candidates:
        raise ValueError("no candidate turning axis could be generated")

    _report(progress, 0.20, f"scoring {len(candidates)} candidate axes")
    scored = []
    for axis in candidates:
        if override.size < 6:
            axis = refine_axis(s_c, s_n, s_w, axis, sin_tol=sin_tol,
                               slack=median_slack, rho_floor=rho_floor,
                               rounds=refine_rounds, cross_cn=s_cross,
                               sq_norms=s_sq)
        residual, rho, _ = azimuthal_residual(s_c, s_n, axis,
                                              cross_cn=s_cross, sq_norms=s_sq)
        score = score_axis(s_w, residual, rho, s_n, axis, sin_tol=sin_tol,
                           slack=median_slack, rho_floor=rho_floor,
                           face_cos=face_cos)
        axis.detail.update(score)
        scored.append(axis)

    scored = merge_axis_lines(scored, angle_tol_deg=0.5,
                              dist_tol=max(1e-3 * diagonal, 1e-3))
    scored.sort(key=lambda a: -a.detail["inlier_fraction"])
    qualified = [a for a in scored
                 if a.detail["radial_fraction"] >= MIN_RADIAL_FRACTION]
    best = qualified[0] if qualified else scored[0]
    if not qualified:
        reasons.append(
            f"no candidate axis sweeps enough area (best "
            f"{100 * scored[0].detail['radial_fraction']:.1f}% radial) — the "
            f"fit is carried by planes perpendicular to the axis, not by a "
            f"rotational sweep")
    if len(scored) > 1:
        runner = scored[1].detail["inlier_fraction"]
        if abs(runner - scored[0].detail["inlier_fraction"]) < 0.01:
            reasons.append("axis is ambiguous: another candidate scores within "
                           "1% (the part may be box-like or fully symmetric)")

    # -- the maximal turned state ----------------------------------------
    _report(progress, 0.55, "building the turned envelope")
    envelope = outer_profile(verts, faces, best, bins=profile_bins)
    low, step, profile = envelope["low"], envelope["step"], envelope["r_out"]
    r_max = float(profile.max())
    length = envelope["high"] - envelope["low"]
    margin = max(2.0 * chord_slack, 1e-3 * max(r_max, 1.0))

    residual, rho, axial = azimuthal_residual(centroids, normals, best)

    _report(progress, 0.75, "classifying faces")
    # every turning decision is taken per EFFECTIVE face; on an STL there are
    # none, so each triangle stands alone as its own face
    if face_ids is None:
        grouping = np.arange(total_faces, dtype=np.int64)
        group_count = total_faces
        reasons.append("no BREP faces (STL part) — roles are per triangle and "
                       "will speckle on milled flats")
    else:
        grouping, group_count = face_ids, n_faces

    # the turned state's INNER boundary, then which boundary each triangle sits
    # on. Both come before the roles because the roles are decided by them —
    # deriving the bore profile from the faces already called internal was
    # circular, and one misfiled face moved the boundary judging all the rest.
    direction = np.asarray(best.direction, dtype=np.float64)
    radial_dot = ((np.einsum('ij,ij->i', normals, centroids - best.point)
                   - axial * (normals @ direction))
                  / np.maximum(rho, rho_floor))
    # per-triangle radial and axial extent, gathered a column at a time:
    # verts[faces] is a float64 (F, 3, 3), which is 200 MB on a 3M-face part
    local = verts - best.point
    vertex_axial = local @ direction
    vertex_rho = np.linalg.norm(local - np.outer(vertex_axial, direction),
                                axis=1)
    corner = [vertex_rho[faces[:, k]] for k in range(3)]
    rho_hi = np.maximum(np.maximum(corner[0], corner[1]), corner[2])
    rho_lo = np.minimum(np.minimum(corner[0], corner[1]), corner[2])
    height = [vertex_axial[faces[:, k]] for k in range(3)]
    last_bin = len(profile) - 1
    lo_bin = np.clip(np.floor((np.minimum.reduce(height) - low) / step),
                     0, last_bin).astype(np.int64)
    hi_bin = np.clip(np.floor((np.maximum.reduce(height) - low) / step),
                     0, last_bin).astype(np.int64)
    del local, vertex_axial, corner, height

    inner = inner_profile(rho_lo, radial_dot, lo_bin, hi_bin, len(profile),
                          rho_floor=rho_floor)
    axial_dot_all = normals @ direction
    on_outer, on_inner = boundary_membership(
        rho_hi, rho_lo, axial, axial_dot_all, low, step, profile, inner,
        margin, face_cos, radial_dot)

    metrics, inlier = face_metrics(
        centroids, normals, areas, best, residual, rho, axial, grouping,
        group_count, sin_tol=sin_tol, slack=slack, rho_floor=rho_floor,
        face_cos=face_cos, on_outer=on_outer, on_inner=on_inner)
    role_guess = classify_effective_faces(
        metrics, face_cos=face_cos, rho_floor=rho_floor,
        swing_tollerance=SWING_TOLLERANCE, span_tollerance=SPAN_TOLLERANCE)
    role_out, role_face, mixed, brep_valid, brep_default = split_state(
        role_guess, inlier, grouping, group_count, metrics["inlier_fraction"],
        metrics["stability"], compat_fraction=COMPAT_FRACTION,
        split_floor=SPLIT_FLOOR, split_stability=SPLIT_STABILITY)
    if face_ids is not None:
        splits.sanitize_retired(brep_valid, brep_default, grouping)

    pairs = _edge_pairs(workdir, group_count) if face_ids is not None else None
    milled = face_regions(pairs, role_face == ROLE_OTHER, group_count)
    milled_region_fine = milled[grouping].astype(np.uint32)
    regions = _region_stats(milled, grouping, areas, rho, axial, kind="milled")
    bores = _region_stats(
        face_regions(pairs, role_face == ROLE_ID_TURN, group_count),
        grouping, areas, rho, axial, kind="bore")
    for bore in bores:
        bore["through"] = bool(bore["z_max"] - bore["z_min"]
                               >= length - 2.0 * step)

    # -- stats ------------------------------------------------------------
    total_area = float(areas.sum()) or 1.0
    role_areas = {name: float(areas[role_out == code].sum())
                  for code, name in enumerate(TURN_ROLES)}
    turned_area = total_area - role_areas["other"]
    radial_area = role_areas["od_turn"] + role_areas["id_turn"]
    turned_share = turned_area / total_area
    radial_share = radial_area / total_area

    if not qualified or radial_share < MIN_RADIAL_FRACTION:
        verdict = "not_turned"
    elif turned_share >= TURNED_FRACTION:
        verdict = "turned"
    elif turned_share >= 0.5:
        verdict = "turn_mill"
    else:
        verdict = "not_turned"
        reasons.append(f"only {100 * turned_share:.0f}% of the area is a "
                       f"surface of revolution about the best axis")

    part_volume = abs(mesh_volume(verts, faces))
    envelope_volume = float(math.pi * (profile ** 2 * envelope["widths"]).sum())
    stock_volume = float(math.pi * r_max ** 2 * length)

    centres = low + step * (np.arange(len(profile)) + 0.5)
    centres[-1] = min(centres[-1], envelope["high"])
    # Kept an order below the mesh's own chord error so the drawn section is
    # limited by the mesh, never by this: the old 2e-3 * length threw 512 bins
    # away to 18 points and 2.2 mm of deviation, which is most of what read as
    # a coarse section.
    simplify_tol = max(0.1 * chord_slack, 1e-5 * max(length, 1.0))
    polyline = np.column_stack([centres, profile])
    polyline = np.vstack([[low, 0.0], polyline, [envelope["high"], 0.0]])
    polyline = simplify_profile(polyline, simplify_tol)

    # The internal contour is emitted as its own polylines rather than folded
    # into the outer one: the meridian of a bored part is a region with holes,
    # not a single closed curve. One polyline per CONTIGUOUS bored run, because
    # a part can be bored from both ends with solid material between — joining
    # those into one curve draws a line straight through the solid, which is
    # what made the section wander on a real part.
    bored = inner > 0
    inner_runs = []
    if bored.any():
        edges = np.flatnonzero(np.diff(bored.astype(np.int8)))
        for begin, end in zip(np.r_[0, edges + 1], np.r_[edges + 1, len(bored)]):
            if not bored[begin]:
                continue
            run = [(centres[i], inner[i]) for i in range(begin, end)]
            # Cap the run wherever the bore STOPS INSIDE the material: that
            # end is a floor, and the contour has to come back to the axis to
            # close. An end that reaches the outside is left open, because the
            # outer contour closes it there. The three cases fall out of it —
            # a through hole caps at neither end, a blind hole at one, a fully
            # internal cavity at both.
            if begin > 0:
                run.insert(0, (low + begin * step, 0.0))
            if end < len(bored):
                run.append((low + end * step, 0.0))
            if len(run) >= 2:
                inner_runs.append(simplify_profile(np.array(run), simplify_tol))

    residual_deg = np.degrees(np.arcsin(np.clip(
        np.abs(residual) / np.maximum(rho, rho_floor), 0.0, 1.0)))

    stats = {
        "verdict": verdict,
        "reasons": reasons,
        "tollerance": float(tollerance),
        "axis": best.as_dict(),
        "candidates": [a.as_dict() for a in scored[:max_candidates]],
        "turned_area_fraction": turned_share,
        "radial_area_fraction": radial_share,
        "milled_area_fraction": role_areas["other"] / total_area,
        "role_areas": role_areas,
        "total_area": total_area,
        "max_diameter": 2.0 * r_max,
        "length": float(length),
        "part_volume": part_volume,
        "envelope_volume": envelope_volume,
        "stock_volume": stock_volume,
        "turning_removal": stock_volume - envelope_volume,
        "non_revolved_volume": envelope_volume - part_volume,
        # profile coordinates are AXIAL: z measured from stats["axis"]["point"]
        # along stats["axis"]["direction"], so a world point is
        # point + z * direction + r * (any unit vector perpendicular to it)
        "profile": [[float(z), float(r)] for z, r in polyline],
        # a LIST of polylines, one per contiguous bored run
        "inner_profiles": [[[float(z), float(r)] for z, r in run]
                           for run in inner_runs],
        "profile_step": float(step),
        "profile_gap_bins": int(envelope["gap_bins"]),
        "milled_regions": regions,
        "bores": bores,
        "needs_split": int(mixed.sum()),
        "needs_split_area": float(areas[mixed[grouping]].sum()),
    }

    arrays = {
        "turn_role": role_out.astype("<u1"),
        "turn_residual": residual_deg.astype("<f4"),
        "milled_region": milled_region_fine.astype("<u4"),
        "brep_valid": brep_valid.astype("<u4"),
        "brep_default": brep_default.astype("<u1"),
    }
    # colors stay frontend-owned: unlike setups, the role set is fixed, so
    # there is nothing per-run for the backend to name
    common = {"kind": "turn_role", "labels": TURN_ROLES,
              "conflict": CONFLICT_ROLE}
    field_meta = {
        "turn_role": {**common, "variant": "turn_role", "association": "face",
                      "role": "category", "dtype": "u1"},
        "turn_residual": {"kind": "turn_residual", "association": "face",
                          "role": "scalar", "dtype": "f4", "units": "deg"},
        "milled_region": {"kind": "milled_region", "association": "face",
                          "role": "data", "dtype": "u4"},
        # per-EFFECTIVE-face, so association "none": indexed by the ids in
        # subfaces/brep_faces, not by fine triangle
        "brep_valid": {**common, "variant": "brep_valid", "association": "none",
                       "role": "data", "dtype": "u4",
                       "count": int(len(brep_valid))},
        "brep_default": {**common, "variant": "brep_default",
                         "association": "none", "role": "data", "dtype": "u1",
                         "count": int(len(brep_default))},
    }

    logger.info(
        f"turning: {verdict} about {np.round(best.direction, 4).tolist()} — "
        f"D{2 * r_max:.2f} x {length:.2f}, {100 * turned_share:.1f}% turned "
        f"({100 * radial_share:.1f}% swept), {len(regions)} milled region(s)"
        + (f" — {reasons[0]}" if reasons else ""))
    _report(progress, 1.0, "turned state classified")
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}
