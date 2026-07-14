"""Height-map (Z-map) based tool accessibility engine.

The undercut-fixed volume for an approach direction is, by construction, a
heightfield along that direction: air above the surface stays air all the way
up. On a heightfield, a Minkowski closing with any rotationally symmetric
tool bottom is exactly a 2D grayscale closing of the height map with the
tool's radial profile (the classic Z-map / inverse tool offset construction
from CAM simulation). This turns each per-tool 3D voxel offset into a 2D
morphology operation on a rendered depth map:

- render ONE depth map per approach direction (this is the undercut-fixed
  heightfield - no 3D fixUndercuts needed)
- ONE grayscale closing per tool tip profile (ball / flat / bull nose)
  -> per-vertex "gap" field: how far the tip stays above each vertex
- ONE flat-disk dilation per cylinder radius -> per-vertex "clearance"
  field: height of the tallest obstruction within that radius

A holder or spindle modelled as stacked concentric cylinders (radius, start
height above the tip) then never touches geometry again: a cylinder collides
at a vertex iff clearance(radius) > stickout + start, so any tool length and
any holder stack is a numpy threshold over cached scalar fields.

All fields are cached per direction in <workdir>/zcache/dir_<idx>.npz.
"""

import os
import tempfile

import numpy as np
from loguru import logger
from meshlib import mrmeshpy as mm
from scipy import ndimage

from utils import file_fingerprint, files_fingerprint, log_execution_time

FREE_SPACE = -1e30  # height of pixels with no material below (tool can plunge)


# ---------------------------------------------------------------------------
# depth map rendering
# ---------------------------------------------------------------------------

@log_execution_time
def render_heightmap(mesh, direction, pixel, margin=0):
    """
    Render the height map of `mesh` seen along approach direction `direction`
    (pointing from the part towards the tool). Returns (heights, frame):

    - heights: 2D float32 array, heights[iy, ix] = surface height along the
      direction axis (larger = closer to the tool), FREE_SPACE where empty
    - frame: dict with orthonormal axes (x, y, d), origin and pixel size, so
      vertices can be projected into map coordinates

    The raster meshlib produces is flush with the mesh bbox, which puts the
    part's outer silhouette walls exactly on the map border; `margin` adds
    that many border pixels of FREE_SPACE (shifting the origin to match) so
    part edges are strictly interior and downstream samplers that clamp to
    the map clamp into genuine exterior air instead of the border column.
    """
    d = np.asarray(direction, dtype=float)
    d /= np.linalg.norm(d)

    # the distance map looks opposite to the approach direction
    look = mm.Vector3f(float(-d[0]), float(-d[1]), float(-d[2]))
    params = mm.MeshToDistanceMapParams(look, mm.Vector2f(pixel, pixel), mesh, True)
    dmap = mm.computeDistanceMap(mesh, params)

    # extract values through the raw dump (fastest binding available)
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
        raw_path = tmp.name
    try:
        mm.DistanceMapSave.toRAW(dmap, raw_path)
        with open(raw_path, "rb") as f:
            res_x, res_y = np.fromfile(f, dtype=np.int64, count=2)
            values = np.fromfile(f, dtype=np.float32).reshape(res_y, res_x)
    finally:
        os.remove(raw_path)

    org = np.array([params.orgPoint.x, params.orgPoint.y, params.orgPoint.z])
    x_range = np.array([params.xRange.x, params.xRange.y, params.xRange.z])
    y_range = np.array([params.yRange.x, params.yRange.y, params.yRange.z])

    # distance values grow away from the org plane along the look direction,
    # so height along the approach direction is their negation
    invalid = values == dmap.NOT_VALID_VALUE
    heights = np.where(invalid, np.float32(FREE_SPACE), -values).astype(np.float32)

    if margin > 0:
        heights = np.pad(heights, margin, constant_values=np.float32(FREE_SPACE))
        org = org - margin * (x_range / res_x) - margin * (y_range / res_y)

    frame = {
        "origin": org,
        "x_axis": x_range / res_x,  # one pixel step in world coordinates
        "y_axis": y_range / res_y,
        "direction": d,
        "pixel": pixel,
    }
    return heights, frame


def project_vertices_float(verts, frame):
    """
    Project vertices into fractional map coordinates. Returns (fx, fy, height)
    where fx/fy are continuous pixel coordinates (pixel i spans [i, i+1)) and
    height is the vertex coordinate along the approach direction axis.
    """
    rel = verts - frame["origin"]
    x_axis = frame["x_axis"]
    y_axis = frame["y_axis"]

    fx = rel @ x_axis / (x_axis @ x_axis)
    fy = rel @ y_axis / (y_axis @ y_axis)
    height = rel @ frame["direction"]
    return fx, fy, height


def project_vertices(verts, frame):
    """Integer-pixel variant of project_vertices_float."""
    fx, fy, height = project_vertices_float(verts, frame)
    return np.floor(fx).astype(int), np.floor(fy).astype(int), height


