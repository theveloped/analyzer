"""K-factor unfold of a sheet skin onto the Z=0 plane.

Port of instapart's flatten.py unfold machinery. The mathematical core is
kept exactly: every BREP edge of the skin owns a 2D pcurve in its face's UV
parametrization (BRep_Tool.CurveOnSurface); re-hosting that pcurve on a
planar surface unrolls the face (a cylinder's U is radians — scaling U by
the K-factor neutral-fiber radius turns it into arc length, which IS the
bend allowance), and a per-face gp_Trsf chain built by BFS over the C1
subgraph rotates each unrolled face into the flat layout (each face is
aligned to its predecessor through their shared edge frame).

The wire bookkeeping is re-based on the AAG's canonical ids instead of
instapart's shape-hash stitching: outline loops are walked topologically
(boundary edges of the subgraph form vertex cycles), chained by endpoint
proximity in 2D, with gaps up to ``tollerance`` bridged by filler segments
and larger gaps counted as open wires (non-developable). The flat area
comes from the discretized loops (shoelace), not BRepBuilderAPI_MakeFace —
same volume-conservation invariant, none of the shape-healing fragility.

Everything returned is JSON-safe: bulge polylines (DXF convention: bulge =
tan(sweep/4) on the segment's start point) plus discretized segments for
the viewer.
"""

import math

import numpy as np
from loguru import logger

import aag as aag_module

TOLLERANCE = 1e-6


def bend_allowance(angle, inner_radius, thickness, k_factor):
    """Neutral-fiber arc length of a bend (angle in radians)."""
    return angle * (inner_radius + thickness * k_factor)


def radius_allowance(angle, original_radius, new_radius):
    """Extra flat length when bending to a different radius than modeled."""
    length = (new_radius - original_radius) / math.tan(angle / 2.0)
    return 2 * length


def _is_forward(shape):
    from OCP.TopAbs import TopAbs_Orientation

    return shape.Orientation() == TopAbs_Orientation.TopAbs_FORWARD


