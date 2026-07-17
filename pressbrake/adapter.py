"""KinematicGraph construction from analyzer artifacts.

Replaces upstream extract.py (which harvested from instapart's flatten
pipeline): panels, bends and the fold tree are built from the analyzer's
persisted AAG (aag.py) plus the live Unfolder (unfold.py) on the retained
source STEP, replicating the upstream semantics:

- panels  = connected components of the PLANAR-face subgraph of the C1
  skin around the base face (coplanar flanges bridged by smooth edges
  merge into one rigid panel);
- bends   = C2-chained cylinder components WITHIN the skin (the same
  local grouping Unfolder.extract_bends uses — global C2 labels chain
  phantom bends through rolled edges), parent/child by BFS depth from
  the base;
- axes    anchored on the bend-allowance center line then shifted to the
  virtual corner (mold line) toward the child;
- panels tile the flat pattern gap-free (_merge_bend_zones splits each
  flattened bend zone at its axis and unions the halves into the
  adjacent panels);
- z_offset (mid-surface height above the flattened-skin plane) from a
  solid-classifier probe along the base face's outward normal.  The
  outward normal maps to layout +Z: build_aag orients face_normal
  outward using the face orientation flag, and the Unfolder applies its
  base flip exactly when that flag is REVERSED — the two sign factors
  cancel, so the probe's material side along the outward normal IS the
  layout material side.  Pinned (with ANGLE_SIGN) by the
  folded-vs-BREP test in test_bendplan.py.

OCP imports are function-local; everything returned is pure numpy.
"""

import math
import os

import numpy as np
from loguru import logger

from pressbrake.kinematics import finalize_graph
from pressbrake.model import Bend, KinematicGraph, Panel, polygon_area, \
    polygon_centroid

# Empirical sign pinning the fold handedness of the analyzer's unfolder
# (folded part must match the source BREP, not its mirror) — validated by
# test_bendplan.py's signed mid-plane test.  Do not change without it.
ANGLE_SIGN = 1.0

CHAIN_TOLERANCE = 1e-3
SISTER_TOLERANCE = 1e-3   # looser than the builders' 1e-6: real geometry


class ExtractionError(RuntimeError):
    """The part cannot be expressed as a panel/hinge fold tree."""


