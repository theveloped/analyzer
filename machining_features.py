"""Rule-based CNC machining-feature recognition (holes family + pockets).

Port of instapart's features.recognize_cavities onto the analyzer's
persisted AAG artifact (aag.npz) + brep_meta.json — no OCC at analysis
time. Concave C2 (same-curvature smooth) face groups are classified by
their analytic surface and boundary-loop topology:

- coaxial cylinder/cone stacks with two open loops -> THROUGH_HOLE,
  one capped end -> BLIND_HOLE, two distinct radii -> COUNTERBORE,
  cone + cylinder -> COUNTERSINK;
- closed freeform concave groups with no capped loop -> best-effort POCKET.

A boundary loop counts as "capped" when its minimum dihedral angle is <= 0
(a convex or smooth rim — the hole opens out or blends away there); a
concave rim (angle > 0) means material closes the loop (a floor).

All face ids are BREP face ids (brep_faces.npy / aag.npz convention).
"""

import json
import math
import os
from dataclasses import dataclass, field

import numpy as np
from loguru import logger

import aag as aag_module
import pipeline

# feature category codes, index == code in the per-face field
FEATURE_TYPES = ["none", "through_hole", "blind_hole", "counterbore",
                 "countersink", "pocket"]

TOLLERANCE = 1e-6


@dataclass
class _Record:
    faces: set
    kind: str                      # 'CYL' | 'CONE' | 'OTHER'
    radius: float = None
    axis_loc: np.ndarray = None
    axis_dir: np.ndarray = None
    semi_angle: float = None
    loop_count: int = 0
    caps: list = field(default_factory=list)


@dataclass
class _Stack:
    faces: set
    members: list
    axis_loc: np.ndarray
    axis_dir: np.ndarray


def _face_surface(index, surface_types, surface_params, curvature):
    """(kind, radius, axis_loc, axis_dir, semi_angle) of one BREP face."""
    params = surface_params[index] if index < len(surface_params) else None
    kind = surface_types[index] if index < len(surface_types) else "other"
    if kind == "cylinder" and params:
        radius = params.get("radius")
        if radius is None and abs(curvature[index]) > TOLLERANCE:
            # legacy brep_meta.json without radii: cylinder mean curvature
            # is 1/(2r)
            radius = 1.0 / (2.0 * abs(curvature[index]))
        return ("CYL", radius, np.array(params["point"], dtype=float),
                np.array(params["axis"], dtype=float), None)
    if kind == "cone" and params:
        return ("CONE", None, np.array(params["apex"], dtype=float),
                np.array(params["axis"], dtype=float),
                float(params["alpha"]))
    return ("OTHER", None, None, None, None)


_group_vertices = aag_module.group_vertices
_axial_span = aag_module.axial_span


def _group_loops(graph, group):
    """Boundary loops of a face group.

    Returns (loop_count, caps, neighbors): caps is a bool per loop (True
    when the loop's minimum dihedral angle <= 0 — convex or smooth rim),
    neighbors the set of face ids bordering the group. Unlike instapart's
    collapsed C0 graph, every BREP edge participates, so parallel edges
    between one face pair close their loops correctly.
    """
    import networkx as nx

    group_arr = np.asarray(sorted(group))
    interior = graph.interior_edges()
    in_group = np.isin(graph.edge_faces, group_arr)
    boundary = interior & (in_group[:, 0] != in_group[:, 1])

    neighbor_side = np.where(in_group[:, 0], graph.edge_faces[:, 1],
                             graph.edge_faces[:, 0])
    neighbors = set(int(f) for f in np.unique(neighbor_side[boundary]))

    edge_ids = np.flatnonzero(boundary)
    if not len(edge_ids):
        return 0, [], neighbors

    loops = nx.MultiGraph()
    for edge_id in edge_ids:
        first, last = graph.edge_vertices[edge_id]
        if first < 0 or last < 0:
            continue
        loops.add_edge(int(first), int(last), key=int(edge_id),
                       angle=float(graph.edge_angle[edge_id]))

    caps = []
    for loop_nodes in nx.connected_components(loops):
        angles = [data["angle"]
                  for _, _, data in loops.subgraph(loop_nodes).edges(data=True)
                  if np.isfinite(data["angle"])]
        caps.append((min(angles) <= 0.0) if angles else False)
    return len(caps), caps, neighbors


