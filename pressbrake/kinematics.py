"""
Batched rigid-body kinematics for the panel/hinge graph.

Two parameterizations of an active bend are provided:

* ``relative_transforms`` — one wing fixed, the moving subtree rotates by the
  full bend parameter about the hinge.  Only relative motion matters for
  part-vs-part (self) collision, so this is what the self-collision checks use.

* ``machine_transforms`` — the air-bending machine-frame model: the hinge line
  is placed on the machine X axis (Y=0, Z=0 = die top plane) and BOTH wings
  rotate upward by half the bend parameter each, mirroring how the sheet
  pivots on the die shoulders while the punch descends.  Tool and machine
  collision checks must use this parameterization.  The pivot is pinned at
  the die top plane in v1; the penetration-depth descent into the V-die is a
  roadmap refinement folded into the clearance margin.
"""

import math

import numpy as np

from pressbrake.model import BendAction

Z_HAT = np.array([0.0, 0.0, 1.0])


def normal_2d(direction):
    """
    In-plane normal ``z_hat x d`` of a 2D direction.
    """
    return np.array([-direction[1], direction[0]])


def cross_2d(a, b):
    """
    Scalar z component of the 2D cross product (np.cross on 2D is deprecated).
    """
    return float(a[0]) * float(b[1]) - float(a[1]) * float(b[0])


def normalize_axis(axis_point, axis_dir, child_reference):
    """
    Flip ``axis_dir`` if needed so the in-plane normal ``z_hat x axis_dir``
    points from the hinge line toward ``child_reference`` (a 2D point on the
    child side).  With that orientation a positive rotation angle lifts the
    child subtree toward +Z.
    """
    axis_dir = np.asarray(axis_dir, dtype=float)
    axis_dir = axis_dir / np.linalg.norm(axis_dir)
    side = np.dot(normal_2d(axis_dir), np.asarray(child_reference) - np.asarray(axis_point))
    if side < 0:
        axis_dir = -axis_dir
    return axis_dir


def rotation_about_line(point2, direction2, angles, z=0.0):
    """
    Homogeneous rotations about the 3D line through ``point2`` (embedded at
    height ``z``) with in-plane unit ``direction2``.  ``angles`` may be a
    scalar or an (S,) array; returns (4,4) or (S,4,4).
    """
    angles = np.asarray(angles, dtype=float)
    scalar = angles.ndim == 0
    angles = np.atleast_1d(angles)

    direction = np.array([direction2[0], direction2[1], 0.0])
    point = np.array([point2[0], point2[1], z])

    # Rodrigues rotation matrices, batched over angles
    kx, ky, kz = direction
    cross = np.array([[0.0, -kz, ky], [kz, 0.0, -kx], [-ky, kx, 0.0]])
    outer = np.outer(direction, direction)
    cos = np.cos(angles)[:, None, None]
    sin = np.sin(angles)[:, None, None]
    rotations = cos * np.eye(3) + sin * cross + (1.0 - cos[:, 0, 0])[:, None, None] * outer

    transforms = np.tile(np.eye(4), (len(angles), 1, 1))
    transforms[:, :3, :3] = rotations
    transforms[:, :3, 3] = point - np.einsum("sij,j->si", rotations, point)
    return transforms[0] if scalar else transforms


def rotation_x(angles):
    """
    Homogeneous rotation about the machine X axis; scalar or (S,) batched.
    """
    return rotation_about_line(np.zeros(2), np.array([1.0, 0.0]), angles)


def fold_transforms(graph, theta):
    """
    Per-panel homogeneous transforms for hinge angles ``theta`` (B,).

    The transform of a panel is the product of the hinge rotations along the
    tree path from the base panel (product-of-exponentials with all hinges
    defined in the flat frame): T_child = T_parent @ R_bend(theta_bend).
    """
    theta = np.asarray(theta, dtype=float)
    transforms = np.tile(np.eye(4), (graph.panel_count, 1, 1))
    resolved = {graph.base_panel}
    pending = list(graph.bends)

    while pending:
        progressed = False
        remaining = []
        for bend in pending:
            if bend.parent_panel in resolved:
                # the hinge (neutral) axis lies on the mid-surface, i.e. at
                # the same height the panels are embedded at
                hinge = rotation_about_line(
                    bend.axis_point, bend.axis_dir, theta[bend.id],
                    z=graph.z_offset)
                deduction = bend_deduction(graph, bend, theta[bend.id])
                if deduction:
                    slide = np.eye(4)
                    slide[:2, 3] = deduction * normal_2d(bend.axis_dir)
                    hinge = hinge @ slide
                transforms[bend.child_panel] = transforms[bend.parent_panel] @ hinge
                resolved.add(bend.child_panel)
                progressed = True
            else:
                remaining.append(bend)
        if not progressed:
            raise ValueError("bend graph is not a tree rooted at the base panel")
        pending = remaining

    return transforms