def bilinear_sample(map2d, fx, fy):
    """Bilinear interpolation of a map at fractional pixel coordinates
    (values live at pixel centers i + 0.5)."""
    gx = np.clip(fx - 0.5, 0.0, map2d.shape[1] - 1.000001)
    gy = np.clip(fy - 0.5, 0.0, map2d.shape[0] - 1.000001)
    x0 = np.floor(gx).astype(int)
    y0 = np.floor(gy).astype(int)
    wx = gx - x0
    wy = gy - y0
    return ((map2d[y0, x0] * (1 - wx) + map2d[y0, x0 + 1] * wx) * (1 - wy)
            + (map2d[y0 + 1, x0] * (1 - wx) + map2d[y0 + 1, x0 + 1] * wx) * wy)


def sample_map(map2d, ix, iy):
    ix = np.clip(ix, 0, map2d.shape[1] - 1)
    iy = np.clip(iy, 0, map2d.shape[0] - 1)
    return map2d[iy, ix]


def _bracket_corners(fx, fy, shape):
    """
    The four pixels whose CENTERS bracket the fractional position, as
    (ix4, iy4) index arrays of shape (4, V). Vertical CAD walls sit within
    float epsilon of integer fx/fy on the bbox-anchored grid, right where
    floor(fx) flips between two columns straddling a height cliff; the
    bracket instead flips half a pixel away, at pixel centers, so every
    coplanar wall vertex reads the identical pixel set. Taking the min of a
    field over the bracket therefore gives subpixel-stable values (the same
    role the fractional window plays in euclidean_gap).
    """
    x0 = np.clip(np.floor(fx - 0.5).astype(int), 0, shape[1] - 1)
    y0 = np.clip(np.floor(fy - 0.5).astype(int), 0, shape[0] - 1)
    x1 = np.minimum(x0 + 1, shape[1] - 1)
    y1 = np.minimum(y0 + 1, shape[0] - 1)
    return np.stack([x0, x1, x0, x1]), np.stack([y0, y0, y1, y1])


@log_execution_time
def face_visibility(mesh, verts, faces, direction, *, tolerance_deg=0.1, pixel=0.1,
                    margin=2, normals=None):
    """Per-face visibility along an approach direction. Returns (F,) bool.

    Replaces meshlib's undercut verdict, whose hard front/back-facing test
    flips arbitrarily for faces tangent to the direction (vertical walls) —
    the speckle the raster fixes in this module solved for the tool fields.
    A face is visible iff:

    - it faces the tool within an angular relaxation: n·d >= -sin(tolerance),
      so a wall at exactly 90 deg is deterministically front-facing, and
    - nothing shadows it per the rendered height map: the column top sampled
      at the centroid (pushed one pixel outward along the lateral component
      of the normal, so walls escape their own silhouette column) does not
      rise above the face itself. The bracket-corner min makes the sample
      subpixel-stable on walls sitting exactly on pixel boundaries.

    ``normals`` overrides the facet cross-product normals — pass exact BREP
    surface normals so curved faces classify by their true angle rather than
    the coarse tessellation's chord planes.

    Conservative limits: cavities narrower than ~1 pixel may not resolve,
    and overhangs closer than 1.5*pixel above a face go undetected.
    """
    heights, frame = render_heightmap(mesh, direction, pixel, margin=margin)
    d = frame["direction"]

    tri = verts[faces]
    centroids = tri.mean(axis=1)
    if normals is None:
        normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True),
                              1e-30)
    else:
        normals = np.asarray(normals, dtype=np.float64)

    ndotd = normals @ d
    facing = ndotd >= -np.sin(np.radians(tolerance_deg))

    # walls (|lateral| ~ 1) sample just outside their own material column,
    # floors (|lateral| ~ 0) sample in place
    lateral = normals - ndotd[:, None] * d
    fx, fy, height = project_vertices_float(centroids + pixel * lateral, frame)

    ix4, iy4 = _bracket_corners(fx, fy, heights.shape)
    top = heights[iy4, ix4].min(axis=0)
    occluded = top > height + 1.5 * pixel

    return facing & ~occluded


