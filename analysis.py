from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn
import numpy as np
import json
import time

from loguru import logger
import os
from utils import log_execution_time


@log_execution_time
def load_mesh(path):
    return mm.loadMesh(path)


@log_execution_time
def save_obj_mesh(verts, faces, path):
    with open(path, 'w') as file:
        # Write vertices to file
        for vert in verts:
            file.write(f"v {vert[0]} {vert[1]} {vert[2]}\n")

        # Write faces to file
        for face in faces:
            face_line = "f " + " ".join([f"{v_idx + 1}" for v_idx in face])
            file.write(face_line + "\n")


@log_execution_time
def get_mesh_data(mesh):
    verts = mn.getNumpyVerts(mesh)
    faces = mn.getNumpyFaces(mesh.topology)
    return verts, faces


@log_execution_time
def save_mesh(mesh, path):
    verts, faces = get_mesh_data(mesh)
    
    if path.endswith(".obj"):
        save_obj_mesh(verts, faces, path)
        
    else:
        raise ValueError("Invalid file format. Only .obj files are supported")
    

@log_execution_time    
def offset_mesh(mesh, offset=0, tollerance=1e-2):
    
    # Setup parameters
    params = mm.OffsetParameters()
    params.voxelSize = tollerance
    
    # If you have holes in mesh
    # if mm.findRightBoundary(mesh.topology).empty():
    params.signDetectionMode = mm.SignDetectionMode.HoleWindingRule
    
    # Make offset mesh
    return mm.offsetMesh(mesh, offset, params)
    

@log_execution_time    
def heal_mesh( mesh : mm.Mesh, voxelSize : float, decimate : bool = True) -> mm.Mesh:
    numHoles = mm.findRightBoundary(mesh.topology).size()
    oParams = mm.GeneralOffsetParameters()
    if (numHoles != 0):
        oParams.signDetectionMode = mm.SignDetectionMode.HoleWindingRule
  
    oParams.voxelSize = voxelSize
    resMesh = mm.generalOffsetMesh(mesh, 0.0, oParams)
    if (decimate):
        resMesh.packOptimally(False)
        dSettings = mm.DecimateSettings()
        dSettings.maxError = 0.25 * voxelSize
        dSettings.tinyEdgeLength = mesh.computeBoundingBox().diagonal() * 1e-4
        dSettings.stabilizer = 1e-5
        dSettings.packMesh = True
        dSettings.subdivideParts = 64
        mm.decimateMesh(resMesh,dSettings)
  
    return resMesh


@log_execution_time
def subdivide_mesh(mesh, max_edge_len):
    """
    Refine the mesh in place until no edge is longer than `max_edge_len`,
    WITHOUT changing the shape (maxDeviationAfterFlip = 0): planar facets
    stay planar and sharp edges stay sharp. Use this instead of healing for
    clean CAD tessellations (STEP): analysis results are reported per face,
    so face density sets how finely results localize on the part, while the
    geometry stays exact.
    """
    settings = mm.SubdivideSettings()
    settings.maxEdgeLen = max_edge_len
    settings.maxEdgeSplits = 100_000_000
    settings.maxDeviationAfterFlip = 0.0
    mm.subdivideMesh(mesh, settings)
    return mesh


@log_execution_time
def get_inside_indices(mesh_a, mesh_b):
    bOperation = mm.BooleanOperation.InsideA
    bResMapper = mm.BooleanResultMapper()
    bResult = mm.boolean(mesh_a, mesh_b, bOperation, None, bResMapper)

    inner_faces = mesh_a.topology.getValidFaces()
    for f in inner_faces:
        bs = mm.FaceBitSet()
        bs.resize( f.get()+1)
        bs.set(f)
        if (bResMapper.map(bs, mm.BooleanResMapObj.A).count() == 0):
            inner_faces.set(f,False)
    inner_verts = mesh_a.topology.getValidVerts()
    for v in inner_verts:
        bs = mm.VertBitSet()
        bs.resize( v.get()+1)
        bs.set(v)
        if (bResMapper.map(bs, mm.BooleanResMapObj.A).count() == 0):
            inner_verts.set(v,False)
    
    return mn.getNumpyBitSet(inner_faces), mn.getNumpyBitSet(inner_verts)