def bend_deduction(graph, bend, angle):
    """
    Prismatic offset of the child frame away from the hinge at fold angle
    ``angle``: the flat bend zone (length ``zone_width`` = BA) is shorter
    than the two mid-plane setbacks it must serve (2 (r+t/2) tan(|a|/2)),
    so rotating rigid panels about the virtual-corner hinge alone leaves
    chained panels short by the classic bend deduction.  Zero when flat
    (the pattern is exact) and at zone_width 0 (sharp synthetic parts).
    """
    if bend.zone_width <= 0.0:
        return 0.0
    magnitude = abs(float(angle))
    if magnitude < 1e-9:
        return 0.0
    target = max(abs(bend.angle_target), 1e-9)
    theta = min(magnitude, math.radians(150.0))
    mid_radius = bend.inner_radius + graph.thickness / 2.0
    consumed_zone = bend.zone_width * min(magnitude / target, 1.0)
    return max(0.0, 2.0 * mid_radius * math.tan(theta / 2.0) - consumed_zone)


def panel_points_3d(panel, z=0.0):
    """
    Outline vertices lifted to 3D at height ``z`` (the pattern plane).
    """
    outline = np.asarray(panel.outline, dtype=float)
    points = np.zeros((len(outline), 3))
    points[:, :2] = outline
    points[:, 2] = z
    return points


def transform_points(transform, points):
    """
    Apply a (4,4) (or (S,4,4)) homogeneous transform to (N,3) points.
    """
    points = np.asarray(points, dtype=float)
    rotation = transform[..., :3, :3]
    translation = transform[..., :3, 3]
    return np.einsum("...ij,nj->...ni", rotation, points) + translation[..., None, :]


def panel_vertices(graph, theta):
    """
    Folded outline vertices (N,3) per panel at hinge angles ``theta``.
    """
    transforms = fold_transforms(graph, theta)
    return [
        transform_points(transforms[panel.id], panel_points_3d(panel, graph.z_offset))
        for panel in graph.panels
    ]


def relative_transforms(graph, state_theta, action, phi):
    """
    Self-collision parameterization: panels of the action's moving side
    rotate by the full ``phi`` about the (state-transformed) hinge line, the
    rest stay at the fold state.  ``phi`` scalar -> (P,4,4).
    """
    theta = np.asarray(state_theta, dtype=float)
    transforms = fold_transforms(graph, theta)
    bend = graph.bends[action.primary]

    mask = 0
    for bend_id in action.bend_ids:
        mask |= graph.bends[bend_id].moving_mask

    point, direction = _state_hinge_line(graph, transforms, bend)
    rotation = _rotation_about_line_3d(point, direction, phi)

    result = np.array(transforms)
    for panel in graph.panels:
        if mask >> panel.id & 1:
            result[panel.id] = rotation @ transforms[panel.id]
    return result


def placement_transform(graph, transforms, action):
    """
    Flat/state frame -> machine frame at phi=0 for a bend action.

    Maps the state-transformed hinge line onto the machine X axis and the
    parent panel plane onto Z=0 (die top).  ``flip`` mirrors the part over
    (part upside down on the die), ``rotation`` chooses which hinge end
    points toward +X.  Rows of the rotation block are the machine axes
    expressed in state coordinates, so the result is always a proper rigid
    transform.
    """
    bend = graph.bends[action.primary]
    point, direction = _state_hinge_line(graph, transforms, bend)
    normal = transforms[bend.parent_panel][:3, :3] @ Z_HAT

    x_axis = -direction if action.rotation else direction
    z_axis = -normal if action.flip else normal
    z_axis = z_axis - np.dot(z_axis, x_axis) * x_axis   # guard tiny numeric drift
    z_axis = z_axis / np.linalg.norm(z_axis)
    y_axis = np.cross(z_axis, x_axis)

    placement = np.eye(4)
    placement[:3, :3] = np.vstack([x_axis, y_axis, z_axis])
    placement[:3, 3] = -placement[:3, :3] @ point
    if action.x_offset:
        placement[0, 3] += action.x_offset
    return placement


