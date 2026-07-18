"""Attributed adjacency graph (AAG) over BREP faces.

Port of instapart's flatten.AdjacencyGraph onto the analyzer's OCP/numpy
stack. Faces are classified by convexity (planar / convex / concave /
complex from principal curvatures), edges by tangency continuity (OCCT
regularity records plus a geometric normal-angle fallback for exporters
that omit them) and signed dihedral angle (positive = concave, material
wraps past 180 degrees). Sheet-metal detection, tube classification and
machining-feature recognition all consume these graphs.

Deterministic ids: face index = brep.iter_faces (TopExp_Explorer) order —
the exact indexing brep_faces.npy / brep_meta.json use; edge and vertex
indices come from TopExp.MapShapes order. All three re-derive identically
from the same STEP bytes, so the persisted artifact (aag.npz + aag.json,
written by save_aag) can be joined against any other workdir artifact and
work can resume from any stage.

Keep this module importable without fastapi or meshlib: OCP imports are
lazy (function level) and everything persisted is plain numpy.
"""

import json
import os
from dataclasses import dataclass, field

import numpy as np
from loguru import logger

from utils import log_execution_time

AAG_SCHEMA = 1
AAG_FILE = "aag.npz"
AAG_META_FILE = "aag.json"

# face convexity codes (instapart FaceTypes values)
FACE_CONVEX = -1
FACE_PLANAR = 0
FACE_CONCAVE = 1
FACE_COMPLEX = 2
FACE_NAMES = {FACE_CONVEX: "convex", FACE_PLANAR: "planar",
              FACE_CONCAVE: "concave", FACE_COMPLEX: "complex"}

# edge convexity codes (instapart EdgeTypes values, -3 = boundary/unknown)
EDGE_CONVEX = -1
EDGE_SMOOTH = 0
EDGE_CONCAVE = 1
EDGE_UNDEFINED = -3
EDGE_NAMES = {EDGE_CONVEX: "convex", EDGE_SMOOTH: "smooth",
              EDGE_CONCAVE: "concave", EDGE_UNDEFINED: "undefined"}

# angular tolerance for geometric tangency detection (radians, ~0.57 deg):
# STEP exporters that omit G1 regularity records leave genuinely tangent
# bend-to-flange edges marked C0, so records alone drop real connections
SMOOTH_ANGLE_TOLERANCE = 1e-2

TOLLERANCE = 1e-6


def _is_forward(shape):
    from OCP.TopAbs import TopAbs_Orientation

    return shape.Orientation() == TopAbs_Orientation.TopAbs_FORWARD


def _mid_uv(face):
    from OCP.BRepTools import BRepTools

    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
    return 0.5 * (umin + umax), 0.5 * (vmin + vmax)


def _surface_props(face, u, v, tollerance):
    from OCP.BRep import BRep_Tool
    from OCP.GeomLProp import GeomLProp_SLProps

    surface = BRep_Tool.Surface_s(face)
    return GeomLProp_SLProps(surface, float(u), float(v), 2, tollerance)


def face_convexity(face, tollerance=TOLLERANCE):
    """(convexity code, mean curvature, (|d2u|, |d2v|)) of a BREP face.

    Curvature is sampled at the face's mid-UV; uniform curvature is assumed
    (exact for the quadrics bends and holes are made of — instapart's
    known limitation, kept). The convexity sign accounts for the face
    orientation, so a shaft reads convex and a drilled hole concave.
    """
    from OCP.BRep import BRep_Tool
    from OCP.GeomLib import GeomLib_IsPlanarSurface

    surface = BRep_Tool.Surface_s(face)
    if GeomLib_IsPlanarSurface(surface, tollerance).IsPlanar():
        return FACE_PLANAR, 0.0, (np.nan, np.nan)

    u, v = _mid_uv(face)
    props = _surface_props(face, u, v, tollerance)
    if not props.IsCurvatureDefined():
        return FACE_COMPLEX, np.nan, (np.nan, np.nan)
    curvature = float(props.MeanCurvature())
    radii = (float(props.D2U().Magnitude()), float(props.D2V().Magnitude()))

    if abs(curvature) < tollerance:
        return FACE_PLANAR, 0.0, radii
    if abs(radii[0]) > tollerance and abs(radii[1]) > tollerance:
        return FACE_COMPLEX, curvature, radii

    reversed_ = not _is_forward(face)
    if curvature > 0:
        code = FACE_CONVEX if reversed_ else FACE_CONCAVE
    else:
        code = FACE_CONCAVE if reversed_ else FACE_CONVEX
    return code, curvature, radii