class Unfolder:
    """Unfolds one C1-connected sheet skin of a live shape.

    ``graph`` is the loaded AAG (deterministic ids), ``shape`` the live
    TopoDS re-read from the same source.stp — the ids line up by
    construction.
    """

    def __init__(self, graph, shape):
        from OCP.BRep import BRep_Tool
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedMapOfShape
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

        import brep

        self.graph = graph
        self.faces = list(brep.iter_faces(shape))
        if len(self.faces) != graph.face_count:
            raise ValueError("live shape face count does not match the AAG")
        self.edge_map = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, self.edge_map)

        # the target: the XY plane at Z=0
        plane_face = BRepBuilderAPI_MakeFace(
            gp_Pln(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))).Face()
        self.base_surface = BRep_Tool.Surface_s(plane_face)

    # -- per-face allowance scale -------------------------------------------

    def _face_domain(self, face_index):
        from OCP.BRepTools import BRepTools

        return BRepTools.UVBounds_s(self.faces[face_index])

    def node_scale(self, face_index, thickness, k_factor=0.5):
        """(u_scale, v_scale) implementing the bend allowance, or None.

        On a cylindrical bend face the curved UV direction is in radians;
        scaling it by allowance/angle (= neutral-fiber radius for the plain
        case) makes the re-hosted pcurve span the bend allowance in mm.
        """
        convexity = int(self.graph.face_convexity[face_index])
        if convexity == aag_module.FACE_PLANAR:
            return None

        domain = self._face_domain(face_index)
        radii = self.graph.face_radii[face_index]
        curvature = self.graph.face_curvature[face_index]
        u_curved = abs(radii[0]) > abs(radii[1])

        face_radius = abs(1.0 / curvature / 2.0)
        face_angle = (abs(domain[0] - domain[1]) if u_curved
                      else abs(domain[2] - domain[3]))
        if convexity != aag_module.FACE_CONCAVE:
            face_radius -= thickness  # outer skin: back to the inner radius
            face_angle *= -1

        allowance = (bend_allowance(face_angle, face_radius, thickness,
                                    k_factor)
                     - radius_allowance(face_angle, face_radius, face_radius))
        scale = allowance / face_angle
        return (scale, 1.0) if u_curved else (1.0, scale)

    # -- pcurve re-hosting ---------------------------------------------------

    def transformed_edge(self, edge, face_index, scale=None,
                         transformations=()):
        """The edge re-hosted on the base plane, allowance-scaled, then
        carried through the face's transformation chain."""
        from OCP.BRep import BRep_Tool
        from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,
                                        BRepBuilderAPI_Transform)
        from OCP.Geom2dAPI import Geom2dAPI_ProjectPointOnCurve
        from OCP.GeomLib import GeomLib
        from OCP.ShapeFix import ShapeFix_Edge
        from OCP.TopoDS import TopoDS
        from OCP.gp import gp_GTrsf2d

        face = self.faces[face_index]
        # the OCP overload takes First/Last as dummy inputs; the true pcurve
        # range comes from Range_s (SameRange holds in valid BREPs)
        pcurve = BRep_Tool.CurveOnSurface_s(edge, face, 0.0, 0.0)
        if pcurve is None:
            raise ValueError("edge has no pcurve on its face")
        u, v = BRep_Tool.Range_s(edge, face)

        if scale:
            first_uv = pcurve.Value(u)
            last_uv = pcurve.Value(v)
            gtrsf = gp_GTrsf2d()
            gtrsf.SetValue(1, 1, float(scale[0]))
            gtrsf.SetValue(2, 2, float(scale[1]))
            pcurve = GeomLib.GTransform_s(pcurve, gtrsf)
            first_uv.SetX(scale[0] * first_uv.X())
            first_uv.SetY(scale[1] * first_uv.Y())
            last_uv.SetX(scale[0] * last_uv.X())
            last_uv.SetY(scale[1] * last_uv.Y())
            u = Geom2dAPI_ProjectPointOnCurve(
                first_uv, pcurve).LowerDistanceParameter()
            v = Geom2dAPI_ProjectPointOnCurve(
                last_uv, pcurve).LowerDistanceParameter()

        local = BRepBuilderAPI_MakeEdge(pcurve, self.base_surface, u, v).Edge()
        for transformation in transformations:
            local = TopoDS.Edge_s(
                BRepBuilderAPI_Transform(local, transformation).Shape())
        ShapeFix_Edge().FixAddCurve3d(local)
        return local

    def transformed_uv(self, face_index, u, v, scale=None,
                       transformations=()):
        """A face UV parameter pair carried into the flat layout.

        Evaluates through the same base plane surface the pcurves are
        re-hosted on (allowance scaling + transform chain applied) — no
        ValueOfUV round trip, no periodic-surface branch ambiguity.
        """
        if scale:
            u, v = scale[0] * u, scale[1] * v
        result = self.base_surface.Value(float(u), float(v))
        for transformation in transformations:
            result = result.Transformed(transformation)
        return result

    def _edge_frame(self, edge, face_index, *, scale, normal,
                    ignore_orientation, transformations=()):
        """gp_Ax3 of the edge in the flat layout (position + tangent frame),
        instapart's transformed_edge_origin distilled.

        Both faces sharing a BREP edge re-host the same 3D curve, so its
        parametrization range and direction agree on either side (BREP
        SameRange/SameParameter rules) — anchoring at the re-hosted curve's
        first parameter yields consistent frames on both faces. Anchoring
        via vertex UV lookups would hit the periodic-surface branch
        ambiguity (a seam vertex reports u=0 while the pcurve lives at
        u=2*pi), which is exactly what this avoids.
        """
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.BRepLProp import BRepLProp_CLProps
        from OCP.gp import gp_Ax3, gp_Dir, gp_Vec

        local = self.transformed_edge(edge, face_index, scale=scale,
                                      transformations=transformations)
        adaptor = BRepAdaptor_Curve(local)
        anchor = adaptor.Value(adaptor.FirstParameter())
        props = BRepLProp_CLProps(adaptor, 2, TOLLERANCE)
        props.SetParameter(adaptor.FirstParameter())
        direction = gp_Dir()
        props.Tangent(direction)
        tangent = gp_Vec(direction)

        frame_normal = gp_Vec(normal.X(), normal.Y(), normal.Z())
        if not ignore_orientation:
            frame_normal.Reverse()
        return gp_Ax3(anchor, gp_Dir(frame_normal), gp_Dir(tangent))

    # -- the unfold ----------------------------------------------------------

    def unfold(self, nodes, base_index, thickness, k_factor=0.5):
        """Per-face transformation chains flattening the subgraph.

        BFS from the base face: each successor gets [T(own edge frame),
        T(predecessor edge frame)^-1] — the predecessor frame is evaluated
        with its own chain applied, so it already lives in the final
        layout. Returns ({face_index: [gp_Trsf, ...]}, base_reversed).
        """
        import networkx as nx
        from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

        subgraph = self.graph.C1_faces.subgraph(set(int(n) for n in nodes))
        base_normal = gp_Vec(0, 0, 1)
        transformations = {int(base_index): []}
        base_reversed = not _is_forward(self.faces[base_index])
        if base_reversed:
            flip = gp_Trsf()
            flip.SetTransformation(
                gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, -1), gp_Dir(1, 0, 0)))
            transformations[int(base_index)] = [flip]

        def compose(successor_frame, predecessor_frame):
            chain = []
            to_frame = gp_Trsf()
            to_frame.SetTransformation(successor_frame)
            chain.append(to_frame)
            from_frame = gp_Trsf()
            from_frame.SetTransformation(predecessor_frame)
            from_frame.Invert()
            chain.append(from_frame)
            return chain

        for predecessor, successors in nx.bfs_successors(
                subgraph, source=int(base_index)):
            for successor in successors:
                edge_index = subgraph[predecessor][successor]["edge"]
                edge = self._live_edge(edge_index)

                predecessor_scale = self.node_scale(predecessor, thickness,
                                                    k_factor)
                successor_scale = self.node_scale(successor, thickness,
                                                  k_factor)

                predecessor_frame = self._edge_frame(
                    edge, predecessor, scale=predecessor_scale,
                    normal=base_normal, ignore_orientation=True,
                    transformations=transformations[predecessor])
                successor_frame = self._edge_frame(
                    edge, successor, scale=successor_scale,
                    normal=base_normal, ignore_orientation=True)
                chain = compose(successor_frame, predecessor_frame)

                # orientation flags don't reliably predict pcurve handedness
                # across the re-hosting, so decide the mirror empirically: a
                # valid unfold puts the two faces on OPPOSITE sides of their
                # shared fold line. Same side -> flip the successor frame.
                anchor = predecessor_frame.Location()
                tangent = predecessor_frame.XDirection()

                def side(point):
                    return (tangent.X() * (point.Y() - anchor.Y())
                            - tangent.Y() * (point.X() - anchor.X()))

                pd = self._face_domain(predecessor)
                predecessor_mid = self.transformed_uv(
                    predecessor, 0.5 * (pd[0] + pd[1]), 0.5 * (pd[2] + pd[3]),
                    scale=predecessor_scale,
                    transformations=transformations[predecessor])
                sd = self._face_domain(successor)
                successor_mid = self.transformed_uv(
                    successor, 0.5 * (sd[0] + sd[1]), 0.5 * (sd[2] + sd[3]),
                    scale=successor_scale, transformations=chain)
                if side(predecessor_mid) * side(successor_mid) > 0:
                    successor_frame = self._edge_frame(
                        edge, successor, scale=successor_scale,
                        normal=base_normal, ignore_orientation=False)
                    chain = compose(successor_frame, predecessor_frame)

                transformations[successor] = chain

        return transformations, base_reversed

    def _live_edge(self, edge_index):
        from OCP.TopoDS import TopoDS

        return TopoDS.Edge_s(self.edge_map.FindKey(int(edge_index) + 1))

    # -- flat geometry extraction --------------------------------------------

    def _edge_entity(self, local_edge, deflection=0.05):
        """(points_2d, bulge_path) of a flattened edge.

        points_2d is the fine discretization (viewer/area); bulge_path the
        DXF-faithful representation: [[x, y, bulge], ..., [x, y]] with the
        bulge on the segment start (tan(sweep/4), CCW positive).
        """
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GCPnts import GCPnts_QuasiUniformDeflection
        from OCP.GeomAbs import GeomAbs_CurveType

        adaptor = BRepAdaptor_Curve(local_edge)
        u0, u1 = adaptor.FirstParameter(), adaptor.LastParameter()
        first = adaptor.Value(u0)
        last = adaptor.Value(u1)
        kind = adaptor.GetType()

        if kind == GeomAbs_CurveType.GeomAbs_Line:
            points = np.array([[first.X(), first.Y()], [last.X(), last.Y()]])
            return points, [[float(first.X()), float(first.Y()), 0.0],
                            [float(last.X()), float(last.Y())]]

        discretizer = GCPnts_QuasiUniformDeflection(adaptor, deflection)
        if discretizer.IsDone() and discretizer.NbPoints() >= 2:
            points = np.array([[discretizer.Value(i).X(),
                                discretizer.Value(i).Y()]
                               for i in range(1, discretizer.NbPoints() + 1)])
        else:
            points = np.array([[first.X(), first.Y()], [last.X(), last.Y()]])

        if kind == GeomAbs_CurveType.GeomAbs_Circle:
            sweep = u1 - u0
            if first.Distance(last) < 1e-9:
                # full circle: a single bulge segment degenerates (start ==
                # end) — emit four quarter arcs through the cardinal points
                quarter = sweep / 4.0
                bulge = math.tan(quarter / 4.0)
                path = []
                for k in range(4):
                    p = adaptor.Value(u0 + k * quarter)
                    path.append([float(p.X()), float(p.Y()), bulge])
                p = adaptor.Value(u1)
                path.append([float(p.X()), float(p.Y())])
                return points, path
            mid = adaptor.Value(0.5 * (u0 + u1))
            chord = np.array([last.X() - first.X(), last.Y() - first.Y()])
            to_mid = np.array([mid.X() - first.X(), mid.Y() - first.Y()])
            bulge = math.tan(abs(sweep) / 4.0)
            # sign: positive when the arc bows left of the chord (CCW)
            if chord[0] * to_mid[1] - chord[1] * to_mid[0] < 0:
                bulge = -bulge
            return points, [[float(first.X()), float(first.Y()), bulge],
                            [float(last.X()), float(last.Y())]]

        # freeform: emit the discretization as a plain polyline
        path = [[float(x), float(y), 0.0] for x, y in points[:-1]]
        path.append([float(points[-1][0]), float(points[-1][1])])
        return points, path

    def _boundary_loops(self, nodes):
        """Topological outline loops of the subgraph: (edge_index,
        owner_face) lists, one per closed vertex cycle of boundary edges."""
        import networkx as nx

        nodes_arr = np.asarray(sorted(int(n) for n in nodes))
        interior = self.graph.interior_edges()
        in_set = np.isin(self.graph.edge_faces, nodes_arr)
        boundary = interior & (in_set[:, 0] != in_set[:, 1])

        owner = np.where(in_set[:, 0], self.graph.edge_faces[:, 0],
                         self.graph.edge_faces[:, 1])

        loops_graph = nx.MultiGraph()
        for edge_index in np.flatnonzero(boundary):
            first, last = self.graph.edge_vertices[edge_index]
            if first < 0 or last < 0:
                continue
            loops_graph.add_edge(int(first), int(last), key=int(edge_index),
                                 owner=int(owner[edge_index]))

        loops = []
        used = set()
        for component in nx.connected_components(loops_graph):
            component_edges = []
            for a, b, key in loops_graph.subgraph(component).edges(keys=True):
                if key not in used:
                    used.add(key)
                    component_edges.append(
                        (key, loops_graph[a][b][key]["owner"], a, b))
            if not component_edges:
                continue
            # walk the cycle: start anywhere, repeatedly pick an unused edge
            # sharing the current vertex (closed manifold loops are simple
            # cycles; ties at touching vertices are broken arbitrarily)
            ordered = [component_edges[0]]
            remaining = component_edges[1:]
            current = component_edges[0][3]
            start = component_edges[0][2]
            while remaining:
                for i, entry in enumerate(remaining):
                    if entry[2] == current:
                        ordered.append(entry)
                        current = entry[3]
                        break
                    if entry[3] == current:
                        ordered.append((entry[0], entry[1], entry[3],
                                        entry[2]))
                        current = entry[2]
                        break
                else:
                    # disconnected walk (touching loops): start a new chain
                    ordered.append(remaining[0])
                    current = remaining[0][3]
                    i = 0
                remaining.pop(i)
            loops.append([(key, owner_face)
                          for key, owner_face, _, _ in ordered])
        return loops

    def extract_wires(self, nodes, thickness, transformations, *,
                      k_factor=0.5, tollerance=1e-1):
        """Flatten the subgraph outline into closed 2D loops.

        Returns (loops, open_wire_count): each loop dict carries the fine
        ``points`` (N,2), the bulge ``path``, its shoelace ``area`` and
        ``length``. Gaps up to ``tollerance`` between consecutive edges are
        bridged (allowance scaling shifts edges tangentially); larger gaps
        mean the skin is not developable and count as open wires.
        """
        open_wire_count = 0
        loops = []
        scales = {}

        for loop_edges in self._boundary_loops(nodes):
            points_chain = []
            path_chain = []
            gap_failures = 0

            for edge_index, owner_face in loop_edges:
                if owner_face not in scales:
                    scales[owner_face] = self.node_scale(owner_face,
                                                         thickness, k_factor)
                local = self.transformed_edge(
                    self._live_edge(edge_index), owner_face,
                    scale=scales[owner_face],
                    transformations=transformations.get(owner_face, []))
                points, path = self._edge_entity(local)

                if points_chain:
                    tail = points_chain[-1][-1]
                    gap_fwd = np.linalg.norm(points[0] - tail)
                    gap_rev = np.linalg.norm(points[-1] - tail)
                    if gap_rev < gap_fwd:
                        points = points[::-1]
                        path = _reverse_path(path)
                        gap_fwd = gap_rev
                    if gap_fwd > tollerance:
                        gap_failures += 1
                points_chain.append(points)
                path_chain.append(path)

            merged_points = [points_chain[0]]
            merged_path = list(path_chain[0])
            for points, path in zip(points_chain[1:], path_chain[1:]):
                merged_points.append(points)
                merged_path = merged_path[:-1] + [
                    merged_path[-1] + [0.0]] + path[1:]
                merged_path[-len(path)] = path[0]
            points = np.vstack(merged_points)

            closing_gap = float(np.linalg.norm(points[0] - points[-1]))
            if closing_gap > tollerance:
                gap_failures += 1
            open_wire_count += gap_failures

            area = _shoelace(points)
            length = float(np.sum(np.linalg.norm(np.diff(
                np.vstack([points, points[:1]]), axis=0), axis=1)))
            loops.append({
                "points": points,
                "path": merged_path,
                "area": abs(area),
                "length": length,
                "closed": gap_failures == 0,
            })

        return loops, open_wire_count

    def extract_bends(self, nodes, thickness, transformations, *,
                      k_factor=0.5, combine_bends=True):
        """One bend line per bend face, C2-connected multi-face bends merged
        into a single angle-weighted line (instapart's combine_bends)."""
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        bends = []
        handled = set()
        nodes = [int(n) for n in nodes]

        for face_index in nodes:
            if face_index in handled:
                continue
            if (self.graph.face_convexity[face_index]
                    == aag_module.FACE_PLANAR):
                handled.add(face_index)
                continue

            group = [face_index]
            if combine_bends:
                label = self.graph.c2_group[face_index]
                group = [f for f in nodes
                         if self.graph.c2_group[f] == label]
            handled.update(group)

            neighbors = set()
            c1 = self.graph.C1_faces
            for member in group:
                neighbors.update(c1.neighbors(member))
            neighbors -= set(group)

            sub_bends = [self._extract_bend(member, thickness,
                                            transformations, k_factor)
                         for member in group]
            sub_bends = [bend for bend in sub_bends if bend is not None]
            if not sub_bends:
                continue

            if len(sub_bends) == 1:
                bend = sub_bends[0]
            else:
                total_angle = sum(b["angle"] for b in sub_bends) or 1.0
                reference = np.array(sub_bends[0]["path"][0])
                start = np.zeros(2)
                end = np.zeros(2)
                for b in sub_bends:
                    a = np.array(b["path"][0])
                    c = np.array(b["path"][1])
                    if (np.linalg.norm(reference - a)
                            > np.linalg.norm(reference - c)):
                        a, c = c, a
                    weight = b["angle"] / total_angle
                    start += weight * a
                    end += weight * c
                bend = {
                    "path": [[float(start[0]), float(start[1])],
                             [float(end[0]), float(end[1])]],
                    "angle": total_angle,
                    "inner_radius": sub_bends[0]["inner_radius"],
                    "k_factor": k_factor,
                    "length": float(np.linalg.norm(end - start)),
                }
            bend["neighbors"] = frozenset(int(n) for n in neighbors)
            bends.append(bend)

        # bends sharing the same flange pair act together (one brake stroke)
        common_ids = {}
        for bend in bends:
            key = bend.pop("neighbors")
            bend["common_id"] = common_ids.setdefault(key,
                                                      len(common_ids) + 1)
            bend["direction"] = "up" if bend["angle"] > 0 else "down"
            bend["angle_deg"] = math.degrees(abs(bend["angle"]))
        return bends

    def _extract_bend(self, face_index, thickness, transformations, k_factor):
        """One bend face -> flat center line + angle/radius (instapart's
        extract_bend, mid-parameter line without the boolean trim)."""
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        try:
            domain = self._face_domain(face_index)
            radii = self.graph.face_radii[face_index]
            curvature = self.graph.face_curvature[face_index]
            u_curved = abs(radii[0]) > abs(radii[1])

            if u_curved:
                mid = 0.5 * (domain[0] + domain[1])
                start_uv = (mid, domain[2])
                end_uv = (mid, domain[3])
                angle = abs(domain[0] - domain[1])
            else:
                mid = 0.5 * (domain[2] + domain[3])
                start_uv = (domain[0], mid)
                end_uv = (domain[1], mid)
                angle = abs(domain[2] - domain[3])

            radius = abs(1.0 / curvature / 2.0)
            if (int(self.graph.face_convexity[face_index])
                    != aag_module.FACE_CONCAVE):
                radius -= thickness
                angle *= -1

            scale = self.node_scale(face_index, thickness, k_factor)
            chain = transformations.get(face_index, [])
            flat_start = self.transformed_uv(face_index, *start_uv,
                                             scale=scale,
                                             transformations=chain)
            flat_end = self.transformed_uv(face_index, *end_uv, scale=scale,
                                           transformations=chain)
            length = flat_start.Distance(flat_end)
            return {
                "path": [[float(flat_start.X()), float(flat_start.Y())],
                         [float(flat_end.X()), float(flat_end.Y())]],
                "angle": angle,
                "inner_radius": float(radius),
                "k_factor": float(k_factor),
                "length": float(length),
            }
        except Exception:
            logger.debug(f"bend extraction failed on face {face_index}")
            return None


def _reverse_path(path):
    """Reverse a bulge path: bulges move to the new segment starts and flip
    sign (arc bows to the other side when traversed backwards)."""
    points = [entry[:2] for entry in path]
    bulges = [entry[2] if len(entry) > 2 else 0.0 for entry in path[:-1]]
    reversed_path = []
    n = len(points)
    for i in range(n - 1, 0, -1):
        reversed_path.append(points[i] + [-bulges[i - 1]])
    reversed_path.append(points[0])
    return reversed_path


def _shoelace(points):
    x = points[:, 0]
    y = points[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
