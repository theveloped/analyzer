"""BREP-aware STEP tessellation with per-triangle face provenance.

meshlib's STEP import merges every face into one anonymous triangle soup, so
we mesh the BREP ourselves through OCCT (OCP bindings — pythonocc-core has
no pip wheels; the OCP API is the same OpenCASCADE surface): tessellate per
TopoDS face, tag each triangle with its face index, and weld the per-face
node arrays into ONE conformal mesh. Welding by exact coordinate is safe
because OCCT discretizes every shared BREP edge once and reuses that
polygon on both adjacent faces — boundary nodes coincide bitwise, giving
shared vertices along BREP edges by construction.

The analysis-resolution refinement is our own conformal midpoint
subdivision (subdivide_tagged): meshlib's subdivideMesh performs edge flips
that move triangles across BREP face boundaries, whereas midpoint splitting
lets every child inherit its parent's face id exactly.
"""

import numpy as np
from loguru import logger

from utils import log_execution_time

# surface type enum values (GeomAbs_SurfaceType) recorded per BREP face for
# later draft/AAG work
SURFACE_TYPES = [
    "plane", "cylinder", "cone", "sphere", "torus", "bezier", "bspline",
    "revolution", "extrusion", "offset", "other",
]


def load_step_shape(path):
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != 1:  # IFSelect_RetDone
        raise ValueError(f"failed to read STEP file: {path}")
    reader.TransferRoots()
    return reader.OneShape()


def iter_faces(shape):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        yield TopoDS.Face_s(explorer.Current())
        explorer.Next()


@log_execution_time
def mesh_step(path, *, deflection=0.5, angular_deflection=0.5):
    """Tessellate a STEP file keeping per-triangle BREP face provenance.

    Returns (verts float64[V,3], faces int32[F,3], face_ids int32[F],
    surface_types list[str] per BREP face). The mesh is conformal: vertices
    along shared BREP edges are welded into single entries.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_Orientation
    from OCP.TopLoc import TopLoc_Location

    shape = load_step_shape(path)
    BRepMesh_IncrementalMesh(shape, float(deflection), False,
                             float(angular_deflection), True)

    all_points = []
    all_triangles = []
    face_ids = []
    surface_types = []
    offset = 0

    for face_index, face in enumerate(iter_faces(shape)):
        surface_types.append(
            SURFACE_TYPES[min(int(BRepAdaptor_Surface(face).GetType()),
                              len(SURFACE_TYPES) - 1)])

        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            continue
        transform = location.Transformation()

        n_nodes = triangulation.NbNodes()
        points = np.empty((n_nodes, 3), dtype=np.float64)
        for i in range(1, n_nodes + 1):
            p = triangulation.Node(i).Transformed(transform)
            points[i - 1] = (p.X(), p.Y(), p.Z())

        n_tris = triangulation.NbTriangles()
        tris = np.empty((n_tris, 3), dtype=np.int64)
        for i in range(1, n_tris + 1):
            t = triangulation.Triangle(i)
            tris[i - 1] = (t.Value(1), t.Value(2), t.Value(3))
        tris -= 1  # OCC is 1-based

        if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
            tris = tris[:, ::-1]

        all_points.append(points)
        all_triangles.append(tris + offset)
        face_ids.append(np.full(n_tris, face_index, dtype=np.int32))
        offset += n_nodes

    if not all_points:
        raise ValueError(f"STEP file produced no triangulation: {path}")

    points = np.vstack(all_points)
    triangles = np.vstack(all_triangles)
    ids = np.concatenate(face_ids)

    # weld: shared BREP edge nodes coincide bitwise (same edge polygon on
    # both faces); round defensively against last-ulp noise from transforms
    keys = np.round(points / 1e-9).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True,
                                  return_inverse=True)
    verts = points[first]
    faces = inverse[triangles].astype(np.int32)

    # drop degenerate triangles collapsed by the weld
    valid = ((faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2])
             & (faces[:, 0] != faces[:, 2]))
    faces = faces[valid]
    ids = ids[valid]

    logger.info(f"Tessellated {len(surface_types)} BREP faces into "
                f"{len(faces)} triangles / {len(verts)} welded vertices")
    return verts, faces, ids, surface_types


@log_execution_time
def subdivide_tagged(verts, faces, face_ids, max_edge_len, max_rounds=32):
    """Conformal midpoint subdivision that preserves per-face tags exactly.

    meshlib's subdivideMesh performs edge flips that move triangles across
    their source BREP face boundaries (losing provenance), so we refine
    ourselves: per round, split every edge longer than max_edge_len at its
    midpoint and re-triangulate each face by its split pattern (1→2, 2→3,
    3→4 children); children inherit the parent's tag. Edge split decisions
    are per unique edge, so adjacent faces stay conformal (shared vertices
    along all edges, including BREP edges).
    """
    verts = np.asarray(verts, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    face_ids = np.asarray(face_ids, dtype=np.int32)

    for _ in range(max_rounds):
        corner_edges = np.stack(
            [faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]], axis=1)
        sorted_edges = np.sort(corner_edges.reshape(-1, 2), axis=1)
        unique_edges, inverse = np.unique(sorted_edges, axis=0,
                                          return_inverse=True)
        lengths = np.linalg.norm(
            verts[unique_edges[:, 0]] - verts[unique_edges[:, 1]], axis=1)
        split = lengths > max_edge_len
        if not split.any():
            break

        midpoint_of = np.full(len(unique_edges), -1, dtype=np.int64)
        midpoint_of[split] = len(verts) + np.arange(int(split.sum()))
        verts = np.vstack([verts, 0.5 * (verts[unique_edges[split, 0]]
                                         + verts[unique_edges[split, 1]])])
        mids = midpoint_of[inverse].reshape(-1, 3)  # per face: m01, m12, m20
        n_split = (mids >= 0).sum(axis=1)

        out_faces = [faces[n_split == 0]]
        out_ids = [face_ids[n_split == 0]]

        def emit(mask, columns):
            f, m, i = faces[mask], mids[mask], face_ids[mask]
            v = {"0": f[:, 0], "1": f[:, 1], "2": f[:, 2],
                 "a": m[:, 0], "b": m[:, 1], "c": m[:, 2]}
            for tri in columns:
                out_faces.append(np.stack([v[tri[0]], v[tri[1]], v[tri[2]]], 1))
                out_ids.append(i)

        # one split edge → 2 children (per which edge is split)
        one = n_split == 1
        emit(one & (mids[:, 0] >= 0), ["0a2", "a12"])
        emit(one & (mids[:, 1] >= 0), ["01b", "0b2"])
        emit(one & (mids[:, 2] >= 0), ["01c", "c12"])
        # two split edges → 3 children (deterministic diagonal)
        two = n_split == 2
        emit(two & (mids[:, 2] < 0), ["a1b", "0ab", "0b2"])   # m01+m12
        emit(two & (mids[:, 0] < 0), ["b2c", "1bc", "01c"])   # m12+m20
        emit(two & (mids[:, 1] < 0), ["0ac", "a1c", "c12"])   # m01+m20
        # three → 4:1
        emit(n_split == 3, ["0ac", "a1b", "cb2", "abc"])

        faces = np.vstack(out_faces)
        face_ids = np.concatenate(out_ids)

    return verts, faces.astype(np.int32), face_ids