def euclidean_gap(closed, fx, fy, height, pixel, window_px):
    """
    Euclidean distance from each vertex to the machined solid described by the
    closed height map (material below closed(x, y), including the vertical
    sheets between adjacent columns). For every pixel q in a window around the
    vertex the distance to the column's epigraph is
    sqrt(lateral(q)^2 + max(closed(q) - h, 0)^2): the clamp makes a column
    whose machined surface passes below the vertex count only laterally, which
    is what keeps swept vertical (and near-vertical draft) walls unflagged.

    Takes FRACTIONAL pixel coordinates: lateral distances are measured from
    the true vertex position, and the vertex's own column is additionally
    sampled bilinearly - on smooth sloped/curved surfaces this removes the
    +-(slope x pixel) quantization noise that otherwise speckles verdicts
    whose true gap sits near the threshold.

    Gaps up to window_px * pixel are exact to pixel resolution; larger gaps
    are lower bounds - fine for thresholding at tolerances within the window.
    """
    ix = np.clip(np.floor(fx).astype(int), 0, closed.shape[1] - 1)
    iy = np.clip(np.floor(fy).astype(int), 0, closed.shape[0] - 1)

    # bilinear center candidate (exact on smooth surfaces)
    dz0 = bilinear_sample(closed, fx, fy) - height
    np.maximum(dz0, 0.0, out=dz0)
    best = dz0 * dz0

    for dy in range(-window_px, window_px + 1):
        qy = np.clip(iy + dy, 0, closed.shape[0] - 1)
        # columns are cells, not points: distance to the cell's epigraph
        # sheet is measured to the nearest cell edge, not the centre
        laty = np.maximum(np.abs(qy + 0.5 - fy) - 0.5, 0.0) * pixel
        for dx in range(-window_px, window_px + 1):
            qx = np.clip(ix + dx, 0, closed.shape[1] - 1)
            latx = np.maximum(np.abs(qx + 0.5 - fx) - 0.5, 0.0) * pixel
            dz = closed[qy, qx] - height
            np.maximum(dz, 0.0, out=dz)
            d2 = latx * latx + laty * laty + dz * dz
            np.minimum(best, d2, out=best)
    return np.sqrt(best)


# ---------------------------------------------------------------------------
# tool bottom profiles and morphology
# ---------------------------------------------------------------------------

def tip_profile(diameter, corner_radius, pixel):
    """
    Radial profile of the tool bottom on the pixel grid: profile[dy, dx] =
    height of the cutting surface above the tip at that radial offset, or
    None outside the tool silhouette.

    corner_radius = diameter/2 -> ball nose, 0 -> flat endmill.
    Returns (footprint bool array, profile float array).
    """
    radius = diameter / 2.0
    corner_radius = min(max(corner_radius, 0.0), radius)
    flat_radius = radius - corner_radius

    n = int(np.ceil(radius / pixel))
    offsets = (np.arange(2 * n + 1) - n) * pixel
    rr = np.hypot(offsets[None, :], offsets[:, None])

    footprint = rr <= radius
    profile = np.zeros_like(rr)
    rim = rr > flat_radius
    if corner_radius > 0:
        arg = np.clip(corner_radius**2 - np.clip(rr - flat_radius, 0, None) ** 2, 0, None)
        profile[rim] = corner_radius - np.sqrt(arg[rim])
    else:
        # sharp corner: anything outside the flat radius is wall
        profile[rim] = 0.0
    return footprint, profile


def _flat_dilate(work, radius, pixel, max_footprint=None, cval=FREE_SPACE):
    """
    Grayscale dilation with a FLAT disk, decomposed row-wise: the disk is a
    stack of horizontal chords, so dilation = max over row offsets of a 1D
    max filter with the chord's length. maximum_filter1d runs a moving max,
    so the cost is O(H * W * n_rows) regardless of the chord lengths - and
    the result is EXACT at full resolution. This replaces the max-pooled
    approximation, whose pooled-cell rounding shifted feature edges by up to
    (pool - 1) pixels with a phase that differed between the min and max map
    edges, biasing verdicts near walls (`max_footprint` is kept for call
    compatibility and ignored). `cval` is the border value: FREE_SPACE for
    plain dilations, -FREE_SPACE when the caller erodes via negation
    (outside the map is air either way).
    """
    if radius <= 0:
        return work
    n = int(radius / pixel)
    r2 = (radius / pixel) ** 2
    H = work.shape[0]
    src = np.pad(work, ((n, n), (0, 0)), constant_values=cval)
    out = None
    for dy in range(-n, n + 1):
        # the disk's chord at row offset dy: include dx iff hypot(dy, dx) <= r
        half = int(np.sqrt(r2 - dy * dy))
        filt = ndimage.maximum_filter1d(src[n + dy:n + dy + H], size=2 * half + 1,
                                        axis=1, mode="constant", cval=cval)
        out = filt if out is None else np.maximum(out, filt, out=out)
    return out


def _sphere_structure(rc, pixel):
    n = int(np.ceil(rc / pixel))
    offsets = (np.arange(2 * n + 1) - n) * pixel
    rr = np.hypot(offsets[None, :], offsets[:, None])
    footprint = rr <= rc
    profile = rc - np.sqrt(np.clip(rc**2 - rr**2, 0.0, None))
    return footprint, np.where(footprint, -profile, 0.0)


