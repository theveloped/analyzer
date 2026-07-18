"""Posing fold-coordinate meshes at arbitrary bend states (pure numpy).

``compute_fold_mesh`` (adapter.py, the OCP side) expresses every fine-mesh
vertex in FLAT coordinates: (x, y) in the pattern frame the panel outlines
live in, z the true material height (the unfolded skin at 0, the opposite
skin at 2*z_offset, mid-surface at z_offset), plus the owning panel or bend.
This module folds those coordinates back up at any hinge angles ``theta``:

* panel vertices ride their panel's rigid ``kinematics.fold_transforms``
  pose (which includes the bend-deduction slide);
* bend-zone vertices follow the fixed-radius progressive-wrap model: at
  bend parameter theta the zone splits into a parent-rigid flat part
  (length e_p - m(theta) from the parent zone edge), a wrapped arc of
  consumed neutral length c(theta) = zone_width * |theta|/|target|, and a
  child-rigid flat part.  m(theta) = (r_i + t/2) tan(min|theta|,150deg / 2)
  is the mid-surface tangent setback.  The model is exactly watertight at
  both zone edges for every theta: the parent-side identity is trivial and
  the child-side identity IS the bend-deduction formula (2 m - c = delta),
  so panels never detach from the arc mid-stroke.  Overbend past the
  target clamps the flat parent part at zero and wraps the whole zone with
  an effective radius zone_width/|theta|.

The TS mirror (frontend/src/processes/sheetmetal/foldmath.ts) must stay a
line-for-line port of ``pose_vertices``/``step_poses`` consumers; the mesh
collision verifier (meshcheck.py) uses the same functions directly.

Frames: ``pose_vertices`` returns positions in the flat/base frame (base
panel fixed).  ``step_poses`` yields, per plan step, the rigid ``placement``
into the machine frame plus the ``lift_sign`` such that
``R_x(lift_sign*phi/2) @ placement @ fold_transforms(theta_with_active_phi)``
is the symmetric air-bend pose (both wings +-phi/2 about machine X).
"""

import math

import numpy as np

from pressbrake import kinematics
from pressbrake.model import BendAction

HEM_CAP = math.radians(150.0)


def pose_vertices(graph, flat_verts, vertex_panel, vertex_bend, theta):
    """Fold flat-coordinate vertices to hinge angles ``theta`` (B,).

    ``flat_verts`` float (V,3); ``vertex_panel``/``vertex_bend`` are the
    +1-encoded owner ids (0 = none) as stored in the bend_plan result.
    Unowned vertices keep their flat position.  Returns float64 (V,3) in
    the flat/base frame.
    """
    flat_verts = np.asarray(flat_verts, dtype=float)
    vertex_panel = np.asarray(vertex_panel)
    vertex_bend = np.asarray(vertex_bend)
    theta = np.asarray(theta, dtype=float)

    transforms = kinematics.fold_transforms(graph, theta)
    posed = np.array(flat_verts, dtype=float)

    for panel in graph.panels:
        sel = vertex_panel == panel.id + 1
        if np.any(sel):
            posed[sel] = _apply(transforms[panel.id], flat_verts[sel])

    for bend in graph.bends:
        sel = vertex_bend == bend.id + 1
        if np.any(sel):
            posed[sel] = _pose_bend_zone(
                graph, bend, transforms, flat_verts[sel],
                float(theta[bend.id]))
    return posed


def _apply(transform, points):
    return points @ transform[:3, :3].T + transform[:3, 3]


