"""
X-dependent collision envelopes (phase 3 of the design).

For one bend action and one punch/die selection this module produces, per
machine-X interval, whether tool or machine material there would collide
with the workpiece at any bend parameter, and where tool material is
REQUIRED (the bend line needs pressing) or merely OPTIONAL.

Key structural facts exploited:

* Every panel vertex keeps its machine-X coordinate for the whole stroke
  (the rotation axis IS the X axis), so the critical-X decomposition is
  computed once per action, not per angle.
* At a fixed X, a wing's cross-section rotates RIGIDLY in the YZ plane
  about the origin by +/- phi/2.  Each slice is therefore computed once at
  phi=0 and swept as a pure 2D rotation.  The sweep of one slice segment is
  covered by an annular sector spanning the segment's radius range and the
  swept polar-angle range, buffered by the material half-thickness (Minkowski
  sums commute with rotation).  For radial slice segments - the wing
  hugging the punch flanks, exactly where tightness matters - the sector is
  the EXACT swept region; for oblique segments it is a conservative
  superset.  The ``swept_region`` API is fixed so the analytic arc-contact
  version (roadmap P4) can swap in without callers changing.

Machine X coordinates are relative to the active hinge start (the placement
maps ``axis_point`` to x=0); ``BendAction.x_offset`` translates the whole
envelope along the machine, which is how position invariance is realised.
"""

import math
from dataclasses import dataclass, field

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from pressbrake import collision, kinematics
from pressbrake.intervals import IntervalSet

# minimum overlap area (mm^2) that counts as penetration: grazes below the
# sector-arc discretization error are noise, real interferences grow fast
AREA_TOLERANCE = 0.02
EDGE_EPSILON = 1e-4
REQUIRED_PROBE_OFFSET = 0.25     # mm on each side of the bend line

# analytic path: exact sector-vs-polygon predicate against pre-buffered
# obstacles instead of per-interval shapely geometry.  PEN_EPS erodes the
# obstacle buffers so exact tangency (the wings hug the punch flanks by
# design) never reads as a hit — it absorbs float noise ONLY; it is not a
# stand-in for AREA_TOLERANCE (a long shallow graze has large area but tiny
# depth, so a bigger erosion would let real oracle hits through).
ANALYTIC_SECTORS = True
PEN_EPS = 1e-4


@dataclass
class ToolEnvelope:
    """
    Per-obstacle view used for reporting and strip charts.
    """
    tool: str
    required: IntervalSet
    optional: IntervalSet
    forbidden: IntervalSet

    @property
    def feasible(self):
        return self.required.intersect(self.forbidden).is_empty()


@dataclass
class CollisionEnvelope:
    action: object
    punch_id: str
    die_id: str
    required: IntervalSet          # true material span of the bend line
    required_core: IntervalSet     # required shrunk by the end relief: what
                                   # MUST be pressed (a few unsupported mm at
                                   # bend ends is standard practice, e.g. box
                                   # corners beside formed side walls)
    forbidden_punch: IntervalSet
    forbidden_die: IntervalSet
    forbidden_machine: IntervalSet
    margin: float
    x_range: tuple

    @property
    def feasible(self):
        """
        The machine frame is not segmentable, so any machine interference is
        fatal; punch and die material can be omitted over forbidden
        intervals as long as the required core spans stay clear.
        """
        if not self.forbidden_machine.is_empty():
            return False
        if not self.required_core.intersect(self.forbidden_punch).is_empty():
            return False
        if not self.required_core.intersect(self.forbidden_die).is_empty():
            return False
        return True

    def optional_for(self, forbidden):
        covered = self.required.union(forbidden)
        return covered.complement(*self.x_range)

    def tool_views(self):
        views = [
            ("punch " + self.punch_id,
             ToolEnvelope("punch", self.required,
                          self.optional_for(self.forbidden_punch),
                          self.forbidden_punch)),
            ("die " + self.die_id,
             ToolEnvelope("die", self.required,
                          self.optional_for(self.forbidden_die),
                          self.forbidden_die)),
        ]
        if not self.forbidden_machine.is_empty():
            views.append((
                "machine",
                ToolEnvelope("machine", self.required,
                             self.optional_for(self.forbidden_machine),
                             self.forbidden_machine)))
        return views


DEFAULT_END_RELIEF = 5.0     # mm of bend line that may go unpressed per end


@dataclass
class SweepProfile:
    """
    Tool-independent half of an envelope: the swept workpiece geometry per
    critical-X interval plus the required bend-line spans.  A pure function
    of (graph, state_theta, action) — punch/die/machine/margin never enter —
    so one profile serves every tool pair evaluated for the same action.

    ``analytic`` selects the interval payload: scalar annular sectors plus
    rare perpendicular-panel polygons (tested with the exact predicate
    against pre-buffered obstacles), or the legacy shapely (swept,
    workpiece) pair.
    """
    action: object
    max_phi: float
    intervals: list        # analytic: (x0, x1, sectors, perp_pieces)
                           # shapely:  (x0, x1, swept, workpiece)
    required: IntervalSet
    x_range: tuple
    analytic: bool
    exclusion_radius: float
    # analytic only: all interval sectors stacked for one-shot predicate
    # evaluation, with the owning interval index per row; hit_cache
    # memoizes per-interval hit flags per obstacle (the same punch is
    # tested against the same sweep for every die it is paired with)
    sectors_all: object = None
    sector_owner: object = None
    hit_cache: dict = field(default_factory=dict)