def _sphere_dilate(work, rc, pixel, max_step_px=12, erode=False):
    """
    Grayscale dilation (or erosion) with a spherical cap of radius rc,
    chunked into steps: ball(r1) + ball(r2) = ball(r1 + r2) under Minkowski
    sums, so a big cap is a sequence of small-cap dilations - O(rc) instead
    of O(rc^2) pixels. Chunking is conservative (the sampled cap is never
    larger than the true cap).
    """
    if rc <= 0:
        return work
    step = max_step_px * pixel
    remaining = rc
    op = ndimage.grey_erosion if erode else ndimage.grey_dilation
    while remaining > 1e-12:
        r = min(step, remaining)
        footprint, structure = _sphere_structure(r, pixel)
        work = op(work, footprint=footprint, structure=structure,
                  mode="constant", cval=FREE_SPACE)
        remaining -= r
    return work


def _mink_pad(radius, pixel):
    """Padding (pixels) so the morphology sees the air beyond the rendered
    map: without it, erosions treat out-of-map columns as bottomless and
    everything within a tool radius of the border reads as machinable."""
    return int(np.ceil(radius / pixel)) + 2


@log_execution_time
def tip_position_map(heights, diameter, corner_radius, pixel):
    """
    Inverse tool offset (ITO): the lowest tip height at which the tool, with
    its axis over each pixel, rests on the part without gouging it. This is
    the classic CAM tool-position surface; the machined surface is its
    forward offset (see close_heightmap).

    The tool bottom is disk(D/2 - rc) + sphere(rc) under Minkowski sums, and
    dilation by a sum is sequential dilation, so the flat part runs
    row-decomposed and the spherical part chunked - cost grows ~linearly
    with the tool radius instead of quadratically.
    """
    radius = diameter / 2.0
    corner_radius = min(max(corner_radius, 0.0), radius)
    pad = _mink_pad(radius, pixel)
    work = np.pad(heights.astype(np.float64), pad, constant_values=FREE_SPACE)
    work = _flat_dilate(work, radius - corner_radius, pixel)
    work = _sphere_dilate(work, corner_radius, pixel)
    return work[pad:-pad, pad:-pad]


@log_execution_time
def close_heightmap(heights, diameter, corner_radius, pixel):
    """
    Grayscale closing of the height map with the tool bottom profile: returns
    the machined surface, i.e. the lowest surface the tool tip envelope can
    generate above the current one. closing >= heights everywhere; the
    difference is material the tool cannot remove. Decomposed like
    tip_position_map (disk part row-decomposed, sphere part chunked); the
    map is padded with free space so borders behave like the real air
    outside.
    """
    radius = diameter / 2.0
    corner_radius = min(max(corner_radius, 0.0), radius)
    flat_radius = radius - corner_radius

    pad = _mink_pad(radius, pixel)
    work = np.pad(heights.astype(np.float64), pad, constant_values=FREE_SPACE)
    work = _flat_dilate(work, flat_radius, pixel)
    work = _sphere_dilate(work, corner_radius, pixel)
    work = _sphere_dilate(work, corner_radius, pixel, erode=True)
    work = -_flat_dilate(-work, flat_radius, pixel, cval=-FREE_SPACE)
    return work[pad:-pad, pad:-pad].astype(np.float32)


def _contact_offsets(diameter, corner_radius, pixel, max_rings=24, max_angles=48):
    """
    Sampled contact offsets (dy_px, dx_px, profile_height) covering the tool
    silhouette as rings x angles instead of every footprint pixel: the profile
    only depends on the ring radius, so the offset budget stays constant no
    matter how large the tool is relative to the pixel size. Skipping
    candidate axis positions is strictly conservative for the stickout min.
    """
    radius = diameter / 2.0
    corner_radius = min(max(corner_radius, 0.0), radius)
    flat_radius = radius - corner_radius
    n = int(np.ceil(radius / pixel))

    ring_radii = np.unique(np.round(np.linspace(0, n, min(n + 1, max_rings))).astype(int))
    offsets = []
    for rr_px in ring_radii:
        rr = min(rr_px * pixel, radius)
        if rr <= flat_radius or corner_radius <= 0:
            prof = 0.0
        else:
            prof = corner_radius - np.sqrt(max(corner_radius**2 - (rr - flat_radius) ** 2, 0.0))
        if rr_px == 0:
            offsets.append((0, 0, prof))
            continue
        n_angles = int(min(max(8, np.ceil(2 * np.pi * rr_px / 2)), max_angles))
        seen = set()
        for k in range(n_angles):
            theta = 2 * np.pi * k / n_angles
            dy = int(round(rr_px * np.sin(theta)))
            dx = int(round(rr_px * np.cos(theta)))
            if (dy, dx) not in seen:
                seen.add((dy, dx))
                offsets.append((dy, dx, prof))
    return offsets


