"""
Sampled-angle collision checking (phase 2 of the design).

The machine-frame air-bend parameterization (``kinematics.machine_transforms``)
is checked against tool/machine YZ profiles by slicing every panel at the
critical machine-X coordinates; self-collision uses the relative
parameterization.  This sampling checker stays in the code base permanently
as the validation oracle for the interval-envelope engine (envelope.py).

v1 modelling notes (all folded into the clearance margin where they matter):

* The pivot is the hinge line at the sheet mid-plane, pinned at the machine
  X axis; the die top is at z=-t/2 and the punch tip at z=+t/2.
* Rigid panels reach all the way to the hinge line while real material
  wraps the punch nose in an arc, so a "pivot exclusion disk" of radius
  inner_radius + thickness around the origin is subtracted from workpiece
  geometry before testing against the punch and die.
* Contact with the active punch and die is the point of the operation (the
  wings hug the punch flanks at the final angle), so workpiece-vs-tool is a
  PENETRATION-ONLY test: no clearance margin, overlap area must be positive.
  The clearance margin applies to the machine ram/table (and to
  self-collision), where proximity genuinely is a hazard.  The punch is
  positioned at the inner-corner height of the final bend parameter (see
  machine.ToolProfile.transformed_profile) so tangency stays tangency.
* Panels directly connected by a bend are exempt from self-collision
  (they legitimately meet at their hinge; folds approaching pi are hems,
  flagged elsewhere).
"""

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.prepared import prep

from pressbrake import kinematics
from pressbrake.model import polygon_area

AREA_TOLERANCE = 1e-6
PERPENDICULAR_LIMIT = 0.999
DEFAULT_PHI_STEP = math.radians(2.0)


@dataclass
class CollisionHit:
    phi: float                # bend parameter at the hit, rad
    x: float                  # machine X of the slice (nan for self hits)
    panel: int
    obstacle: str             # punch|die|ram|table|panel:<id>


@dataclass
class CollisionReport:
    hits: list = field(default_factory=list)
    phis: np.ndarray = None

    @property
    def collided(self):
        return bool(self.hits)

    @property
    def first_phi(self):
        return min(hit.phi for hit in self.hits) if self.hits else None

    def summary(self):
        if not self.collided:
            return "no collision"
        first = min(self.hits, key=lambda h: h.phi)
        return "collision at phi={:.1f} deg: panel {} vs {}".format(
            math.degrees(first.phi), first.panel, first.obstacle)


def check_action(graph, state_theta, action, punch=None, die=None, machine=None,
                 margin=2.0, phi_step=DEFAULT_PHI_STEP, stop_on_first=False):
    """
    Sampled binary collision check of one bend action.  Returns a
    CollisionReport with every (phi, x, panel, obstacle) witness found.
    """
    max_phi = max(abs(graph.bends[b].angle_overbend) for b in action.bend_ids)
    steps = max(int(math.ceil(max_phi / phi_step)), 1)
    phis = np.linspace(0.0, max_phi, steps + 1)

    report = CollisionReport(phis=phis)
    obstacles = build_obstacles(punch, die, machine, graph.thickness,
                                max_phi=max_phi, margin=margin)
    exclusion = pivot_exclusion(graph, action)

    poses = kinematics.machine_transforms(graph, state_theta, action, phis)
    panel_points = [
        kinematics.panel_points_3d(panel, graph.z_offset) for panel in graph.panels
    ]
    hole_points = [
        [np.column_stack([h, np.full(len(h), graph.z_offset)]) for h in panel.holes]
        for panel in graph.panels
    ]

    # Machine X of every vertex is invariant during the bend rotation:
    # critical X coordinates are computed once from the phi=0 pose.
    verts0 = [
        kinematics.transform_points(poses[0, p.id], panel_points[p.id])
        for p in graph.panels
    ]
    samples = _x_samples([v[:, 0] for v in verts0])

    if obstacles:
        for index, phi in enumerate(phis):
            for panel in graph.panels:
                verts = kinematics.transform_points(
                    poses[index, panel.id], panel_points[panel.id])
                holes = [
                    kinematics.transform_points(poses[index, panel.id], h)
                    for h in hole_points[panel.id]
                ]
                for x in samples:
                    slice_geometry = slice_panel(
                        verts, holes, poses[index, panel.id], graph.thickness, x)
                    if slice_geometry is None:
                        continue
                    if exclusion is not None:
                        slice_geometry = slice_geometry.difference(exclusion)
                        if slice_geometry.is_empty:
                            continue
                    for name, prepared, polygon in obstacles:
                        if not prepared.intersects(slice_geometry):
                            continue
                        if slice_geometry.intersection(polygon).area > AREA_TOLERANCE:
                            report.hits.append(
                                CollisionHit(phi=float(phi), x=float(x),
                                             panel=panel.id, obstacle=name))
                            if stop_on_first:
                                return report

    self_hits = check_self_collision(
        graph, state_theta, action, margin=margin, phis=phis,
        stop_on_first=stop_on_first)
    report.hits.extend(self_hits)
    return report