def _pose_bend_zone(graph, bend, transforms, flat, theta):
    """Progressive-wrap pose of one bend zone's vertices (N,3)."""
    parent_pose = transforms[bend.parent_panel]
    if abs(theta) < 1e-9 or bend.zone_width <= 0.0:
        return _apply(parent_pose, flat)

    axis_point = np.asarray(bend.axis_point, dtype=float)
    axis_dir = np.asarray(bend.axis_dir, dtype=float)
    normal = kinematics.normal_2d(axis_dir)

    rel = flat[:, :2] - axis_point
    d = rel @ normal
    a = rel @ axis_dir
    zeta = flat[:, 2] - graph.z_offset

    zone = float(bend.zone_width)
    e_p = zone / 2.0 + float(bend.zone_shift)
    u = d + e_p

    magnitude = abs(theta)
    target = max(abs(bend.angle_target), 1e-9)
    consumed = zone * min(magnitude / target, 1.0)
    mid_radius = bend.inner_radius + graph.thickness / 2.0
    setback = mid_radius * math.tan(min(magnitude, HEM_CAP) / 2.0)
    u0 = float(np.clip(e_p - setback, 0.0, max(zone - consumed, 0.0)))
    radius_eff = consumed / magnitude
    s = 1.0 if theta >= 0 else -1.0

    parent_mask = u <= u0
    child_mask = u >= u0 + consumed
    arc_mask = ~parent_mask & ~child_mask

    posed = np.empty_like(flat)
    if np.any(parent_mask):
        posed[parent_mask] = _apply(parent_pose, flat[parent_mask])
    if np.any(child_mask):
        # transforms[child] already contains R(theta) @ deduction slide
        posed[child_mask] = _apply(transforms[bend.child_panel],
                                   flat[child_mask])
    if np.any(arc_mask):
        rho = mid_radius - s * zeta[arc_mask]
        phi_v = (u[arc_mask] - u0) / radius_eff
        d_new = (u0 - e_p) + rho * np.sin(phi_v)
        z_new = graph.z_offset + s * (mid_radius - rho * np.cos(phi_v))
        local = np.empty((int(arc_mask.sum()), 3))
        local[:, :2] = (axis_point + a[arc_mask, None] * axis_dir
                        + d_new[:, None] * normal)
        local[:, 2] = z_new
        arc = _apply(parent_pose, local)

        # exact child-edge closure: the ideal circle meets the child-rigid
        # region exactly while the tangent identity holds (up to the target
        # angle — the child-side identity IS the bend-deduction formula);
        # overbend clamps u0/consumed and breaks it slightly, so blend the
        # residual linearly over the wrap to keep the zone watertight at
        # every theta
        end_d = (u0 + consumed) - e_p
        arc_end = np.empty_like(local)
        rho_end = rho
        arc_end[:, :2] = (axis_point + a[arc_mask, None] * axis_dir
                          + ((u0 - e_p) + rho_end * math.sin(magnitude))
                          [:, None] * normal)
        arc_end[:, 2] = graph.z_offset + s * (
            mid_radius - rho_end * math.cos(magnitude))
        end_flat = np.empty_like(local)
        end_flat[:, :2] = (axis_point + a[arc_mask, None] * axis_dir
                           + end_d * normal)
        end_flat[:, 2] = graph.z_offset + zeta[arc_mask]
        gap = _apply(transforms[bend.child_panel], end_flat) \
            - _apply(parent_pose, arc_end)
        posed[arc_mask] = arc + (phi_v / magnitude)[:, None] * gap
    return posed


def step_poses(graph, steps):
    """Per-plan-step machine pose data for the viewer and mesh verifier.

    ``steps`` iterable of dicts with ``bend_ids``/``rotation``/``flip``
    (the ``report.dump_plan`` step shape).  Returns a list of dicts:
    ``placement`` (16 floats row-major), ``lift_sign`` (parent-wing half
    lift sign), ``theta_before`` (B floats), ``phi_target`` (positive
    stroke magnitude; the signed active angle is
    sign(angle_overbend) * phi).  Mutates nothing.
    """
    theta = np.zeros(graph.bend_count)
    poses = []
    for step in steps:
        bend_ids = [int(b) for b in step["bend_ids"]]
        action = BendAction(bend_ids=tuple(bend_ids),
                            flip=bool(step["flip"]),
                            rotation=int(step["rotation"]))
        transforms = kinematics.fold_transforms(graph, theta)
        placement = kinematics.placement_transform(graph, transforms, action)

        primary = graph.bends[bend_ids[0]]
        parent_pose = placement @ transforms[primary.parent_panel]
        centroid = np.append(graph.panels[primary.parent_panel].centroid(),
                             graph.z_offset)
        machine_point = _apply(parent_pose, centroid[None, :])[0]
        lift_sign = 1.0 if machine_point[1] >= 0 else -1.0

        poses.append({
            "placement": [float(v) for v in placement.reshape(-1)],
            "lift_sign": float(lift_sign),
            "theta_before": [float(v) for v in theta],
            "phi_target": float(abs(primary.angle_overbend)),
        })
        for bend_id in bend_ids:
            theta[bend_id] = graph.bends[bend_id].angle_relaxed
    return poses