def _face_mid_sample(face, tollerance):
    """(point, outward normal) at the face's mid-UV, NaN when undefined."""
    try:
        u, v = _mid_uv(face)
        props = _surface_props(face, u, v, tollerance)
        point = props.Value()
        point = np.array([point.X(), point.Y(), point.Z()])
        if not props.IsNormalDefined():
            return point, np.full(3, np.nan)
        normal_dir = props.Normal()
        normal = np.array([normal_dir.X(), normal_dir.Y(), normal_dir.Z()])
        if not _is_forward(face):
            normal = -normal
        return point, normal
    except Exception:
        return np.full(3, np.nan), np.full(3, np.nan)


def normal_at_point(face, point, tollerance=TOLLERANCE):
    """Outward face normal (gp_Vec) at a 3D point on the face's surface."""
    from OCP.BRep import BRep_Tool
    from OCP.GeomLProp import GeomLProp_SLProps
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.gp import gp_Vec

    surface = BRep_Tool.Surface_s(face)
    analysis = ShapeAnalysis_Surface(surface)
    uv = analysis.ValueOfUV(point, tollerance)
    props = GeomLProp_SLProps(surface, uv.X(), uv.Y(), 2, tollerance)
    normal = gp_Vec(props.Normal())
    if not _is_forward(face):
        normal.Reverse()
    return normal


def _edge_end_points(edge):
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp

    return (BRep_Tool.Pnt_s(TopExp.FirstVertex_s(edge)),
            BRep_Tool.Pnt_s(TopExp.LastVertex_s(edge)))


def _edge_tangents(edge, tollerance):
    """Tangent gp_Vecs at the edge's first and last parameters.

    Keeps instapart's orientation convention (tangents reversed on FORWARD
    edges) — the dihedral sign below depends on it and is validated by
    test_aag.py, so change both together or neither.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepLProp import BRepLProp_CLProps
    from OCP.gp import gp_Dir, gp_Vec

    adaptor = BRepAdaptor_Curve(edge)
    props = BRepLProp_CLProps(adaptor, 2, tollerance)
    tangents = []
    for parameter in (adaptor.FirstParameter(), adaptor.LastParameter()):
        props.SetParameter(parameter)
        if not props.IsTangentDefined():
            raise ValueError("no tangent defined")
        direction = gp_Dir()
        props.Tangent(direction)
        tangents.append(gp_Vec(direction))
    if _is_forward(edge):
        tangents[0].Reverse()
        tangents[1].Reverse()
    return tangents


def edge_dihedral(edge, face_a, face_b, tollerance=TOLLERANCE):
    """Signed dihedral angle between two faces along an edge.

    Positive = concave (pocket floor/wall), negative = convex (outer box
    edge). Averaged over both edge endpoints — the dihedral varies along
    tapered/conical joints and one sample is not robust. ``edge`` must be
    oriented as traversed in face_b's wire (build_aag handles this).
    """
    first_point, last_point = _edge_end_points(edge)
    normal_a_first = normal_at_point(face_a, first_point, tollerance)
    normal_a_last = normal_at_point(face_a, last_point, tollerance)
    normal_b_first = normal_at_point(face_b, first_point, tollerance)
    normal_b_last = normal_at_point(face_b, last_point, tollerance)
    tangent_first, tangent_last = _edge_tangents(edge, tollerance)
    first_angle = normal_b_first.AngleWithRef(normal_a_first, tangent_first)
    last_angle = normal_b_last.AngleWithRef(normal_a_last, tangent_last)
    return 0.5 * (first_angle + last_angle)


def _edge_smooth(edge, face_a, face_b, smooth_angle, tollerance):
    """True when two faces connect tangentially (G1+) along the edge."""
    from OCP.BRep import BRep_Tool
    from OCP.GeomAbs import GeomAbs_Shape

    smooth = False
    if BRep_Tool.HasContinuity_s(edge, face_a, face_b):
        smooth = (int(BRep_Tool.Continuity_s(edge, face_a, face_b))
                  >= int(GeomAbs_Shape.GeomAbs_G1))
    if not smooth:
        try:
            first_point, _ = _edge_end_points(edge)
            normal_a = normal_at_point(face_a, first_point, tollerance)
            normal_b = normal_at_point(face_b, first_point, tollerance)
            smooth = normal_a.Angle(normal_b) < smooth_angle
        except Exception:
            smooth = False
    return smooth


def _edge_polyline(edge, deflection):
    """Discretized 3D polyline of an edge, endpoints as a fallback."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection

    if BRep_Tool.Degenerated_s(edge):
        return np.zeros((0, 3))
    try:
        adaptor = BRepAdaptor_Curve(edge)
        discretizer = GCPnts_QuasiUniformDeflection(adaptor, deflection)
        if discretizer.IsDone() and discretizer.NbPoints() >= 2:
            return np.array([
                [discretizer.Value(i).X(), discretizer.Value(i).Y(),
                 discretizer.Value(i).Z()]
                for i in range(1, discretizer.NbPoints() + 1)])
    except Exception:
        pass
    try:
        first_point, last_point = _edge_end_points(edge)
        return np.array([[first_point.X(), first_point.Y(), first_point.Z()],
                         [last_point.X(), last_point.Y(), last_point.Z()]])
    except Exception:
        return np.zeros((0, 3))