@log_execution_time
def sample_unity_vectors(n):
    # Using the Golden Spiral method to uniformly distribute points on a sphere
    indices = np.arange(0, n, 1)
    phi = np.pi * (3. - np.sqrt(5.))  # golden angle in radians
    y = 1 - (indices / (n - 1)) * 2  # y goes from 1 to -1
    radius = np.sqrt(1 - y * y)  # radius at y

    theta = phi * indices  # golden angle increment

    x = np.cos(theta) * radius
    z = np.sin(theta) * radius

    return np.vstack((x, y, z)).T


@log_execution_time
def sample_unity_vector_pairs(n):
    # Double the number of points to account for mirroring 
    n  *= 2

    # Using the Golden Spiral method to uniformly distribute n points on a sphere
    indices = np.arange(0, n, 1)
    phi = np.pi * (3. - np.sqrt(5.))  # Golden angle in radians
    y = 1 - (indices / ((n) - 1)) * 2  # y goes from 1 to -1, adjusted for n points
    
    # Sample only the top hemisphere
    indices = indices[y >= 0]
    y = y[y >= 0]
    
    # Compute the radius and theta
    radius = np.sqrt(1 - y * y)  # Radius at y
    theta = phi * indices  # Golden angle increment

    # Compute the x and z coordinates
    x = np.cos(theta) * radius
    z = np.sin(theta) * radius

    # Generate the original points
    points = np.vstack((x, y, z)).T

    # Mirror the points by negating the coordinates
    mirrored_points = points * -1

    # Initialize an array to hold both points and their mirrors
    full_points = np.zeros((n, 3))

    # Place each point and its mirror next to each other
    full_points[0::2] = points
    full_points[1::2] = mirrored_points

    return full_points


# --- multi-source candidate directions -------------------------------------
# Every generated set stays laid out as antipodal pairs (row 2k / 2k+1 are
# opposite) so mold search (range(0, D, 2)) and CNC setups (direction ^ 1)
# keep working. New sources are appended as pairs via _append_pair, tagged
# with a provenance record index-aligned to directions.npy rows.

DIRECTION_SOURCES = ("uniform", "principal_axis", "bbox_axis", "hole_axis",
                     "face_normal", "average_normal", "manual")

_AXIS_NAMES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")


def _axis_label(vec):
    """'+Z'-style tag when the vector is (near) a world axis, else ''."""
    v = np.asarray(vec, dtype=float)
    a = int(np.argmax(np.abs(v)))
    if abs(v[a]) >= 0.99:
        return _AXIS_NAMES[2 * a + (0 if v[a] > 0 else 1)]
    return ""


def _unit(vec):
    v = np.asarray(vec, dtype=float).reshape(3)
    norm = float(np.linalg.norm(v))
    if norm < 1e-9:
        raise ValueError(f"degenerate direction vector: {list(vec)}")
    return v / norm


def canonical_vector(vec):
    """Unit vector rounded to 6 dp — stable for hashing and dedup."""
    return np.round(_unit(vec), 6)


def average_face_normal(normals, face_indices):
    """Mean of the exact per-face normals over a face group, normalized.

    The averaging is what makes a single candidate direction meaningful for
    curved / double-curved surfaces: a group of faces collapses to one
    representative approach axis. ``normals`` is the (F, 3) array from
    ``pipeline.load_face_normals``; ``face_indices`` are rows into it.
    """
    idx = np.asarray(face_indices, dtype=int)
    if idx.size == 0:
        raise ValueError("empty face group")
    mean = normals[idx].mean(axis=0)
    return _unit(mean)


def _dedup_seen(existing):
    """A membership test for canonical direction vectors, sign-agnostic.

    Antipodal pairs mean +v and -v are the same candidate, so we key on the
    lexicographically-canonical of (v, -v).
    """
    seen = set()

    def key(v):
        v = np.round(v, 6)
        flip = tuple(v) < tuple(-v)
        canon = -v if flip else v
        return tuple(np.round(canon, 6))

    for v in existing:
        seen.add(key(v))
    return seen, key


