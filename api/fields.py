"""Binary serving of mesh arrays and cached per-vertex/per-face fields.

Everything is streamed as raw little-endian typed arrays, the transport the
viewer consumes directly into Float32Array/Uint32Array/Uint8Array.
"""

import os

import numpy as np

import pipeline
from processes.base import RESULTS_DIR


# fine mesh arrays + the coarse display preview (served the same way; the
# coarse buffers are display-only and never an index space for results)
_MESH_ARRAYS = {
    "verts": (pipeline.FINE_VERTS_FILE, "<f4"),
    "faces": (pipeline.FINE_FACES_FILE, "<u4"),
    "coarse_verts": (pipeline.COARSE_VERTS_FILE, "<f4"),
    "coarse_faces": (pipeline.COARSE_FACES_FILE, "<u4"),
    "coarse_normals": (pipeline.COARSE_NORMALS_FILE, "<f4"),
    "coarse_brep_faces": (pipeline.COARSE_BREP_FACES_FILE, "<u4"),
}


# which -> the file backing it, for ETag revalidation on the serving route
MESH_ARRAY_FILE = {which: path for which, (path, _dt) in _MESH_ARRAYS.items()}
MESH_ARRAY_FILE["normals"] = pipeline.NORMALS_FILE


def mesh_bytes(workdir, which):
    """Serve a mesh array as raw little-endian bytes. Returns (bytes, dtype)."""
    if which == "normals":
        return face_normals(workdir).tobytes(), "<f4"
    spec = _MESH_ARRAYS.get(which)
    if spec is None:
        raise FileNotFoundError(which)
    path, dtype = spec
    full = os.path.join(workdir, path)
    if not os.path.exists(full):
        raise FileNotFoundError(full)
    return np.ascontiguousarray(np.load(full), dtype=dtype).tobytes(), dtype


def face_normals(workdir):
    """Unit face normals for client-side classification (exact BREP surface
    normals for STEP parts, facet normals otherwise)."""
    return np.ascontiguousarray(pipeline.load_face_normals(workdir),
                                dtype="<f4")


def zcache_field_bytes(workdir, file_stem, key):
    """One member of <workdir>/zcache/<file_stem>.npz as float32 bytes."""
    path = os.path.join(workdir, "zcache", f"{file_stem}.npz")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with np.load(path, allow_pickle=False) as stored:
        if key not in stored.files:
            raise FileNotFoundError(key)
        return np.ascontiguousarray(stored[key], dtype="<f4").tobytes(), "<f4"


def brep_faces_bytes(workdir):
    """Per-face BREP face ids as uint32."""
    path = os.path.join(workdir, pipeline.BREP_FACES_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return np.ascontiguousarray(np.load(path), dtype="<u4").tobytes(), "<u4"


def brep_edges_bytes(workdir):
    """BREP edge segment coordinates as float32 (E*2*3)."""
    path = os.path.join(workdir, pipeline.BREP_EDGES_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return np.ascontiguousarray(np.load(path), dtype="<f4").tobytes(), "<f4"


def brep_edge_pairs_bytes(workdir):
    """Unordered BREP face id pair per edge segment as uint32 (E*2)."""
    path = os.path.join(workdir, pipeline.BREP_EDGE_PAIRS_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return np.ascontiguousarray(np.load(path), dtype="<u4").tobytes(), "<u4"


def subfaces_bytes(workdir):
    """Per-face effective (sub-)face ids as uint32."""
    path = os.path.join(workdir, pipeline.SUBFACES_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return np.ascontiguousarray(np.load(path), dtype="<u4").tobytes(), "<u4"


def subface_edges_bytes(workdir):
    """Effective-face boundary segment coordinates as float32 (E*2*3)."""
    path = os.path.join(workdir, pipeline.SUBFACE_EDGES_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return np.ascontiguousarray(np.load(path), dtype="<f4").tobytes(), "<f4"


def subface_edge_pairs_bytes(workdir):
    """Unordered effective face id pair per segment as uint32 (E*2)."""
    path = os.path.join(workdir, pipeline.SUBFACE_EDGE_PAIRS_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return np.ascontiguousarray(np.load(path), dtype="<u4").tobytes(), "<u4"


def accessibility_bytes(workdir, direction_index):
    """One accessibility row as a per-face uint8 mask."""
    path = os.path.join(workdir, pipeline.ACCESSIBILITY_FILE)
    accessibility = np.load(path, mmap_mode="r")
    if not 0 <= direction_index < accessibility.shape[0]:
        raise FileNotFoundError(f"direction {direction_index}")
    row = np.ascontiguousarray(accessibility[direction_index], dtype="<u1")
    return row.tobytes(), "<u1"


def result_field_bytes(workdir, process_id, analysis_id, params_hash, key):
    """One member of a generic result npz; masks as u1, scalars as f4."""
    path = os.path.join(workdir, RESULTS_DIR, process_id, analysis_id,
                        f"{params_hash}.npz")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with np.load(path, allow_pickle=False) as stored:
        if key not in stored.files:
            raise FileNotFoundError(key)
        array = stored[key]
    if array.dtype in (np.uint8, np.bool_):
        return np.ascontiguousarray(array, dtype="<u1").tobytes(), "<u1"
    if array.dtype in (np.uint32, np.int32, np.uint64, np.int64):
        return np.ascontiguousarray(array, dtype="<u4").tobytes(), "<u4"
    return np.ascontiguousarray(array, dtype="<f4").tobytes(), "<f4"
