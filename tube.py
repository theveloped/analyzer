"""Tube / profile classification over the AAG: straight constant-section
round, rectangular and square profiles.

Port of instapart's analyse.py onto the persisted AAG artifact. Works on
the C1 component around the largest face with C2 (same-curvature smooth)
groups contracted to single nodes (grouped graph), instapart's
order-independent rewrite: one node per multi-face bend / cylinder ring.

- one node per side, curved: round tube (radii from mean curvature,
  length from vertex extent along the cylinder axis);
- one node per side, planar: a flat sheet, not a tube;
- more nodes than edges: a bent open profile, not a tube;
- otherwise: rectangular/square tube via the four planar-normal clusters
  (custom cosine clusterer — the sklearn KMeans replacement instapart
  documents), wall spacing from projected mid-points, corner radii from
  the curved groups.

The optional unroll reuses the sheet unfolder on the outer shell: the BFS
spanning tree cuts the closed ring once, the cap rims chain into the two
long edges of the unrolled plate and wall holes come along as closed
loops.
"""

import math

import numpy as np
from loguru import logger

import aag as aag_module
import pipeline

ROLE_OTHER = 0
ROLE_OUTER = 1
ROLE_INNER = 2
ROLE_END = 3
ROLE_NAMES = ["other", "outer shell", "inner shell", "end cut"]


def grouped_graph(graph, base_face, labels=None):
    """C1 component around ``base_face`` with C2 groups contracted.

    Returns (grouped, component): grouped is a networkx Graph whose nodes
    are group leaders with a ``members`` list; ``labels`` (optional dict)
    accumulates member -> leader for dedup across sides.
    """
    import networkx as nx

    c1 = graph.C1_faces
    component = nx.node_connected_component(c1, int(base_face))
    subgraph = c1.subgraph(component)

    c2_graph = nx.Graph()
    c2_graph.add_nodes_from(component)
    c2_graph.add_edges_from(
        (a, b) for a, b, continuity in subgraph.edges(data="continuity")
        if abs(continuity) == 2 and a != b)

    grouped = nx.Graph()
    leader_of = {}
    for group in nx.connected_components(c2_graph):
        members = sorted(group)
        leader = int(base_face) if int(base_face) in group else members[0]
        grouped.add_node(leader, members=members)
        for member in members:
            leader_of[member] = leader
            if labels is not None:
                labels[member] = leader
    for a, b in subgraph.edges():
        if leader_of[a] != leader_of[b]:
            grouped.add_edge(leader_of[a], leader_of[b])
    return grouped, component


def cluster_directions(normals, n_clusters=4, tolerance_deg=2.0):
    """Group near-identical direction vectors (KMeans replacement).

    Raises when the normals do not form exactly ``n_clusters`` groups —
    the 'could not fit a rectangular section' failure mode.
    """
    cos_tol = math.cos(math.radians(tolerance_deg))
    centers = []
    labels = []
    for normal in normals:
        for index, center in enumerate(centers):
            direction = center[:3] / (np.linalg.norm(center[:3]) or 1.0)
            if float(np.dot(normal, direction)) >= cos_tol:
                center[:3] += normal
                center[3] += 1
                labels.append(index)
                break
        else:
            centers.append(np.array([*normal, 1.0]))
            labels.append(len(centers) - 1)
    if len(centers) != n_clusters:
        raise ValueError(f"expected {n_clusters} normal directions, "
                         f"found {len(centers)}")
    directions = [center[:3] / center[3] for center in centers]
    return labels, directions


def _members(grouped):
    for leader in grouped.nodes():
        for member in grouped.nodes[leader]["members"]:
            yield member


