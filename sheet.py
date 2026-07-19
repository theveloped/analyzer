"""Sheet-metal recognition over the AAG: base/opposite skins, thickness,
bend faces and per-face roles.

Port of the detection half of instapart's sheet pipeline (flatten.py
get_sheet_base + the C1-component reasoning auto.py builds on). The unfold
and flat-pattern extraction live in unfold.py / the flat_pattern analysis —
this module answers "is this a sheet-metal part, which faces play which
role, and how thick is it".

Face roles (u1 codes, broadcast to fine faces via brep_faces.npy):
0 other/feature, 1 base skin, 2 opposite skin, 3 bend, 4 wall/edge face.
"""

import os

import numpy as np
from loguru import logger

import aag as aag_module
import pipeline

ROLE_OTHER = 0
ROLE_BASE = 1
ROLE_OPPOSITE = 2
ROLE_BEND = 3
ROLE_WALL = 4
ROLE_FEATURE = 5
ROLE_NAMES = ["other", "base", "opposite", "bend", "wall", "feature"]

SQUARE_TOL = np.pi / 180.0
TOLLERANCE = 1e-6


def _skin_nodes(graph, base_index, opposite_index):
    """(nodes_a, nodes_b): the two unfoldable skin subgraphs (complex faces
    excluded, instapart's ignore_complex) — side A grows from the base."""
    nodes_a = set(int(n) for n in aag_module.get_connected_subgraph(
        graph, base_index, ignore_complex=True).nodes())
    nodes_b = set()
    if opposite_index is not None:
        nodes_b = set(int(n) for n in aag_module.get_connected_subgraph(
            graph, opposite_index, ignore_complex=True).nodes())
    return nodes_a, nodes_b


def _feature_extrema(graph, base_index, faces):
    """(min, max) distance of the face group's vertices along the base
    face's outward normal, always including 0 (instapart's extrema)."""
    base_point = graph.face_point[int(base_index)]
    base_normal = graph.face_normal[int(base_index)]
    if not (np.isfinite(base_point).all() and np.isfinite(base_normal).all()):
        return 0.0, 0.0
    vertex_ids = aag_module.group_vertices(graph, faces)
    if not len(vertex_ids):
        return 0.0, 0.0
    distances = (graph.vertex_points[vertex_ids] - base_point) @ base_normal
    return min(0.0, float(distances.min())), max(0.0, float(distances.max()))


def _feature_loop(graph, component, side):
    """Boundary-loop statistics of a feature component against one skin:
    (convex, concave, smooth, square, base_faces, loop_edge_ids)."""
    convex = concave = smooth = square = 0
    base_faces = []
    loop_edges = []
    component = set(component)
    side = set(side)
    interior = graph.interior_edges()
    in_component = np.isin(graph.edge_faces, sorted(component))
    for edge_index in np.flatnonzero(
            interior & (in_component[:, 0] != in_component[:, 1])):
        face_a, face_b = (int(f) for f in graph.edge_faces[edge_index])
        outside = face_b if face_a in component else face_a
        if outside not in side:
            continue
        loop_edges.append(int(edge_index))
        if outside not in base_faces:
            base_faces.append(outside)
        convexity = int(graph.edge_convexity[edge_index])
        angle = float(graph.edge_angle[edge_index])
        if convexity == aag_module.EDGE_CONVEX:
            convex += 1
            if np.isfinite(angle) and abs(abs(angle) - np.pi / 2) <= SQUARE_TOL:
                square += 1
        elif convexity == aag_module.EDGE_CONCAVE:
            concave += 1
        else:
            smooth += 1
    return convex, concave, smooth, square, base_faces, loop_edges


