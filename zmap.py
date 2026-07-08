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

from utils import log_execution_time

FREE_SPACE = -1e30  # height of pixels with no material below (tool can plunge)


# ---------------------------------------------------------------------------
# depth map rendering
# ---------------------------------------------------------------------------

@log_execution_time
def render_heightmap(mesh, direction, pixel):
    """
    Render the height map of `mesh` seen along approach direction `direction`
    (pointing from the part towards the tool). Returns (heights, frame):

    - heights: 2D float32 array, heights[iy, ix] = surface height along the
      direction axis (larger = closer to the tool), FREE_SPACE where empty
    - frame: dict with orthonormal axes (x, y, d), origin and pixel size, so
      vertices can be projected into map coordinates
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


def disk_footprint(radius, pixel):
    n = int(np.ceil(radius / pixel))
    offsets = (np.arange(2 * n + 1) - n) * pixel
    rr = np.hypot(offsets[None, :], offsets[:, None])
    return rr <= radius


def _flat_dilate(work, radius, pixel, max_footprint=32, cval=FREE_SPACE):
    """
    Grayscale dilation with a FLAT disk, max-pooling the map first when the
    footprint would be large (same conservative trick as clearance_heightmap:
    obstructions round up and outward by at most one pooled pixel). `cval` is
    the border value: FREE_SPACE for plain dilations, -FREE_SPACE when the
    caller erodes via negation (outside the map is air either way).
    """
    if radius <= 0:
        return work
    pool = int(np.ceil(radius / (max_footprint * pixel)))
    src = work
    if pool > 1:
        pad_y = (-src.shape[0]) % pool
        pad_x = (-src.shape[1]) % pool
        src = np.pad(src, ((0, pad_y), (0, pad_x)), constant_values=cval)
        src = src.reshape(src.shape[0] // pool, pool, src.shape[1] // pool, pool).max(axis=(1, 3))
    eff_pixel = pixel * pool
    eff_radius = radius + (0.71 * eff_pixel if pool > 1 else 0.0)
    footprint = disk_footprint(eff_radius, eff_pixel)
    out = ndimage.grey_dilation(src, footprint=footprint, mode="constant", cval=cval)
    if pool > 1:
        out = np.repeat(np.repeat(out, pool, axis=0), pool, axis=1)
        out = out[: work.shape[0], : work.shape[1]]
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
    dilation by a sum is sequential dilation, so the flat part runs pooled
    and the spherical part chunked - cost grows ~linearly with the tool
    radius instead of quadratically.
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
    tip_position_map (disk part pooled, sphere part chunked); the map is
    padded with free space so borders behave like the real air outside.
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


@log_execution_time
def tip_aware_min_stickout(tip_map, clear_map, diameter, corner_radius, pixel,
                           ix, iy, height):
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
    """
    eps = 1.5 * pixel

    ix = np.clip(ix, 0, tip_map.shape[1] - 1)
    iy = np.clip(iy, 0, tip_map.shape[0] - 1)

    best = np.full(height.shape, np.inf)
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
    return np.maximum(best, 0.0)