def compute_sweep(graph, state_theta, action, analytic=None):
    """
    Swept workpiece geometry of one bend action, independent of tooling.
    """
    if analytic is None:
        analytic = ANALYTIC_SECTORS
    max_phi = max(abs(graph.bends[b].angle_overbend) for b in action.bend_ids)

    poses = kinematics.machine_transforms(graph, state_theta, action, [0.0])[0]

    # wing sign per panel (Y side at phi=0; X-invariant during the stroke)
    signs = {}
    panel_points = {}
    for panel in graph.panels:
        points = kinematics.transform_points(
            poses[panel.id], kinematics.panel_points_3d(panel, graph.z_offset))
        holes = [
            kinematics.transform_points(
                poses[panel.id],
                np.column_stack([h, np.full(len(h), graph.z_offset)]))
            for h in panel.holes
        ]
        panel_points[panel.id] = (points, holes)
        centroid = points.mean(axis=0)
        signs[panel.id] = 1.0 if centroid[1] >= 0 else -1.0

    events = _critical_x(graph, action, panel_points, poses=poses,
                         z_offset=graph.z_offset)
    normals = {panel.id: poses[panel.id][:3, :3] @ np.array([0.0, 0.0, 1.0])
               for panel in graph.panels}

    # keep in sync with collision.pivot_exclusion
    exclusion_radius = max(
        graph.bends[b].inner_radius for b in action.bend_ids
    ) + graph.thickness + 0.5

    if analytic:
        intervals = _analytic_intervals(
            graph, panel_points, signs, normals, poses, events, max_phi)
    else:
        intervals = _shapely_intervals(
            graph, panel_points, signs, normals, poses, events, max_phi,
            collision.pivot_exclusion(graph, action))

    required = _required_intervals(graph, action, poses)
    x_low = min(events[0], required.arr[0, 0] if len(required) else events[0])
    x_high = max(events[-1], required.arr[-1, 1] if len(required) else events[-1])

    sectors_all = None
    sector_owner = None
    if analytic and intervals:
        stacks = [(index, sectors)
                  for index, (_x0, _x1, sectors, _perp) in enumerate(intervals)
                  if len(sectors)]
        if stacks:
            sectors_all = np.vstack([sectors for _index, sectors in stacks])
            sector_owner = np.concatenate([
                np.full(len(sectors), index, dtype=np.intp)
                for index, sectors in stacks])

    return SweepProfile(action=action, max_phi=max_phi, intervals=intervals,
                        required=required, x_range=(x_low, x_high),
                        analytic=analytic, exclusion_radius=exclusion_radius,
                        sectors_all=sectors_all, sector_owner=sector_owner)


def _shapely_intervals(graph, panel_points, signs, normals, poses, events,
                       max_phi, exclusion):
    """
    Legacy per-interval shapely geometry: (x0, x1, swept, workpiece) with
    empty workpieces skipped.  Kept as the A/B reference for the analytic
    path (ANALYTIC_SECTORS = False).
    """
    intervals = []
    for x0, x1 in zip(events[:-1], events[1:]):
        if x1 - x0 < 1e-9:
            continue
        probes = _probe_positions(x0, x1)
        # raw sector pieces union+buffer ONCE per interval (dilation
        # distributes over union), instead of per (panel, probe) — the
        # dominant cost on hole-rich panels with many critical intervals
        sector_pieces = []
        inflated_pieces = []
        for panel in graph.panels:
            points, holes = panel_points[panel.id]
            if points[:, 0].min() > x1 or points[:, 0].max() < x0:
                continue
            wing = signs[panel.id] / 2.0
            angle_low = min(0.0, wing * max_phi)
            angle_high = max(0.0, wing * max_phi)
            for x in probes:
                segments = collision.slice_panel_segments(
                    points, holes, poses[panel.id], graph.thickness, x)
                if segments:
                    sector_pieces.extend(swept_region(
                        segments, graph.thickness / 2.0, angle_low,
                        angle_high, raw=True))
                # panels perpendicular to X: cover the polygon interior at
                # the end angles too (the sector sweep covers the boundary)
                if abs(normals[panel.id][0]) > collision.PERPENDICULAR_LIMIT:
                    full = collision.slice_panel(
                        points, holes, poses[panel.id], graph.thickness, x)
                    if full is not None and not full.is_empty:
                        for angle in (angle_low, angle_high):
                            inflated_pieces.append(affinity.rotate(
                                full, math.degrees(angle), origin=(0.0, 0.0)))
        if not sector_pieces and not inflated_pieces:
            continue
        swept_parts = list(inflated_pieces)
        if sector_pieces:
            swept_parts.append(unary_union(sector_pieces).buffer(
                graph.thickness / 2.0, quad_segs=8))
        swept = (unary_union(swept_parts) if len(swept_parts) > 1
                 else swept_parts[0])
        workpiece = swept.difference(exclusion)
        if workpiece.is_empty:
            continue
        intervals.append((x0, x1, swept, workpiece))
    return intervals


