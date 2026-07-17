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
from dataclasses import dataclass

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from pressbrake import collision, kinematics
from pressbrake.intervals import IntervalSet

# minimum overlap area (mm^2) that counts as penetration: grazes below the
# sector-arc discretization error are noise, real interferences grow fast
AREA_TOLERANCE = 0.02
EDGE_EPSILON = 1e-4
REQUIRED_PROBE_OFFSET = 0.25     # mm on each side of the bend line


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


def compute_envelope(graph, state_theta, action, punch, die, machine=None,
                     margin=2.0, end_relief=DEFAULT_END_RELIEF):
    """
    Full X-interval envelope of one bend action for one punch/die selection.
    """
    max_phi = max(abs(graph.bends[b].angle_overbend) for b in action.bend_ids)

    poses = kinematics.machine_transforms(graph, state_theta, action, [0.0])[0]
    exclusion = collision.pivot_exclusion(graph, action)

    # obstacles: punch/die penetration-only, ram/table margin-buffered
    tool_obstacles = collision.build_obstacles(
        punch, die, None, graph.thickness, max_phi=max_phi)
    machine_obstacles = []
    if machine is not None:
        machine_obstacles = [
            entry for entry in collision.build_obstacles(
                punch, die, machine, graph.thickness, max_phi=max_phi,
                margin=margin)
            if entry[0] in ("ram", "table")
        ]

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
    hits = {"punch": [], "die": [], "ram": [], "table": []}

    for x0, x1 in zip(events[:-1], events[1:]):
        if x1 - x0 < 1e-9:
            continue
        probes = _probe_positions(x0, x1)
        swept_pieces = []
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
                    swept_pieces.append(swept_region(
                        segments, graph.thickness / 2.0, angle_low, angle_high))
                # panels perpendicular to X: cover the polygon interior at
                # the end angles too (the sector sweep covers the boundary)
                normal = poses[panel.id][:3, :3] @ np.array([0.0, 0.0, 1.0])
                if abs(normal[0]) > collision.PERPENDICULAR_LIMIT:
                    full = collision.slice_panel(
                        points, holes, poses[panel.id], graph.thickness, x)
                    if full is not None and not full.is_empty:
                        for angle in (angle_low, angle_high):
                            swept_pieces.append(affinity.rotate(
                                full, math.degrees(angle), origin=(0.0, 0.0)))
        if not swept_pieces:
            continue
        swept = unary_union(swept_pieces)
        workpiece = swept.difference(exclusion)
        if workpiece.is_empty:
            continue
        for name, prepared, polygon in tool_obstacles:
            if prepared.intersects(workpiece) and \
                    workpiece.intersection(polygon).area > AREA_TOLERANCE:
                hits[name].append((x0, x1))
        for name, prepared, polygon in machine_obstacles:
            if prepared.intersects(swept) and \
                    swept.intersection(polygon).area > AREA_TOLERANCE:
                hits[name].append((x0, x1))

    required = _required_intervals(graph, action, poses)
    x_low = min(events[0], required.arr[0, 0] if len(required) else events[0])
    x_high = max(events[-1], required.arr[-1, 1] if len(required) else events[-1])

    # note: the placement transform already carries action.x_offset, so all
    # coordinates here are final machine X - no further translation
    envelope = CollisionEnvelope(
        action=action,
        punch_id=punch.id if punch else "",
        die_id=die.id if die else "",
        required=required,
        required_core=shrink_intervals(required, end_relief),
        forbidden_punch=IntervalSet(hits["punch"]).buffer(margin),
        forbidden_die=IntervalSet(hits["die"]).buffer(margin),
        forbidden_machine=IntervalSet(hits["ram"] + hits["table"]),
        margin=margin,
        x_range=(x_low, x_high),
    )
    return envelope


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


def swept_region(segments, width, angle_low, angle_high):
    """
    Region covered by material segments (inflated by ``width``) rotating
    about the origin from ``angle_low`` to ``angle_high`` (rad).

    Each segment is covered by an annular sector spanning its radius range
    [distance(origin, segment), max endpoint radius] and its swept
    polar-angle range, then buffered by ``width`` (buffering commutes with
    rotation, so inflating after sweeping is exact).  Exact for radial
    segments, conservative superset for oblique ones.

    This is the API the analytic arc-contact implementation (P4) replaces.
    """
    pieces = []
    for (y0, z0), (y1, z1) in segments:
        for a, b in _split_at_foot(np.array([y0, z0]), np.array([y1, z1])):
            radius_a = float(np.linalg.norm(a))
            radius_b = float(np.linalg.norm(b))
            r_max = max(radius_a, radius_b)
            if r_max < 1e-9:
                continue
            r_min = _segment_distance_to_origin(a, b)
            # a piece with one end at the pivot is purely radial: its polar
            # angle is defined by the other end
            if radius_a < 1e-9:
                theta_a = theta_b = math.atan2(b[1], b[0])
            elif radius_b < 1e-9:
                theta_a = theta_b = math.atan2(a[1], a[0])
            else:
                theta_a = math.atan2(a[1], a[0])
                theta_b = theta_a + _wrap_angle(math.atan2(b[1], b[0]) - theta_a)
            theta_low = min(theta_a, theta_b) + angle_low
            theta_high = max(theta_a, theta_b) + angle_high
            pieces.append(_annular_sector(r_min, r_max, theta_low, theta_high))
    if not pieces:
        return Polygon()
    return unary_union(pieces).buffer(width, quad_segs=8)


def _split_at_foot(a, b):
    """
    Split a segment at the perpendicular foot of the origin when it lies in
    the interior: each piece is then monotone in polar angle so its angular
    extent is exactly the endpoint range.
    """
    direction = b - a
    length_sq = float(direction @ direction)
    if length_sq < 1e-18:
        return [(a, b)]
    t = -(a @ direction) / length_sq
    if 1e-9 < t < 1.0 - 1e-9:
        foot = a + t * direction
        return [(a, foot), (foot, b)]
    return [(a, b)]


def _segment_distance_to_origin(a, b):
    direction = b - a
    length_sq = float(direction @ direction)
    if length_sq < 1e-18:
        return float(np.linalg.norm(a))
    t = float(np.clip(-(a @ direction) / length_sq, 0.0, 1.0))
    return float(np.linalg.norm(a + t * direction))


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
    steps = max(int(math.ceil(span / ARC_STEP)), 1)
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