def check_self_collision(graph, state_theta, action, margin=2.0, phis=None,
                         phi_step=DEFAULT_PHI_STEP, stop_on_first=False):
    """
    Relative-parameterization self-collision: only pairs straddling the
    moving mask are tested; bend-adjacent pairs are exempt.
    """
    if phis is None:
        max_phi = max(abs(graph.bends[b].angle_overbend) for b in action.bend_ids)
        steps = max(int(math.ceil(max_phi / phi_step)), 1)
        phis = np.linspace(0.0, max_phi, steps + 1)

    moving_mask = 0
    for bend_id in action.bend_ids:
        moving_mask |= graph.bends[bend_id].moving_mask
    sign = 1.0 if graph.bends[action.primary].angle_target >= 0 else -1.0

    adjacent = {
        frozenset((bend.parent_panel, bend.child_panel)) for bend in graph.bends
    }
    pairs = [
        (a.id, b.id)
        for a in graph.panels for b in graph.panels
        if a.id < b.id
        and (moving_mask >> a.id & 1) != (moving_mask >> b.id & 1)
        and frozenset((a.id, b.id)) not in adjacent
    ]
    if not pairs:
        return []

    panel_points = [
        kinematics.panel_points_3d(panel, graph.z_offset) for panel in graph.panels
    ]
    hits = []
    for phi in phis:
        transforms = kinematics.relative_transforms(
            graph, state_theta, action, sign * float(phi))
        for a_id, b_id in pairs:
            verts_a = kinematics.transform_points(transforms[a_id], panel_points[a_id])
            verts_b = kinematics.transform_points(transforms[b_id], panel_points[b_id])
            if _panels_intersect(
                    verts_a, transforms[a_id], graph.panels[a_id],
                    verts_b, transforms[b_id], graph.panels[b_id],
                    graph.thickness, margin):
                hits.append(CollisionHit(
                    phi=float(phi), x=float("nan"), panel=b_id,
                    obstacle="panel:{}".format(a_id)))
                if stop_on_first:
                    return hits
    return hits


# --- obstacles ---------------------------------------------------------------


def build_obstacles(punch, die, machine, thickness, max_phi=0.0, margin=0.0):
    """
    List of (name, prepared_polygon, polygon) YZ obstacles in machine frame.
    Workpiece-vs-tool is penetration-only, so punch/die enter raw; the
    machine ram/table are buffered by the clearance margin.
    """
    obstacles = []
    if punch is not None:
        polygon = Polygon(punch.transformed_profile(thickness, max_phi))
        obstacles.append(("punch", prep(polygon), polygon))
    if die is not None:
        polygon = Polygon(die.transformed_profile(thickness))
        obstacles.append(("die", prep(polygon), polygon))
    if machine is not None and punch is not None:
        polygon = Polygon(
            machine.ram_transformed(punch, thickness, max_phi)).buffer(margin)
        obstacles.append(("ram", prep(polygon), polygon))
    if machine is not None and die is not None:
        polygon = Polygon(
            machine.table_transformed(die, thickness)).buffer(margin)
        obstacles.append(("table", prep(polygon), polygon))
    return obstacles


def pivot_exclusion(graph, action):
    """
    Disk around the pivot standing in for the bend-zone arc (rigid panels
    reach the hinge line; real material wraps the punch nose).
    """
    radius = max(
        graph.bends[b].inner_radius for b in action.bend_ids
    ) + graph.thickness + 0.5
    return Point(0.0, 0.0).buffer(radius, quad_segs=16)