def _analytic_intervals(graph, panel_points, signs, normals, poses, events,
                        max_phi):
    """
    Per-interval scalar sector arrays: the sweep of every mid-plane slice
    piece as ideal (r_min, r_max, theta_low, theta_high) rows, uninflated —
    the t/2 material buffer moves to the obstacle side (Minkowski swap).
    Perpendicular-panel interiors stay shapely polygons (they are tested
    unbuffered, exactly as in the legacy path).
    """
    pairs = [(x0, x1) for x0, x1 in zip(events[:-1], events[1:])
             if x1 - x0 >= 1e-9]
    probes = [_probe_positions(x0, x1) for x0, x1 in pairs]
    sector_lists = [[] for _ in pairs]
    perp_lists = [[] for _ in pairs]

    for panel in graph.panels:
        points, holes = panel_points[panel.id]
        x_min = points[:, 0].min()
        x_max = points[:, 0].max()
        active = [index for index, (x0, x1) in enumerate(pairs)
                  if x_min <= x1 and x_max >= x0]
        if not active:
            continue
        wing = signs[panel.id] / 2.0
        angle_low = min(0.0, wing * max_phi)
        angle_high = max(0.0, wing * max_phi)

        if abs(normals[panel.id][0]) > collision.PERPENDICULAR_LIMIT:
            for index in active:
                for x in probes[index]:
                    segments = collision.slice_panel_segments(
                        points, holes, poses[panel.id], graph.thickness, x)
                    if segments:
                        sector_lists[index].extend(_sector_params(
                            segments, angle_low, angle_high))
                    full = collision.slice_panel(
                        points, holes, poses[panel.id], graph.thickness, x)
                    if full is not None and not full.is_empty:
                        for angle in (angle_low, angle_high):
                            perp_lists[index].append(affinity.rotate(
                                full, math.degrees(angle), origin=(0.0, 0.0)))
            continue

        starts, ends = _loop_edges([points] + list(holes))
        xs = [x for index in active for x in probes[index]]
        owners = [index for index in active for _x in probes[index]]
        for segments, index in zip(
                _batched_plane_cut(starts, ends, xs), owners):
            if segments:
                sector_lists[index].extend(_sector_params(
                    segments, angle_low, angle_high))

    intervals = []
    for (x0, x1), sectors, perp in zip(pairs, sector_lists, perp_lists):
        if not sectors and not perp:
            continue
        intervals.append(
            (x0, x1, np.asarray(sectors, dtype=float).reshape(-1, 4), perp))
    return intervals


def _loop_edges(loops):
    """
    All edges of a list of closed 3D loops as (starts, ends) arrays.
    """
    starts = []
    ends = []
    for loop in loops:
        arr = np.asarray(loop, dtype=float)
        starts.append(arr)
        ends.append(np.roll(arr, -1, axis=0))
    return np.vstack(starts), np.vstack(ends)


def _batched_plane_cut(starts, ends, xs, block=512):
    """
    collision._plane_cut_segments for many X planes in one numpy pass: the
    edge-crossing search is broadcast over the plane positions, only the
    per-plane even-odd pairing stays a (tiny) Python loop.  Returns one
    segment list per entry of ``xs``, matching the per-plane results.
    """
    results = []
    xs = np.asarray(xs, dtype=float)
    sx = starts[:, 0]
    ex = ends[:, 0]
    for base in range(0, len(xs), block):
        chunk = xs[base:base + block]
        da = sx[:, None] - chunk[None, :]
        db = ex[:, None] - chunk[None, :]
        mask = ((da > 0) != (db > 0)) & (da != db)
        edge_idx, plane_idx = np.nonzero(mask)
        t = da[edge_idx, plane_idx] / \
            (da[edge_idx, plane_idx] - db[edge_idx, plane_idx])
        points = starts[edge_idx, 1:] + \
            t[:, None] * (ends[edge_idx, 1:] - starts[edge_idx, 1:])
        order = np.argsort(plane_idx, kind="stable")
        plane_sorted = plane_idx[order]
        points = points[order]
        bounds = np.searchsorted(plane_sorted, np.arange(len(chunk) + 1))
        for local in range(len(chunk)):
            results.append(_pair_crossings(
                points[bounds[local]:bounds[local + 1]]))
    return results


