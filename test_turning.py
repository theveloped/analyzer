"""Turning recognition checks — plain script, run with `python test_turning.py`.

Fixtures with analytically known answers:

0. pure math, no OCP and no meshing: the residual vanishes on an exact axis,
   the closed-form blocks and the Plucker seed recover a known OBLIQUE and
   OFF-ORIGIN line, and a deliberately bad seed converges to it. Runs first
   because a failure here localizes instantly instead of through a mesh.
1. stepped shaft on +Z with a blind bore: verdict `turned`, axis +Z, exact
   stock diameter and length, the bore recognized as internal.
2. the same shaft plus a cross-drilled hole and a milled flat: verdict
   `turn_mill`, the axis unmoved by the outliers, exactly those faces milled.
   Also the false-positive check — a milled flat has a hairline stripe through
   its centre where the normal happens to lie in the meridian plane, and the
   BREP-face inlier FRACTION gate is what has to kill it.
3. fixture 1 rotated obliquely and translated off the origin: same answers in
   the moved frame. This is the test that catches a sign error in the axis
   point recovery — fixture 1's axis runs through the origin, where a
   mirrored line looks identical.
4. a plain box and 5. a drilled plate: both `not_turned`. Their INLIER
   fractions are high and correct (every plane perpendicular to a candidate
   axis is trivially a surface of revolution about it) — only the swept-area
   guard separates them from a real turned part, so these assert the verdict.
6. stored fields and the cache round-trip.
7. boss on a flange — the four role defects found in the field: an OD chamfer
   below the flange diameter, a wide facing annulus that a per-triangle vote
   would split, a counterbore floor, and a square pad that passes the
   revolution test trivially and must be rejected as non-annular.
8. the needs-split contract, driven directly (a clean solid has no genuinely
   mixed faces to author in BREP), and the split round-trip: a cut re-keys the
   result, resizes the per-face arrays and leaves the classification unmoved.
"""

import math
import os
import warnings
import shutil
import sys
import tempfile

import numpy as np

import pipeline
import turning


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:52s} {detail}")
        if not condition:
            failures.append(name)
    return check


# --------------------------------------------------------------------------
# fixture builders
# --------------------------------------------------------------------------

def _write_step(shape, path):
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(path)