def _append_pair(dirs, sources, vec, source, label, detail, seen, keyfn,
                 dedup_deg):
    """Append (v, -v) with provenance, unless a near-duplicate already exists.

    Returns True when the pair was added. ``dedup_deg`` is the angular
    threshold (degrees) below which the candidate coincides with an existing
    direction and is dropped, folding its meaning onto the surviving row.
    """
    v = canonical_vector(vec)
    if seen is not None:
        cos_tol = np.cos(np.radians(dedup_deg))
        for existing_key in seen:
            e = np.asarray(existing_key, dtype=float)
            if abs(float(np.dot(v, e))) >= cos_tol:
                return False
        seen.add(keyfn(v))
    pos = _axis_label(v) or label
    neg = _axis_label(-v) or (f"{label} (−)" if label else label)
    dirs.append(v)
    sources.append({"source": source, "label": pos, "detail": detail})
    dirs.append(-v)
    sources.append({"source": source, "label": neg, "detail": detail})
    return True


def pca_axes(verts):
    """Three orthonormal principal (PCA) axes of the vertex cloud.

    Genuinely part-aligned, unlike the world ±X/±Y/±Z of ``axes=True``.
    Eigenvectors of the vertex covariance, ordered by descending spread.
    """
    pts = np.asarray(verts, dtype=float)
    cov = np.cov(pts.T)
    _, vecs = np.linalg.eigh(cov)          # ascending eigenvalues
    return [vecs[:, i] for i in (2, 1, 0)]  # major axis first


def hole_axes_from_geometry(surface_params):
    """Candidate axes from analytic quadric faces (cylinder/cone/torus).

    ``surface_params`` is ``brep_meta.json['surface_params']`` — a per-BREP-face
    list of dicts or None. Returns [(axis_vec, detail_dict), ...], coaxial
    duplicates merged so a hole drilled through N faces yields one axis whose
    ``detail['brep_faces']`` lists every contributing BREP face id (so the
    viewer can highlight the hole when its arrow is clicked).
    """
    out = []  # [ [vec, detail], ... ]
    for idx, params in enumerate(surface_params or []):
        if not params:
            continue
        kind = params.get("type")
        if kind == "cylinder":
            detail = {"surface": "cylinder", "radius": params.get("radius"),
                      "point": params.get("point")}
        elif kind == "cone":
            detail = {"surface": "cone", "apex": params.get("apex")}
        elif kind == "torus":
            detail = {"surface": "torus", "center": params.get("center")}
        else:
            continue
        axis = params.get("axis")
        if axis is None:
            continue
        try:
            vec = _unit(axis)
        except ValueError:
            continue
        # merge coaxial: same axis line collapses to one entry, accumulating
        # the contributing BREP face ids
        for entry in out:
            if abs(float(np.dot(vec, entry[0]))) >= np.cos(np.radians(1.0)):
                entry[1]["brep_faces"].append(idx)
                break
        else:
            detail["brep_faces"] = [idx]
            out.append([vec, detail])
    return [(vec, detail) for vec, detail in out]