def _pair_crossings(points):
    """
    Even-odd pairing of plane-crossing points — identical to the tail of
    collision._plane_cut_segments (sort along the dominant YZ direction,
    pair consecutively, drop degenerate spans).
    """
    if len(points) < 2:
        return []
    direction = points.max(axis=0) - points.min(axis=0)
    norm = np.linalg.norm(direction)
    if norm < 1e-12:
        return []
    direction = direction / norm
    points = points[np.argsort(points @ direction)]
    segments = []
    for index in range(0, len(points) - 1, 2):
        start, end = points[index], points[index + 1]
        if np.linalg.norm(end - start) > 1e-9:
            segments.append((tuple(start), tuple(end)))
    return segments


def compute_envelope(graph, state_theta, action, punch, die, machine=None,
                     margin=2.0, end_relief=DEFAULT_END_RELIEF, sweep=None,
                     obstacle_cache=None):
    """
    Full X-interval envelope of one bend action for one punch/die selection.

    ``sweep`` short-circuits the expensive tool-independent geometry: pass a
    ``compute_sweep`` result for the SAME (graph, state_theta, action) to
    reuse it across punch/die pairs (results are identical either way).
    ``obstacle_cache`` (a plain dict owned by the caller) memoizes obstacle
    polygons across calls sharing tools and max_phi.
    """
    if sweep is None:
        sweep = compute_sweep(graph, state_theta, action)
    max_phi = sweep.max_phi

    hits = {"punch": [], "die": [], "ram": [], "table": []}
    if sweep.analytic:
        tool_sets, machine_sets = _analytic_obstacles(
            punch, die, machine, graph.thickness, max_phi, margin,
            sweep.exclusion_radius, obstacle_cache)
        # tools: sectors vs (obstacle − exclusion) ⊕ t/2, perpendicular
        # interiors vs the unbuffered carved obstacle — the Minkowski
        # image of the legacy (swept − exclusion) vs raw-obstacle test
        for name, obstacle_key, arrays, polygon in tool_sets + machine_sets:
            flags = sweep.hit_cache.get(obstacle_key)
            if flags is None:
                flags = np.zeros(len(sweep.intervals), dtype=bool)
                if sweep.sectors_all is not None:
                    rows = _sector_hits(sweep.sectors_all, arrays)
                    flags[sweep.sector_owner[rows]] = True
                for index, (_x0, _x1, _sec, perp) in \
                        enumerate(sweep.intervals):
                    if not flags[index] and perp and \
                            _pieces_hit(perp, polygon):
                        flags[index] = True
                sweep.hit_cache[obstacle_key] = flags
            hits[name] = [(x0, x1) for flag, (x0, x1, _sec, _perp)
                          in zip(flags, sweep.intervals) if flag]
    else:
        tool_obstacles, machine_obstacles = _obstacles_for(
            punch, die, machine, graph.thickness, max_phi, margin,
            obstacle_cache)
        for x0, x1, swept, workpiece in sweep.intervals:
            for name, prepared, polygon in tool_obstacles:
                if prepared.intersects(workpiece) and \
                        workpiece.intersection(polygon).area > AREA_TOLERANCE:
                    hits[name].append((x0, x1))
            for name, prepared, polygon in machine_obstacles:
                if prepared.intersects(swept) and \
                        swept.intersection(polygon).area > AREA_TOLERANCE:
                    hits[name].append((x0, x1))

    # note: the placement transform already carries action.x_offset, so all
    # coordinates here are final machine X - no further translation
    envelope = CollisionEnvelope(
        action=sweep.action,
        punch_id=punch.id if punch else "",
        die_id=die.id if die else "",
        required=sweep.required,
        required_core=shrink_intervals(sweep.required, end_relief),
        forbidden_punch=IntervalSet(hits["punch"]).buffer(margin),
        forbidden_die=IntervalSet(hits["die"]).buffer(margin),
        forbidden_machine=IntervalSet(hits["ram"] + hits["table"]),
        margin=margin,
        x_range=sweep.x_range,
    )
    return envelope


def _obstacles_for(punch, die, machine, thickness, max_phi, margin, cache):
    """
    (tool_obstacles, machine_obstacles) for one pairing, memoized in the
    caller-owned ``cache`` dict: obstacle polygons depend only on the tool
    profiles, thickness and max_phi (plus margin for ram/table), and are
    read-only after construction, so sharing them across envelopes is safe.
    """
    key = (punch.id if punch else None, die.id if die else None,
           machine.name if machine else None, thickness,
           round(max_phi, 9), margin)
    if cache is not None and key in cache:
        return cache[key]

    # obstacles: punch/die penetration-only, ram/table margin-buffered
    tool_obstacles = collision.build_obstacles(
        punch, die, None, thickness, max_phi=max_phi)
    machine_obstacles = []
    if machine is not None:
        machine_obstacles = [
            entry for entry in collision.build_obstacles(
                punch, die, machine, thickness, max_phi=max_phi,
                margin=margin)
            if entry[0] in ("ram", "table")
        ]
    result = (tool_obstacles, machine_obstacles)
    if cache is not None:
        cache[key] = result
    return result