def _rect_parameters(graph, grouped, x_axis=None):
    """Width/height/corner radius/axes/length of one rectangular shell."""
    normals = []
    mid_points = []
    corner_radii = []
    faces = []
    for member in _members(grouped):
        faces.append(member)
        if graph.face_convexity[member] == aag_module.FACE_PLANAR:
            normal = graph.face_normal[member]
            point = graph.face_point[member]
            if not (np.isfinite(normal).all() and np.isfinite(point).all()):
                raise ValueError("undefined face normal on the shell")
            normals.append(normal)
            mid_points.append(point)
        else:
            corner_radii.append(abs(0.5 / graph.face_curvature[member]))

    labels, directions = cluster_directions(normals, n_clusters=4)
    vectors = [d / np.linalg.norm(d) for d in directions]

    if x_axis is None:
        x_axis = vectors[0]
    angle_tol = math.cos(math.radians(1.0))
    width_labels = []
    height_labels = []
    y_axis = None
    for i, vector in enumerate(vectors):
        if abs(float(np.dot(x_axis, vector))) >= angle_tol:
            width_labels.append(i)
        elif y_axis is None:
            y_axis = vector
            height_labels.append(i)
        elif abs(float(np.dot(y_axis, vector))) >= angle_tol:
            height_labels.append(i)
        else:
            raise ValueError("could not fit a rectangular section")
    if len(width_labels) != 2 or len(height_labels) != 2:
        raise ValueError("could not fit a rectangular section")

    positions = [[] for _ in vectors]
    for label, point in zip(labels, mid_points):
        axis = (vectors[width_labels[0]] if label in width_labels
                else vectors[height_labels[0]])
        positions[label].append(float(np.dot(point, axis)))
    values = [float(np.mean(p)) if p else 0.0 for p in positions]

    z_axis = np.cross(vectors[width_labels[0]], vectors[height_labels[0]])
    z_axis /= np.linalg.norm(z_axis)
    span = aag_module.axial_span(graph, faces, np.zeros(3), z_axis)

    return {
        "width": abs(values[width_labels[0]] - values[width_labels[1]]),
        "height": abs(values[height_labels[0]] - values[height_labels[1]]),
        "corner_radius": float(np.mean(corner_radii)) if corner_radii else 0.0,
        "x_axis": vectors[width_labels[0]],
        "y_axis": vectors[height_labels[0]],
        "z_axis": z_axis,
        "length": span[1] - span[0],
    }


def _round_parameters(graph, grouped_a, grouped_b, surface_params):
    """Radii, axis and length of a round tube from its two shells."""
    def mean_radius(grouped):
        radii = [abs(0.5 / graph.face_curvature[m])
                 for m in _members(grouped) if graph.face_curvature[m]]
        return float(np.mean(radii)) if radii else 0.0

    radius_a = mean_radius(grouped_a)
    radius_b = mean_radius(grouped_b)

    axis = None
    for member in _members(grouped_a):
        params = surface_params[member] if member < len(surface_params) else None
        if params and params.get("type") == "cylinder":
            axis = np.array(params["axis"], dtype=float)
            break
    if axis is None:
        # freeform (bspline) cylinders carry no analytic axis — every shell
        # normal is perpendicular to it, so the axis is the null direction
        # of the normal bundle (smallest right singular vector)
        members = list(_members(grouped_a)) + list(_members(grouped_b))
        normals = graph.face_normal[members]
        normals = normals[np.isfinite(normals).all(axis=1)]
        if len(normals) >= 2:
            _, singular, vectors = np.linalg.svd(normals)
            if singular[-1] < 0.1 * max(singular[0], 1e-12):
                axis = vectors[-1]
    if axis is None:
        return None
    faces = list(_members(grouped_a))
    span = aag_module.axial_span(graph, faces, np.zeros(3), axis)
    return {"radius_a": radius_a, "radius_b": radius_b, "axis": axis,
            "length": span[1] - span[0]}


