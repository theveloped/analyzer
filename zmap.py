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
    Project vertices into map coordinates. Returns (ix, iy, height) where
    ix/iy are integer pixel indices (clipped to the map) and height is the
    vertex coordinate along the approach direction axis.
    """
    rel = verts - frame["origin"]
    x_axis = frame["x_axis"]
    y_axis = frame["y_axis"]

    fx = rel @ x_axis / (x_axis @ x_axis)
    fy = rel @ y_axis / (y_axis @ y_axis)
    height = rel @ frame["direction"]

    ix = np.floor(fx).astype(int)
    iy = np.floor(fy).astype(int)
    return ix, iy, height


def sample_map(map2d, ix, iy):
    ix = np.clip(ix, 0, map2d.shape[1] - 1)
    iy = np.clip(iy, 0, map2d.shape[0] - 1)
    return map2d[iy, ix]


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
def clearance_heightmap(heights, radius, pixel, max_footprint=32):
    """
    Height of the tallest obstruction within `radius` of each pixel: a flat
    grayscale dilation. A cylinder of this radius whose bottom sits at height
    h above a vertex collides iff clearance(vertex) - height(vertex) > h.

    Holder radii are typically much larger than the pixel size and a direct
    footprint would explode, so the map is max-pooled first until the
    footprint radius fits in `max_footprint` pixels. Pooling is conservative:
    obstructions are rounded up and outward by at most one pooled pixel.
    """
    pool = int(np.ceil(radius / (max_footprint * pixel)))
    work = heights.astype(np.float64)

    if pool > 1:
        pad_y = (-work.shape[0]) % pool
        pad_x = (-work.shape[1]) % pool
        work = np.pad(work, ((0, pad_y), (0, pad_x)), constant_values=FREE_SPACE)
        work = work.reshape(work.shape[0] // pool, pool, work.shape[1] // pool, pool).max(axis=(1, 3))

    eff_pixel = pixel * pool
    # grow the radius by the pooled cell half-diagonal to stay conservative
    eff_radius = radius + (0.71 * eff_pixel if pool > 1 else 0.0)
    footprint = disk_footprint(eff_radius, eff_pixel)
    dilated = ndimage.grey_dilation(work, footprint=footprint, mode="constant", cval=FREE_SPACE)

    if pool > 1:
        dilated = np.repeat(np.repeat(dilated, pool, axis=0), pool, axis=1)
        dilated = dilated[: heights.shape[0], : heights.shape[1]]
    return dilated.astype(np.float32)


# ---------------------------------------------------------------------------
# per-direction cache
# ---------------------------------------------------------------------------

def _tip_key(diameter, corner_radius):
    return f"tip_{diameter:.6g}_{corner_radius:.6g}"


def _clear_key(radius):
    return f"clear_{radius:.6g}"


class DirectionCache:
    """
    Cached per-vertex fields for one approach direction: the rendered height
    map plus any number of tip gap fields and clearance fields. Persisted as
    an .npz so repeated tool queries never touch geometry again.
    """

    def __init__(self, workdir, direction_index, verts=None, faces=None, pixel=0.1):
        self.path = os.path.join(workdir, "zcache", f"dir_{direction_index:04d}.npz")
        self.direction_index = direction_index
        self.verts = verts
        self.pixel = pixel
        self._fields = {}

        directions = np.load(os.path.join(workdir, "directions.npy"))
        self.direction = directions[direction_index]

        if os.path.exists(self.path):
            stored = np.load(self.path, allow_pickle=False)
            if abs(stored["pixel"][0] - pixel) < 1e-12:
                self._fields = {k: stored[k] for k in stored.files}
                logger.debug(f"Loaded zmap cache {self.path} with {len(self._fields)} arrays")
            else:
                logger.warning(f"Pixel size changed, discarding cache {self.path}")

        if "heights" not in self._fields:
            if verts is None or faces is None:
                raise ValueError("No cache present: verts and faces are required to render")
            from meshlib import mrmeshnumpy as mn
            mesh = mn.meshFromFacesVerts(faces, verts)
            heights, frame = render_heightmap(mesh, self.direction, pixel)
            self._fields = {
                "heights": heights,
                "pixel": np.array([pixel]),
                "origin": frame["origin"],
                "x_axis": frame["x_axis"],
                "y_axis": frame["y_axis"],
                "direction": frame["direction"],
            }
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
            self._ix, self._iy, self._vheight = project_vertices(verts, self.frame)

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        np.savez_compressed(self.path, **self._fields)

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
            footprint, profile = tip_profile(diameter, corner_radius, self.frame["pixel"])
            closed = close_heightmap(self.heights, footprint, profile)
            # one pixel of lateral tolerance: a vertex lying exactly on a
            # vertical wall may project into the neighbouring material
            # column; the tool side sweeping the adjacent column down to the
            # vertex height still reaches the vertex
            closed = ndimage.grey_erosion(closed, size=(3, 3), mode="nearest")
            ix, iy, vheight = self._vertex_samples()
            gap = sample_map(closed, ix, iy) - vheight
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
            logger.debug(f"Computing clearance field {key} for direction {self.direction_index}")
            dilated = clearance_heightmap(self.heights, radius, self.frame["pixel"])
            ix, iy, vheight = self._vertex_samples()
            clear = sample_map(dilated, ix, iy) - vheight
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
