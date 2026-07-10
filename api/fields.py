"""Binary serving of mesh arrays and cached per-vertex/per-face fields.

Everything is streamed as raw little-endian typed arrays, the transport the
viewer consumes directly into Float32Array/Uint32Array/Uint8Array.
"""

import os

import numpy as np

import pipeline
from processes.base import RESULTS_DIR

NORMALS_FILE = "normals.npy"


def mesh_bytes(workdir, which):
    """Serve verts/faces/normals as raw arrays. Returns (bytes, dtype)."""
    if which == "verts":
        verts = np.load(os.path.join(workdir, pipeline.FINE_VERTS_FILE))
        return np.ascontiguousarray(verts, dtype="<f4").tobytes(), "<f4"
    if which == "faces":
        faces = np.load(os.path.join(workdir, pipeline.FINE_FACES_FILE))
        return np.ascontiguousarray(faces, dtype="<u4").tobytes(), "<u4"
    if which == "normals":
        return face_normals(workdir).tobytes(), "<f4"
    raise FileNotFoundError(which)


def face_normals(workdir):
    """Unit face normals, memo-cached next to the mesh arrays.

    The angle to any approach direction is then a client-side dot product
    (surface classification, wall detection).
    """
    normals_path = os.path.join(workdir, NORMALS_FILE)
    faces_path = os.path.join(workdir, pipeline.FINE_FACES_FILE)
    if os.path.exists(normals_path) and (
            os.path.getmtime(normals_path) >= os.path.getmtime(faces_path)):
        return np.load(normals_path)

    verts, faces = pipeline.load_mesh_arrays(workdir)
    tri = verts[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = (normals / np.maximum(lengths, 1e-30)).astype("<f4")
    np.save(normals_path, normals)
    return normals


def zcache_field_bytes(workdir, file_stem, key):
    """One member of <workdir>/zcache/<file_stem>.npz as float32 bytes."""
    path = os.path.join(workdir, "zcache", f"{file_stem}.npz")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    stored = np.load(path, allow_pickle=False)
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
    stored = np.load(path, allow_pickle=False)
    if key not in stored.files:
        raise FileNotFoundError(key)
    array = stored[key]
    if array.dtype in (np.uint8, np.bool_):
        return np.ascontiguousarray(array, dtype="<u1").tobytes(), "<u1"
    if array.dtype in (np.uint32, np.int32, np.uint64, np.int64):
        return np.ascontiguousarray(array, dtype="<u4").tobytes(), "<u4"
    return np.ascontiguousarray(array, dtype="<f4").tobytes(), "<f4"