def _tip_aware_min_stickout_ref(tip_map, clear_map, diameter, corner_radius,
                                pixel, fx, fy, height):
    """Reference implementation of tip_aware_min_stickout: the direct
    offset-by-offset loop the fast path must match to float noise. Kept for
    the A/B checks in test_zmap.py — O(offsets x verts) with two fancy-index
    gathers per offset, ~1000x slower than the chunked path on big parts."""
    eps = 1.5 * pixel

    ix, iy = _bracket_corners(fx, fy, tip_map.shape)  # (4, V)

    best = np.full(ix.shape, np.inf)
    for dy, dx, prof in _contact_offsets(diameter, corner_radius, pixel):
        ax = np.clip(ix - dx, 0, tip_map.shape[1] - 1)
        ay = np.clip(iy - dy, 0, tip_map.shape[0] - 1)
        tip_req = height - prof
        feasible = tip_map[ay, ax] <= tip_req + eps
        value = clear_map[ay, ax] - tip_req
        np.minimum(best, np.where(feasible, value, np.inf), out=best)

    # vertices no contact offset can touch are tip-blocked anyway; fall back
    # to the vertex-centred estimate so the field stays finite
    fallback = clear_map[iy, ix] - height
    best = np.where(np.isfinite(best), best, fallback)
    return np.maximum(best.min(axis=0), 0.0)


def _contact_rings(diameter, corner_radius, pixel, padded_width):
    """_contact_offsets grouped by profile value, linearized into a map with
    `padded_width` columns. All offsets of one ring share one profile, so
    the feasibility threshold and the profile term hoist out of the
    per-offset loop (rc=0 collapses every offset into a single ring)."""
    rings = {}
    for dy, dx, prof in _contact_offsets(diameter, corner_radius, pixel):
        rings.setdefault(prof, []).append(dy * padded_width + dx)
    return [(prof, offsets) for prof, offsets in rings.items()]


def _interleaved_padded_maps(tip_map, clear_map, pad):
    """(tip, clear) edge-padded by `pad` pixels and interleaved into one
    complex64 plane (real = tip, imag = clear), flattened. Edge padding
    replicates exactly the border value np.clip indexing reads, and the
    single 8-byte gather per axis candidate replaces two float64
    fancy-index gathers. Returns (flat plane, padded width)."""
    tip_pad = np.pad(tip_map.astype(np.float32), pad, mode="edge")
    clear_pad = np.pad(clear_map.astype(np.float32), pad, mode="edge")
    pair = np.empty(tip_pad.shape + (2,), dtype=np.float32)
    pair[..., 0] = tip_pad
    pair[..., 1] = clear_pad
    return pair.reshape(-1).view(np.complex64), tip_pad.shape[1]