def skin_features(graph, nodes_a, nodes_b, thickness, *,
                  ignore_complex=True, tollerance=TOLLERANCE):
    """Recognize the features connecting (or riding on) the two sheet
    skins: extrusions (raised), embossings (recessed) and chamfered /
    countersunk through holes.

    Port of instapart's get_connecting_features: every C0 component that
    belongs to neither skin is classified by its boundary-loop convexity
    against each skin and its vertex extrema along the base normal. Sign
    conventions carried over: positive extrusion/embossing = on the A
    (top) side, negative = on the B side.
    """
    import networkx as nx

    c0 = graph.C0_faces
    remaining = set(int(n) for n in c0.nodes()) - set(nodes_a) - set(nodes_b)
    features = []
    for component in nx.connected_components(c0.subgraph(remaining)):
        component = set(int(n) for n in component)
        labels = graph.c2_group[sorted(component)]
        groups = [sorted(f for f in component
                         if graph.c2_group[f] == label)
                  for label in np.unique(labels)]

        convex_a, concave_a, smooth_a, square_a, base_a, loop_a = \
            _feature_loop(graph, component, nodes_a)
        convex_b, concave_b, smooth_b, square_b, base_b, loop_b = \
            _feature_loop(graph, component, nodes_b)
        if ignore_complex and (len(base_a) > 1 or len(base_b) > 1):
            continue

        extrusion = None
        embossing = None
        chamfer_a = False
        chamfer_b = False

        if convex_b + concave_b + smooth_b == 0 and base_a:
            # single sided feature on top
            minimum, maximum = _feature_extrema(graph, base_a[0], component)
            if maximum >= tollerance:
                extrusion = maximum
            elif minimum <= -tollerance:
                embossing = -minimum
        elif convex_a + concave_a + smooth_a == 0 and base_b:
            # single sided feature on bottom
            minimum, maximum = _feature_extrema(graph, base_b[0], component)
            if maximum >= tollerance:
                extrusion = -maximum
            elif minimum <= -tollerance:
                embossing = minimum
        elif concave_a + smooth_a > 0 and base_a:
            # double sided feature reaching through, anchored on top
            minimum, maximum = _feature_extrema(graph, base_a[0], component)
            if maximum >= tollerance:
                extrusion = maximum
            elif minimum + thickness <= -tollerance:
                extrusion = minimum + thickness
            else:
                extrusion = 0.0
                embossing = 0.0
        elif concave_b + smooth_b > 0 and base_b:
            # double sided feature reaching through, anchored on bottom
            minimum, maximum = _feature_extrema(graph, base_b[0], component)
            if maximum >= tollerance:
                extrusion = -maximum
            elif minimum + thickness <= -tollerance:
                extrusion = -minimum + thickness
            else:
                extrusion = 0.0
                embossing = 0.0
        elif len(groups) >= 2:
            # through hole; non-square convex rims mean chamfers
            if convex_a != square_a and base_a:
                chamfer_a = max(_feature_extrema(graph, base_a[0], group)[0]
                                for group in groups)
            if convex_b != square_b and base_b:
                chamfer_b = max(_feature_extrema(graph, base_b[0], group)[0]
                                for group in groups)

        if extrusion is None and embossing is None \
                and chamfer_a is False and chamfer_b is False:
            continue

        if extrusion:
            kind, value = "extrusion", extrusion
        elif embossing:
            kind, value = "embossing", embossing
        elif chamfer_a is not False or chamfer_b is not False:
            kind = "chamfer"
            value = min(v for v in (chamfer_a, chamfer_b) if v is not False)
        else:
            kind, value = "flush", 0.0

        if kind in ("extrusion", "embossing"):
            side = "top" if value > 0 else "bottom"
        elif kind == "chamfer":
            side = ("both" if chamfer_a is not False
                    and chamfer_b is not False
                    else "top" if chamfer_a is not False else "bottom")
        else:
            side = None

        features.append({
            "type": kind,
            "value": float(abs(value)),
            "side": side,
            "faces": sorted(component),
            "groups": groups,
            "base_a": base_a,
            "base_b": base_b,
            "loop_a": loop_a,
            "loop_b": loop_b,
            "chamfer_a": (float(chamfer_a) if chamfer_a is not False
                          else None),
            "chamfer_b": (float(chamfer_b) if chamfer_b is not False
                          else None),
        })
    return features