def _edge_length(edge):
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint

    if BRep_Tool.Degenerated_s(edge):
        return 0.0
    try:
        return float(GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(edge)))
    except Exception:
        return 0.0


def _face_wire_edges(face):
    """Yield (edge, wire_reversed) in wire order, instapart's convention:
    reversed wires are walked on a Reversed() copy so edge orientations
    compose the same way flatten.wire_edges produced them."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    wire_explorer = TopExp_Explorer(face, TopAbs_WIRE)
    while wire_explorer.More():
        wire = TopoDS.Wire_s(wire_explorer.Current())
        is_reversed = not _is_forward(wire)
        walk = TopoDS.Wire_s(wire.Reversed()) if is_reversed else wire
        edge_explorer = TopExp_Explorer(walk, TopAbs_EDGE)
        while edge_explorer.More():
            yield TopoDS.Edge_s(edge_explorer.Current()), is_reversed
            edge_explorer.Next()
        wire_explorer.Next()


def _connected_labels(count, pairs):
    """Connected-component label per face over undirected face pairs."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    if not len(pairs):
        return np.arange(count, dtype=np.int32)
    pairs = np.asarray(pairs)
    ones = np.ones(len(pairs))
    matrix = coo_matrix((ones, (pairs[:, 0], pairs[:, 1])),
                        shape=(count, count))
    _, labels = connected_components(matrix, directed=False)
    return labels.astype(np.int32)


