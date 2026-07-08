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


def project_vertices(verts, frame):
    """
    Project vertices into map coordinates. Returns (fx, fy, height) where
    fx/fy are continuous pixel coordinates (pixel i covers [i, i+1)) and
    height is the vertex coordinate along the approach direction axis.
    """
    rel = verts - frame["origin"]
    x_axis = frame["x_axis"]
    y_axis = frame["y_axis"]

    fx = rel @ x_axis / (x_axis @ x_axis)
    fy = rel @ y_axis / (y_axis @ y_axis)
    height = rel @ frame["direction"]
    return fx, fy, height


def sample_map(map2d, fx, fy):
    ix = np.clip(np.floor(fx).astype(int), 0, map2d.shape[1] - 1)
    iy = np.clip(np.floor(fy).astype(int), 0, map2d.shape[0] - 1)
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

    Lateral distances are measured from the vertex to the nearest EDGE of each
    pixel column (columns are pixel-sized squares, not points), so a vertex
    exactly on a machined face reads ~0 no matter which column it falls in.

    The window must cover the tool's lateral scale (tip_gap passes at least
    the tool radius): a vertex whose whole window is unmachined would
    otherwise fall back to the vertical distance to the surface above it -
    the full wall depth - instead of the small lateral distance to the
    machined boundary. Beyond the window, gaps are lower bounds
    (>= window_px * pixel), fine for thresholding and display saturation.

    Offsets are visited in rings of increasing minimal lateral distance and
    vertices leave the search once no farther column can improve them, so
    flat/machined regions (the vast majority) cost only the first few rings.
    """
    h_map, w_map = closed.shape
    fx = np.asarray(fx, dtype=np.float64)
    fy = np.asarray(fy, dtype=np.float64)
    height = np.asarray(height, dtype=np.float64)
    ix = np.clip(np.floor(fx).astype(int), 0, w_map - 1)
    iy = np.clip(np.floor(fy).astype(int), 0, h_map - 1)

    # offsets sorted by the smallest lateral distance any vertex in the
    # center cell can have to the offset cell
    offsets = sorted(
        (max(abs(dx) - 1, 0) ** 2 + max(abs(dy) - 1, 0) ** 2, dx, dy)
        for dy in range(-window_px, window_px + 1)
        for dx in range(-window_px, window_px + 1)
    )

    best = np.full(height.shape, np.inf)
    active = np.arange(len(best))
    afx, afy, ah, aix, aiy = fx, fy, height, ix, iy
    for lat_min2_px, dx, dy in offsets:
        lat_min2 = lat_min2_px * pixel * pixel
        resolved = best[active] <= lat_min2
        if resolved.any():
            keep = ~resolved
            active = active[keep]
            if len(active) == 0:
                break
            afx, afy, ah = afx[keep], afy[keep], ah[keep]
            aix, aiy = aix[keep], aiy[keep]

        qx = np.clip(aix + dx, 0, w_map - 1)
        qy = np.clip(aiy + dy, 0, h_map - 1)
        lx = np.maximum(np.abs(afx - (qx + 0.5)) - 0.5, 0.0) * pixel
        ly = np.maximum(np.abs(afy - (qy + 0.5)) - 0.5, 0.0) * pixel
        dz = closed[qy, qx] - ah
        np.maximum(dz, 0.0, out=dz)
        d2 = lx * lx + ly * ly + dz * dz

        cur = best[active]
        np.minimum(cur, d2, out=cur)
        best[active] = cur
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


@log_execution_time
def close_heightmap(heights, footprint, profile):
    """
    Grayscale closing of the height map with the tool bottom profile: returns
    the machined surface, i.e. the lowest surface the tool tip envelope can
    generate above the current one. closing >= heights everywhere; the
    difference is material the tool cannot remove.
    """
    structure = np.where(footprint, -profile, 0.0)
    lifted = ndimage.grey_dilation(
        heights.astype(np.float64), footprint=footprint, structure=structure,
        mode="constant", cval=FREE_SPACE,
    )
    closed = ndimage.grey_erosion(
        lifted, footprint=footprint, structure=structure,
        mode="constant", cval=FREE_SPACE,
    )
    return closed.astype(np.float32)


@log_execution_time
def clearance_heightmap(heights, radius, pixel):
    """
    Height of the tallest obstruction within `radius` of each pixel: a flat
    grayscale dilation with a disk. A cylinder of this radius whose bottom
    sits at height h above a vertex collides iff
    clearance(vertex) - height(vertex) > h.

    The disk is decomposed into one horizontal chord per row offset, each a
    1D running-max filter, so the dilation runs exactly at full resolution
    for any radius (no pooling, no conservative rounding) in
    O(radius/pixel) linear passes.
    """
    n = int(np.floor(radius / pixel + 1e-9))
    work = heights.astype(np.float64)
    out = np.full_like(work, FREE_SPACE)

    for dy in range(-n, n + 1):
        chord = np.sqrt(max(radius * radius - (dy * pixel) ** 2, 0.0)) / pixel
        size = 2 * int(np.floor(chord + 1e-9)) + 1
        row_max = ndimage.maximum_filter1d(work, size=size, axis=1, mode="constant", cval=FREE_SPACE)
        if dy >= 0:
            np.maximum(out[: out.shape[0] - dy], row_max[dy:], out=out[: out.shape[0] - dy])
        else:
            np.maximum(out[-dy:], row_max[:dy], out=out[-dy:])
    return out.astype(np.float32)


# ---------------------------------------------------------------------------
# per-direction cache
# ---------------------------------------------------------------------------

def _tip_key(diameter, corner_radius):
    return f"tip_{diameter:.6g}_{corner_radius:.6g}"


def _clear_key(radius):
    return f"clear_{radius:.6g}"


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

    VERSION = 3  # gap window covers the tool radius; exact chord-decomposed clearance

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
                self._fx, self._fy, self._vheight = project_vertices(verts, self.frame)

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
        if not hasattr(self, "_fx"):
            raise ValueError("Vertex projections need verts passed to the constructor")
        return self._fx, self._fy, self._vheight

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
                footprint, profile = tip_profile(diameter, corner_radius, pixel)
                closed = close_heightmap(self.heights, footprint, profile)
                fx, fy, vheight = self._vertex_samples()
                # the window must cover the tool's lateral scale, otherwise
                # wall vertices near unreachable pockets read the vertical
                # distance to the surface above instead of the small lateral
                # distance to the machined boundary
                window_px = max(
                    2,
                    int(np.ceil(self.window / pixel)),
                    int(np.ceil(diameter / 2.0 / pixel)) + 2,
                )
                gap = euclidean_gap(closed, fx, fy, vheight, pixel, window_px)
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

    def clearance(self, radius):
        """
        Per-vertex clearance: height of the tallest obstruction within
        `radius`, measured above the vertex. Computed once per radius.
        """
        key = _clear_key(radius)
        if key not in self._fields:
            logger.debug(f"Computing clearance field {key} for direction {self.direction_index} ({self.engine})")
            if self.engine == "zmap":
                dilated = clearance_heightmap(self.heights, radius, self.frame["pixel"])
                fx, fy, vheight = self._vertex_samples()
                clear = sample_map(dilated, fx, fy) - vheight
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
                fx, fy, vheight = project_vertices(self.verts, frame)
                clear = sample_map(grown_heights, fx, fy) - vheight
            self._fields[key] = clear.astype(np.float32)
            self._save()
        return self._fields[key]

    def min_stickout(self, cylinders):
        """
        Per-vertex minimal stickout (tool length out of the holder) so that a
        holder modelled as stacked concentric cylinders [(radius, start), ...]
        (start = distance from the tool tip to the cylinder's lower end for
        stickout 0) clears the part.
        """
        stickout = None
        for radius, start in cylinders:
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
        min_stick = cache.min_stickout(cylinders)
        if stickout is not None:
            blocked = blocked | (min_stick > stickout + tollerance)

    return faces_all_verts(faces, blocked), gap, min_stick
