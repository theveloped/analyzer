"""Mesh-backed collision verification of bend plans (numpy + meshlib).

The analytic interval envelope stays the fast search-time pruner; this
module verifies a FINAL plan against the real geometry: the fine mesh is
posed through the fold coordinates (foldmesh.pose_vertices) at sampled
stroke angles per step, and checked with meshlib's exact triangle-pair
test against the installed tool sections and the machine frame, all
extruded from their YZ profiles across the solved X spans.

Conventions match the viewer (bendsequence.ts): tools are built at the
phi = 0 position, the punch/ram follow the stroke through a rigid Z
translation handed to findCollidingTriangles as rigidB2A.  Tool profiles
are inset by ``eps`` before extrusion so the designed tangency (wings
hugging the punch flanks, the sheet resting on the die) never reads as a
collision; every reported hit is a genuine penetration deeper than eps.

meshlib is never called concurrently (AGENTS hard rule 2) — the analysis
job worker is single-threaded and this module spawns nothing.
"""

import math

import numpy as np
from loguru import logger

from pressbrake import foldmesh, kinematics

DEFAULT_PHI_STEP = math.radians(2.0)
DEFAULT_EPS = 0.1
FRAME_PAD = 40.0        # mm of ram/table drawn beyond the outermost section


def check_plan(graph, flat_verts, vertex_panel, vertex_bend, fine_faces,
               plan, tooling, *, phi_step=DEFAULT_PHI_STEP, eps=DEFAULT_EPS,
               progress=None):
    """Verify one dumped plan (report.dump_plan + step_poses shape).

    Returns {"clean": bool, "eps": eps, "phi_step_deg": ..., "steps":
    [{"step", "clean", "hits": [{"phi_deg", "tool", "x", "face_count"}]}]}
    plus "hit_faces": set of fine-mesh triangle indices for painting.
    """
    from meshlib import mrmeshpy as mm

    flat_verts = np.asarray(flat_verts, dtype=float)
    thickness = float(graph.thickness)
    steps = plan["steps"]
    setups = plan["setups"]
    report = {"clean": True, "eps": float(eps),
              "phi_step_deg": math.degrees(phi_step), "steps": []}
    hit_faces = set()

    for index, step in enumerate(steps):
        setup = next((s for s in setups if index in s["step_indices"]), None)
        step_report = {"step": index, "clean": True, "hits": []}
        if setup is None:
            report["steps"].append(step_report)
            continue
        tools = _setup_tools(setup, tooling, thickness, eps)
        if progress is not None:
            progress(0.9 + 0.08 * index / max(len(steps), 1),
                     f"mesh check step {index + 1}/{len(steps)}")

        placement = np.asarray(step["placement"], dtype=float).reshape(4, 4)
        lift_sign = float(step["lift_sign"])
        theta_before = np.asarray(step["theta_before"], dtype=float)
        phi_target = float(step["phi_target"])
        signs = {b: math.copysign(1.0, graph.bends[b].angle_overbend)
                 for b in step["bend_ids"]}

        # the active bend zone is where the punch/die legitimately press
        # (the sheet wraps the nose; the v1 punch-height convention pins the
        # tip at the sharp-corner intersection which sits below the real
        # arc apex) — exclude it from the tool tests, exactly like the 2D
        # oracle's pivot_exclusion disk.  Ram/table still see everything.
        active = np.zeros(len(vertex_panel), dtype=bool)
        for bend_id in step["bend_ids"]:
            active |= np.asarray(vertex_bend) == bend_id + 1
        zone_faces = active[fine_faces].any(axis=1)

        die_stats = tooling.get("dies", {}).get(setup["die_id"]) or {}
        v_width = die_stats.get("v_width")

        count = max(int(math.ceil(phi_target / phi_step)), 1)
        for phi in np.linspace(phi_target / count, phi_target, count):
            theta = np.array(theta_before)
            for bend_id, sign in signs.items():
                theta[bend_id] = sign * phi
            posed = foldmesh.pose_vertices(
                graph, flat_verts, vertex_panel, vertex_bend, theta)
            machine = kinematics.rotation_x(lift_sign * phi / 2.0) @ placement
            posed = posed @ machine[:3, :3].T + machine[:3, 3]
            # the part sinks into the V as the wings pivot on the die
            # shoulders; the punch/ram ride down with it
            descent = foldmesh.stroke_descent(thickness, v_width, phi)
            posed[:, 2] -= descent

            shift = _punch_shift(thickness, phi) - _punch_shift(thickness, 0) \
                - descent
            hits = _collisions(posed, fine_faces, tools, shift, zone_faces,
                               mm)
            for name, faces in hits:
                xs = posed[fine_faces[faces].ravel(), 0]
                step_report["hits"].append({
                    "phi_deg": float(math.degrees(phi)),
                    "tool": name,
                    "x": [float(xs.min()), float(xs.max())],
                    "face_count": int(len(faces)),
                })
                step_report["clean"] = False
                hit_faces.update(int(f) for f in faces)
        if not step_report["clean"]:
            report["clean"] = False
            logger.info(f"mesh check: step {index} collides "
                        f"({len(step_report['hits'])} phi samples)")
        report["steps"].append(step_report)

    report["hit_faces"] = hit_faces
    return report