@log_execution_time
def clearance_heightmap(heights, radius, pixel, max_footprint=32):
    """
    Height of the tallest obstruction within `radius` of each pixel: a flat
    grayscale dilation (padded with free space so the border behaves like the
    real air outside the rendered map). A cylinder of this radius whose
    bottom sits at height h above a vertex collides iff
    clearance(vertex) - height(vertex) > h. Implemented with the pooled flat
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
    queries never touch geometry again.

    Two interchangeable engines fill the fields:
    - "zmap" (default): 2D grayscale morphology on a rendered height map;
      gaps are windowed Euclidean distances to the machined solid
    - "voxel": 3D voxel closings on the undercut-fixed mesh (analysis.py);
      gaps are mesh projection distances

    Both produce per-vertex float fields with identical semantics, so
    compose_unreachable works on either cache.
    """

    VERSION = 3  # padded-border morphology + subpixel Euclidean gap sampling

    def __init__(self, workdir, direction_index, verts=None, faces=None, pixel=0.1,
                 window=0.3, engine="zmap", scale=10.0):
        suffix = "" if engine == "zmap" else f"_{engine}"
        self.path = os.path.join(workdir, "zcache", f"dir_{direction_index:04d}{suffix}.npz")
        self.direction_index = direction_index
        self.verts = verts
        self.faces = faces
        self.pixel = pixel
        self.window = window  # gap accuracy window: gaps up to this are Euclidean-exact
        self.engine = engine
        self.scale = scale  # anisotropy stretch factor for voxel in-plane offsets
        self._fields = {}
        self._maps = {}  # in-memory full-resolution maps (not persisted)
        self._mesh = None
        self._undercut_mesh = None

        directions = np.load(os.path.join(workdir, "directions.npy"))
        self.direction = directions[direction_index]

        if os.path.exists(self.path):
            stored = np.load(self.path, allow_pickle=False)
            same_pixel = abs(stored["pixel"][0] - pixel) < 1e-12
            same_version = "version" in stored.files and stored["version"][0] == self.VERSION
            if same_pixel and same_version:
                self._fields = {k: stored[k] for k in stored.files}
                logger.debug(f"Loaded cache {self.path} with {len(self._fields)} arrays")
            else:
                logger.warning(f"Pixel size or cache version changed, discarding cache {self.path}")
                self._fields = {}

        if not self._fields:
            self._fields = {
                "version": np.array([self.VERSION]),
                "pixel": np.array([pixel]),
            }
            if engine == "zmap":
                heights, frame = render_heightmap(self._get_mesh(), self.direction, pixel)
                self._fields.update({
                    "heights": heights,
                    "origin": frame["origin"],
                    "x_axis": frame["x_axis"],
                    "y_axis": frame["y_axis"],
                    "direction": frame["direction"],
                })
            self._save()

        if engine == "zmap":
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

    def _get_undercut_mesh(self):
        if self._undercut_mesh is None:
            from analysis import fix_undercuts
            d = self.direction
            self._undercut_mesh = fix_undercuts(self._get_mesh(), d[0], d[1], d[2], tollerance=self.pixel)
        return self._undercut_mesh

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
            logger.debug(f"Computing tip field {key} for direction {self.direction_index} ({self.engine})")
            if self.engine == "zmap":
                pixel = self.frame["pixel"]
                closed = close_heightmap(self.heights, diameter, corner_radius, pixel)
                self._vertex_samples()  # ensure projections exist
                window_px = max(2, int(np.ceil(self.window / pixel)))
                gap = euclidean_gap(closed, self._fx, self._fy, self._vheight, pixel, window_px)
            else:
                from analysis import endmill_closing, get_distance
                closed_mesh = endmill_closing(
                    self._get_undercut_mesh(), self.direction, diameter, corner_radius,
                    self.pixel, scale=self.scale,
                )
                distances = get_distance(self._get_mesh(), closed_mesh)
                gap = np.abs(np.asarray(distances))
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
        (diameter, corner_radius) - see tip_aware_min_stickout. zmap engine
        only; computed once per (tip, radius) and cached.
        """
        key = _sreq_key(diameter, corner_radius, radius)
        if key not in self._fields:
            if self.engine != "zmap":
                raise NotImplementedError("tip-aware stickout fields need the zmap engine")
            logger.debug(f"Computing stickout field {key} for direction {self.direction_index}")
            pixel = self.frame["pixel"]
            ix, iy, vheight = self._vertex_samples()
            sreq = tip_aware_min_stickout(
                self._tip_map(diameter, corner_radius), self._clearance_map(radius),
                diameter, corner_radius, pixel, ix, iy, vheight,
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
            logger.debug(f"Computing clearance field {key} for direction {self.direction_index} ({self.engine})")
            if self.engine == "zmap":
                dilated = self._clearance_map(radius)
                ix, iy, vheight = self._vertex_samples()
                clear = sample_map(dilated, ix, iy) - vheight
            else:
                # grow the undercut-fixed mesh in-plane by the cylinder
                # radius (3D, exact), then read the grown volume's top
                # surface: its height above a vertex is the clearance
                from meshlib import mrmeshpy as mm_
                from analysis import scale_along_axis, single_offset
                work = mm_.copyMesh(self._get_undercut_mesh())
                scale_along_axis(work, self.direction, self.scale)
                work = single_offset(work, radius, self.pixel, decimate=False)
                scale_along_axis(work, self.direction, 1.0 / self.scale)
                grown_heights, frame = render_heightmap(work, self.direction, self.pixel)
                ix, iy, vheight = project_vertices(self.verts, frame)
                clear = sample_map(grown_heights, ix, iy) - vheight
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
            if tip is not None and self.engine == "zmap":
                required = self.tip_min_stickout(tip[0], tip[1], radius) - start
            else:
                required = self.clearance(radius) - start
            stickout = required if stickout is None else np.maximum(stickout, required)
        return stickout


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------

def faces_all_verts(faces, vertex_flags):
    """Faces whose three vertices are all flagged (same rule as map_result_faces)."""
    return np.where(vertex_flags[faces].all(axis=1))[0]


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
