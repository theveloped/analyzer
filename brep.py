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


def shape_diagonal(shape):
    """Bounding-box diagonal of a BREP shape, before any tessellation."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return float(np.linalg.norm([xmax - xmin, ymax - ymin, zmax - zmin]))


def mesh_step(path, *, deflection=0.5, angular_deflection=0.5):
    """Tessellate a STEP file keeping per-triangle BREP face provenance."""
    return mesh_shape(load_step_shape(path), deflection=deflection,
                      angular_deflection=angular_deflection)


def _xyz(v):
    return [float(v.X()), float(v.Y()), float(v.Z())]


def _surface_params(surf):
    """JSON-safe analytic parameters of a BREP face surface, or None.

    Only the five analytic quadrics are captured — enough to evaluate the
    exact surface normal at any point, at any subdivision level. The cone is
    normalized at extraction (apex + axis it opens along, alpha >= 0) so the
    downstream formula is unconditional.
    """
    from OCP.GeomAbs import GeomAbs_SurfaceType

    kind = surf.GetType()
    if kind == GeomAbs_SurfaceType.GeomAbs_Plane:
        return {"type": "plane",
                "normal": _xyz(surf.Plane().Axis().Direction())}
    if kind == GeomAbs_SurfaceType.GeomAbs_Cylinder:
        cylinder = surf.Cylinder()
        axis = cylinder.Axis()
        return {"type": "cylinder", "point": _xyz(axis.Location()),
                "axis": _xyz(axis.Direction()),
                "radius": float(cylinder.Radius())}
    if kind == GeomAbs_SurfaceType.GeomAbs_Cone:
        cone = surf.Cone()
        axis = _xyz(cone.Axis().Direction())
        alpha = float(cone.SemiAngle())
        if alpha < 0:
            axis = [-c for c in axis]
            alpha = -alpha
        return {"type": "cone", "apex": _xyz(cone.Apex()), "axis": axis,
                "alpha": alpha}
    if kind == GeomAbs_SurfaceType.GeomAbs_Sphere:
        sphere = surf.Sphere()
        return {"type": "sphere", "center": _xyz(sphere.Location()),
                "radius": float(sphere.Radius())}
    if kind == GeomAbs_SurfaceType.GeomAbs_Torus:
        torus = surf.Torus()
        return {"type": "torus", "center": _xyz(torus.Location()),
                "axis": _xyz(torus.Axis().Direction()),
                "major_radius": float(torus.MajorRadius()),
                "minor_radius": float(torus.MinorRadius())}
    return None


@log_execution_time
def mesh_shape(shape, *, deflection=0.5, angular_deflection=0.5):
    """Tessellate a BREP shape keeping per-triangle face provenance.

    Returns (verts float64[V,3], faces int32[F,3], face_ids int32[F],
    surface_types list[str] per BREP face, surface_params list per BREP
    face, corner_uv float64[F,3,2]). The mesh is conformal: vertices along
    shared BREP edges are welded into single entries. corner_uv holds each
    triangle corner's surface parameters — per corner, not per vertex,
    because welded BREP-edge vertices sit on faces with different
    parametrizations. Only non-analytic faces (no surface_params) carry
    UVs; quadric faces get NaN rows since their normals never need them.
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_Orientation
    from OCP.TopLoc import TopLoc_Location

    BRepMesh_IncrementalMesh(shape, float(deflection), False,
                             float(angular_deflection), True)

    all_points = []
    all_triangles = []
    all_uv = []
    face_ids = []
    surface_types = []
    surface_params = []
    offset = 0

    for face_index, face in enumerate(iter_faces(shape)):
        adaptor = BRepAdaptor_Surface(face)
        surface_types.append(
            SURFACE_TYPES[min(int(adaptor.GetType()),
                              len(SURFACE_TYPES) - 1)])
        surface_params.append(_surface_params(adaptor))

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

        if surface_params[-1] is None and triangulation.HasUVNodes():
            uv_nodes = np.empty((n_nodes, 2), dtype=np.float64)
            for i in range(1, n_nodes + 1):
                p2 = triangulation.UVNode(i)
                uv_nodes[i - 1] = (p2.X(), p2.Y())
            all_uv.append(uv_nodes[tris])  # post-flip: corner k = faces[:, k]
        else:
            all_uv.append(np.full((n_tris, 3, 2), np.nan))

        all_points.append(points)
        all_triangles.append(tris + offset)
        face_ids.append(np.full(n_tris, face_index, dtype=np.int32))
        offset += n_nodes

    if not all_points:
        raise ValueError("BREP shape produced no triangulation")

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
    corner_uv = np.concatenate(all_uv)[valid]

    logger.info(f"Tessellated {len(surface_types)} BREP faces into "
                f"{len(faces)} triangles / {len(verts)} welded vertices")
    return verts, faces, ids, surface_types, surface_params, corner_uv