@log_execution_time
def tip_aware_min_stickout(tip_map, clear_map, diameter, corner_radius, pixel,
                           fx, fy, height):
    """
    Per-vertex minimal stickout for ONE holder cylinder (measured from the
    tool tip), coupling the tip geometry with the holder:

    For a vertex v the tool can touch it with its axis at any offset o inside
    the tool silhouette - bottom contact through the tip profile, flank
    contact at the rim. Touching v via offset o puts the tip at
    t = height(v) - profile(o), feasible iff t >= tip_map(axis) (no gouging;
    for bottom contact this reduces to exact tangency). The cylinder whose
    lower end sits `stickout` above the tip clears iff
    clear_map(axis) <= t + stickout, so:

        min_stickout(v) = min over feasible o of clear_map(a) - height(v) + profile(o)

    This is what the vertex-centred clearance field gets wrong for ball and
    bull noses: flank contact adds profile(o) (up to the corner radius) of
    extra stickout, and the axis - where the holder actually is - sits up to
    D/2 away from the contact point. Contact offsets are ring/angle sampled
    (see _contact_offsets) so the cost per field is O(samples x verts),
    independent of the tool diameter.

    Takes FRACTIONAL pixel coordinates and searches from the 2x2 pixel
    bracket around each vertex (min over the four corners): floor-indexed
    starts alternate between the two columns straddling a vertical wall's
    height cliff, speckling the field vertex-by-vertex. Axis candidates that
    fall outside the map clamp to the border; with the rendered margin those
    are exterior-air columns whose dilated values only ever err conservative
    (over-required stickout for axes far outside the silhouette).

    Implementation (matches _tip_aware_min_stickout_ref to float32 noise):
    the maps are edge-padded (== the reference's np.clip border reads) and
    interleaved as one complex64 plane so each offset is a single 8-byte
    gather through a shifted view — no per-offset index arithmetic. Offsets
    group into profile rings whose threshold hoists out of the inner loop,
    the mask fuses into a where= minimum, and vertices run in spatially
    sorted chunks so the gather footprint stays cache-resident across the
    whole offset set.
    """
    eps = np.float32(1.5 * pixel)
    n = int(np.ceil((diameter / 2.0) / pixel))
    pad = n + 1

    ix, iy = _bracket_corners(fx, fy, tip_map.shape)  # (4, V)
    flat, padded_width = _interleaved_padded_maps(tip_map, clear_map, pad)
    rings = _contact_rings(diameter, corner_radius, pixel, padded_width)

    # flat_ext[omax - off:][g0] == map[clip(iy - dy), clip(ix - dx)]: the
    # zero prefix absorbs the largest positive shift so every offset is a
    # view, and padded bracket indices never reach the prefix or the end
    omax = n * padded_width + n
    flat_ext = np.concatenate([np.zeros(omax, dtype=np.complex64), flat])

    g0 = (iy.astype(np.intp) + pad) * padded_width + (ix + pad)
    height32 = np.asarray(height, dtype=np.float32)

    # spatial sort: each chunk gathers from a compact map region that stays
    # L2/L3-resident across all offsets (pure permutation — exact)
    order = np.argsort(g0[0], kind="stable")
    g0 = g0[:, order]
    height32 = height32[order]

    count = g0.shape[1]
    chunk = 32768
    out = np.empty(count, dtype=np.float32)
    pairbuf = np.empty((4, chunk), dtype=np.complex64)
    feasible = np.empty((4, chunk), dtype=bool)
    ringbest = np.empty((4, chunk), dtype=np.float32)
    best = np.empty((4, chunk), dtype=np.float32)

    for start in range(0, count, chunk):
        stop = min(start + chunk, count)
        size = stop - start
        g = g0[:, start:stop]
        h = height32[start:stop]
        pb, fb = pairbuf[:, :size], feasible[:, :size]
        rb, bb = ringbest[:, :size], best[:, :size]
        tips, clears = pb.real, pb.imag

        bb.fill(np.inf)
        for prof, offsets in rings:
            thr = h - np.float32(prof) + eps
            rb.fill(np.inf)
            for offset in offsets:
                np.take(flat_ext[omax - offset:], g, out=pb, mode="clip")
                np.less_equal(tips, thr, out=fb)
                np.minimum(rb, clears, out=rb, where=fb)
            np.add(rb, np.float32(prof), out=rb)
            np.minimum(bb, rb, out=bb)

        # vertices no contact offset can touch are tip-blocked anyway; fall
        # back to the vertex-centred estimate so the field stays finite
        np.take(flat, g, out=pb, mode="clip")  # zero offset: own columns
        value = bb - h
        np.copyto(value, clears - h, where=~np.isfinite(bb))
        out[start:stop] = value.min(axis=0)

    result = np.empty(count, dtype=np.float32)
    result[order] = out
    return np.maximum(result, 0.0)


@log_execution_time
def clearance_heightmap(heights, radius, pixel, max_footprint=32):
    """
    Height of the tallest obstruction within `radius` of each pixel: a flat
    grayscale dilation (padded with free space so the border behaves like the
    real air outside the rendered map). A cylinder of this radius whose
    bottom sits at height h above a vertex collides iff
    clearance(vertex) - height(vertex) > h. Implemented with the exact flat
    dilation used everywhere else.
    """
    pad = _mink_pad(radius, pixel)
    work = np.pad(heights.astype(np.float64), pad, constant_values=FREE_SPACE)
    dilated = _flat_dilate(work, radius, pixel, max_footprint=max_footprint)
    return dilated[pad:-pad, pad:-pad].astype(np.float32)


# ---------------------------------------------------------------------------
# per-direction cache
# ---------------------------------------------------------------------------

def _tip_key(diameter, corner_radius):
    return f"tip_{diameter:.6g}_{corner_radius:.6g}"


def _clear_key(radius):
    return f"clear_{radius:.6g}"


def _sreq_key(diameter, corner_radius, radius):
    return f"sreq_{diameter:.6g}_{corner_radius:.6g}_{radius:.6g}"