@dataclass
class AAG:
    """The attributed adjacency graph as flat numpy tables.

    Face/edge/vertex indices are the deterministic BREP ids (module
    docstring). networkx views are built lazily; consumers that only need
    numpy logic can stay on the arrays.
    """

    face_convexity: np.ndarray   # i1[F] FACE_* codes
    face_curvature: np.ndarray   # f8[F] mean curvature at mid-UV
    face_radii: np.ndarray       # f8[F,2] |d2u|,|d2v| magnitudes
    face_area: np.ndarray        # f8[F]
    face_normal: np.ndarray      # f8[F,3] outward normal at mid-UV
    face_point: np.ndarray       # f8[F,3] surface point at mid-UV
    edge_faces: np.ndarray       # i4[E,2] adjacent face ids (-1 open)
    edge_continuity: np.ndarray  # i1[E] 0 sharp, +-1 smooth, +-2 same curv
    edge_convexity: np.ndarray   # i1[E] EDGE_* codes
    edge_angle: np.ndarray       # f8[E] signed dihedral (0 smooth)
    edge_length: np.ndarray      # f8[E]
    edge_vertices: np.ndarray    # i4[E,2] canonical vertex ids
    vertex_points: np.ndarray    # f8[V,3]
    polyline_points: np.ndarray  # f4[M,3] concatenated edge polylines
    polyline_offsets: np.ndarray  # i4[E+1] slice bounds into polyline_points
    c1_group: np.ndarray         # i4[F] C1 connected-component label
    c2_group: np.ndarray         # i4[F] C2 connected-component label
    meta: dict = field(default_factory=dict)
    _graphs: dict = field(default_factory=dict, repr=False)

    @property
    def face_count(self):
        return len(self.face_convexity)

    @property
    def edge_count(self):
        return len(self.edge_faces)

    def polyline(self, edge_index):
        start, stop = self.polyline_offsets[edge_index:edge_index + 2]
        return self.polyline_points[start:stop]

    def interior_edges(self, min_continuity=0):
        """Edge-index mask: edges between two distinct faces at (at least)
        the given continuity level — the graph edges of C<level>_faces."""
        mask = (self.edge_faces[:, 0] >= 0) & (self.edge_faces[:, 1] >= 0)
        mask &= self.edge_faces[:, 0] != self.edge_faces[:, 1]
        if min_continuity:
            mask &= np.abs(self.edge_continuity) >= min_continuity
        return mask

    def graph(self, min_continuity=0):
        """networkx face graph at the given continuity level (cached).

        Nodes are BREP face ids with convexity/curvature/radii/area/
        normal/point attrs; edges carry angle/continuity/convexity/edge
        (the canonical edge id). Parallel BREP edges between the same face
        pair collapse to one graph edge, matching instapart's nx.Graph.
        """
        import networkx as nx

        if min_continuity in self._graphs:
            return self._graphs[min_continuity]
        graph = nx.Graph()
        for index in range(self.face_count):
            graph.add_node(
                index,
                convexity=int(self.face_convexity[index]),
                curvature=float(self.face_curvature[index]),
                radii=self.face_radii[index],
                area=float(self.face_area[index]),
                normal=self.face_normal[index],
                point=self.face_point[index])
        for edge_index in np.flatnonzero(self.interior_edges(min_continuity)):
            face_a, face_b = self.edge_faces[edge_index]
            graph.add_edge(
                int(face_a), int(face_b),
                angle=float(self.edge_angle[edge_index]),
                continuity=int(self.edge_continuity[edge_index]),
                convexity=int(self.edge_convexity[edge_index]),
                edge=int(edge_index))
        self._graphs[min_continuity] = graph
        return graph

    @property
    def C0_faces(self):
        return self.graph(0)

    @property
    def C1_faces(self):
        return self.graph(1)

    @property
    def C2_faces(self):
        return self.graph(2)

    def edge_graph(self):
        """networkx MultiGraph over canonical vertex ids, one keyed edge
        per BREP edge (instapart's C0_edges). Built on demand, uncached."""
        import networkx as nx

        graph = nx.MultiGraph()
        for index in range(len(self.vertex_points)):
            graph.add_node(index, point=self.vertex_points[index])
        for edge_index in range(self.edge_count):
            first, last = self.edge_vertices[edge_index]
            if first < 0 or last < 0:
                continue
            graph.add_edge(
                int(first), int(last), key=int(edge_index),
                faces=[int(f) for f in self.edge_faces[edge_index] if f >= 0],
                continuity=int(self.edge_continuity[edge_index]),
                convexity=int(self.edge_convexity[edge_index]),
                angle=float(self.edge_angle[edge_index]),
                length=float(self.edge_length[edge_index]))
        return graph