def analyse_profile(workdir, *, unroll=True, k_factor=0.5, progress=None):
    """Classify a straight constant-section profile; optional unroll.

    Returns the analyzer result triple. Stats carry a verdict
    ("round" | "rectangular" | "square" | "none") with reasons.
    """
    import json
    import os

    graph = aag_module.load_aag(workdir)
    with open(os.path.join(workdir, pipeline.BREP_META_FILE)) as f:
        brep_meta = json.load(f)
    surface_params = brep_meta["surface_params"]
    if len(surface_params) != graph.face_count:
        raise ValueError("brep_meta.json face count does not match the AAG — "
                         "re-run prep/mesh and prep/aag from the same STEP")

    if progress is not None:
        progress(0.1, "grouping shells")

    order = np.argsort(graph.face_area)[::-1]
    base = int(order[0])
    labels = {}
    grouped_a, component_a = grouped_graph(graph, base, labels)

    other = None
    for candidate in order[1:]:
        candidate = int(candidate)
        if candidate in labels:
            continue
        if (int(graph.face_convexity[base])
                == -int(graph.face_convexity[candidate])):
            other = candidate
            break

    verdict = "none"
    reasons = []
    stats = {}
    roles = np.zeros(graph.face_count, dtype=np.uint8)

    grouped_b = None
    component_b = set()
    if other is None:
        reasons.append("no opposite shell with mirrored convexity")
    else:
        grouped_b, component_b = grouped_graph(graph, other, labels)

    nodes_a = grouped_a.number_of_nodes()
    edges_a = grouped_a.number_of_edges()

    if grouped_b is not None and nodes_a == 1:
        if graph.face_convexity[base] == aag_module.FACE_PLANAR:
            reasons.append("flat sheet — use the sheet_metal process")
        else:
            round_params = _round_parameters(graph, grouped_a, grouped_b,
                                             surface_params)
            if round_params is None:
                reasons.append("curved shell without a cylindrical face")
            else:
                verdict = "round"
                inner = min(round_params["radius_a"], round_params["radius_b"])
                outer = max(round_params["radius_a"], round_params["radius_b"])
                stats = {
                    "inner_radius": inner,
                    "outer_radius": outer,
                    "thickness": outer - inner,
                    "width": 2 * outer,
                    "height": 2 * outer,
                    "length": round_params["length"],
                    "axis": [float(c) for c in round_params["axis"]],
                }
    elif grouped_b is not None:
        if nodes_a > edges_a:
            reasons.append("open bent profile — use the sheet_metal process")
        else:
            try:
                params_a = _rect_parameters(graph, grouped_a)
                params_b = _rect_parameters(graph, grouped_b,
                                            x_axis=params_a["x_axis"])
                verdict = "rectangular"
                width = max(params_a["width"], params_b["width"])
                height = max(params_a["height"], params_b["height"])
                stats = {
                    "inner_radius": min(params_a["corner_radius"],
                                        params_b["corner_radius"]),
                    "outer_radius": max(params_a["corner_radius"],
                                        params_b["corner_radius"]),
                    "thickness": abs(params_a["width"]
                                     - params_b["width"]) / 2,
                    "width": width,
                    "height": height,
                    "length": max(params_a["length"], params_b["length"]),
                    "axis": [float(c) for c in params_a["z_axis"]],
                }
                if abs(width - height) < 1e-3:
                    verdict = "square"
            except ValueError as exc:
                reasons.append(f"no constant rectangular section ({exc})")

    # roles: outer = larger total area shell (round: larger radius side)
    if verdict != "none":
        area_a = float(graph.face_area[list(component_a)].sum())
        area_b = float(graph.face_area[list(component_b)].sum())
        outer_component, inner_component = (
            (component_a, component_b) if area_a >= area_b
            else (component_b, component_a))
        roles[list(outer_component)] = ROLE_OUTER
        roles[list(inner_component)] = ROLE_INNER
        interior = graph.interior_edges()
        shell = roles > 0
        for face_a, face_b in graph.edge_faces[interior]:
            if shell[face_a] != shell[face_b]:
                roles[face_b if shell[face_a] else face_a] = ROLE_END

    arrays = {}
    field_meta = {}
    entities = None
    if verdict != "none" and unroll:
        if progress is not None:
            progress(0.6, "unrolling the outer shell")
        try:
            entities, unroll_arrays, unroll_meta = _unroll(
                workdir, graph, base if roles[base] == ROLE_OUTER
                else other, stats.get("thickness", 0.0), k_factor)
            arrays.update(unroll_arrays)
            field_meta.update(unroll_meta)
        except Exception as exc:
            logger.warning(f"unroll failed: {exc}")
            reasons.append(f"unroll failed ({exc})")

    brep_ids = np.load(os.path.join(workdir, pipeline.BREP_FACES_FILE))
    arrays["face_role"] = roles[brep_ids].astype("<u1")
    field_meta["face_role"] = {
        "kind": "tube_face_role", "association": "face",
        "role": "category", "dtype": "u1", "labels": ROLE_NAMES}

    stats = {"verdict": verdict, "reasons": reasons, **stats}
    if entities is not None:
        stats["entities"] = entities["entities"]
        stats["flat_size"] = entities["flat_size"]
        stats["hole_count"] = entities["hole_count"]
    logger.info(f"tube profile: {verdict} "
                + (f"{stats.get('width', 0):.1f} x {stats.get('height', 0):.1f}"
                   f" x {stats.get('thickness', 0):.1f} "
                   f"L{stats.get('length', 0):.1f}" if verdict != "none"
                   else f"{reasons}"))
    if progress is not None:
        progress(1.0, "profile classified")
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}