def _analytic_obstacles(punch, die, machine, thickness, max_phi, margin,
                        exclusion_radius, cache):
    """
    Obstacle sets for the analytic predicate, memoized like _obstacles_for.

    Tool obstacles carry the pivot exclusion and the material half-thickness
    on THEIR side (exact Minkowski identities for the legacy tests):
        ((S ⊕ t/2) − E) ∩ O  ⇔  S ∩ ((O − E) ⊕ t/2)
    so punch/die become (O − E).buffer(t/2 − PEN_EPS) and ram/table (no
    exclusion, margin-buffered in the legacy path) become
    O.buffer(t/2 + margin − PEN_EPS).  Each entry also keeps the shapely
    polygon the (unbuffered) perpendicular-panel interiors test against:
    the carved obstacle for tools, the margin-buffered one for the machine.
    """
    key = ("analytic", punch.id if punch else None, die.id if die else None,
           machine.name if machine else None, thickness,
           round(max_phi, 9), margin, round(exclusion_radius, 9))
    if cache is not None and key in cache:
        return cache[key]

    exclusion = Point(0.0, 0.0).buffer(exclusion_radius, quad_segs=16)
    raw = {name: polygon for name, _prepared, polygon
           in collision.build_obstacles(punch, die, machine, thickness,
                                        max_phi=max_phi, margin=0.0)}
    # per-obstacle hit-cache keys carry only what that obstacle's geometry
    # depends on, so e.g. the punch flags are shared across the dies it is
    # paired with (the die profile does not depend on max_phi)
    base = (thickness, round(exclusion_radius, 9))
    obstacle_keys = {
        "punch": ("punch", punch.id if punch else None,
                  round(max_phi, 9)) + base,
        "die": ("die", die.id if die else None) + base,
        "ram": ("ram", machine.name if machine else None,
                punch.id if punch else None, round(max_phi, 9), margin,
                thickness),
        "table": ("table", machine.name if machine else None,
                  die.id if die else None, margin, thickness),
    }
    tool_sets = []
    for name in ("punch", "die"):
        if name not in raw:
            continue
        carved = raw[name].difference(exclusion)
        arrays = _obstacle_arrays(
            carved.buffer(thickness / 2.0 - PEN_EPS, quad_segs=8))
        tool_sets.append((name, obstacle_keys[name], arrays, carved))
    machine_sets = []
    for name in ("ram", "table"):
        if name not in raw:
            continue
        arrays = _obstacle_arrays(
            raw[name].buffer(thickness / 2.0 + margin - PEN_EPS, quad_segs=8))
        machine_sets.append(
            (name, obstacle_keys[name], arrays, raw[name].buffer(margin)))

    result = (tool_sets, machine_sets)
    if cache is not None:
        cache[key] = result
    return result


def _obstacle_arrays(geometry):
    """
    Numpy form of a (multi)polygon for the sector predicate: ring vertices
    with polar coordinates, ring edges, and a radial range for the cheap
    prefilter.  Holes are covered by the even-odd containment test.
    """
    verts = []
    for polygon in getattr(geometry, "geoms", [geometry]):
        if polygon.is_empty:
            continue
        for ring in [polygon.exterior] + list(polygon.interiors):
            coords = np.asarray(ring.coords)[:, :2]
            verts.append(coords[:-1])
    if not verts:
        return None
    e0 = np.vstack(verts)
    e1 = np.vstack([np.roll(v, -1, axis=0) for v in verts])
    radii = np.hypot(e0[:, 0], e0[:, 1])
    return {
        "e0": e0,
        "e1": e1,
        "vr": radii,
        "vt": np.arctan2(e0[:, 1], e0[:, 0]),
        "r_lo": float(geometry.distance(Point(0.0, 0.0))),
        "r_hi": float(radii.max()),
    }


def _sectors_intersect(sectors, obstacle):
    """
    True when any annular sector (row of r_min, r_max, theta_low,
    theta_high about the origin) intersects the polygon.
    """
    return bool(_sector_hits(np.asarray(sectors, dtype=float),
                             obstacle).any())


def _sector_hits(sectors, obstacle, block=2048):
    """
    Exact per-row boolean intersection between annular sectors about the
    origin and a polygon in numpy form: any polygon vertex inside a
    sector, any sector corner inside the polygon, or any boundary crossing
    (polygon edge vs sector arc, polygon edge vs radial sector edge).
    Handles r_min = 0 pie sectors and spans of a full turn or more.
    Vectorized over all sectors of a sweep at once; each test stage only
    runs on the rows still unresolved.
    """
    count = len(sectors)
    hits = np.zeros(count, dtype=bool)
    if obstacle is None or count == 0:
        return hits
    for base in range(0, count, block):
        chunk = sectors[base:base + block]
        rows = np.nonzero((chunk[:, 1] > obstacle["r_lo"]) &
                          (chunk[:, 0] < obstacle["r_hi"]))[0]
        if len(rows):
            flags = _sector_hits_dense(chunk[rows], obstacle)
            hits[base + rows[flags]] = True
    return hits