def _vote_sign(areas, candidate, current):
    """One sign per BREP face: area-weighted agreement of the candidate
    normals with the current (facet) normals. Returns +1.0 / -1.0, or 0.0
    when the vote is ambiguous (keep the facet normals then)."""
    vote = float(np.sum(
        areas * np.einsum("ij,ij->i", candidate, current)))
    if abs(vote) < 1e-12:
        return 0.0
    return 1.0 if vote > 0 else -1.0


def analytic_face_normals(verts, faces, face_ids, surface_params,
                          facet_normals):
    """Exact per-triangle surface normals on analytic BREP faces.

    Facet normals on curved faces are frozen at the coarse tessellation's
    chord planes (midpoint subdivision moves nothing), carrying an angular
    error of ±theta/2 that no affordable deflection removes. This evaluates
    each analytic surface's true normal at the triangle centroids instead —
    exact at any subdivision level. The sign is chosen once per BREP face by
    an area-weighted vote against the facet normals (REVERSED faces and hole
    walls come out outward without trusting individual sliver triangles).
    Non-analytic faces (handled by freeform_face_normals while the shape is
    alive) and degenerate evaluations keep their facet normals.

    Returns (normals, exact) — exact is the bool[F] mask of fine faces whose
    normal was actually replaced by an evaluated surface normal.
    """
    verts = np.asarray(verts, dtype=np.float64)
    faces = np.asarray(faces)
    normals = np.array(facet_normals, dtype=np.float64, copy=True)
    exact = np.zeros(len(faces), dtype=bool)

    tri = verts[faces]
    centroids = tri.mean(axis=1)
    areas = np.linalg.norm(
        np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)

    def unit(v):
        v = np.asarray(v, dtype=np.float64)
        return v / np.linalg.norm(v)

    for fid, params in enumerate(surface_params):
        if not params:
            continue
        idx = np.flatnonzero(face_ids == fid)
        if not idx.size:
            continue
        c = centroids[idx]
        kind = params["type"]

        if kind == "plane":
            candidate = np.tile(unit(params["normal"]), (len(idx), 1))
        elif kind == "cylinder":
            axis = unit(params["axis"])
            v = c - np.asarray(params["point"], dtype=np.float64)
            candidate = v - np.outer(v @ axis, axis)
        elif kind == "cone":
            axis = unit(params["axis"])
            alpha = float(params["alpha"])
            v = c - np.asarray(params["apex"], dtype=np.float64)
            radial = v - np.outer(v @ axis, axis)
            r = np.linalg.norm(radial, axis=1, keepdims=True)
            candidate = (np.cos(alpha) * radial / np.maximum(r, 1e-30)
                         - np.sin(alpha) * axis)
            candidate[r[:, 0] < 1e-9] = 0.0  # apex: keep facet normal
        elif kind == "sphere":
            candidate = c - np.asarray(params["center"], dtype=np.float64)
        elif kind == "torus":
            axis = unit(params["axis"])
            center = np.asarray(params["center"], dtype=np.float64)
            v = c - center
            inplane = v - np.outer(v @ axis, axis)
            ilen = np.linalg.norm(inplane, axis=1, keepdims=True)
            ring = center + params["major_radius"] * (
                inplane / np.maximum(ilen, 1e-30))
            candidate = c - ring
            candidate[ilen[:, 0] < 1e-9] = 0.0  # on the axis: keep facet
        else:
            continue

        length = np.linalg.norm(candidate, axis=1, keepdims=True)
        valid = length[:, 0] > 1e-9
        if not valid.any():
            continue
        candidate = candidate / np.maximum(length, 1e-30)

        sign = _vote_sign(areas[idx[valid]], candidate[valid],
                          normals[idx[valid]])
        if sign == 0.0:
            continue  # ambiguous — keep facet normals for this face
        normals[idx[valid]] = sign * candidate[valid]
        exact[idx[valid]] = True

    return normals, exact