def _feature_stats(features):
    return [{key: feature[key] for key in
             ("type", "value", "side", "faces")} for feature in features]


def _feature_ring_edges(graph, feature):
    """Edges between a feature's C2 groups (the cone/cylinder junction of a
    countersink, chamfer break lines) — the geometry worth projecting."""
    faces = sorted(feature["faces"])
    interior = graph.interior_edges()
    in_feature = np.isin(graph.edge_faces, faces)
    ring = []
    for edge_index in np.flatnonzero(interior & in_feature.all(axis=1)):
        face_a, face_b = graph.edge_faces[edge_index]
        if graph.c2_group[face_a] != graph.c2_group[face_b]:
            ring.append(int(edge_index))
    return ring


def _component_roles(graph, base_index, opposite_index):
    """Per-BREP-face role codes + per-face bend radius (NaN off-bend).

    The base and opposite C1 components are the two sheet skins; single-
    curvature faces inside them are bends (radius from mean curvature,
    1/(2|H|) on a cylinder). Everything outside both skins that touches a
    skin is a wall (cut edge); the rest keeps role other.
    """
    roles = np.zeros(graph.face_count, dtype=np.uint8)
    bend_radius = np.full(graph.face_count, np.nan)

    base_component = np.flatnonzero(
        graph.c1_group == graph.c1_group[base_index])
    roles[base_component] = ROLE_BASE
    if opposite_index is not None:
        opposite_component = np.flatnonzero(
            graph.c1_group == graph.c1_group[opposite_index])
        roles[opposite_component] = ROLE_OPPOSITE

    curved = np.isin(graph.face_convexity,
                     (aag_module.FACE_CONVEX, aag_module.FACE_CONCAVE))
    bends = (roles > 0) & curved
    roles[bends] = ROLE_BEND
    with np.errstate(divide="ignore"):
        bend_radius[bends] = 1.0 / (2.0 * np.abs(graph.face_curvature[bends]))

    # walls: faces outside both skins adjacent to a skin/bend face
    interior = graph.interior_edges()
    skin = roles > 0
    for face_a, face_b in graph.edge_faces[interior]:
        if skin[face_a] != skin[face_b]:
            roles[face_b if skin[face_a] else face_a] = ROLE_WALL

    return roles, bend_radius