@log_execution_time
def build_aag(shape, *, smooth_angle=SMOOTH_ANGLE_TOLERANCE,
              tollerance=TOLLERANCE, deflection=0.5, progress=None):
    """Build the AAG of a BREP shape (see module docstring for id rules)."""
    import brep
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    faces = list(brep.iter_faces(shape))

    edge_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)
    vertex_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, vertex_map)

    n_faces = len(faces)
    n_edges = edge_map.Extent()
    n_vertices = vertex_map.Extent()

    # per-face attributes
    face_convexity_codes = np.zeros(n_faces, dtype=np.int8)
    face_curvature = np.zeros(n_faces)
    face_radii = np.full((n_faces, 2), np.nan)
    face_area = np.zeros(n_faces)
    face_normal = np.full((n_faces, 3), np.nan)
    face_point = np.full((n_faces, 3), np.nan)

    for index, face in enumerate(faces):
        if progress is not None and index % 64 == 0:
            progress(0.5 * index / max(n_faces, 1), "classifying faces")
        code, curvature, radii = face_convexity(face, tollerance)
        face_convexity_codes[index] = code
        face_curvature[index] = curvature
        face_radii[index] = radii
        properties = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, properties)
        face_area[index] = abs(properties.Mass())
        face_point[index], face_normal[index] = _face_mid_sample(
            face, tollerance)

    # per-edge geometry, canonical order
    vertex_points = np.zeros((n_vertices, 3))
    from OCP.BRep import BRep_Tool
    for index in range(n_vertices):
        point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertex_map.FindKey(index + 1)))
        vertex_points[index] = (point.X(), point.Y(), point.Z())

    from OCP.TopExp import TopExp as _TopExp
    edge_faces = np.full((n_edges, 2), -1, dtype=np.int32)
    edge_continuity = np.zeros(n_edges, dtype=np.int8)
    edge_convexity = np.full(n_edges, EDGE_UNDEFINED, dtype=np.int8)
    edge_angle = np.zeros(n_edges)
    edge_length = np.zeros(n_edges)
    edge_vertices = np.full((n_edges, 2), -1, dtype=np.int32)
    polylines = []

    for index in range(n_edges):
        edge = TopoDS.Edge_s(edge_map.FindKey(index + 1))
        edge_length[index] = _edge_length(edge)
        polylines.append(_edge_polyline(edge, deflection).astype("<f4"))
        try:
            first = _TopExp.FirstVertex_s(edge)
            last = _TopExp.LastVertex_s(edge)
            edge_vertices[index] = (vertex_map.FindIndex(first) - 1,
                                    vertex_map.FindIndex(last) - 1)
        except Exception:
            pass

    # pair faces across edges in instapart's face/wire traversal order so
    # the dihedral sign convention carries over exactly
    for face_index, face in enumerate(faces):
        if progress is not None and face_index % 64 == 0:
            progress(0.5 + 0.5 * face_index / max(n_faces, 1),
                     "classifying edges")
        for edge, wire_reversed in _face_wire_edges(face):
            edge_index = edge_map.FindIndex(edge) - 1
            if edge_index < 0:
                continue
            if edge_faces[edge_index, 0] < 0:
                edge_faces[edge_index, 0] = face_index
                continue
            if edge_faces[edge_index, 1] >= 0:
                continue  # non-manifold: keep the first two faces
            first_index = edge_faces[edge_index, 0]
            edge_faces[edge_index, 1] = face_index
            face_a = faces[first_index]

            if first_index == face_index:
                smooth = True  # seam edge within one face (cylinder seam)
            else:
                smooth = _edge_smooth(edge, face_a, face, smooth_angle,
                                      tollerance)
            if smooth:
                same_curvature = abs(face_curvature[first_index]
                                     - face_curvature[face_index]) <= tollerance
                is_complex = (face_convexity_codes[first_index] == FACE_COMPLEX
                              or face_convexity_codes[face_index] == FACE_COMPLEX)
                level = 2 if same_curvature else 1
                edge_continuity[edge_index] = -level if is_complex else level
                edge_convexity[edge_index] = EDGE_SMOOTH
                edge_angle[edge_index] = 0.0
            else:
                edge_continuity[edge_index] = 0
                work_edge = edge
                if wire_reversed:
                    work_edge = TopoDS.Edge_s(edge.Reversed())
                try:
                    angle = edge_dihedral(work_edge, face_a, face, tollerance)
                except Exception:
                    logger.debug(f"dihedral failed on edge {edge_index}")
                    edge_angle[edge_index] = np.nan
                    continue
                edge_angle[edge_index] = angle
                edge_convexity[edge_index] = (EDGE_CONCAVE if angle > 0.0
                                              else EDGE_CONVEX)

    interior = ((edge_faces[:, 0] >= 0) & (edge_faces[:, 1] >= 0)
                & (edge_faces[:, 0] != edge_faces[:, 1]))
    c1_pairs = edge_faces[interior & (np.abs(edge_continuity) >= 1)]
    c2_pairs = edge_faces[interior & (np.abs(edge_continuity) >= 2)]

    offsets = np.zeros(n_edges + 1, dtype=np.int32)
    offsets[1:] = np.cumsum([len(p) for p in polylines])
    polyline_points = (np.concatenate(polylines).astype("<f4")
                       if polylines else np.zeros((0, 3), dtype="<f4"))

    return AAG(
        face_convexity=face_convexity_codes,
        face_curvature=face_curvature,
        face_radii=face_radii,
        face_area=face_area,
        face_normal=face_normal,
        face_point=face_point,
        edge_faces=edge_faces,
        edge_continuity=edge_continuity,
        edge_convexity=edge_convexity,
        edge_angle=edge_angle,
        edge_length=edge_length,
        edge_vertices=edge_vertices,
        vertex_points=vertex_points,
        polyline_points=polyline_points,
        polyline_offsets=offsets,
        c1_group=_connected_labels(n_faces, c1_pairs),
        c2_group=_connected_labels(n_faces, c2_pairs),
        meta={"schema": AAG_SCHEMA,
              "params": {"smooth_angle": float(smooth_angle),
                         "tollerance": float(tollerance),
                         "deflection": float(deflection)}},
    )