def _unroll(workdir, graph, outer_face, thickness, k_factor):
    """Unroll the outer shell into the cut pattern.

    The BFS spanning tree of the closed shell ring cuts it once; the two
    cap rims chain into open polylines (the long edges of the unrolled
    plate) and wall holes arrive as closed loops.
    """
    import brep
    import sheet
    import unfold as unfold_module

    shape = brep.load_step_shape_cached(pipeline.source_step_path(workdir))
    component = aag_module.get_connected_subgraph(graph, outer_face,
                                                  ignore_complex=True)
    nodes = sorted(int(n) for n in component.nodes())
    unfolder = unfold_module.Unfolder(graph, shape)
    transformations, _ = unfolder.unfold(nodes, int(outer_face), thickness,
                                         k_factor=k_factor)
    loops, _ = unfolder.extract_wires(nodes, thickness, transformations,
                                      k_factor=k_factor)

    open_loops = [loop for loop in loops if not loop["closed"]]
    holes = [loop for loop in loops if loop["closed"]]

    # chain the two open rim polylines into the outer contour
    if len(open_loops) == 2:
        first, second = open_loops
        a = first["points"]
        b = second["points"]
        if (np.linalg.norm(a[-1] - b[0])
                > np.linalg.norm(a[-1] - b[-1])):
            b = b[::-1]
        contour_points = np.vstack([a, b])
    elif len(open_loops) == 1:
        contour_points = open_loops[0]["points"]
    else:
        contour_points = max(loops, key=lambda l: len(l["points"]))["points"]
        holes = [loop for loop in loops
                 if loop["points"] is not contour_points]

    origin = contour_points.min(axis=0)
    size = contour_points.max(axis=0) - origin

    def as_path(points):
        path = [[float(x), float(y), 0.0] for x, y in points[:-1]]
        path.append([float(points[-1][0]), float(points[-1][1])])
        return path

    contour_points = contour_points - origin
    hole_points = [hole["points"] - origin for hole in holes]
    entities = {
        "contour": as_path(np.vstack([contour_points, contour_points[:1]])),
        "holes": [as_path(points) for points in hole_points],
        "bend_lines": [],
    }
    outline = sheet._segments_from_points([contour_points])
    hole_lines = sheet._segments_from_points(hole_points)
    arrays = {"outline_lines": outline, "hole_lines": hole_lines}
    field_meta = {
        "outline_lines": {"kind": "flat_pattern", "association": "none",
                          "role": "lines", "dtype": "f4",
                          "length": int(outline.size),
                          "segments": int(outline.size // 6)},
        "hole_lines": {"kind": "flat_pattern", "association": "none",
                       "role": "lines", "dtype": "f4",
                       "length": int(hole_lines.size),
                       "segments": int(hole_lines.size // 6)},
    }
    payload = {"entities": entities,
               "flat_size": [float(size[0]), float(size[1])],
               "hole_count": len(holes)}
    return payload, arrays, field_meta