def _collisions(posed, fine_faces, tools, punch_shift, zone_faces, mm):
    """Triangle-pair hits of the posed part against every tool mesh."""
    from meshlib import mrmeshnumpy as mn

    # prefilter part triangles to the tool neighborhood before building
    # the meshlib mesh — the dominant cost on big parts
    low = np.minimum.reduce([t["bounds"][0] for t in tools])
    high = np.maximum.reduce([t["bounds"][1] for t in tools])
    low = low - np.array([0.0, 0.0, abs(punch_shift)])
    high = high + np.array([0.0, 0.0, abs(punch_shift)])
    inside = np.all((posed >= low - 1.0) & (posed <= high + 1.0), axis=1)
    face_touch = inside[fine_faces].any(axis=1)

    parts = {}
    for exclude_zone in (True, False):
        selected = np.nonzero(
            face_touch & ~zone_faces if exclude_zone else face_touch)[0]
        parts[exclude_zone] = (selected, mn.meshFromFacesVerts(
            fine_faces[selected].astype(np.int32),
            posed.astype(np.float32)) if len(selected) else None)

    results = []
    for tool in tools:
        selected, part = parts[tool["name"] in ("punch", "die")]
        if part is None:
            continue
        rigid = None
        if tool["moves"] and abs(punch_shift) > 1e-12:
            rigid = mm.AffineXf3f.translation(
                mm.Vector3f(0.0, 0.0, float(punch_shift)))
        pairs = mm.findCollidingTriangles(
            mm.MeshPart(part), mm.MeshPart(tool["mesh"]), rigidB2A=rigid)
        if len(pairs):
            faces = np.unique([int(p.aFace) for p in pairs])
            results.append((tool["name"], selected[faces]))
    return results


def _punch_shift(thickness, phi):
    """machine.ToolProfile.transformed_profile's punch tip height."""
    return (thickness / 2.0) / max(
        math.cos(min(abs(phi), 2.6) / 2.0), 0.2)


def _setup_tools(setup, tooling, thickness, eps):
    """Extruded meshlib meshes of one setup's installed tooling, at the
    phi = 0 position; the punch/ram entries move with the stroke."""
    from meshlib import mrmeshnumpy as mn

    entries = []

    def add(name, profile, spans, dz, moves):
        inset = _inset(profile, eps)
        if inset is None or not spans:
            return
        for x0, x1 in spans:
            verts, faces = _extrude(inset, float(x0), float(x1), dz)
            entries.append({
                "name": name,
                "mesh": mn.meshFromFacesVerts(faces, verts),
                "bounds": (verts.min(axis=0).astype(float),
                           verts.max(axis=0).astype(float)),
                "moves": moves,
            })

    punch = tooling.get("punches", {}).get(setup["punch_id"])
    die = tooling.get("dies", {}).get(setup["die_id"])
    machine = tooling.get("machine", {})
    punch_spans = _spans(setup.get("punch"))
    die_spans = _spans(setup.get("die"))
    if punch:
        add("punch", punch["profile"], punch_spans,
            _punch_shift(thickness, 0.0), True)
        if machine.get("ram_profile") and punch_spans:
            min_z = min(point[1] for point in punch["profile"])
            frame_span = [(min(x for x, _ in punch_spans) - FRAME_PAD,
                           max(x for _, x in punch_spans) + FRAME_PAD)]
            add("ram", machine["ram_profile"], frame_span,
                _punch_shift(thickness, 0.0) + punch["height"] + min_z, True)
    if die:
        add("die", die["profile"], die_spans, -thickness / 2.0, False)
        if machine.get("table_profile") and die_spans:
            frame_span = [(min(x for x, _ in die_spans) - FRAME_PAD,
                           max(x for _, x in die_spans) + FRAME_PAD)]
            add("table", machine["table_profile"], frame_span,
                -thickness / 2.0 - die["height"], False)
    return entries