_ARRAY_FIELDS = [
    "face_convexity", "face_curvature", "face_radii", "face_area",
    "face_normal", "face_point", "edge_faces", "edge_continuity",
    "edge_convexity", "edge_angle", "edge_length", "edge_vertices",
    "vertex_points", "polyline_points", "polyline_offsets",
    "c1_group", "c2_group",
]


def stats(graph):
    """JSON-safe summary counts of an AAG (analysis stats + aag.json)."""
    interior = graph.interior_edges()
    convexity = graph.face_convexity
    return {
        "faces": int(graph.face_count),
        "edges": int(graph.edge_count),
        "face_convexity": {name: int(np.sum(convexity == code))
                           for code, name in FACE_NAMES.items()},
        "edge_convexity": {name: int(np.sum(
            graph.edge_convexity[interior] == code))
            for code, name in EDGE_NAMES.items()},
        "smooth_edges": int(np.sum(
            np.abs(graph.edge_continuity[interior]) >= 1)),
        "c1_groups": int(len(np.unique(graph.c1_group))),
        "c2_groups": int(len(np.unique(graph.c2_group))),
    }


def save_aag(workdir, graph, *, source_sha=None, mesh_fingerprint=None):
    """Persist the AAG as aag.npz + aag.json in the working directory.

    The json header records schema, the source STEP's content hash and the
    mesh fingerprint so any later stage can verify it is aligned with the
    artifacts it joins against before resuming work.
    """
    arrays = {name: getattr(graph, name) for name in _ARRAY_FIELDS}
    np.savez(os.path.join(workdir, AAG_FILE), **arrays)
    meta = {
        "schema": AAG_SCHEMA,
        "source_sha": source_sha,
        "mesh_fingerprint": mesh_fingerprint,
        "face_count": int(graph.face_count),
        "edge_count": int(graph.edge_count),
        "vertex_count": int(len(graph.vertex_points)),
        "params": graph.meta.get("params", {}),
        "stats": stats(graph),
    }
    with open(os.path.join(workdir, AAG_META_FILE), "w") as f:
        json.dump(meta, f)
    graph.meta = meta
    return meta


def load_aag(workdir):
    """Load the persisted AAG, or raise with a pointer at prep/aag.

    Refuses schema mismatches outright; a stale mesh fingerprint only
    matters to face-id joins, so callers compare meta["mesh_fingerprint"]
    themselves when they mix in mesh artifacts.
    """
    npz_path = os.path.join(workdir, AAG_FILE)
    meta_path = os.path.join(workdir, AAG_META_FILE)
    if not (os.path.exists(npz_path) and os.path.exists(meta_path)):
        raise ValueError("no AAG artifact in the working directory — run "
                         "the prep/aag analysis first")
    with open(meta_path) as f:
        meta = json.load(f)
    if meta.get("schema") != AAG_SCHEMA:
        raise ValueError(
            f"AAG artifact schema {meta.get('schema')} != {AAG_SCHEMA} — "
            "re-run the prep/aag analysis")
    with np.load(npz_path) as data:
        arrays = {name: np.array(data[name]) for name in _ARRAY_FIELDS}
    return AAG(**arrays, meta=meta)


def group_vertices(graph, faces):
    """Canonical vertex ids of every edge adjacent to any face in the set."""
    faces = np.asarray(sorted(faces))
    adjacent = np.isin(graph.edge_faces, faces).any(axis=1)
    vertex_ids = np.unique(graph.edge_vertices[adjacent])
    return vertex_ids[vertex_ids >= 0]