def _cylinder(radius, height, origin=(0, 0, 0), direction=(0, 0, 1)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    axis = gp_Ax2(gp_Pnt(*origin), gp_Dir(*direction))
    return BRepPrimAPI_MakeCylinder(axis, radius, height).Shape()


def _fuse(a, b):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    return BRepAlgoAPI_Fuse(a, b).Shape()


def _cut(a, b):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    return BRepAlgoAPI_Cut(a, b).Shape()


def _box(dx, dy, dz, origin=(0, 0, 0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    return BRepPrimAPI_MakeBox(gp_Pnt(*origin), dx, dy, dz).Shape()


def stepped_shaft():
    """D40 x 60 stepped down to D20 x 40, with a D12 blind bore 25 deep."""
    shape = _fuse(_cylinder(20.0, 60.0), _cylinder(10.0, 40.0, (0, 0, 60)))
    return _cut(shape, _cylinder(6.0, 25.0, (0, 0, 75)))


# the cross-hole and the flat are kept axially disjoint on purpose: if they
# touched they would be ONE connected milled region, and the region count would
# stop testing anything
FLAT_X = 15.0
FLAT_Z = (30.0, 50.0)
CROSS_HOLE_Z = 10.0


def shaft_with_milling():
    """The shaft plus a cross-drilled D10 hole and a flat milled to x=15."""
    shape = _cut(stepped_shaft(),
                 _cylinder(5.0, 60.0, (-30, 0, CROSS_HOLE_Z), (1, 0, 0)))
    return _cut(shape, _box(40.0, 40.0, FLAT_Z[1] - FLAT_Z[0],
                            (FLAT_X, -20.0, FLAT_Z[0])))


def _transform(shape, rx_deg, ry_deg, translation):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    origin = gp_Pnt(0, 0, 0)
    rx = gp_Trsf()
    rx.SetRotation(gp_Ax1(origin, gp_Dir(1, 0, 0)), math.radians(rx_deg))
    ry = gp_Trsf()
    ry.SetRotation(gp_Ax1(origin, gp_Dir(0, 1, 0)), math.radians(ry_deg))
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(*translation))
    combined = move.Multiplied(ry.Multiplied(rx))
    return BRepBuilderAPI_Transform(shape, combined, True).Shape(), combined


def build_workdir(shape, root, name, *, resolution=2.0):
    workdir = os.path.join(root, name)
    os.makedirs(workdir, exist_ok=True)
    step_path = os.path.join(root, f"{name}.stp")
    _write_step(shape, step_path)
    pipeline.mesh_part(step_path, workdir, resolution=resolution,
                       subdivide=resolution)
    return workdir


def run(workdir, **params):
    import processes
    from processes.base import apply_defaults

    analysis = processes.get_analysis("cnc", "turning")
    merged = apply_defaults(analysis, params)
    return analysis.run(workdir, merged, None), merged


# --------------------------------------------------------------------------
# 0. pure math
# --------------------------------------------------------------------------

def _frame(direction):
    direction = direction / np.linalg.norm(direction)
    other = (np.array([1.0, 0, 0]) if abs(direction[0]) < 0.9
             else np.array([0, 1.0, 0]))
    first = np.cross(direction, other)
    first /= np.linalg.norm(first)
    return direction, first, np.cross(direction, first)


def _synthetic_revolution(point, direction, rng):
    """(centroids, normals) on a cylinder + cone + facing annulus."""
    axis, u, v = _frame(direction)
    positions, normals = [], []

    angle = rng.uniform(0, 2 * np.pi, 4000)
    height = rng.uniform(0, 60, 4000)
    radial = np.cos(angle)[:, None] * u + np.sin(angle)[:, None] * v
    positions.append(point + height[:, None] * axis + 20.0 * radial)
    normals.append(radial)

    angle = rng.uniform(0, 2 * np.pi, 3000)
    height = rng.uniform(60, 95, 3000)
    radial = np.cos(angle)[:, None] * u + np.sin(angle)[:, None] * v
    radius = (100.0 - height) * np.tan(np.radians(30.0))
    positions.append(point + height[:, None] * axis + radius[:, None] * radial)
    half = np.radians(30.0)
    normals.append(np.cos(half) * radial + np.sin(half) * axis)

    angle = rng.uniform(0, 2 * np.pi, 2000)
    radius = rng.uniform(5, 20, 2000)
    radial = np.cos(angle)[:, None] * u + np.sin(angle)[:, None] * v
    positions.append(point + radius[:, None] * radial)
    normals.append(np.repeat(-axis[None, :], 2000, axis=0))

    return np.vstack(positions), np.vstack(normals)


def test_math(check):
    print("\nfixture 0: the axis math (no OCP, no meshing)")
    rng = np.random.default_rng(0)
    point = np.array([13.0, -7.0, 5.0])
    direction = np.array([1.0, 2.0, 3.0]) / np.linalg.norm([1.0, 2.0, 3.0])
    centroids, normals = _synthetic_revolution(point, direction, rng)
    weights = np.ones(len(centroids))
    origin = centroids.mean(axis=0)
    truth = turning._canonical_axis(point, direction, origin)

    residual, rho, _ = turning.azimuthal_residual(centroids, normals, truth)
    check("residual vanishes on the exact axis", np.abs(residual).max() < 1e-9,
          f"max |r| = {np.abs(residual).max():.2e}")

    cross_cn = np.cross(centroids, normals)
    sq_norms = np.einsum('ij,ij->i', centroids, centroids)
    fast, fast_rho, _ = turning.azimuthal_residual(
        centroids, normals, truth, cross_cn=cross_cn, sq_norms=sq_norms)
    check("precomputed residual == direct residual",
          np.abs(residual - fast).max() < 1e-8
          and np.abs(rho - fast_rho).max() < 1e-8)

    fitted, _, _ = turning._fit_direction(centroids, normals, weights,
                                          truth.point)
    if fitted @ truth.direction < 0:
        fitted = -fitted
    check("_fit_direction recovers the direction",
          np.linalg.norm(fitted - truth.direction) < 1e-9)

    moved = turning._fit_point(centroids, normals, weights, truth.direction)
    check("_fit_point returns the canonical foot",
          abs(moved @ truth.direction) < 1e-8,
          f"p.d = {moved @ truth.direction:.2e}")

    scale = float(np.linalg.norm(centroids.max(axis=0) - centroids.min(axis=0)))
    seed = turning._plucker_axis(centroids, normals, weights, origin, scale)
    check("_plucker_axis recovers an off-origin line",
          turning._line_distance(truth, seed) < 1e-6,
          f"offset = {turning._line_distance(truth, seed):.2e} mm")

    bad = turning._canonical_axis(
        truth.point + np.array([4.0, -3.0, 2.0]),
        truth.direction + np.array([0.08, -0.05, 0.03]), origin)
    got = turning.refine_axis(centroids, normals, weights, bad,
                              sin_tol=math.sin(math.radians(1.0)), slack=1e-6,
                              rho_floor=1e-3, rounds=3)
    check("refine_axis converges from a 5 deg / 5 mm seed",
          abs(abs(got.direction @ truth.direction) - 1.0) < 1e-9
          and turning._line_distance(truth, got) < 1e-7,
          f"offset = {turning._line_distance(truth, got):.2e} mm")

    # a plane CONTAINING the axis is not a surface of revolution about it
    axis, u, v = _frame(direction)
    across = rng.uniform(-30, 30, 500)
    along = rng.uniform(0, 60, 500)
    flat = point + along[:, None] * axis + across[:, None] * u
    flat_residual, flat_rho, _ = turning.azimuthal_residual(
        flat, np.repeat(v[None, :], 500, axis=0), truth)
    check("plane through the axis is rejected",
          np.median(np.abs(flat_residual) / flat_rho) > 0.9)


# --------------------------------------------------------------------------
# meshed fixtures
# --------------------------------------------------------------------------

def test_shaft(check, root):
    print("\nfixture 1: stepped shaft on +Z with a blind bore")
    workdir = build_workdir(stepped_shaft(), root, "shaft")
    result, _ = run(workdir)
    stats = result.stats

    direction = np.array(stats["axis"]["direction"])
    check("verdict is 'turned'", stats["verdict"] == "turned",
          f"{stats['verdict']} {stats['reasons']}")
    check("axis is +Z", abs(abs(direction @ [0, 0, 1]) - 1.0) < 1e-4,
          f"d = {np.round(direction, 6).tolist()}")
    offset = np.linalg.norm(np.array(stats["axis"]["point"])[:2])
    check("axis passes through the origin", offset < 1e-3,
          f"offset = {offset:.2e} mm")
    check("stock diameter is 40", abs(stats["max_diameter"] - 40.0) < 0.05,
          f"{stats['max_diameter']:.4f}")
    check("length is 100", abs(stats["length"] - 100.0) < 0.05,
          f"{stats['length']:.4f}")
    check("everything is turnable",
          stats["turned_area_fraction"] > 0.999,
          f"{100 * stats['turned_area_fraction']:.2f}%")
    check("the blind bore is recognized", len(stats["bores"]) >= 1,
          f"{[round(b.get('diameter', 0), 2) for b in stats['bores']]}")
    if stats["bores"]:
        check("bore diameter is 12",
              abs(stats["bores"][0]["diameter"] - 12.0) < 0.3,
              f"{stats['bores'][0]['diameter']:.3f}")
        check("the bore is not through", not stats["bores"][0]["through"])
    check("no milled regions", not stats["milled_regions"],
          f"{len(stats['milled_regions'])}")

    # profile coordinates are axial — measured from axis.point along
    # axis.direction, not in world z
    profile = np.array(stats["profile"])
    origin = np.array(stats["axis"]["point"])

    def radius_at(world_z):
        axial = (np.array([0.0, 0.0, world_z]) - origin) @ direction
        return float(np.interp(axial, profile[:, 0], profile[:, 1]))

    check("profile r(z=30) == 20", abs(radius_at(30.0) - 20.0) < 0.5,
          f"{radius_at(30.0):.3f}")
    check("profile r(z=80) == 10", abs(radius_at(80.0) - 10.0) < 0.5,
          f"{radius_at(80.0):.3f}")
    check("no profile bins needed gap filling",
          stats["profile_gap_bins"] == 0, f"{stats['profile_gap_bins']} bins")

    # the shoulder at z=60 is an annulus facing +Z. It sits well below the
    # large diameter, so a naive "rho >= R_out at my own z" test would call it
    # internal; sampling one bin BEYOND it (where the envelope has already
    # dropped to r=10) is what makes it an external facing cut.
    from processes import resolver
    from processes.base import load_result_arrays

    arrays = load_result_arrays(
        workdir, "cnc", "turning",
        resolver.cache_key(workdir, "cnc/turning", run(workdir)[1]))
    roles = arrays["turn_role"]
    verts, faces = pipeline.load_mesh_arrays(workdir)
    normals = pipeline.load_face_normals(workdir)
    centroids = verts[faces].mean(axis=1)
    radius = np.linalg.norm(centroids[:, :2], axis=1)
    shoulder = ((normals @ [0.0, 0, 1.0] > 0.99)
                & (np.abs(centroids[:, 2] - 60.0) < 0.5)
                & (radius > 12.0) & (radius < 18.0))
    check("the shoulder has fine faces to test", shoulder.sum() > 10,
          f"{int(shoulder.sum())} faces")
    check("the shoulder is an external facing cut, not internal",
          bool((roles[shoulder] == turning.ROLE_OD_FACE).all()),
          f"roles = {sorted(set(roles[shoulder].tolist()))}")
    return workdir


def test_turn_mill(check, root):
    print("\nfixture 2: the same shaft, cross-drilled and flatted")
    workdir = build_workdir(shaft_with_milling(), root, "turnmill")
    result, merged = run(workdir)
    stats = result.stats

    direction = np.array(stats["axis"]["direction"])
    check("verdict is 'turn_mill'", stats["verdict"] == "turn_mill",
          f"{stats['verdict']} {stats['reasons']}")
    check("outliers do not drag the axis off +Z",
          abs(abs(direction @ [0, 0, 1]) - 1.0) < 1e-3,
          f"d = {np.round(direction, 6).tolist()}")
    check("turned fraction is between 0.6 and 0.98",
          0.6 < stats["turned_area_fraction"] < 0.98,
          f"{100 * stats['turned_area_fraction']:.1f}%")
    check("two or more milled regions", len(stats["milled_regions"]) >= 2,
          f"{len(stats['milled_regions'])}")

    # the discriminating check: a milled flat has a hairline stripe through its
    # centre where the normal lies in the meridian plane, and a cross-hole has
    # a mid-plane ring. Both must be suppressed by the inlier FRACTION gate.
    from processes import resolver
    from processes.base import load_result_arrays

    arrays = load_result_arrays(
        workdir, "cnc", "turning",
        resolver.cache_key(workdir, "cnc/turning", merged))
    roles = arrays["turn_role"]
    verts, faces = pipeline.load_mesh_arrays(workdir)
    normals = pipeline.load_face_normals(workdir)
    centroids = verts[faces].mean(axis=1).astype(np.float64)
    on_flat = ((np.abs(normals @ [1.0, 0, 0]) > 0.99)
               & (np.abs(centroids[:, 0] - FLAT_X) < 0.5)
               & (centroids[:, 2] > FLAT_Z[0] + 1.0)
               & (centroids[:, 2] < FLAT_Z[1] - 1.0))
    check("the milled flat has fine faces to test", on_flat.sum() > 20,
          f"{int(on_flat.sum())} faces")
    check("NO part of the milled flat is called turnable",
          bool((roles[on_flat] == turning.ROLE_OTHER).all()),
          f"{int((roles[on_flat] != turning.ROLE_OTHER).sum())} leaked")
    return workdir


def test_oblique(check, root):
    print("\nfixture 3: the shaft rotated obliquely and moved off the origin")
    shape, trsf = _transform(stepped_shaft(), 30.0, 20.0, (13.0, -7.0, 5.0))
    workdir = build_workdir(shape, root, "oblique")
    stats = run(workdir)[0].stats

    rotation = np.array([[trsf.Value(row + 1, col + 1) for col in range(3)]
                         for row in range(3)])
    expected = rotation @ np.array([0.0, 0.0, 1.0])
    origin = np.array([13.0, -7.0, 5.0])

    direction = np.array(stats["axis"]["direction"])
    check("axis direction follows the transform",
          abs(abs(direction @ expected) - 1.0) < 1e-3,
          f"|d.e| = {abs(direction @ expected):.8f}")
    delta = np.array(stats["axis"]["point"]) - origin
    offset = float(np.linalg.norm(delta - (delta @ direction) * direction))
    check("axis LINE passes through the moved origin (sign test)",
          offset < 0.05, f"offset = {offset:.4f} mm")
    check("verdict survives the transform", stats["verdict"] == "turned",
          f"{stats['verdict']} {stats['reasons']}")
    check("stock diameter is invariant",
          abs(stats["max_diameter"] - 40.0) < 0.05,
          f"{stats['max_diameter']:.4f}")
    check("length is invariant", abs(stats["length"] - 100.0) < 0.05,
          f"{stats['length']:.4f}")


def boss_on_flange():
    """A flange with a concave-filleted boss, a counterbore and a square pad.

    Each feature reproduces one field-reported defect:

    - the boss/flange transition carries an OD chamfer that sits well below
      the flange diameter at its own axial station — it must still read as OD
      turning, not as an internal cut;
    - the flange top is a wide annulus whose triangles straddle any radius
      threshold — deciding per triangle splits it and the vote lands wrong;
    - the counterbore gives a genuine ID facing surface and an ID diameter;
    - the square pad is a plane perpendicular to the axis, so it passes the
      revolution test trivially and can only be rejected as non-annular.
    """
    shape = _fuse(_cylinder(75.0, 20.0), _cylinder(30.0, 60.0, (0, 0, 20)))
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    chamfer = BRepPrimAPI_MakeCone(
        gp_Ax2(gp_Pnt(0, 0, 20), gp_Dir(0, 0, 1)), 34.0, 30.0, 4.0).Shape()
    shape = _fuse(shape, chamfer)
    shape = _cut(shape, _cylinder(12.0, 40.0, (0, 0, 40)))    # bore
    shape = _cut(shape, _cylinder(18.0, 10.0, (0, 0, 70)))    # counterbore
    return _fuse(shape, _box(24.0, 24.0, 6.0, (-12.0, -12.0, 80.0)))


# A rib standing on the flange top at ONE azimuth, kept in its own fixture so
# it cannot shadow the chamfer the previous one checks. This is the only way a
# facing cut ends up partly blocked on a clean solid: material sitting
# directly on a face trims that face away, so the blocker has to be somewhere
# else around the turn.
RIB = (30.0, 50.0, 8.0)  # inner x, outer x, half width


def flange_with_rib():
    """A flanged hub with one radial rib on the flange top."""
    inner, outer, half = RIB
    shape = _fuse(_cylinder(75.0, 20.0), _cylinder(20.0, 50.0))
    return _fuse(shape, _box(outer - inner, 2 * half, 15.0,
                             (inner, -half, 20.0)))


def test_roles(check, root):
    print("\nfixture 7: boss on a flange — the reported role defects")
    workdir = build_workdir(boss_on_flange(), root, "boss", resolution=1.5)
    stats = run(workdir)[0].stats

    import splits
    from processes import resolver
    from processes.base import load_result_arrays

    arrays = load_result_arrays(
        workdir, "cnc", "turning",
        resolver.cache_key(workdir, "cnc/turning", run(workdir)[1]))
    roles = arrays["turn_role"]
    verts, faces = pipeline.load_mesh_arrays(workdir)
    normals = pipeline.load_face_normals(workdir)
    centroids = verts[faces].mean(axis=1)
    radius = np.linalg.norm(centroids[:, :2], axis=1)
    ids = splits.effective_face_ids(workdir)[0]

    def role_of(mask, name):
        picked = np.unique(roles[mask])
        check(name, len(picked) == 1, f"roles {picked.tolist()}")
        return int(picked[0]) if len(picked) else -1

    # the OD chamfer: outward-facing, but far below the flange diameter at its z
    chamfer = ((np.abs(normals @ [0.0, 0, 1.0]) > 0.3)
               & (np.abs(normals @ [0.0, 0, 1.0]) < 0.9)
               & (centroids[:, 2] > 20.5) & (centroids[:, 2] < 23.5)
               & (radius > 30.0) & (radius < 34.0))
    check("the OD chamfer has faces to test", chamfer.sum() > 10,
          f"{int(chamfer.sum())}")
    check("OD chamfer is OD turning, not an internal cut",
          bool((roles[chamfer] == turning.ROLE_OD_TURN).all()),
          f"roles {sorted(set(roles[chamfer].tolist()))}")

    # the flange top annulus — one face, one verdict, and it is external
    flange = ((normals @ [0.0, 0, 1.0] > 0.99)
              & (np.abs(centroids[:, 2] - 20.0) < 0.4) & (radius > 40.0))
    check("the flange top has faces to test", flange.sum() > 20,
          f"{int(flange.sum())}")
    check("flange top is OD facing (not split per triangle)",
          role_of(flange, "flange top is one role") == turning.ROLE_OD_FACE,
          f"role {turning.TURN_ROLES[roles[flange][0]]}")

    # the counterbore floor: a genuine annulus buried inside the envelope
    floor = ((normals @ [0.0, 0, 1.0] > 0.99)
             & (np.abs(centroids[:, 2] - 70.0) < 0.4)
             & (radius > 13.0) & (radius < 17.0))
    if floor.sum() > 5:
        check("counterbore floor is ID facing",
              bool((roles[floor] == turning.ROLE_ID_FACE).all()),
              f"roles {sorted(set(roles[floor].tolist()))}")

    # the square pad top is perpendicular to the axis but is not an annulus
    pad = ((normals @ [0.0, 0, 1.0] > 0.99)
           & (np.abs(centroids[:, 2] - 86.0) < 0.4))
    check("the square pad has faces to test", pad.sum() > 10,
          f"{int(pad.sum())}")
    check("square pad top is milled, not a facing cut",
          bool((roles[pad] == turning.ROLE_OTHER).all()),
          f"roles {sorted(set(roles[pad].tolist()))}")

    # no ID face may be reported where the geometry has no bore
    check("bores are reported", len(stats["bores"]) >= 1,
          f"{[round(b['diameter'], 2) for b in stats['bores']]}")
    check("the turned section carries an internal contour",
          sum(len(run) for run in stats["inner_profiles"]) >= 2,
          f"{len(stats['inner_profiles'])} run(s)")
    inner_max = max((r for run in stats["inner_profiles"] for _, r in run),
                    default=0.0)
    check("internal contour reaches the counterbore radius",
          abs(inner_max - 18.0) < 1.5, f"r_max {inner_max:.2f}")
    del ids


def test_shadowed_facing(check, root):
    """A facing cut a lathe can only partly make, and the cut that fixes it.

    The turned state is a maximum over azimuth, so one rib shadows the whole
    annulus out to its own radius even though the face is wide open at every
    other azimuth. The lathe leaves the WHOLE turned state and the milling
    comes afterwards, so that is the right blocker: the ring outside the rib
    can be faced, the annulus inside it cannot, and the face wants a cut
    between them.
    """
    print("\nfixture 9: a facing cut blocked by a rib at one azimuth")
    workdir = build_workdir(flange_with_rib(), root, "ribbed", resolution=1.5)
    result, params = run(workdir)

    import splits
    from processes import resolver
    from processes.base import load_result_arrays

    arrays = load_result_arrays(workdir, "cnc", "turning",
                                resolver.cache_key(workdir, "cnc/turning",
                                                   params))
    roles = arrays["turn_role"]
    default = arrays["brep_default"]
    verts, faces = pipeline.load_mesh_arrays(workdir)
    normals = pipeline.load_face_normals(workdir)
    centroids = verts[faces].mean(axis=1)
    radius = np.hypot(centroids[:, 0], centroids[:, 1])
    ids = splits.effective_face_ids(workdir)[0]

    inner, outer, half = RIB
    reach = math.hypot(outer, half)  # the rib's corner radius
    top = ((normals @ [0.0, 0, 1.0] > 0.99)
           & (np.abs(centroids[:, 2] - 20.0) < 0.4) & (radius > 22.0))
    check("the flange top has faces to test", top.sum() > 50,
          f"{int(top.sum())}")

    ids_top = np.unique(ids[top])
    check("the shadowed facing cut is flagged for a cut",
          all(default[i] == turning.CONFLICT_ROLE for i in ids_top),
          f"{[int(default[i]) for i in ids_top]}")

    clear = top & (radius > reach + 4.0)
    blocked = top & (radius < reach - 6.0)
    check("the ring outside the rib is OD facing",
          bool((roles[clear] == turning.ROLE_OD_FACE).all()),
          f"reach {reach:.1f}, roles {sorted(set(roles[clear].tolist()))}")
    check("the annulus the rib shadows is milled, not faced",
          bool((roles[blocked] == turning.ROLE_OTHER).all()),
          f"roles {sorted(set(roles[blocked].tolist()))}")
    check("the part still reads as turned overall",
          result.stats["verdict"] in ("turned", "turn_mill"),
          result.stats["verdict"])


def test_split_state(check):
    """The needs-split contract, on hand-built faces with known answers.

    Driven directly rather than through a STEP fixture because the interesting
    cases are hard to author in BREP: on a clean solid the face boundaries
    already follow the role changes, so a genuinely mixed face is rare (the
    NIST part has none). The contract still has to be right for the ones that
    do occur — freeform faces that are revolved over part of their extent, and
    anything arriving without BREP faces at all.
    """
    print("\nsplit state: mixed faces, artifacts and the brep arrays")

    # 4 faces x 100 triangles: uniform turned, uniform milled, genuinely
    # mixed (60/40), and an artifact (5% compatible but unstable)
    grouping = np.repeat(np.arange(4), 100)
    role_guess = np.array([turning.ROLE_OD_TURN, turning.ROLE_OD_TURN,
                           turning.ROLE_OD_TURN, turning.ROLE_OD_TURN],
                          dtype=np.uint8)
    inlier = np.zeros(400, dtype=bool)
    inlier[0:100] = True                    # face 0: all compatible
    inlier[200:260] = True                  # face 2: 60% compatible
    inlier[300:305] = True                  # face 3: 5%, a stripe
    share = np.array([1.0, 0.0, 0.6, 0.05])
    stability = np.array([1.0, 1.0, 1.0, 0.5])  # face 3 halves with the tol

    role_fine, role_face, mixed, valid, default = turning.split_state(
        role_guess, inlier, grouping, 4, share, stability,
        compat_fraction=turning.COMPAT_FRACTION,
        split_floor=turning.SPLIT_FLOOR,
        split_stability=turning.SPLIT_STABILITY)

    check("uniformly turned face is not flagged", not mixed[0], f"{mixed[0]}")
    check("uniformly milled face is not flagged", not mixed[1], f"{mixed[1]}")
    check("genuinely mixed face is flagged", bool(mixed[2]), f"{mixed[2]}")
    check("zero-crossing artifact is NOT flagged", not mixed[3],
          "5% compatible but the share halves with the tolerance")

    check("uniform turned face keeps its role",
          bool((role_fine[0:100] == turning.ROLE_OD_TURN).all()), "")
    check("uniform milled face floods as other",
          bool((role_fine[100:200] == turning.ROLE_OTHER).all()), "")
    check("mixed face shows the turned part per triangle",
          bool((role_fine[200:260] == turning.ROLE_OD_TURN).all()), "")
    check("mixed face shows the milled part per triangle",
          bool((role_fine[260:300] == turning.ROLE_OTHER).all()), "")
    check("artifact face does not speckle",
          bool((role_fine[300:400] == turning.ROLE_OTHER).all()),
          "the documented stripe must not paint a turned role")

    check("uniform face sets exactly its role bit",
          valid[0] == (1 << turning.ROLE_OD_TURN), f"{valid[0]}")
    check("mixed face sets no valid bit", valid[2] == 0, f"{valid[2]}")
    check("uniform face defaults to its role",
          default[0] == turning.ROLE_OD_TURN,
          f"{turning.TURN_ROLES[default[0]]}")
    check("mixed face defaults to the conflict sentinel",
          default[2] == turning.CONFLICT_ROLE, f"{default[2]}")
    check("milled face defaults to other",
          default[1] == turning.ROLE_OTHER, f"{default[1]}")
    del role_face


def test_split_roundtrip(check, root):
    """A user cut re-keys the result and every piece classifies on its own.

    The classification must not MOVE under a cut — splitting a face changes
    how the answer is reported, never what the geometry is — and the per-face
    arrays must follow the new effective ids, including the retired parent
    (which carries no triangles and so no axial extent at all; casting its
    +/-inf extremes to a bin index is undefined, which this exercises).
    """
    print("\nsplit round-trip: a cut through the OD cylinder")
    import test_splits
    import splits

    workdir = build_workdir(shaft_with_milling(), root, "split_shaft")
    before = run(workdir)[0].stats
    ids, n_before, _ = splits.effective_face_ids(workdir)

    verts, faces = pipeline.load_mesh_arrays(workdir)
    verts = verts.astype(np.float64)
    centroids = verts[faces].mean(axis=1)
    radius = np.hypot(centroids[:, 0], centroids[:, 1])
    on_od = ((np.abs(radius - 20.0) < 0.6) & (centroids[:, 2] > 5.0)
             & (centroids[:, 2] < 55.0))
    face = int(np.bincount(ids[on_od]).argmax())

    state = splits.add_cut(
        workdir, face,
        test_splits.nearest_boundary_vertex(verts, faces, ids, face, (20, 0, 0)),
        test_splits.nearest_boundary_vertex(verts, faces, ids, face, (20, 0, 60)))
    created = state.cut_info[-1]["created"]
    check("the cut separates the face", state.cut_info[-1]["separated"],
          f"created {created}")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        after = run(workdir)[0].stats
        noisy = [str(w.message) for w in caught
                 if issubclass(w.category, (RuntimeWarning, DeprecationWarning))]
    check("re-run is warning-clean (retired ids have no axial extent)",
          not noisy, "; ".join(noisy[:2]) or "none")

    ids_after, n_after, _ = splits.effective_face_ids(workdir)
    check("effective face count grows by the pieces",
          n_after == n_before + len(created), f"{n_before} -> {n_after}")

    from processes import resolver
    from processes.base import load_result_arrays
    arrays = load_result_arrays(
        workdir, "cnc", "turning",
        resolver.cache_key(workdir, "cnc/turning", run(workdir)[1]))
    check("brep_default is sized to the new effective ids",
          len(arrays["brep_default"]) == n_after,
          f"{len(arrays['brep_default'])} vs {n_after}")

    pieces = sorted(set(ids_after[ids == face].tolist()))
    roles = {int(arrays["brep_default"][p]) for p in pieces}
    check("every piece of a turned face is still turned",
          roles == {turning.ROLE_OD_TURN},
          f"{[turning.TURN_ROLES[r] for r in sorted(roles)]}")

    check("the verdict does not move under a cut",
          after["verdict"] == before["verdict"],
          f"{before['verdict']} -> {after['verdict']}")
    check("the turned area does not move under a cut",
          abs(after["turned_area_fraction"]
              - before["turned_area_fraction"]) < 1e-9,
          f"{100 * before['turned_area_fraction']:.2f}% -> "
          f"{100 * after['turned_area_fraction']:.2f}%")


def test_negatives(check, root):
    print("\nfixtures 4 and 5: a plain box and a drilled plate")
    workdir = build_workdir(_box(60.0, 40.0, 20.0), root, "box")
    stats = run(workdir)[0].stats
    check("box: verdict is 'not_turned'", stats["verdict"] == "not_turned",
          f"{stats['verdict']}")
    check("box: swept area is negligible",
          stats["radial_area_fraction"] < 0.05,
          f"{100 * stats['radial_area_fraction']:.2f}% swept, "
          f"{100 * stats['turned_area_fraction']:.1f}% inliers")
    check("box: the reason names the swept-area guard",
          any("sweep" in reason for reason in stats["reasons"]),
          f"{stats['reasons']}")

    plate = _cut(_box(100.0, 100.0, 5.0, (-50, -50, 0)),
                 _cylinder(10.0, 5.0))
    workdir = build_workdir(plate, root, "plate")
    stats = run(workdir)[0].stats
    check("plate: verdict is 'not_turned'", stats["verdict"] == "not_turned",
          f"{stats['verdict']}, inliers "
          f"{100 * stats['turned_area_fraction']:.1f}%, swept "
          f"{100 * stats['radial_area_fraction']:.1f}%")


def test_scan_math(check):
    """_best_line_along must recover the offset, not just the direction.

    A candidate direction is a direction, not a line. Scoring the line through
    the part centroid would under-report every part whose axis of revolution
    is off-centre, so the scan solves for the perpendicular offset.
    """
    print("\nfixture 7: the axis scan's offset fit")
    rng = np.random.default_rng(3)
    point = np.array([13.0, -7.0, 5.0])
    direction = np.array([0.0, 0.0, 1.0])
    centroids, normals = _synthetic_revolution(point, direction, rng)
    areas = np.ones(len(centroids))
    # deliberately NOT on the axis: this is what the fit has to undo
    origin = centroids.mean(axis=0) + np.array([25.0, -18.0, 0.0])

    fitted = turning._best_line_along(direction, centroids, normals, areas,
                                      origin)
    truth = turning._canonical_axis(point, direction, origin)
    offset = float(np.linalg.norm(
        (fitted.point - truth.point)
        - ((fitted.point - truth.point) @ truth.direction) * truth.direction))
    check("scan recovers the off-centre axis line", offset < 1e-6,
          f"{offset:.3e} mm off")

    naive = turning._canonical_axis(origin, direction, origin)
    residual, _, _ = turning.azimuthal_residual(centroids, normals, naive)
    check("the centroid line alone would have missed it",
          np.abs(residual).max() > 1.0,
          f"max |r| = {np.abs(residual).max():.2f}")


def test_scan(check, root):
    print("\nfixture 8: cnc/turning_scan over candidate directions")
    import processes
    from processes import resolver
    from processes.base import apply_defaults, params_hash

    def scan(workdir, **params):
        analysis = processes.get_analysis("cnc", "turning_scan")
        merged = apply_defaults(analysis, params)
        return analysis.run(workdir, merged, None).stats, merged

    AXES = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

    # the scan takes VECTORS, so it needs nothing but the mesh — no
    # accessibility run stands between a candidate and its turnability
    workdir = build_workdir(_cylinder(15.0, 50.0), root, "scan_cyl")
    stats, merged = scan(workdir, axis_vectors=AXES)
    by_dir = {tuple(round(c) for c in a["vector"]): a for a in stats["axes"]}
    check("scan scores every candidate axis", len(by_dir) == 3,
          f"{sorted(by_dir)}")

    z, x = by_dir[(0, 0, 1)], by_dir[(1, 0, 0)]
    check("cylinder: its own axis is revolution-compatible",
          z["inlier_fraction"] > 0.95, f"{100 * z['inlier_fraction']:.1f}%")
    check("cylinder: its own axis is mostly swept",
          z["radial_fraction"] > 0.5, f"{100 * z['radial_fraction']:.1f}%")
    check("cylinder: its own axis qualifies", z["qualified"], "")
    check("cylinder: a perpendicular axis does not",
          not x["qualified"],
          f"swept {100 * x['radial_fraction']:.1f}%")

    # the plate is why two numbers are reported and not one: every plane
    # perpendicular to a candidate axis is trivially a surface of revolution
    plate = _cut(_box(100.0, 100.0, 5.0, (-50, -50, 0)), _cylinder(10.0, 5.0))
    plate_dir = build_workdir(plate, root, "scan_plate")
    pz = scan(plate_dir, axis_vectors=[[0, 0, 1]])[0]["axes"][0]
    check("plate: reads high on revolution-compatible area",
          pz["inlier_fraction"] > 0.8, f"{100 * pz['inlier_fraction']:.1f}%")
    check("plate: fails the swept-area gate anyway",
          not pz["qualified"], f"swept {100 * pz['radial_fraction']:.1f}%")

    # one axis at a time is its own cache entry, which is what lets the
    # overview fill a single missing cell instead of recomputing everything
    single = scan(workdir, axis_vectors=[[0, 0, 1]])[1]
    check("a different candidate set is a different cache key",
          params_hash(resolver.cache_key(workdir, "cnc/turning_scan", merged))
          != params_hash(resolver.cache_key(workdir, "cnc/turning_scan",
                                            single)), "")

    # the painted field IS the reported number — that is what running at full
    # resolution buys, and what lets the overview union roles across axes
    import machining
    from processes.base import load_result_arrays
    key = resolver.cache_key(workdir, "cnc/turning_scan", merged)
    stored = load_result_arrays(workdir, "cnc", "turning_scan", key)
    verts, faces = pipeline.load_mesh_arrays(workdir)
    areas = machining.face_areas(verts, faces)
    total = float(areas.sum())
    for row in stats["axes"]:
        role = stored[row["field"]]
        tag = f"axis {tuple(round(c) for c in row['vector'])}"
        check(f"{tag}: role array covers every fine face",
              role.shape == (len(faces),), f"{role.shape} vs {len(faces)}")
        check(f"{tag}: role codes are in range",
              bool(((role >= 0) & (role <= 2)).all()), "")
        compatible = float(areas[role >= 1].sum()) / total
        swept = float(areas[role == 2].sum()) / total
        check(f"{tag}: painted compatible area == inlier_fraction",
              abs(compatible - row["inlier_fraction"]) < 1e-5,
              f"{compatible:.6f} vs {row['inlier_fraction']}")
        check(f"{tag}: painted swept area == radial_fraction",
              abs(swept - row["radial_fraction"]) < 1e-5,
              f"{swept:.6f} vs {row['radial_fraction']}")


def test_fields_and_cache(check, workdir):
    print("\nfixture 6: stored fields and the cache round-trip")
    from processes import resolver
    from processes.base import load_result_arrays

    _, merged = run(workdir)
    arrays = load_result_arrays(
        workdir, "cnc", "turning",
        resolver.cache_key(workdir, "cnc/turning", merged))
    faces = np.load(os.path.join(workdir, pipeline.FINE_FACES_FILE))

    check("turn_role covers the fine mesh",
          arrays["turn_role"].shape == (len(faces),),
          f"{arrays['turn_role'].shape} vs {len(faces)}")
    check("turn_role dtype is u1", arrays["turn_role"].dtype == np.uint8)
    check("turn_role codes are in range",
          int(arrays["turn_role"].max()) < len(turning.TURN_ROLES))
    check("turn_residual is per face and finite",
          arrays["turn_residual"].shape == (len(faces),)
          and bool(np.isfinite(arrays["turn_residual"]).all()))
    check("milled_region is 0 wherever a face is turnable",
          bool((arrays["milled_region"][arrays["turn_role"] != 0] == 0).all()))

    calls = []
    run(workdir)  # warm
    import processes
    from processes.base import apply_defaults
    analysis = processes.get_analysis("cnc", "turning")
    analysis.run(workdir, apply_defaults(analysis, {}),
                 lambda fraction, message: calls.append(message))
    check("cache round-trip recomputes nothing", not calls,
          f"{len(calls)} progress calls")


def main():
    failures = []
    check = check_factory(failures)
    test_math(check)

    root = tempfile.mkdtemp(prefix="turning_")
    try:
        shaft = test_shaft(check, root)
        test_turn_mill(check, root)
        test_oblique(check, root)
        test_roles(check, root)
        test_shadowed_facing(check, root)
        test_split_state(check)
        test_split_roundtrip(check, root)
        test_negatives(check, root)
        test_scan_math(check)
        test_scan(check, root)
        test_fields_and_cache(check, shaft)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} CHECKS FAILED: {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