def build_kinematic_graph(workdir, *, k_factor=0.5, min_thickness=0.1,
                          deflection=0.05, merge_bend_zones=True,
                          progress=None):
    """Build the press-brake KinematicGraph for a sheet part workdir.

    Returns (graph, info): info carries the BREP-face-id maps for viewer
    fields ({panel_id: [...]}, {bend_id: [...]}), the base/opposite face
    ids, thickness and the flat-frame display origin (min corner over all
    panel outlines).
    """
    import aag as aag_module
    import brep
    import pipeline
    import unfold as unfold_module

    graph = aag_module.load_aag(workdir)
    source = pipeline.source_step_path(workdir)
    if progress is not None:
        progress(0.05, "loading STEP")
    shape = brep.load_step_shape(source)
    faces = list(brep.iter_faces(shape))
    if len(faces) != graph.face_count:
        raise ExtractionError("source STEP face count does not match the "
                              "AAG — re-run prep/aag")

    base_index, opposite_index, thickness = aag_module.get_sheet_base(
        graph, faces, min_thickness=min_thickness)
    if opposite_index is None or thickness <= 0:
        raise ExtractionError("no sheet base/thickness detected — run "
                              "sheet_metal/detect for the reasons")

    # deterministic side: always unfold from the base (largest) face
    import networkx as nx

    component = aag_module.get_connected_subgraph(graph, base_index,
                                                  ignore_complex=True)
    nodes = sorted(int(n) for n in component.nodes())
    subgraph = graph.C1_faces.subgraph(set(nodes))

    if progress is not None:
        progress(0.2, "unfolding the skin")
    unfolder = unfold_module.Unfolder(graph, shape)
    transformations, _ = unfolder.unfold(nodes, base_index, thickness,
                                         k_factor=k_factor)

    planar = {n for n in nodes
              if graph.face_convexity[n] == aag_module.FACE_PLANAR}
    if base_index not in planar:
        raise ExtractionError("base face is not planar")

    panel_groups = [frozenset(c) for c in
                    nx.connected_components(subgraph.subgraph(planar))]
    group_of_face = {f: g for g in panel_groups for f in g}

    # C2-chained bend components WITHIN the skin (skin-local grouping)
    c2_local = nx.Graph()
    c2_local.add_nodes_from(nodes)
    c2_local.add_edges_from(
        (a, b) for a, b, continuity in subgraph.edges(data="continuity")
        if abs(continuity) == 2)
    bend_components = []
    seen = set()
    for node in nodes:
        if node in planar or node in seen:
            continue
        member_component = frozenset(
            f for f in nx.node_connected_component(c2_local, node)
            if f not in planar)
        seen |= member_component
        bend_components.append(member_component)

    # fold tree depth, the same BFS traversal Unfolder.unfold runs
    depth = {base_index: 0}
    for predecessor, successors in nx.bfs_successors(subgraph,
                                                     source=base_index):
        for successor in successors:
            depth[successor] = depth[predecessor] + 1

    if progress is not None:
        progress(0.4, "harvesting panels")
    panels = []
    panel_index = {}
    base_group = group_of_face[base_index]
    ordered_groups = sorted(
        panel_groups,
        key=lambda g: (g != base_group,
                       min(depth.get(f, 1 << 30) for f in g)))
    for group in ordered_groups:
        outline, holes = _group_polygon(unfolder, graph, group,
                                        transformations, thickness,
                                        k_factor, deflection)
        panels.append(Panel(id=len(panels), outline=outline, holes=holes,
                            face_hashes=tuple(sorted(group))))
        panel_index[group] = panels[-1].id

    if progress is not None:
        progress(0.6, "harvesting bends")
    bends = []
    bend_zones = []
    for member_component in bend_components:
        neighbor_groups = set()
        for face in member_component:
            for neighbor in subgraph.neighbors(face):
                if neighbor in group_of_face:
                    neighbor_groups.add(group_of_face[neighbor])
        if len(neighbor_groups) != 2:
            raise ExtractionError(
                f"bend component {sorted(member_component)} connects "
                f"{len(neighbor_groups)} panels (expected 2)")

        group_a, group_b = sorted(
            neighbor_groups,
            key=lambda g: min(depth.get(f, 1 << 30) for f in g))
        parent_id = panel_index[group_a]
        child_id = panel_index[group_b]

        entity, zone_polygons = _merged_bend_entity(
            unfolder, graph, sorted(member_component), transformations,
            thickness, k_factor, deflection)

        start = np.asarray(entity["start"], dtype=float)
        end = np.asarray(entity["end"], dtype=float)
        direction = end - start
        norm = float(np.linalg.norm(direction))
        if norm < CHAIN_TOLERANCE:
            raise ExtractionError("degenerate bend axis")
        direction = direction / norm

        # virtual-corner (mold line) hinge placement: the entity's axis
        # sits on the CENTER line of the bend-allowance zone; the two
        # mid-planes intersect at (r+t/2)*tan(|a|/2) from the tangent
        shift = _virtual_corner_shift(
            entity["angle"], entity["inner_radius"], thickness, k_factor)
        child_centroid = polygon_centroid(panels[child_id].outline)
        toward_child = np.array([-direction[1], direction[0]])
        if float(toward_child @ (child_centroid - start)) < 0:
            toward_child = -toward_child
        start = start + shift * toward_child

        bends.append(Bend(
            id=len(bends),
            axis_point=start,
            axis_dir=direction,
            angle_target=ANGLE_SIGN * entity["angle"],
            inner_radius=entity["inner_radius"],
            k_factor=k_factor,
            length=entity["length"],
            parent_panel=parent_id,
            child_panel=child_id,
            zone_width=abs(entity["angle"]) * (
                entity["inner_radius"] + k_factor * thickness),
            face_hashes=tuple(sorted(member_component)),
        ))
        bend_zones.append((parent_id, child_id, start, direction,
                           zone_polygons))

    _validate_tree(panels, bends)

    kinematic = KinematicGraph(
        panels=panels, bends=bends, base_panel=0, thickness=float(thickness),
        z_offset=_material_z_offset(graph, shape, base_index, thickness),
        source=os.path.basename(source),
    )
    if merge_bend_zones:
        _merge_bend_zones(kinematic, bend_zones)
    finalize_graph(kinematic, sister_tolerance=SISTER_TOLERANCE)

    origin = np.min(np.vstack([panel.outline for panel in kinematic.panels]),
                    axis=0)
    info = {
        "panel_faces": {panel.id: list(panel.face_hashes)
                        for panel in kinematic.panels},
        "bend_faces": {bend.id: list(bend.face_hashes)
                       for bend in kinematic.bends},
        "base_index": int(base_index),
        "opposite_index": int(opposite_index),
        "thickness": float(thickness),
        "origin": [float(origin[0]), float(origin[1])],
    }
    logger.info(f"kinematic graph: {kinematic.panel_count} panels, "
                f"{kinematic.bend_count} bends, thickness {thickness:.2f}, "
                f"z_offset {kinematic.z_offset:+.2f}")
    if progress is not None:
        progress(0.8, "kinematic graph built")
    return kinematic, info