def _spans(placement):
    spans = []
    for run in (placement or {}).get("runs", []):
        for section in run["sections"]:
            spans.append((section["x_start"], section["x_end"]))
    return spans


def _inset(profile, eps):
    """YZ profile shrunk by eps (largest piece; None when it vanishes)."""
    from shapely.geometry import Polygon

    polygon = Polygon(profile)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    shrunk = polygon.buffer(-eps, join_style=2)
    if shrunk.is_empty:
        return None
    if shrunk.geom_type == "MultiPolygon":
        shrunk = max(shrunk.geoms, key=lambda g: g.area)
    ring = np.asarray(shrunk.exterior.coords[:-1], dtype=float)
    # CCW for a consistent outward extrusion orientation
    area = 0.5 * float(np.sum(
        ring[:, 0] * np.roll(ring[:, 1], -1)
        - np.roll(ring[:, 0], -1) * ring[:, 1]))
    return ring if area > 0 else ring[::-1]


def _extrude(profile, x0, x1, dz):
    """Watertight extrusion of a YZ polygon along X: (verts f32, faces i32).

    Caps are ear-clip triangulated so concave profiles (goosenecks, V
    dies) come out correctly.
    """
    count = len(profile)
    verts = np.empty((2 * count, 3), dtype=np.float32)
    verts[:count, 0] = x0
    verts[count:, 0] = x1
    verts[:count, 1] = profile[:, 0]
    verts[count:, 1] = profile[:, 0]
    verts[:count, 2] = profile[:, 1] + dz
    verts[count:, 2] = profile[:, 1] + dz

    faces = []
    cap = _ear_clip(profile)
    for a, b, c in cap:
        faces.append((a, c, b))                        # x0 cap, inward -X
        faces.append((count + a, count + b, count + c))  # x1 cap, +X
    for i in range(count):
        j = (i + 1) % count
        faces.append((i, j, count + j))
        faces.append((i, count + j, count + i))
    return verts, np.asarray(faces, dtype=np.int32)


def _ear_clip(polygon):
    """Triangle index list of a simple CCW polygon (small N; O(N^2))."""
    indices = list(range(len(polygon)))
    triangles = []
    guard = 0
    while len(indices) > 3 and guard < 10000:
        guard += 1
        ear_found = False
        for k in range(len(indices)):
            a = polygon[indices[k - 1]]
            b = polygon[indices[k]]
            c = polygon[indices[(k + 1) % len(indices)]]
            cross = (b[0] - a[0]) * (c[1] - a[1]) \
                - (b[1] - a[1]) * (c[0] - a[0])
            if cross <= 1e-12:
                continue
            corner_ids = (indices[k - 1], indices[k],
                          indices[(k + 1) % len(indices)])
            if _any_point_inside(polygon, indices, corner_ids, a, b, c):
                continue
            triangles.append(corner_ids)
            indices.pop(k)
            ear_found = True
            break
        if not ear_found:      # degenerate leftovers: fan the rest
            break
    if len(indices) >= 3:
        for k in range(1, len(indices) - 1):
            triangles.append((indices[0], indices[k], indices[k + 1]))
    return triangles


def _any_point_inside(polygon, indices, corner_ids, a, b, c):
    for other in indices:
        if other in corner_ids:
            continue
        point = polygon[other]
        d1 = (b[0] - a[0]) * (point[1] - a[1]) \
            - (b[1] - a[1]) * (point[0] - a[0])
        d2 = (c[0] - b[0]) * (point[1] - b[1]) \
            - (c[1] - b[1]) * (point[0] - b[0])
        d3 = (a[0] - c[0]) * (point[1] - c[1]) \
            - (a[1] - c[1]) * (point[0] - c[0])
        if d1 >= -1e-12 and d2 >= -1e-12 and d3 >= -1e-12:
            return True
    return False