# --- panel slicing -----------------------------------------------------------


def slice_panel_segments(vertices, hole_vertices, transform, thickness, x):
    """
    YZ cross-section of a panel mid-plane at machine X = x as a list of
    ((y0,z0), (y1,z1)) segments (to be inflated by t/2 for the material
    slab).  Panels lying (nearly) perpendicular to the X axis fall back to
    their outline edges when the cut plane is within the slab.
    """
    normal = transform[:3, :3] @ np.array([0.0, 0.0, 1.0])

    if abs(normal[0]) > PERPENDICULAR_LIMIT:
        if abs(float(vertices[0, 0]) - x) > thickness / 2.0:
            return []
        segments = []
        for loop in [vertices] + list(hole_vertices):
            count = len(loop)
            for index in range(count):
                a, b = loop[index], loop[(index + 1) % count]
                segments.append(((float(a[1]), float(a[2])),
                                 (float(b[1]), float(b[2]))))
        return segments

    return _plane_cut_segments([vertices] + list(hole_vertices), x)


def slice_panel(vertices, hole_vertices, transform, thickness, x, inflate=0.0):
    """
    YZ cross-section of a thickness-inflated panel at machine X = x, as a
    shapely geometry (or None).  The mid-plane polygon is cut by the plane
    and the resulting segments are buffered by t/2 + inflate; panels lying
    (nearly) parallel to the cut plane fall back to their full YZ projection.
    """
    normal = transform[:3, :3] @ np.array([0.0, 0.0, 1.0])
    radius = thickness / 2.0 + inflate

    if abs(normal[0]) > PERPENDICULAR_LIMIT:
        # panel is perpendicular to the X axis: all at one x
        if abs(float(vertices[0, 0]) - x) > radius:
            return None
        outline = [(float(v[1]), float(v[2])) for v in vertices]
        holes = [[(float(v[1]), float(v[2])) for v in h] for h in hole_vertices]
        polygon = Polygon(outline, holes)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        return polygon.buffer(inflate) if inflate > 0 else polygon

    segments = _plane_cut_segments([vertices] + list(hole_vertices), x)
    if not segments:
        return None
    geometry = None
    for start, end in segments:
        piece = LineString([start, end]).buffer(radius, quad_segs=8)
        geometry = piece if geometry is None else geometry.union(piece)
    return geometry


def _plane_cut_segments(loops, x):
    """
    Even-odd material segments of a set of closed 3D loops cut by the plane
    X=x, projected to (y, z).
    """
    crossings = []
    for loop in loops:
        count = len(loop)
        for index in range(count):
            a = loop[index]
            b = loop[(index + 1) % count]
            da, db = a[0] - x, b[0] - x
            if (da > 0) == (db > 0):
                continue
            if da == db:
                continue
            t = da / (da - db)
            point = a + t * (b - a)
            crossings.append((point[1], point[2]))
    if len(crossings) < 2:
        return []

    # order crossings along the cut line; the cut of a plane with the panel
    # plane is a straight line, so sorting by the dominant YZ direction is
    # exact
    points = np.array(crossings)
    direction = points.max(axis=0) - points.min(axis=0)
    if np.linalg.norm(direction) < 1e-12:
        return []
    direction = direction / np.linalg.norm(direction)
    order = np.argsort(points @ direction)
    points = points[order]

    segments = []
    for index in range(0, len(points) - 1, 2):
        start, end = points[index], points[index + 1]
        if np.linalg.norm(end - start) > 1e-9:
            segments.append((tuple(start), tuple(end)))
    return segments


def _x_samples(vertex_x_arrays):
    """
    Midpoints of the critical-X intervals spanned by the panel vertices.
    """
    values = np.unique(np.round(np.concatenate(vertex_x_arrays), 6))
    if len(values) < 2:
        return list(values)
    return list((values[:-1] + values[1:]) / 2.0)


# --- panel vs panel ----------------------------------------------------------