def axial_span(graph, faces, axis_loc, axis_dir):
    """(min_t, max_t) of the face group's vertices projected onto an axis."""
    vertex_ids = group_vertices(graph, faces)
    if not len(vertex_ids):
        return 0.0, 0.0
    t = (graph.vertex_points[vertex_ids] - axis_loc) @ axis_dir
    return float(t.min()), float(t.max())


def get_connected_subgraph(graph, base_face, ignore_complex=False):
    """C1-connected component of ``base_face`` as a networkx subgraph
    (instapart AdjacencyGraph.get_connected_subgraph)."""
    import networkx as nx

    c1 = graph.C1_faces
    component = nx.node_connected_component(c1, int(base_face))
    if ignore_complex:
        kept = {node for node in component
                if c1.nodes[node]["convexity"] != FACE_COMPLEX}
        sub = c1.subgraph(kept)
        component = nx.node_connected_component(sub, int(base_face))
    return c1.subgraph(component)


def get_sheet_base(graph, faces, min_thickness=1e-3, tollerance=TOLLERANCE):
    """Detect the two opposite sheet faces and the sheet thickness.

    Port of instapart AdjacencyGraph.get_sheet_base: from the largest face,
    shoot a ray backwards along its outward normal (GeomAPI_IntCS against
    each candidate's surface) and keep the nearest anti-parallel face with
    opposite convexity that is not an embossing (a face whose adjacent
    edges are mostly concave sits inside a feature, not on the sheet).

    ``faces`` is the live TopoDS face list in canonical order (the caller
    reloads source.stp — the ray cast needs real surfaces).
    Returns (base_index, opposite_index or None, thickness).
    """
    from OCP.Geom import Geom_Line
    from OCP.GeomAPI import GeomAPI_IntCS
    from OCP.BRep import BRep_Tool
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt, gp_Pnt2d, gp_Vec

    order = np.argsort(graph.face_area)[::-1]
    first_index = int(order[0])
    first_point_arr = graph.face_point[first_index]
    first_normal_arr = graph.face_normal[first_index]
    if not (np.isfinite(first_point_arr).all()
            and np.isfinite(first_normal_arr).all()):
        return first_index, None, 0.0
    first_point = gp_Pnt(*[float(c) for c in first_point_arr])
    first_normal = gp_Vec(*[float(c) for c in first_normal_arr])

    thickness = float("inf")
    second_index = None

    for candidate in order[1:]:
        candidate = int(candidate)
        if (int(graph.face_convexity[first_index])
                != -int(graph.face_convexity[candidate])):
            continue

        surface = BRep_Tool.Surface_s(faces[candidate])
        ray = Geom_Line(gp_Lin(first_point, gp_Dir(first_normal)))
        intersection = GeomAPI_IntCS(ray, surface)
        if not intersection.IsDone() or intersection.NbPoints() == 0:
            continue

        best = None
        for i in range(1, intersection.NbPoints() + 1):
            u, v, w = intersection.Parameters(i)
            if w < 0 and (best is None or abs(w) < abs(best[2])):
                best = (u, v, w)
        if best is None:
            continue  # candidate is in front of, not behind, the base

        analysis = ShapeAnalysis_Surface(surface)
        candidate_point = analysis.Value(gp_Pnt2d(best[0], best[1]))
        candidate_thickness = first_point.Distance(candidate_point)
        try:
            candidate_normal = normal_at_point(
                faces[candidate], candidate_point, tollerance)
        except Exception:
            continue
        if not first_normal.IsOpposite(candidate_normal, np.pi / 180):
            continue

        # embossing veto: a candidate ringed by mostly concave edges sits
        # inside a recessed/raised feature rather than on the sheet body
        adjacent = graph.interior_edges() & np.any(
            graph.edge_faces == candidate, axis=1)
        concave = graph.edge_convexity[adjacent] == EDGE_CONCAVE
        is_embossing = concave.size > 0 and (
            int(concave.sum()) >= int((~concave).sum()))

        if (candidate_thickness + tollerance < thickness
                and candidate_thickness >= min_thickness
                and not is_embossing):
            thickness = candidate_thickness
            second_index = candidate

    if not np.isfinite(thickness):
        thickness = 0.0
    logger.info(f"Detected sheet thickness: {thickness:.3f}")
    return first_index, second_index, thickness