def detect_sheet(workdir, *, min_thickness=0.1, max_thickness=None,
                 progress=None):
    """Classify a part as sheet metal and assign per-face roles.

    Returns the analyzer result triple (stats / arrays / field_meta).
    Stats carry a verdict ("sheet" | "not_sheet") with human-readable
    reasons rather than auto-dispatching — the user picks the process.
    """
    import brep

    graph = aag_module.load_aag(workdir)
    source = pipeline.source_step_path(workdir)
    if progress is not None:
        progress(0.1, "loading STEP for thickness ray cast")
    shape = brep.load_step_shape_cached(source)  # topology only — reuse parse
    faces = list(brep.iter_faces(shape))
    if len(faces) != graph.face_count:
        raise ValueError("source STEP face count does not match the AAG — "
                         "re-run prep/aag")

    if progress is not None:
        progress(0.4, "detecting sheet base and thickness")
    base_index, opposite_index, thickness = aag_module.get_sheet_base(
        graph, faces, min_thickness=min_thickness)

    reasons = []
    verdict = "sheet"
    if opposite_index is None:
        verdict = "not_sheet"
        reasons.append("no anti-parallel opposite face at uniform distance "
                       "behind the largest face")
    elif max_thickness is not None and thickness > max_thickness:
        verdict = "not_sheet"
        reasons.append(f"thickness {thickness:.2f} mm exceeds the "
                       f"{max_thickness:.2f} mm sheet limit")
    elif (graph.c1_group[base_index]
            == graph.c1_group[opposite_index]):
        verdict = "not_sheet"
        reasons.append("base and opposite faces connect smoothly — "
                       "no separable sheet skins")

    roles = np.zeros(graph.face_count, dtype=np.uint8)
    bend_radius = np.full(graph.face_count, np.nan)
    bend_groups = 0
    features = []
    warnings = []
    if verdict == "sheet":
        roles, bend_radius = _component_roles(graph, base_index,
                                              opposite_index)
        base_bends = np.flatnonzero(roles == ROLE_BEND)
        bend_groups = len(np.unique(graph.c2_group[base_bends])) if len(
            base_bends) else 0

        complex_in_skin = np.flatnonzero(
            (roles == ROLE_BASE) | (roles == ROLE_OPPOSITE))
        complex_in_skin = complex_in_skin[
            graph.face_convexity[complex_in_skin] == aag_module.FACE_COMPLEX]
        if len(complex_in_skin):
            reasons.append(f"{len(complex_in_skin)} doubly-curved faces in "
                           "the sheet skins — not fully developable")

        nodes_a, nodes_b = _skin_nodes(graph, base_index, opposite_index)
        features = skin_features(graph, nodes_a, nodes_b, thickness)
        for feature in features:
            roles[feature["faces"]] = ROLE_FEATURE
        top = sum(1 for f in features if f["side"] in ("top", "both"))
        bottom = sum(1 for f in features if f["side"] in ("bottom", "both"))
        if top and bottom:
            warnings.append("features on both sides of the sheet — only "
                            "one side is visible in the 2D pattern")

    counts = {name: int(np.sum(roles == code))
              for code, name in enumerate(ROLE_NAMES)}
    stats = {
        "verdict": verdict,
        "reasons": reasons,
        "warnings": warnings,
        "thickness": float(thickness),
        "base_face": int(base_index),
        "opposite_face": (None if opposite_index is None
                          else int(opposite_index)),
        "bend_count": int(bend_groups // 2),  # pairs: inner+outer per bend
        "bend_groups": int(bend_groups),
        "features": _feature_stats(features),
        "role_counts": counts,
    }
    logger.info(f"sheet detect: {verdict} thickness {thickness:.2f} "
                f"({counts})")

    brep_ids = np.load(os.path.join(workdir, pipeline.BREP_FACES_FILE))
    arrays = {
        "face_role": roles[brep_ids].astype("<u1"),
        "bend_radius": bend_radius[brep_ids].astype("<f4"),
    }
    field_meta = {
        "face_role": {"kind": "sheet_face_role", "association": "face",
                      "role": "category", "dtype": "u1",
                      "labels": ROLE_NAMES},
        "bend_radius": {"kind": "sheet_bend_radius", "association": "face",
                        "role": "scalar", "dtype": "f4", "units": "mm"},
    }
    if progress is not None:
        progress(1.0, "sheet detection done")
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}


def _segments_from_points(points_list):
    """Flattened 3D segment endpoints (z=0) from 2D polylines, the viewer's
    lines-role layout (N*2*3 floats)."""
    segments = []
    for points in points_list:
        if len(points) < 2:
            continue
        starts = points[:-1]
        ends = points[1:]
        chunk = np.zeros((len(starts), 2, 3), dtype=np.float32)
        chunk[:, 0, :2] = starts
        chunk[:, 1, :2] = ends
        segments.append(chunk)
    if not segments:
        return np.zeros((0,), dtype="<f4")
    return np.concatenate(segments).astype("<f4").ravel()


def _shift_path(path, offset):
    shifted = []
    for entry in path:
        moved = [entry[0] - offset[0], entry[1] - offset[1]]
        if len(entry) > 2:
            moved.append(entry[2])
        shifted.append(moved)
    return shifted