def _sector_hits_dense(sectors, obstacle):
    two_pi = 2.0 * math.pi
    r0 = sectors[:, 0]
    r1 = sectors[:, 1]
    t0 = sectors[:, 2]
    span = sectors[:, 3] - sectors[:, 2]
    full = span >= two_pi - 1e-9
    hit = np.zeros(len(sectors), dtype=bool)

    # 1. polygon vertex strictly inside a sector
    vr = obstacle["vr"]
    vt = obstacle["vt"]
    radial = (vr[None, :] > r0[:, None]) & (vr[None, :] < r1[:, None])
    angular = np.mod(vt[None, :] - t0[:, None], two_pi) < span[:, None]
    hit |= np.any(radial & (angular | full[:, None]), axis=1)

    # 2. sector corner strictly inside the polygon
    live = np.nonzero(~hit)[0]
    if len(live):
        theta1 = t0[live] + span[live]
        cos0 = np.cos(t0[live])
        sin0 = np.sin(t0[live])
        cos1 = np.cos(theta1)
        sin1 = np.sin(theta1)
        corners = np.concatenate([
            np.column_stack([r0[live] * cos0, r0[live] * sin0]),
            np.column_stack([r0[live] * cos1, r0[live] * sin1]),
            np.column_stack([r1[live] * cos0, r1[live] * sin0]),
            np.column_stack([r1[live] * cos1, r1[live] * sin1]),
        ])
        inside = _points_in_polygon(corners, obstacle).reshape(4, len(live))
        hit[live[np.any(inside, axis=0)]] = True

    # 3. polygon edge crossing a sector arc: |e0 + t*d| = r quadratic
    live = np.nonzero(~hit)[0]
    if len(live):
        e0 = obstacle["e0"]
        d = obstacle["e1"] - e0
        a = np.einsum("ij,ij->i", d, d)
        b = 2.0 * np.einsum("ij,ij->i", e0, d)
        c0 = np.einsum("ij,ij->i", e0, e0)
        safe_a = np.where(a > 1e-18, a, 1.0)
        for which in (0, 1):
            rows = np.nonzero(sectors[live, which] > 1e-9)[0]
            if not len(rows):
                continue
            sel = live[rows]
            c = c0[None, :] - sectors[sel, which][:, None] ** 2
            disc = b[None, :] ** 2 - 4.0 * a[None, :] * c
            valid = (disc > 0.0) & (a[None, :] > 1e-18)
            if not np.any(valid):
                continue
            sq = np.sqrt(np.where(valid, disc, 0.0))
            crossed = np.zeros(len(sel), dtype=bool)
            for sign in (-1.0, 1.0):
                t = (-b[None, :] + sign * sq) / (2.0 * safe_a[None, :])
                on_edge = valid & (t > 0.0) & (t < 1.0)
                if not np.any(on_edge):
                    continue
                angles = np.arctan2(e0[None, :, 1] + t * d[None, :, 1],
                                    e0[None, :, 0] + t * d[None, :, 0])
                in_span = np.mod(angles - t0[sel][:, None], two_pi) < \
                    span[sel][:, None]
                crossed |= np.any(on_edge & (in_span | full[sel][:, None]),
                                  axis=1)
            hit[sel[crossed]] = True

    # 4. polygon edge crossing a radial sector edge
    live = np.nonzero(~hit & ~full)[0]
    if len(live):
        cos0 = np.cos(t0[live])
        sin0 = np.sin(t0[live])
        theta1 = t0[live] + span[live]
        cos1 = np.cos(theta1)
        sin1 = np.sin(theta1)
        for cos_t, sin_t in ((cos0, sin0), (cos1, sin1)):
            inner = np.column_stack([r0[live] * cos_t, r0[live] * sin_t])
            outer = np.column_stack([r1[live] * cos_t, r1[live] * sin_t])
            crossed = _segments_cross_mask(inner, outer, obstacle)
            hit[live[crossed]] = True
    return hit