def _collect_records(graph, surface_types, surface_params):
    """Concave C2 groups -> cylinder/cone records + pocket candidates."""
    records = []
    pocket_candidates = []

    for label in np.unique(graph.c2_group):
        group = set(int(f) for f in np.flatnonzero(graph.c2_group == label))
        representative = next(iter(group))
        if graph.face_convexity[representative] != aag_module.FACE_CONCAVE:
            continue

        kind, radius, axis_loc, axis_dir, semi_angle = (
            "OTHER", None, None, None, None)
        for face in sorted(group):
            surface = _face_surface(face, surface_types, surface_params,
                                    graph.face_curvature)
            if surface[0] in ("CYL", "CONE"):
                kind, radius, axis_loc, axis_dir, semi_angle = surface
                break

        loop_count, caps, _ = _group_loops(graph, group)
        record = _Record(faces=group, kind=kind, radius=radius,
                         axis_loc=axis_loc, axis_dir=axis_dir,
                         semi_angle=semi_angle, loop_count=loop_count,
                         caps=caps)

        if kind == "OTHER":
            pocket_candidates.append(record)
            continue
        # single-loop concave cylinders (threads, fillets, lead-ins) are the
        # noisy tail; cones are kept regardless so they can fold into a
        # coaxial stack and yield a countersink
        if kind == "CYL" and loop_count != 2:
            continue
        records.append(record)

    return records, pocket_candidates


def _coaxial(graph, stack, record, angle_tol, dist_tol):
    if abs(float(stack.axis_dir @ record.axis_dir)) < angle_tol:
        return False
    offset = record.axis_loc - stack.axis_loc
    distance = float(np.linalg.norm(np.cross(offset, stack.axis_dir)))
    if distance > dist_tol:
        return False
    stack_min, stack_max = _axial_span(graph, stack.faces,
                                       stack.axis_loc, stack.axis_dir)
    record_min, record_max = _axial_span(graph, record.faces,
                                         stack.axis_loc, stack.axis_dir)
    return not (record_min > stack_max + dist_tol
                or stack_min > record_max + dist_tol)


def _merge_coaxial(graph, records, angle_tol, dist_tol):
    stacks = []
    for record in records:
        for stack in stacks:
            if _coaxial(graph, stack, record, angle_tol, dist_tol):
                stack.faces |= record.faces
                stack.members.append(record)
                break
        else:
            stacks.append(_Stack(faces=set(record.faces), members=[record],
                                 axis_loc=record.axis_loc,
                                 axis_dir=record.axis_dir))
    return stacks


def _classify_stack(graph, stack):
    """A dimensioned hole feature dict for the stack, or None."""
    cyls = [m for m in stack.members if m.kind == "CYL"
            and m.radius is not None]
    cones = [m for m in stack.members if m.kind == "CONE"]
    radii = sorted({round(m.radius, 4) for m in cyls})

    feature_type = None
    if cones and cyls:
        feature_type = "countersink"
    elif len(radii) >= 2:
        feature_type = "counterbore"
    elif len(cyls) == 1:
        cap_count = sum(1 for cap in cyls[0].caps if cap)
        if cap_count >= 2:
            feature_type = "through_hole"
        elif cap_count == 1:
            feature_type = "blind_hole"
    if feature_type is None:
        return None

    span = _axial_span(graph, stack.faces, stack.axis_loc, stack.axis_dir)
    feature = {
        "type": feature_type,
        "faces": sorted(int(f) for f in stack.faces),
        "diameter": 2.0 * min(m.radius for m in cyls),
        "axis": [float(c) for c in stack.axis_dir],
        "depth": span[1] - span[0],
    }
    if feature_type == "counterbore":
        feature["counterbore_diameter"] = 2.0 * max(m.radius for m in cyls)
    if feature_type == "countersink":
        feature["angle"] = math.degrees(cones[0].semi_angle)
    return feature