# --- geometry harvesting ----------------------------------------------------

def _face_loops(unfolder, graph, face_index, transformations, thickness,
                k_factor, deflection):
    """All closed loops (outer + holes, unclassified) of one face after
    unfolding, as (N,2) arrays in the flat frame."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    scale = unfolder.node_scale(face_index, thickness, k_factor)
    chain = transformations.get(face_index, [])

    loops = []
    wire_explorer = TopExp_Explorer(unfolder.faces[face_index], TopAbs_WIRE)
    while wire_explorer.More():
        wire = TopoDS.Wire_s(wire_explorer.Current())
        polylines = []
        edge_explorer = TopExp_Explorer(wire, TopAbs_EDGE)
        while edge_explorer.More():
            edge = TopoDS.Edge_s(edge_explorer.Current())
            edge_explorer.Next()
            try:
                local = unfolder.transformed_edge(edge, face_index,
                                                  scale=scale,
                                                  transformations=chain)
                points, _ = unfolder._edge_entity(local, deflection)
            except Exception:
                continue
            if len(points) >= 2:
                polylines.append(np.asarray(points, dtype=float))
        loop = _chain_polylines(polylines)
        if loop is not None and len(loop) >= 3:
            loops.append(loop)
        wire_explorer.Next()

    if not loops:
        raise ExtractionError(f"face {face_index} produced no closed loops")
    return loops


def _group_polygon(unfolder, graph, group, transformations, thickness,
                   k_factor, deflection):
    """Flattened outline + holes of a coplanar panel group."""
    outlines = [_face_loops(unfolder, graph, face, transformations,
                            thickness, k_factor, deflection)
                for face in sorted(group)]
    if len(outlines) == 1:
        return _orient_loops(outlines[0])

    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polygons = []
    for loops in outlines:
        outer, holes = _orient_loops(loops)
        polygons.append(Polygon(outer, [h for h in holes]))
    merged = unary_union(polygons).buffer(0)
    if merged.geom_type == "MultiPolygon":
        logger.warning("coplanar panel group did not merge cleanly; "
                       "keeping the largest piece")
        merged = max(merged.geoms, key=lambda g: g.area)
    outline = np.asarray(merged.exterior.coords[:-1], dtype=float)
    holes = [np.asarray(ring.coords[:-1], dtype=float)
             for ring in merged.interiors]
    return _orient_loops([outline] + holes)


def _chain_polylines(polylines, tolerance=CHAIN_TOLERANCE):
    """Chain unordered, arbitrarily oriented polylines into a closed loop."""
    if not polylines:
        return None
    remaining = [np.asarray(p) for p in polylines]
    chain = list(remaining.pop(0))
    while remaining:
        tail = chain[-1]
        best = None
        for index, polyline in enumerate(remaining):
            if np.linalg.norm(polyline[0] - tail) < tolerance:
                best = (index, polyline[1:])
                break
            if np.linalg.norm(polyline[-1] - tail) < tolerance:
                best = (index, polyline[::-1][1:])
                break
        if best is None:
            logger.debug("open loop while chaining wire edges")
            return None
        index, points = best
        remaining.pop(index)
        chain.extend(points)
    if np.linalg.norm(np.asarray(chain[0]) - np.asarray(chain[-1])) \
            < tolerance:
        chain = chain[:-1]
    return np.asarray(chain, dtype=float)


def _orient_loops(loops):
    """Split loops into (outer CCW, holes CW) by absolute area."""
    areas = [abs(polygon_area(loop)) for loop in loops]
    outer_index = int(np.argmax(areas))
    outer = loops[outer_index]
    if polygon_area(outer) < 0:
        outer = outer[::-1]
    holes = []
    for index, loop in enumerate(loops):
        if index == outer_index:
            continue
        if polygon_area(loop) > 0:
            loop = loop[::-1]
        holes.append(loop)
    return np.asarray(outer, dtype=float), holes


def _merged_bend_entity(unfolder, graph, component, transformations,
                        thickness, k_factor, deflection):
    """Axis/angle/radius of a (possibly multi-face) bend component plus the
    flattened bend-zone outlines for the panel merge step.

    Consumes the RAW signed angles from Unfolder._extract_bend (never the
    massaged direction/angle_deg display fields of flat_pattern)."""
    sub_entities = []
    zone_polygons = []
    for face in component:
        entity = unfolder._extract_bend(face, thickness, transformations,
                                        k_factor)
        if entity is None:
            raise ExtractionError(f"bend face {face} produced no entity")
        sub_entities.append(entity)
        try:
            loops = _face_loops(unfolder, graph, face, transformations,
                                thickness, k_factor, deflection)
            zone_polygons.append(_orient_loops(loops)[0])
        except ExtractionError:
            pass

    total_angle = sum(entity["angle"] for entity in sub_entities)
    if abs(total_angle) < 1e-9:
        raise ExtractionError("bend component has zero total angle")

    reference = np.asarray(sub_entities[0]["path"][0], dtype=float)
    start = np.zeros(2)
    end = np.zeros(2)
    for entity in sub_entities:
        point_a = np.asarray(entity["path"][0], dtype=float)
        point_b = np.asarray(entity["path"][1], dtype=float)
        if (np.linalg.norm(reference - point_a)
                > np.linalg.norm(reference - point_b)):
            point_a, point_b = point_b, point_a
        weight = entity["angle"] / total_angle
        start += weight * point_a
        end += weight * point_b

    return {
        "start": start,
        "end": end,
        "angle": total_angle,
        "inner_radius": sub_entities[-1]["inner_radius"],
        "length": max(entity["length"] for entity in sub_entities),
    }, zone_polygons


def _virtual_corner_shift(angle, inner_radius, thickness, k_factor):
    """Distance from the BA center line to the mid-plane intersection
    (virtual sharp corner), along the in-plane normal toward the child."""
    theta = min(abs(angle), math.radians(150.0))
    if theta < 1e-9:
        return 0.0
    mid_radius = inner_radius + thickness / 2.0
    allowance = theta * (inner_radius + k_factor * thickness)
    return max(0.0, mid_radius * math.tan(theta / 2.0) - allowance / 2.0)


def _panel_side(kinematic, panel_id, axis_point, normal):
    centroid = polygon_centroid(kinematic.panels[panel_id].outline)
    side = float(np.dot(normal, centroid - axis_point))
    return 1.0 if side >= 0 else -1.0


def _merge_bend_zones(kinematic, bend_zones):
    """Split each flattened bend zone at its axis and union the halves into
    the adjacent panels so panels tile the pattern with no hinge gap."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import split as shapely_split, unary_union

    additions = {}
    for parent_id, child_id, axis_point, axis_dir, zone_polygons in bend_zones:
        for zone in zone_polygons:
            polygon = Polygon(zone)
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            if polygon.is_empty:
                continue
            diameter = math.hypot(polygon.bounds[2] - polygon.bounds[0],
                                  polygon.bounds[3] - polygon.bounds[1]) + 1.0
            line = LineString([axis_point - diameter * axis_dir,
                               axis_point + diameter * axis_dir])
            try:
                pieces = list(shapely_split(polygon, line).geoms)
            except Exception:
                pieces = [polygon]
            normal = np.array([-axis_dir[1], axis_dir[0]])
            child_side = _panel_side(kinematic, child_id, axis_point, normal)
            for piece in pieces:
                centroid = np.array([piece.centroid.x, piece.centroid.y])
                side = float(np.dot(normal, centroid - axis_point))
                target = child_id if side * child_side > 0 else parent_id
                # hairline overlap so pieces sharing only a float-fuzzy
                # tangent edge with the panel still union into one polygon
                additions.setdefault(target, []).append(
                    piece.buffer(1e-3, join_style=2))

    from shapely.geometry import Polygon as _Polygon

    for panel_id, pieces in additions.items():
        panel = kinematic.panels[panel_id]
        merged = unary_union(
            [_Polygon(panel.outline, [h for h in panel.holes])]
            + pieces).buffer(0)
        if merged.geom_type == "MultiPolygon":
            merged = max(merged.geoms, key=lambda g: g.area)
        outline = np.asarray(merged.exterior.coords[:-1], dtype=float)
        holes = [np.asarray(ring.coords[:-1], dtype=float)
                 for ring in merged.interiors]
        # re-orient after every union so hole winding stays CW
        panel.outline, panel.holes = _orient_loops([outline] + holes)