def flat_pattern(workdir, *, k_factor=0.5, combine_bends=True,
                 min_thickness=0.1, volume_tolerance=0.025,
                 tollerance=1e-1, progress=None):
    """Unfold the sheet and extract the flat pattern.

    Contour/holes/bend lines are returned as bulge polylines in the flat
    frame (bbox min at the origin — the DXF source of truth) plus
    discretized segment arrays for the viewer. The K-factor drives the
    bend allowance; the volume-conservation check |flat_area * t - volume|
    validates the whole unfold end to end.
    """
    import brep
    import unfold as unfold_module

    graph = aag_module.load_aag(workdir)
    source = pipeline.source_step_path(workdir)
    if progress is not None:
        progress(0.05, "loading STEP")
    shape = brep.load_step_shape_cached(source)  # topology only — reuse parse
    faces = list(brep.iter_faces(shape))
    if len(faces) != graph.face_count:
        raise ValueError("source STEP face count does not match the AAG — "
                         "re-run prep/aag")

    base_index, opposite_index, thickness = aag_module.get_sheet_base(
        graph, faces, min_thickness=min_thickness)
    if opposite_index is None or thickness <= 0:
        raise ValueError("no sheet base/thickness detected — run "
                         "sheet_metal/detect for the reasons")

    # skin features pick the unfold side: the pattern shows the side that
    # carries more of them (instapart's unfold_a switch)
    nodes_a, nodes_b = _skin_nodes(graph, base_index, opposite_index)
    features = skin_features(graph, nodes_a, nodes_b, thickness)
    top = sum(1 for f in features if f["side"] in ("top", "both"))
    bottom = sum(1 for f in features if f["side"] in ("bottom", "both"))
    warnings = []
    if top and bottom:
        warnings.append("features on both sides of the sheet — only one "
                        "side is visible in the 2D pattern")
    unfold_a = bottom <= top
    unfold_base = base_index if unfold_a else opposite_index
    nodes = sorted(nodes_a if unfold_a else nodes_b)
    loop_key = "loop_a" if unfold_a else "loop_b"
    base_key = "base_a" if unfold_a else "base_b"

    if progress is not None:
        progress(0.2, "unfolding")
    unfolder = unfold_module.Unfolder(graph, shape)
    transformations, _ = unfolder.unfold(nodes, unfold_base, thickness,
                                         k_factor=k_factor)
    if progress is not None:
        progress(0.5, "extracting flat outline")
    loops, open_wires = unfolder.extract_wires(
        nodes, thickness, transformations, k_factor=k_factor,
        tollerance=tollerance)
    if not loops:
        raise ValueError("unfold produced no outline loops")
    bends = unfolder.extract_bends(nodes, thickness, transformations,
                                   k_factor=k_factor,
                                   combine_bends=combine_bends)

    # largest-bbox loop is the outer contour, the rest are holes
    def bbox_area(loop):
        span = loop["points"].max(axis=0) - loop["points"].min(axis=0)
        return float(span[0] * span[1])

    contour_index = int(np.argmax([bbox_area(loop) for loop in loops]))
    contour = loops[contour_index]
    holes = [loop for i, loop in enumerate(loops) if i != contour_index]

    # feature annotation: a hole loop made of a feature's boundary edges
    # carries that feature's type/value onto the pattern entity
    feature_by_edge = {}
    for feature in features:
        for edge_index in feature[loop_key]:
            feature_by_edge[edge_index] = feature
    for hole in holes:
        matched = next((feature_by_edge[e] for e in hole.get("edges", [])
                        if e in feature_by_edge), None)
        hole["feature"] = matched

    # chamfer/countersink rings project onto the skin as engravings
    engravings = []
    for feature in features:
        chamfer = feature["chamfer_a" if unfold_a else "chamfer_b"]
        if chamfer is None or not feature[base_key]:
            continue
        ring = _feature_ring_edges(graph, feature)
        if not ring:
            continue
        try:
            projected = unfolder.project_ring(
                ring, feature[base_key][0], transformations,
                k_factor=k_factor, thickness=thickness)
        except Exception:
            logger.debug("feature ring projection failed")
            continue
        if projected is not None:
            engravings.append(projected)

    flat_area = contour["area"] - sum(hole["area"] for hole in holes)
    origin = contour["points"].min(axis=0)
    size = contour["points"].max(axis=0) - origin

    # volume conservation: the acceptance test of the whole unfold
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    volume = abs(properties.Mass())
    volume_error = (abs(flat_area * thickness - volume) / volume
                    if volume > 0 else float("inf"))

    developable = open_wires == 0
    if not developable:
        logger.warning(f"{open_wires} open wires — skin not (fully) "
                       "developable, pattern is approximate")
    logger.info(f"flat pattern: area {flat_area:.1f} mm2, size "
                f"{size[0]:.1f} x {size[1]:.1f}, volume error "
                f"{volume_error * 100:.2f}%")

    # shift everything to put the contour bbox min at the origin
    def hole_entity(hole):
        entity = {"path": _shift_path(hole["path"], origin)}
        if hole.get("feature") is not None:
            entity["feature_type"] = hole["feature"]["type"]
            entity["feature_value"] = hole["feature"]["value"]
        return entity

    entities = {
        "contour": _shift_path(contour["path"], origin),
        "holes": [hole_entity(hole) for hole in holes],
        "engravings": [_shift_path(path, origin)
                       for _, path in engravings],
        "bend_lines": [{**bend, "path": _shift_path(bend["path"], origin)}
                       for bend in bends],
    }

    roles, _ = _component_roles(graph, base_index, opposite_index)
    for feature in features:
        roles[feature["faces"]] = ROLE_FEATURE
    brep_ids = np.load(os.path.join(workdir, pipeline.BREP_FACES_FILE))
    outline = _segments_from_points([contour["points"] - origin])
    hole_lines = _segments_from_points(
        [hole["points"] - origin for hole in holes])
    bend_lines = _segments_from_points(
        [np.array(bend["path"], dtype=float) for bend in entities["bend_lines"]])
    engraving_lines = _segments_from_points(
        [points - origin for points, _ in engravings])

    arrays = {
        "outline_lines": outline,
        "hole_lines": hole_lines,
        "bend_lines": bend_lines,
        "engraving_lines": engraving_lines,
        "face_role": roles[brep_ids].astype("<u1"),
    }
    field_meta = {
        "outline_lines": {"kind": "flat_pattern", "association": "none",
                          "role": "lines", "dtype": "f4",
                          "length": int(outline.size),
                          "segments": int(outline.size // 6)},
        "hole_lines": {"kind": "flat_pattern", "association": "none",
                       "role": "lines", "dtype": "f4",
                       "length": int(hole_lines.size),
                       "segments": int(hole_lines.size // 6)},
        "bend_lines": {"kind": "flat_pattern", "association": "none",
                       "role": "lines", "dtype": "f4",
                       "length": int(bend_lines.size),
                       "segments": int(bend_lines.size // 6)},
        "engraving_lines": {"kind": "flat_pattern", "association": "none",
                            "role": "lines", "dtype": "f4",
                            "length": int(engraving_lines.size),
                            "segments": int(engraving_lines.size // 6)},
        "face_role": {"kind": "sheet_face_role", "association": "face",
                      "role": "category", "dtype": "u1",
                      "labels": ROLE_NAMES},
    }

    stats = {
        "thickness": float(thickness),
        "k_factor": float(k_factor),
        "developable": bool(developable),
        "open_wires": int(open_wires),
        "flat_area": float(flat_area),
        "flat_size": [float(size[0]), float(size[1])],
        "volume_error_pct": float(volume_error * 100),
        "volume_ok": bool(volume_error <= volume_tolerance),
        "hole_count": len(holes),
        "unfolded_side": "top" if unfold_a else "bottom",
        "features": _feature_stats(features),
        "warnings": warnings,
        "bends": [{key: value for key, value in bend.items()
                   if key != "path"} for bend in entities["bend_lines"]],
        "entities": entities,
    }
    if progress is not None:
        progress(1.0, "flat pattern extracted")
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}