def _points_in_polygon(points, obstacle):
    """
    Even-odd containment of points in the obstacle's rings (vectorized
    crossing number; correct for multipolygons and holes).
    """
    x0 = obstacle["e0"][:, 0]
    y0 = obstacle["e0"][:, 1]
    x1 = obstacle["e1"][:, 0]
    y1 = obstacle["e1"][:, 1]
    px = points[:, 0][:, None]
    py = points[:, 1][:, None]
    crosses = (y0[None, :] > py) != (y1[None, :] > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_at = x0[None, :] + (py - y0[None, :]) * \
            (x1 - x0)[None, :] / (y1 - y0)[None, :]
    inside = crosses & (px < x_at)
    return np.count_nonzero(inside, axis=1) % 2 == 1


def _segments_cross_mask(p1, p2, obstacle):
    """
    Per-row proper-crossing test of one segment per row of (p1, p2)
    against every obstacle edge (touching endpoints and collinear overlap
    excluded — measure-zero contacts the PEN_EPS erosion already
    discards).
    """
    q1 = obstacle["e0"]
    d1 = p2 - p1
    d2 = obstacle["e1"] - q1
    diff = q1[None, :, :] - p1[:, None, :]
    denom = d1[:, None, 0] * d2[None, :, 1] - d1[:, None, 1] * d2[None, :, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (diff[..., 0] * d2[None, :, 1] -
             diff[..., 1] * d2[None, :, 0]) / denom
        u = (diff[..., 0] * d1[:, None, 1] -
             diff[..., 1] * d1[:, None, 0]) / denom
    valid = np.abs(denom) > 1e-18
    return np.any(valid & (s > 0.0) & (s < 1.0) & (u > 0.0) & (u < 1.0),
                  axis=1)


def _pieces_hit(pieces, obstacle_polygon):
    """
    Legacy area test for the (rare, unbuffered) perpendicular-panel
    interior polygons.
    """
    for piece in pieces:
        if piece.intersection(obstacle_polygon).area > AREA_TOLERANCE:
            return True
    return False


def shrink_intervals(intervals, relief):
    """
    Pull every interval's ends in by ``relief``, capped at a quarter of the
    span so short spans never vanish entirely.
    """
    pairs = []
    for start, end in intervals.to_pairs():
        pull = min(relief, (end - start) / 4.0)
        pairs.append((start + pull, end - pull))
    return IntervalSet(pairs)


ARC_STEP = math.radians(0.5)
MIN_EVENT_SPACING = 1.0   # mm; coalesce denser critical-X clusters


def swept_region(segments, width, angle_low, angle_high, raw=False):
    """
    Region covered by material segments (inflated by ``width``) rotating
    about the origin from ``angle_low`` to ``angle_high`` (rad).

    Each segment is covered by an annular sector spanning its radius range
    [distance(origin, segment), max endpoint radius] and its swept
    polar-angle range, then buffered by ``width`` (buffering commutes with
    rotation, so inflating after sweeping is exact).  Exact for radial
    segments, conservative superset for oblique ones.

    ``raw=True`` returns the UNBUFFERED sector polygons instead — callers
    aggregating many calls union everything once and buffer the union
    (dilation distributes over union, so the result is identical).

    This is the API the analytic arc-contact implementation (P4) replaces.
    """
    pieces = [_annular_sector(*params)
              for params in _sector_params(segments, angle_low, angle_high)]
    if raw:
        return pieces
    if not pieces:
        return Polygon()
    return unary_union(pieces).buffer(width, quad_segs=8)


def _sector_params(segments, angle_low, angle_high):
    """
    (r_min, r_max, theta_low, theta_high) of the ideal (undiscretized)
    annular sector covering each segment's sweep — the shared core of the
    shapely and analytic paths.  Scalar math on purpose: this runs per
    slice segment in the hot loop.  After the foot split each piece is
    polar-monotone, so its radius range is exactly the endpoint range.
    """
    params = []
    for (y0, z0), (y1, z1) in segments:
        dy = y1 - y0
        dz = z1 - z0
        length_sq = dy * dy + dz * dz
        pieces = None
        if length_sq >= 1e-18:
            t = -(y0 * dy + z0 * dz) / length_sq
            if 1e-9 < t < 1.0 - 1e-9:
                fy = y0 + t * dy
                fz = z0 + t * dz
                pieces = ((y0, z0, fy, fz), (fy, fz, y1, z1))
        if pieces is None:
            pieces = ((y0, z0, y1, z1),)
        for ay, az, by, bz in pieces:
            radius_a = math.hypot(ay, az)
            radius_b = math.hypot(by, bz)
            r_max = max(radius_a, radius_b)
            if r_max < 1e-9:
                continue
            r_min = min(radius_a, radius_b)
            # a piece with one end at the pivot is purely radial: its polar
            # angle is defined by the other end
            if radius_a < 1e-9:
                theta_a = theta_b = math.atan2(bz, by)
            elif radius_b < 1e-9:
                theta_a = theta_b = math.atan2(az, ay)
            else:
                theta_a = math.atan2(az, ay)
                theta_b = theta_a + _wrap_angle(math.atan2(bz, by) - theta_a)
            theta_low = min(theta_a, theta_b) + angle_low
            theta_high = max(theta_a, theta_b) + angle_high
            params.append((r_min, r_max, theta_low, theta_high))
    return params


def _wrap_angle(angle):
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def _annular_sector(r_min, r_max, theta_low, theta_high):
    """
    Polygon of the annular sector r in [r_min, r_max], theta in
    [theta_low, theta_high], arcs discretized outward-conservatively.
    """
    span = max(theta_high - theta_low, 1e-9)
    # cap the discretization: the outward chord correction keeps the
    # polygon a conservative superset at any step count, and 64 segments
    # overshoot by <0.05% while shrinking union costs ~5x on wide sweeps
    steps = min(max(int(math.ceil(span / ARC_STEP)), 1), 64)
    thetas = np.linspace(theta_low, theta_high, steps + 1)
    # chord correction: push the outer arc points outward so the polygon
    # contains the true arc
    chord_factor = 1.0 / math.cos(span / steps / 2.0)
    outer = r_max * chord_factor
    points = [(outer * math.cos(t), outer * math.sin(t)) for t in thetas]
    if r_min <= 1e-9:
        points.append((0.0, 0.0))
    else:
        points.extend(
            (r_min * math.cos(t), r_min * math.sin(t)) for t in thetas[::-1])
    return Polygon(points)


def _probe_positions(x0, x1):
    """
    Sample X positions inside one critical interval: near both edges and at
    the midpoint (slice geometry varies linearly in between).
    """
    width = x1 - x0
    if width <= 4 * EDGE_EPSILON:
        return [(x0 + x1) / 2.0]
    return [x0 + EDGE_EPSILON, (x0 + x1) / 2.0, x1 - EDGE_EPSILON]


def _critical_x(graph, action, panel_points, poses=None, z_offset=0.0):
    """
    Sorted unique machine-X event coordinates: panel and hole vertices plus
    the active bend endpoints.
    """
    values = []
    for points, holes in panel_points.values():
        values.append(points[:, 0])
        for hole in holes:
            values.append(hole[:, 0])
    if poses is not None:
        for bend_id in action.bend_ids:
            bend = graph.bends[bend_id]
            transform = poses[bend.parent_panel]
            for point in (bend.axis_point,
                          bend.axis_point + bend.length * bend.axis_dir):
                point3 = np.append(np.append(point, z_offset), 1.0)
                values.append(np.array([float((transform @ point3)[0])]))
    events = np.unique(np.round(np.concatenate(values), 6))
    # coalesce sub-millimetre event clusters: discretized circular holes
    # spew dozens of near-identical X values each, and press-brake tooling
    # margins dwarf sub-mm geometry — the 3-probe sampling per interval
    # still sees the true geometry, only the sampling density is capped
    if len(events) > 2:
        keep = [events[0]]
        for value in events[1:-1]:
            if value - keep[-1] >= MIN_EVENT_SPACING:
                keep.append(value)
        keep.append(events[-1])
        events = np.asarray(keep)
    return events


def _required_intervals(graph, action, poses):
    """
    Machine-X spans of the active bend lines where material must be pressed:
    the axis segment clipped against material on BOTH sides of the line
    (holes and notches crossing the bend line become optional gaps).
    """
    spans = IntervalSet()
    for bend_id in action.bend_ids:
        bend = graph.bends[bend_id]
        start = bend.axis_point
        end = bend.axis_point + bend.length * bend.axis_dir
        normal = kinematics.normal_2d(bend.axis_dir)

        parent = _panel_polygon(graph.panels[bend.parent_panel])
        child = _panel_polygon(graph.panels[bend.child_panel])

        # child sits on the +normal side (normalized axis); probe just off
        # the line on each side
        child_probe = LineString([
            start + REQUIRED_PROBE_OFFSET * normal,
            end + REQUIRED_PROBE_OFFSET * normal,
        ])
        parent_probe = LineString([
            start - REQUIRED_PROBE_OFFSET * normal,
            end - REQUIRED_PROBE_OFFSET * normal,
        ])
        child_spans = _line_coverage(child_probe, child, bend.length)
        parent_spans = _line_coverage(parent_probe, parent, bend.length)
        both = child_spans.intersect(parent_spans)

        # map flat axis parameters to machine X through the parent pose
        transform = poses[bend.parent_panel]
        point3 = np.append(np.append(start, graph.z_offset), 1.0)
        x_start = float((transform @ point3)[0])
        direction3 = transform[:3, :3] @ np.array(
            [bend.axis_dir[0], bend.axis_dir[1], 0.0])
        x_scale = float(direction3[0])   # +/-1: the hinge lies on the X axis
        pairs = []
        for low, high in both:
            a = x_start + x_scale * low
            b = x_start + x_scale * high
            pairs.append((min(a, b), max(a, b)))
        spans = spans.union(IntervalSet(pairs))
    return spans


def _line_coverage(line, polygon, length):
    """
    Parameter intervals (0..length) of ``line`` covered by ``polygon``.
    """
    clipped = line.intersection(polygon.buffer(EDGE_EPSILON))
    if clipped.is_empty:
        return IntervalSet()
    origin = np.array(line.coords[0])
    direction = (np.array(line.coords[-1]) - origin)
    direction = direction / np.linalg.norm(direction)
    pairs = []
    for geometry in getattr(clipped, "geoms", [clipped]):
        coords = list(getattr(geometry, "coords", []))
        if len(coords) < 2:
            continue
        params = [float((np.array(c) - origin) @ direction) for c in coords]
        pairs.append((max(min(params), 0.0), min(max(params), length)))
    return IntervalSet(pairs)


def _panel_polygon(panel):
    polygon = Polygon(panel.outline, [h for h in panel.holes])
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    return polygon