def machine_interval_segments(graph, bend_ids, rotation, x_offset, pairs):
    """Map machine-X intervals back onto the flat frame for display.

    The rigid placement maps the primary bend's axis_point to machine
    x = x_offset with the hinge direction along +-X (negated when
    ``rotation``), preserving arc length along the hinge line:
    x(p) = x_offset + sign * (p - A) . d.  Returns flat-frame segment
    endpoint pairs [((x, y), (x, y)), ...] for the given [[x0, x1], ...].
    """
    primary = graph.bends[bend_ids[0]]
    anchor = np.asarray(primary.axis_point, dtype=float)
    direction = np.asarray(primary.axis_dir, dtype=float)
    sign = -1.0 if rotation else 1.0
    segments = []
    for x0, x1 in pairs:
        p0 = anchor + sign * (x0 - x_offset) * direction
        p1 = anchor + sign * (x1 - x_offset) * direction
        segments.append(((float(p0[0]), float(p0[1])),
                         (float(p1[0]), float(p1[1]))))
    return segments


def _validate_tree(panels, bends):
    """Every non-base panel must hang off exactly one parent.

    Several bends may share one (parent, child) pair — a notch splitting a
    bend line yields collinear sister segments that form together — but a
    child with two DIFFERENT parents closes a loop (box corner, closed
    profile) which the fold kinematics cannot express.
    """
    parents_of = {}
    for bend in bends:
        if bend.child_panel == 0 or bend.parent_panel == bend.child_panel:
            raise ExtractionError("bend graph is not rooted at the base "
                                  "panel")
        parents_of.setdefault(bend.child_panel, set()).add(bend.parent_panel)
    if sorted(parents_of) != list(range(1, len(panels))):
        raise ExtractionError(
            f"bend graph is not a fold tree: {len(panels)} panels but "
            f"children {sorted(parents_of)} (closed profile or disconnected "
            "flange)")
    for child, parents in parents_of.items():
        if len(parents) > 1:
            raise ExtractionError(
                f"panel {child} hangs off {len(parents)} parents — the "
                "profile closes a loop the fold tree cannot express")