def machine_transforms(graph, state_theta, action, phis):
    """
    Machine-frame air-bending poses: (S, P, 4, 4) for bend parameters
    ``phis`` (S,).  Both wings rotate about the machine X axis by half the
    bend parameter each, signs chosen so both lift away from the die.  The
    wing of a panel is decided by the Y sign of its centroid at phi=0.
    """
    phis = np.atleast_1d(np.asarray(phis, dtype=float))
    theta = np.asarray(state_theta, dtype=float)
    transforms = fold_transforms(graph, theta)
    placement = placement_transform(graph, transforms, action)
    base = np.einsum("ij,pjk->pik", placement, transforms)

    signs = np.zeros(graph.panel_count)
    for panel in graph.panels:
        centroid = np.append(panel.centroid(), graph.z_offset)
        machine_point = transform_points(base[panel.id], centroid[None, :])[0]
        signs[panel.id] = 1.0 if machine_point[1] >= 0 else -1.0

    lifts_pos = rotation_x(phis / 2.0)            # (S,4,4) for +Y wing
    lifts_neg = rotation_x(-phis / 2.0)           # (S,4,4) for -Y wing

    result = np.empty((len(phis), graph.panel_count, 4, 4))
    for panel in graph.panels:
        lift = lifts_pos if signs[panel.id] > 0 else lifts_neg
        result[:, panel.id] = np.einsum("sij,jk->sik", lift, base[panel.id])
    return result


def enumerate_actions(graph, sister_group_bends):
    """
    The candidate machine placements for one sister group: flip is forced by
    the bend sign (the bend must always form upward, the die being below),
    rotation (which hinge end faces +X) is free -> 2 actions.
    """
    bend = graph.bends[sister_group_bends[0]]
    flip = bend.angle_target < 0
    return [
        BendAction(bend_ids=tuple(sister_group_bends), flip=flip, rotation=rotation)
        for rotation in (0, 1)
    ]


def finalize_graph(graph, sister_tolerance=1e-6):
    """
    Derive the redundant fields of a freshly built graph in place:
    normalized hinge axis directions, moving-subtree bitmasks and collinear
    sister groups.  Both the synthetic test builders and the OCC extraction
    layer call this instead of duplicating the logic.
    """
    children = {}
    for bend in graph.bends:
        children.setdefault(bend.parent_panel, []).append(bend)

    def subtree_mask(panel_id):
        mask = 1 << panel_id
        for bend in children.get(panel_id, []):
            mask |= subtree_mask(bend.child_panel)
        return mask

    for bend in graph.bends:
        child_centroid = graph.panels[bend.child_panel].centroid()
        normalized = normalize_axis(bend.axis_point, bend.axis_dir, child_centroid)
        if float(normalized @ bend.axis_dir) < 0:
            # direction was flipped: keep the axis segment
            # [axis_point, axis_point + length*dir] pointing at the same
            # material by moving the anchor to the segment's other end
            bend.axis_point = bend.axis_point + bend.length * bend.axis_dir
        bend.axis_dir = normalized
        bend.moving_mask = subtree_mask(bend.child_panel)

    assign_sister_groups(graph, tolerance=sister_tolerance)
    return graph


def assign_sister_groups(graph, tolerance=1e-6, angle_tolerance=1e-6):
    """
    Group bends that lie on one infinite line with the same signed angle and
    radius: they may be formed simultaneously in a single press stroke.
    """
    group_of = {}
    next_group = 0
    for bend in graph.bends:
        assigned = None
        for other_id, group in group_of.items():
            other = graph.bends[other_id]
            if abs(bend.angle_target - other.angle_target) > angle_tolerance:
                continue
            if abs(bend.inner_radius - other.inner_radius) > tolerance:
                continue
            if not _collinear(bend, other, tolerance):
                continue
            assigned = group
            break
        if assigned is None:
            assigned = next_group
            next_group += 1
        group_of[bend.id] = assigned
        bend.sister_group = assigned
    return graph


def _collinear(bend_a, bend_b, tolerance):
    if abs(cross_2d(bend_a.axis_dir, bend_b.axis_dir)) > tolerance:
        return False
    offset = bend_b.axis_point - bend_a.axis_point
    return abs(cross_2d(bend_a.axis_dir, offset)) <= tolerance


def _state_hinge_line(graph, transforms, bend):
    """
    Hinge line of ``bend`` in state coordinates: (point (3,), direction (3,)).
    """
    parent = transforms[bend.parent_panel]
    point = parent @ np.append(np.append(bend.axis_point, graph.z_offset), 1.0)
    direction = parent[:3, :3] @ np.array([bend.axis_dir[0], bend.axis_dir[1], 0.0])
    return point[:3], direction / np.linalg.norm(direction)


def _rotation_about_line_3d(point, direction, angle):
    kx, ky, kz = direction
    cross = np.array([[0.0, -kz, ky], [kz, 0.0, -kx], [-ky, kx, 0.0]])
    rotation = (
        np.cos(angle) * np.eye(3)
        + np.sin(angle) * cross
        + (1.0 - np.cos(angle)) * np.outer(direction, direction)
    )
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = point - rotation @ point
    return transform