def _mean_normal(graph, faces):
    normals = graph.face_normal[sorted(faces)]
    normals = normals[np.isfinite(normals).all(axis=1)]
    if not len(normals):
        return None
    total = normals.sum(axis=0)
    length = np.linalg.norm(total)
    if length >= TOLLERANCE:
        return total / length
    return normals[0] / np.linalg.norm(normals[0])


def _pocket_features(graph, pocket_candidates):
    """Best-effort POCKET emission for closed freeform-concave groups."""
    features = []
    for record in pocket_candidates:
        if record.loop_count < 1 or not record.caps or any(record.caps):
            continue
        axis_dir = _mean_normal(graph, record.faces)
        if axis_dir is None:
            continue
        vertex_ids = _group_vertices(graph, record.faces)
        if not len(vertex_ids):
            continue
        axis_loc = graph.vertex_points[vertex_ids[0]]
        span = _axial_span(graph, record.faces, axis_loc, axis_dir)
        features.append({
            "type": "pocket",
            "faces": sorted(int(f) for f in record.faces),
            "diameter": None,
            "axis": [float(c) for c in axis_dir],
            "depth": span[1] - span[0],
        })
    return features


def recognize_features(workdir, *, axis_angle_tol=1.0, axis_dist_tol=1e-2,
                       include_pockets=True, progress=None):
    """Recognize machining features and express them over the fine mesh.

    Returns the analyzer result triple: stats (counts + the dimensioned
    feature list), per-fine-face arrays (feature_category u1, feature_id
    u4 with index+1, 0 = none) and their field_meta.
    """
    graph = aag_module.load_aag(workdir)
    if progress is not None:
        progress(0.1, "scanning concave groups")

    meta_path = os.path.join(workdir, pipeline.BREP_META_FILE)
    with open(meta_path) as f:
        brep_meta = json.load(f)
    surface_types = brep_meta["surface_types"]
    surface_params = brep_meta["surface_params"]
    if len(surface_types) != graph.face_count:
        raise ValueError("brep_meta.json face count does not match the AAG — "
                         "re-run prep/mesh and prep/aag from the same STEP")

    records, pocket_candidates = _collect_records(
        graph, surface_types, surface_params)
    if progress is not None:
        progress(0.5, f"stacking {len(records)} coaxial records")
    stacks = _merge_coaxial(graph, records,
                            math.cos(math.radians(axis_angle_tol)),
                            axis_dist_tol)

    features = []
    for stack in stacks:
        feature = _classify_stack(graph, stack)
        if feature is not None:
            features.append(feature)
    if include_pockets:
        features.extend(_pocket_features(graph, pocket_candidates))
    features.sort(key=lambda f: (f["type"], -(f["diameter"] or 0)))
    for index, feature in enumerate(features):
        feature["id"] = index + 1

    # broadcast to fine faces
    brep_ids = np.load(os.path.join(workdir, pipeline.BREP_FACES_FILE))
    category_by_face = np.zeros(graph.face_count, dtype=np.uint8)
    id_by_face = np.zeros(graph.face_count, dtype=np.uint32)
    for feature in features:
        code = FEATURE_TYPES.index(feature["type"])
        for face in feature["faces"]:
            category_by_face[face] = code
            id_by_face[face] = feature["id"]
    arrays = {
        "feature_category": category_by_face[brep_ids].astype("<u1"),
        "feature_id": id_by_face[brep_ids].astype("<u4"),
    }
    field_meta = {
        "feature_category": {"kind": "feature_category", "association": "face",
                             "role": "category", "dtype": "u1",
                             "types": FEATURE_TYPES},
        "feature_id": {"kind": "feature_id", "association": "face",
                       "role": "data", "dtype": "u4"},
    }

    counts = {}
    for feature in features:
        counts[feature["type"]] = counts.get(feature["type"], 0) + 1
    logger.info(f"machining features: {counts or 'none'}")
    stats = {"counts": counts, "features": features}
    if progress is not None:
        progress(1.0, "features classified")
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}