def assemble_directions(workdir, *, count=64, axes=False, bbox_axes=False,
                        hole_axes=False, manual=(), face_groups=(),
                        dedup_deg=1.0):
    """Build the candidate direction set from all sources, with provenance.

    Returns ``(directions (N, 3) float, sources list[dict])`` where sources[i]
    describes directions[i]. World axes and uniform samples come first (index
    stability); bbox/hole/face/manual sources append at the tail so toggling
    them does not renumber the base set. Every block is antipodal-paired.
    """
    import pipeline  # local import: pipeline imports analysis at module load

    dirs, sources = [], []
    seen, keyfn = _dedup_seen([])

    # 1. world axes (kept first so indices 0-5 stay put across recomputes)
    if axes:
        for vec, name in (([1, 0, 0], "X"), ([0, 1, 0], "Y"), ([0, 0, 1], "Z")):
            _append_pair(dirs, sources, vec, "principal_axis", f"+{name}",
                         {"axis": name}, seen, keyfn, dedup_deg)

    # 2. uniform golden-spiral samples
    for i, vec in enumerate(sample_unity_vector_pairs(count)[0::2]):
        _append_pair(dirs, sources, vec, "uniform", f"uniform {i}", {},
                     seen, keyfn, dedup_deg)

    # 3. PCA / oriented bounding-box axes
    if bbox_axes:
        verts, _ = pipeline.load_mesh_arrays(workdir)
        for i, vec in enumerate(pca_axes(verts)):
            _append_pair(dirs, sources, vec, "bbox_axis", f"pca {i}",
                         {"axis": i}, seen, keyfn, dedup_deg)

    # 4. hole / cylinder / cone / torus axes
    if hole_axes:
        meta_path = os.path.join(workdir, pipeline.BREP_META_FILE)
        if os.path.exists(meta_path):
            with open(meta_path) as handle:
                surface_params = json.load(handle).get("surface_params")
            for vec, detail in hole_axes_from_geometry(surface_params):
                label = "hole"
                radius = detail.get("radius")
                if radius:
                    label = f"hole ø{2 * radius:.1f}"
                _append_pair(dirs, sources, vec, "hole_axis", label, detail,
                             seen, keyfn, dedup_deg)

    # 5. averaged-normal face groups
    if len(face_groups):
        normals = pipeline.load_face_normals(workdir)
        for gi, group in enumerate(face_groups):
            group = [int(f) for f in group]
            if not group:
                continue
            vec = average_face_normal(normals, group)
            src = "face_normal" if len(group) == 1 else "average_normal"
            label = (f"face {group[0]}" if len(group) == 1
                     else f"avg of {len(group)} faces")
            _append_pair(dirs, sources, vec, src, label,
                         {"faces": group, "group": gi}, seen, keyfn, dedup_deg)

    # 6. manual axes
    for vec in manual:
        _append_pair(dirs, sources, vec, "manual",
                     f"manual [{', '.join(f'{c:g}' for c in _unit(vec))}]",
                     {"vector": [float(c) for c in _unit(vec)]},
                     seen, keyfn, dedup_deg)

    if not dirs:
        # nothing requested — fall back to a bare uniform sample so downstream
        # never sees an empty direction set
        for i, vec in enumerate(sample_unity_vector_pairs(count)[0::2]):
            _append_pair(dirs, sources, vec, "uniform", f"uniform {i}", {},
                         seen, keyfn, dedup_deg)

    return np.asarray(dirs, dtype=float), sources


@log_execution_time
def compute_accessibility(mesh, directions, face_count, *, tolerance_deg=0.1,
                          pixel=None, normals=None, chord_error=0.0):
    """Per-direction face accessibility via our own visibility test.

    A face is accessible iff it faces the direction within `tolerance_deg`
    (near-vertical walls are deterministically front-facing — no speckle)
    and no material shadows it per a rendered height map (zmap engine).
    `pixel` is the height-map resolution; None derives it from the part's
    bounding box diagonal. ``normals`` overrides the facet normals (pass
    exact BREP surface normals for STEP parts). ``chord_error`` is the
    mesh's chord/deflection bound (see zmap.face_visibility).
    """
    from zmap import face_visibility

    verts, faces = get_mesh_data(mesh)

    if pixel is None:
        diagonal = np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))
        pixel = float(np.clip(diagonal / 1000.0, 0.05, 1.0))
        logger.debug(f"Auto visibility map pixel: {pixel:.3f}")

    # gather the per-direction invariants once — the loop below renders one
    # height map per direction and everything else is shared
    tri = verts[faces]
    centroids = tri.mean(axis=1)
    if normals is None:
        normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True),
                              1e-30)
    normals = np.asarray(normals, dtype=np.float64)

    dir_count = len(directions)
    accessibility = np.ones((dir_count, face_count), dtype=bool)
    for i in range(dir_count):
        accessibility[i, :] = face_visibility(
            mesh, verts, faces, directions[i],
            tolerance_deg=tolerance_deg, pixel=pixel, normals=normals,
            chord_error=chord_error, centroids=centroids)

    return accessibility