class DirectionCache:
    """
    Cached per-vertex fields for one approach direction: any number of tip
    gap fields and clearance fields, persisted as an .npz so repeated tool
    queries never touch geometry again. Fields come from 2D grayscale
    morphology on a rendered height map; gaps are windowed Euclidean
    distances to the machined solid.
    """

    VERSION = 6  # + mesh fingerprint guards re-meshed workdirs

    def __init__(self, workdir, direction_index, verts=None, faces=None, pixel=0.1,
                 window=0.3):
        self.path = os.path.join(workdir, "zcache", f"dir_{direction_index:04d}.npz")
        self.direction_index = direction_index
        self.verts = verts
        self.faces = faces
        self.pixel = pixel
        self.window = window  # gap accuracy window: gaps up to this are Euclidean-exact
        self._fields = {}
        self._maps = {}  # in-memory full-resolution maps (not persisted)
        self._mesh = None

        directions_path = os.path.join(workdir, "directions.npy")
        self.directions_fingerprint = file_fingerprint(directions_path)
        self.mesh_fingerprint = files_fingerprint(
            [os.path.join(workdir, "fine_verts.npy"),
             os.path.join(workdir, "fine_faces.npy")]) or ""
        directions = np.load(directions_path)
        self.direction = directions[direction_index]

        if os.path.exists(self.path):
            stored = np.load(self.path, allow_pickle=False)
            same_pixel = abs(stored["pixel"][0] - pixel) < 1e-12
            same_version = "version" in stored.files and stored["version"][0] == self.VERSION
            # fields are keyed by direction INDEX over the fine mesh: a
            # regenerated directions.npy renumbers them and a re-meshed
            # workdir re-indexes the vertices, so a cache from another
            # direction set or mesh must not be trusted
            same_dirs = ("dirfp" in stored.files
                         and stored["dirfp"][0].decode() == self.directions_fingerprint)
            same_mesh = ("meshfp" in stored.files
                         and stored["meshfp"][0].decode() == self.mesh_fingerprint)
            if same_pixel and same_version and same_dirs and same_mesh:
                self._fields = {k: stored[k] for k in stored.files}
                logger.debug(f"Loaded cache {self.path} with {len(self._fields)} arrays")
            else:
                logger.warning(
                    f"Pixel size, cache version, direction set or mesh "
                    f"changed, discarding cache {self.path}")
                self._fields = {}

        if not self._fields:
            self._fields = {
                "version": np.array([self.VERSION]),
                "pixel": np.array([pixel]),
                "dirfp": np.array([self.directions_fingerprint], dtype="S12"),
                "meshfp": np.array([self.mesh_fingerprint], dtype="S12"),
            }
            # margin covers the whole euclidean_gap window (window_px in
            # tip_gap) plus one pixel, so border-wall vertices see real
            # exterior air instead of clamping into the last material column
            margin = max(2, int(np.ceil(window / pixel))) + 1
            heights, frame = render_heightmap(self._get_mesh(), self.direction, pixel,
                                              margin=margin)
            self._fields.update({
                "heights": heights,
                "origin": frame["origin"],
                "x_axis": frame["x_axis"],
                "y_axis": frame["y_axis"],
                "direction": frame["direction"],
            })
            self._save()

        self.frame = {
            "origin": self._fields["origin"],
            "x_axis": self._fields["x_axis"],
            "y_axis": self._fields["y_axis"],
            "direction": self._fields["direction"],
            "pixel": self._fields["pixel"][0],
        }
        self.heights = self._fields["heights"]
        if verts is not None:
            self._fx, self._fy, self._vheight = project_vertices_float(verts, self.frame)
            self._ix = np.floor(self._fx).astype(int)
            self._iy = np.floor(self._fy).astype(int)

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        np.savez_compressed(self.path, **self._fields)

    def _get_mesh(self):
        if self._mesh is None:
            if self.verts is None or self.faces is None:
                raise ValueError("verts and faces are required to compute new fields")
            from meshlib import mrmeshnumpy as mn
            self._mesh = mn.meshFromFacesVerts(self.faces, self.verts)
        return self._mesh

    def _vertex_samples(self):
        if not hasattr(self, "_ix"):
            raise ValueError("Vertex projections need verts passed to the constructor")
        return self._ix, self._iy, self._vheight

    def tip_gap(self, diameter, corner_radius):
        """
        Per-vertex gap left by the tool tip: 0 where the tip touches the
        surface, > 0 where material blocks it. Computed once per (D, rc) and
        cached.
        """
        key = _tip_key(diameter, corner_radius)
        if key not in self._fields:
            logger.debug(f"Computing tip field {key} for direction {self.direction_index}")
            pixel = self.frame["pixel"]
            closed = close_heightmap(self.heights, diameter, corner_radius, pixel)
            self._vertex_samples()  # ensure projections exist
            window_px = max(2, int(np.ceil(self.window / pixel)))
            gap = euclidean_gap(closed, self._fx, self._fy, self._vheight, pixel, window_px)
            self._fields[key] = gap.astype(np.float32)
            self._save()
        return self._fields[key]

    def _clearance_map(self, radius):
        key = "map_" + _clear_key(radius)
        if key not in self._maps:
            self._maps[key] = clearance_heightmap(self.heights, radius, self.frame["pixel"])
        return self._maps[key]

    def _tip_map(self, diameter, corner_radius):
        key = "map_" + _tip_key(diameter, corner_radius)
        if key not in self._maps:
            self._maps[key] = tip_position_map(self.heights, diameter, corner_radius, self.frame["pixel"])
        return self._maps[key]

    def tip_min_stickout(self, diameter, corner_radius, radius):
        """
        Per-vertex minimal stickout (from the tool tip) for one holder
        cylinder of `radius`, coupled with the tip geometry of
        (diameter, corner_radius) - see tip_aware_min_stickout. Computed
        once per (tip, radius) and cached.
        """
        key = _sreq_key(diameter, corner_radius, radius)
        if key not in self._fields:
            logger.debug(f"Computing stickout field {key} for direction {self.direction_index}")
            pixel = self.frame["pixel"]
            self._vertex_samples()  # ensure projections exist
            sreq = tip_aware_min_stickout(
                self._tip_map(diameter, corner_radius), self._clearance_map(radius),
                diameter, corner_radius, pixel, self._fx, self._fy, self._vheight,
            )
            self._fields[key] = sreq.astype(np.float32)
            self._save()
        return self._fields[key]

    def clearance(self, radius):
        """
        Per-vertex clearance: height of the tallest obstruction within
        `radius`, measured above the vertex. Computed once per radius.
        NOTE: vertex-centred - only exact for a tool of negligible radius or
        pure bottom contact; prefer tip_min_stickout for real tools.
        """
        key = _clear_key(radius)
        if key not in self._fields:
            logger.debug(f"Computing clearance field {key} for direction {self.direction_index}")
            dilated = self._clearance_map(radius)
            self._vertex_samples()  # ensure projections exist
            ix4, iy4 = _bracket_corners(self._fx, self._fy, dilated.shape)
            clear = dilated[iy4, ix4].min(axis=0) - self._vheight
            self._fields[key] = clear.astype(np.float32)
            self._save()
        return self._fields[key]

    def min_stickout(self, cylinders, tip=None):
        """
        Per-vertex minimal stickout (tool length out of the holder) so that a
        holder modelled as stacked concentric cylinders [(radius, start), ...]
        (start = distance from the tool tip to the cylinder's lower end for
        stickout 0) clears the part.

        Pass tip=(diameter, corner_radius) to couple the holder with the tip
        geometry (flank contact, axis offset) - required for correct results
        with ball and bull noses. Without a tip the vertex-centred clearance
        approximation is used (a tool of negligible diameter).
        """
        stickout = None
        for radius, start in cylinders:
            if tip is not None:
                required = self.tip_min_stickout(tip[0], tip[1], radius) - start
            else:
                required = self.clearance(radius) - start
            stickout = required if stickout is None else np.maximum(stickout, required)
        return stickout


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------