def _material_z_offset(graph, shape, base_index, thickness):
    """Mid-surface height above the flat pattern plane (+-t/2).

    Probes the solid on both sides of the base face along its OUTWARD
    normal (build_aag orients face_normal by the face's orientation flag).
    The Unfolder applies its base mirror flip exactly when that same flag
    is REVERSED, so the two sign factors cancel: the material side along
    the outward normal IS the layout material side.
    """
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_State
    from OCP.gp import gp_Pnt

    anchor = graph.face_point[int(base_index)]
    normal = graph.face_normal[int(base_index)]
    if not (np.isfinite(anchor).all() and np.isfinite(normal).all()):
        raise ExtractionError("base face has no defined normal")

    basis_a = np.cross(normal, [1.0, 0.0, 0.0])
    if np.linalg.norm(basis_a) < 1e-6:
        basis_a = np.cross(normal, [0.0, 1.0, 0.0])
    basis_a /= np.linalg.norm(basis_a)
    basis_b = np.cross(normal, basis_a)

    classifier = BRepClass3d_SolidClassifier(shape)
    for du, dv in ((0, 0), (5, 0), (-5, 0), (0, 5), (0, -5), (10, 10),
                   (-10, -10)):
        probe_anchor = anchor + du * basis_a + dv * basis_b
        for sign in (1.0, -1.0):
            point = probe_anchor + sign * (thickness / 4.0) * normal
            classifier.Perform(gp_Pnt(*[float(c) for c in point]), 1e-4)
            if classifier.State() == TopAbs_State.TopAbs_IN:
                return sign * thickness / 2.0
    raise ExtractionError("could not classify the material side of the "
                          "base face")