@log_execution_time
def freeform_face_normals(shape, verts, faces, face_ids, surface_params,
                          coarse_faces, corner_uv, parent_facets, normals):
    """True surface normals on non-analytic BREP faces (bspline, bezier,
    revolution, extrusion, offset), evaluated on the live shape.

    The analytic path covers the five quadrics from JSON-safe params; every
    other surface would keep its facet normal, frozen at the coarse
    tessellation's chord planes. Midpoint subdivision is affine, so a fine
    face's centroid maps to the barycentric blend of its parent coarse
    facet's corner UVs — BRepAdaptor_Surface.D1 at that parameter gives the
    exact tangent plane, at any subdivision level. Each triangulation facet
    is continuous in its face's UV domain, so the interpolation never
    crosses a parametric seam. The sign is the same area-weighted
    per-BREP-face vote the analytic path uses. Degenerate parents, poles
    (|dU x dV| ~ 0) and non-C1 evaluations keep their facet normals.

    ``normals`` is the analytic_face_normals output; returns an updated
    copy plus the bool[F] mask of fine faces it set exactly. The D1 loop is
    per fine face by necessity (OCP evaluates one parameter at a time) but
    only runs on the freeform subset.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.gp import gp_Pnt, gp_Vec

    verts = np.asarray(verts, dtype=np.float64)
    faces = np.asarray(faces)
    normals = np.array(normals, dtype=np.float64, copy=True)
    exact = np.zeros(len(faces), dtype=bool)

    no_params = np.array([params is None for params in surface_params])
    uv_known = np.isfinite(corner_uv).all(axis=(1, 2))  # per coarse facet
    target = no_params[face_ids] & uv_known[parent_facets]
    if not target.any():
        return normals, exact

    idx = np.flatnonzero(target)
    tri = verts[faces[idx]]
    centroids = tri.mean(axis=1)
    areas = np.linalg.norm(
        np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)

    # barycentric coordinates of each centroid in its parent coarse facet
    # (children are coplanar with the parent, so the 2x2 solve is exact)
    corners = verts[np.asarray(coarse_faces)[parent_facets[idx]]]
    e0 = corners[:, 1] - corners[:, 0]
    e1 = corners[:, 2] - corners[:, 0]
    d = centroids - corners[:, 0]
    d00 = np.einsum("ij,ij->i", e0, e0)
    d01 = np.einsum("ij,ij->i", e0, e1)
    d11 = np.einsum("ij,ij->i", e1, e1)
    denom = d00 * d11 - d01 * d01
    ok = denom > 1e-12 * np.maximum(d00 * d11, 1e-300)  # sliver parents out
    safe = np.where(ok, denom, 1.0)
    wb = (d11 * np.einsum("ij,ij->i", d, e0)
          - d01 * np.einsum("ij,ij->i", d, e1)) / safe
    wc = (d00 * np.einsum("ij,ij->i", d, e1)
          - d01 * np.einsum("ij,ij->i", d, e0)) / safe
    parent_uv = corner_uv[parent_facets[idx]]
    uvs = (parent_uv[:, 0]
           + wb[:, None] * (parent_uv[:, 1] - parent_uv[:, 0])
           + wc[:, None] * (parent_uv[:, 2] - parent_uv[:, 0]))

    brep_faces_list = list(iter_faces(shape))
    fine_ids = face_ids[idx]
    candidate = np.zeros((len(idx), 3))
    evaluated = np.zeros(len(idx), dtype=bool)

    point, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    for fid in np.unique(fine_ids):
        rows = np.flatnonzero((fine_ids == fid) & ok)
        if not rows.size:
            continue
        adaptor = BRepAdaptor_Surface(brep_faces_list[fid])
        for row in rows:
            try:
                adaptor.D1(float(uvs[row, 0]), float(uvs[row, 1]),
                           point, du, dv)
            except Exception:
                continue  # non-C1 interval: keep the facet normal
            n = du.Crossed(dv)
            candidate[row] = (n.X(), n.Y(), n.Z())
            evaluated[row] = True

    lengths = np.linalg.norm(candidate, axis=1, keepdims=True)
    evaluated &= lengths[:, 0] > 1e-9
    evaluated &= np.isfinite(candidate).all(axis=1)
    candidate = candidate / np.maximum(lengths, 1e-30)

    updated = 0
    for fid in np.unique(fine_ids):
        rows = np.flatnonzero((fine_ids == fid) & evaluated)
        if not rows.size:
            continue
        sign = _vote_sign(areas[rows], candidate[rows], normals[idx[rows]])
        if sign == 0.0:
            continue  # ambiguous — keep facet normals for this face
        normals[idx[rows]] = sign * candidate[rows]
        exact[idx[rows]] = True
        updated += rows.size

    logger.info(f"Evaluated true normals on {updated} of {len(idx)} fine "
                f"faces across {int(no_params.sum())} freeform BREP faces")
    return normals, exact


@log_execution_time
def subdivide_tagged(verts, faces, face_ids, max_edge_len, max_rounds=32):
    """Conformal midpoint subdivision that preserves per-face tags exactly.

    meshlib's subdivideMesh performs edge flips that move triangles across
    their source BREP face boundaries (losing provenance), so we refine
    ourselves: per round, split every edge longer than max_edge_len at its
    midpoint and re-triangulate each face by its split pattern (1→2, 2→3,
    3→4 children); children inherit the parent's tag. Edge split decisions
    are per unique edge, so adjacent faces stay conformal (shared vertices
    along all edges, including BREP edges). ``face_ids`` may be (F,) or
    (F, K) — every operation is a row selection, so K tag columns (e.g.
    BREP id + parent facet index) ride through unchanged.
    """
    verts = np.asarray(verts, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    face_ids = np.asarray(face_ids, dtype=np.int32)

    for _ in range(max_rounds):
        corner_edges = np.stack(
            [faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]], axis=1)
        sorted_edges = np.sort(corner_edges.reshape(-1, 2), axis=1)
        # pack each sorted pair into one int64 — scalar np.unique is ~10x
        # faster than axis=0 on the multi-million edge sets of fine rounds
        keys = (sorted_edges[:, 0] << 32) | sorted_edges[:, 1]
        unique_keys, inverse = np.unique(keys, return_inverse=True)
        unique_edges = np.stack(
            [unique_keys >> 32, unique_keys & 0xFFFFFFFF], axis=1)
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
