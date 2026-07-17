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
ROLE_NAMES = ["other", "base", "opposite", "bend", "wall"]


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
    shape = brep.load_step_shape(source)
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

    counts = {name: int(np.sum(roles == code))
              for code, name in enumerate(ROLE_NAMES)}
    stats = {
        "verdict": verdict,
        "reasons": reasons,
        "thickness": float(thickness),
        "base_face": int(base_index),
        "opposite_face": (None if opposite_index is None
                          else int(opposite_index)),
        "bend_count": int(bend_groups // 2),  # pairs: inner+outer per bend
        "bend_groups": int(bend_groups),
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
    shape = brep.load_step_shape(source)
    faces = list(brep.iter_faces(shape))
    if len(faces) != graph.face_count:
        raise ValueError("source STEP face count does not match the AAG — "
                         "re-run prep/aag")

    base_index, opposite_index, thickness = aag_module.get_sheet_base(
        graph, faces, min_thickness=min_thickness)
    if opposite_index is None or thickness <= 0:
        raise ValueError("no sheet base/thickness detected — run "
                         "sheet_metal/detect for the reasons")

    if progress is not None:
        progress(0.2, "unfolding")
    component = aag_module.get_connected_subgraph(graph, base_index,
                                                  ignore_complex=True)
    nodes = sorted(int(n) for n in component.nodes())

    unfolder = unfold_module.Unfolder(graph, shape)
    transformations, _ = unfolder.unfold(nodes, base_index, thickness,
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
    entities = {
        "contour": _shift_path(contour["path"], origin),
        "holes": [_shift_path(hole["path"], origin) for hole in holes],
        "bend_lines": [{**bend, "path": _shift_path(bend["path"], origin)}
                       for bend in bends],
    }

    roles, _ = _component_roles(graph, base_index, opposite_index)
    brep_ids = np.load(os.path.join(workdir, pipeline.BREP_FACES_FILE))
    outline = _segments_from_points([contour["points"] - origin])
    hole_lines = _segments_from_points(
        [hole["points"] - origin for hole in holes])
    bend_lines = _segments_from_points(
        [np.array(bend["path"], dtype=float) for bend in entities["bend_lines"]])

    arrays = {
        "outline_lines": outline,
        "hole_lines": hole_lines,
        "bend_lines": bend_lines,
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
        "bends": [{key: value for key, value in bend.items()
                   if key != "path"} for bend in entities["bend_lines"]],
        "entities": entities,
    }
    if progress is not None:
        progress(1.0, "flat pattern extracted")
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}