def _panels_intersect(verts_a, transform_a, panel_a, verts_b, transform_b,
                      panel_b, thickness, margin):
    """
    Thickness-slab intersection test between two rigid panels.
    """
    clearance = thickness + margin
    normal_a = transform_a[:3, :3] @ np.array([0.0, 0.0, 1.0])
    normal_b = transform_b[:3, :3] @ np.array([0.0, 0.0, 1.0])

    # slab reject: all of B on one side of A's slab (and vice versa)
    dist_b = (verts_b - verts_a[0]) @ normal_a
    if np.min(dist_b) > clearance or np.max(dist_b) < -clearance:
        return False
    dist_a = (verts_a - verts_b[0]) @ normal_b
    if np.min(dist_a) > clearance or np.max(dist_a) < -clearance:
        return False

    cross = np.cross(normal_a, normal_b)
    sin_angle = np.linalg.norm(cross)

    polygon_a = _local_polygon(panel_a)
    polygon_b = _local_polygon(panel_b)

    if sin_angle < 1e-3:
        # (near-)parallel planes within reach: compare footprints in A's frame
        to_a = np.linalg.inv(transform_a)
        b_in_a = kinematics.transform_points(to_a @ transform_b, _panel_points(panel_b))
        footprint_b = Polygon(b_in_a[:, :2])
        if not footprint_b.is_valid:
            footprint_b = footprint_b.buffer(0)
        return polygon_a.buffer(margin).intersection(footprint_b).area > AREA_TOLERANCE

    # general case: clip the plane-plane intersection line to both panels
    direction = cross / sin_angle
    point = _plane_intersection_point(verts_a[0], normal_a, verts_b[0], normal_b,
                                      direction)
    if point is None:
        return False

    interval_a = _line_polygon_interval(point, direction, transform_a, polygon_a)
    interval_b = _line_polygon_interval(point, direction, transform_b, polygon_b)
    if interval_a is None or interval_b is None:
        return False
    # slab thickness extends each panel's reach along the line
    reach = clearance / max(sin_angle, 1e-3)
    return _overlaps(interval_a, interval_b, reach)


def _overlaps(interval_a, interval_b, reach):
    return (interval_a[0] - reach) < (interval_b[1] + reach) and \
           (interval_b[0] - reach) < (interval_a[1] + reach)


def _panel_points(panel):
    points = np.zeros((len(panel.outline), 3))
    points[:, :2] = panel.outline
    return points


def _local_polygon(panel):
    polygon = Polygon(panel.outline, [h for h in panel.holes])
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    return polygon


def _plane_intersection_point(point_a, normal_a, point_b, normal_b, direction):
    """
    Any point on the intersection line of two planes.
    """
    # solve for a point: n_a . p = n_a . p_a ; n_b . p = n_b . p_b ; d . p = 0
    matrix = np.vstack([normal_a, normal_b, direction])
    rhs = np.array([
        float(normal_a @ point_a), float(normal_b @ point_b), 0.0,
    ])
    try:
        return np.linalg.solve(matrix, rhs)
    except np.linalg.LinAlgError:
        return None


def _line_polygon_interval(point, direction, transform, polygon):
    """
    Parameter interval of the 3D line (point + t*direction) inside a panel's
    outline polygon (in the panel's local frame).
    """
    inverse = np.linalg.inv(transform)
    local_point = (inverse @ np.append(point, 1.0))[:3]
    local_direction = inverse[:3, :3] @ direction

    planar = np.array([local_direction[0], local_direction[1]])
    norm = np.linalg.norm(planar)
    if norm < 1e-9:
        return None
    bounds = polygon.bounds
    diameter = math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]) + 1.0
    start = np.array(local_point[:2]) - (diameter / norm) * planar
    end = np.array(local_point[:2]) + (diameter / norm) * planar
    clipped = LineString([start, end]).intersection(polygon)
    if clipped.is_empty:
        return None
    coords = []
    geoms = getattr(clipped, "geoms", [clipped])
    for geometry in geoms:
        coords.extend(getattr(geometry, "coords", []))
    if not coords:
        return None
    # the intersection line lies in the panel's plane, so |planar| is ~1 and
    # the 2D parameter equals the 3D line parameter
    params = [
        float((np.array(c) - local_point[:2]) @ planar) / (norm * norm)
        for c in coords
    ]
    return (min(params), max(params))