def faces_all_verts(faces, vertex_flags):
    """Faces whose three vertices are all flagged."""
    return np.where(vertex_flags[faces].all(axis=1))[0]


def tool_face_verdict(cache, faces, angles_deg, *, diameter, corner_radius=0.0,
                      stickout=None, cylinders=None, tollerance=0.1,
                      wall_tollerance=1.0):
    """Per-face machinability verdict for one tool, from cached fields only.

    THE canonical face rule — shared by CLI compose and the tool-aware setup
    verdict; the viewer's interactive thresholds mirror it client-side:

    - tip: a face is blocked iff all three vertex gaps exceed the threshold.
      Near-vertical walls (within ``wall_tollerance`` degrees of 90°) are
      finished by the tool flank, so they use the pixel-noise-proof
      threshold max(tollerance, 2.5 * pixel) instead of the plain tolerance
      — reachable walls carry ~1 pixel of height-map quantization noise,
      unreachable ones sit whole millimetres inside the closed solid.
    - holder: with ``cylinders`` [(radius, start), ...] and a ``stickout``,
      a face is additionally blocked iff all three vertices require more
      stickout than the tool has (tip-aware fields when available).

    ``angles_deg`` is the per-face angle between the outward normal and the
    cache's approach direction. Returns (machinable bool[F], gap,
    min_stick); machinable is NOT masked by visibility — callers AND it
    with the direction's accessibility row.
    """
    gap = cache.tip_gap(diameter, corner_radius)
    wall_threshold = max(tollerance, 2.5 * float(cache.pixel))
    is_wall = np.abs(np.asarray(angles_deg) - 90.0) <= wall_tollerance
    threshold = np.where(is_wall, wall_threshold, tollerance)

    blocked = (gap[faces] > threshold[:, None]).all(axis=1)

    min_stick = None
    if cylinders:
        min_stick = cache.min_stickout(cylinders, tip=(diameter, corner_radius))
        if stickout is not None:
            blocked |= (min_stick[faces] > stickout + tollerance).all(axis=1)
    return ~blocked, gap, min_stick


def compose_unreachable(cache, faces, diameter, corner_radius, tollerance,
                        stickout=None, cylinders=None):
    """
    Per-face unreachable mask for a full tool assembly, assembled purely from
    cached per-vertex fields:

    - tip: gap field of (diameter, corner_radius) thresholded at `tollerance`
    - holder/shank: stacked cylinders [(radius, start), ...] at `stickout`

    Returns (unreachable_faces, vertex_gap, vertex_min_stickout).
    """
    gap = cache.tip_gap(diameter, corner_radius)
    blocked = gap > tollerance

    min_stick = None
    if cylinders:
        min_stick = cache.min_stickout(cylinders, tip=(diameter, corner_radius))
        if stickout is not None:
            blocked = blocked | (min_stick > stickout + tollerance)

    return faces_all_verts(faces, blocked), gap, min_stick
