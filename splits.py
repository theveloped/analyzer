"""User-driven splitting of BREP faces into sub-faces, without remeshing.

A cut is a path along existing mesh edges between two boundary vertices of
an effective face. Sub-faces are the connected components of a face's
triangles once adjacency across cut edges is removed — a pure relabeling
of the per-triangle face id, so `fine_faces.npy` indexing, accessibility,
zcache and every per-triangle result stay valid.

Source of truth is `face_splits.json` (the ordered cut list). Everything
else is derived by sequential replay and regenerated in full on every
mutation:

- `subfaces.npy`       int32 (F,)   effective face id per fine triangle
- `subface_edges.npy`  f32 (E,2,3)  boundary mesh-edge segments over
- `subface_edge_pairs.npy` u32 (E,2)  effective ids (pipeline recipe)
- `subface_meta.json`  n_brep / n_effective / parents / mesh fingerprint

Id rules: ids below n_brep always mean an untouched original BREP face.
New pieces get fresh ids appended above (never reused); a label that
separates retires entirely. Replay is deterministic, so undoing the last
cut reproduces the previous `subfaces.npy` byte for byte.

Framework-free on purpose (numpy/scipy only): the API routes wrap these
functions, the CLI and pipeline import them directly.
"""

import json
import os
from dataclasses import dataclass, field

import numpy as np
from loguru import logger

import molding
import pipeline

SPLITS_SCHEMA = 1


class StaleSplitsError(Exception):
    """face_splits.json references a mesh that has been regenerated."""


@dataclass
class ReplayState:
    eff: np.ndarray                # int32 (F,) effective face id per triangle
    n_brep: int
    parents: list                  # parents[i] = original id of n_brep + i
    cut_info: list                 # per cut: {"created": [...], "separated": bool}
    cut_edges: dict = field(default_factory=dict)  # face_orig -> set[(v0, v1)]

    @property
    def n_effective(self):
        return int(self.eff.max()) + 1


def _edge_key(a, b):
    return (a, b) if a < b else (b, a)


def _path_edges(path):
    return {_edge_key(int(a), int(b)) for a, b in zip(path[:-1], path[1:])}


def replay(faces, brep_ids, cuts):
    """Sequentially apply cuts, relabeling triangles into sub-face ids."""
    brep_ids = np.asarray(brep_ids)
    eff = brep_ids.astype(np.int32).copy()
    n_brep = int(brep_ids.max()) + 1
    state = ReplayState(eff=eff, n_brep=n_brep, parents=[], cut_info=[])
    pairs, edge_verts = molding.face_adjacency(faces)
    edge_keys = (edge_verts[:, 0].astype(np.int64) << 32
                 | edge_verts[:, 1].astype(np.int64))
    next_id = n_brep

    for cut in cuts:
        face_orig = int(cut["face_orig"])
        edges = state.cut_edges.setdefault(face_orig, set())
        edges |= _path_edges(cut["path"])

        # components of the original face's triangles with cut edges removed
        tri_mask = brep_ids == face_orig
        tri_idx = np.nonzero(tri_mask)[0]
        sel = tri_mask[pairs[:, 0]] & tri_mask[pairs[:, 1]]
        cut_keys = np.array([(a << 32) | b for a, b in edges], dtype=np.int64)
        sel &= ~np.isin(edge_keys, cut_keys)
        local = np.full(len(brep_ids), -1, dtype=np.int64)
        local[tri_idx] = np.arange(len(tri_idx))
        labels = _components(local[pairs[sel]], len(tri_idx))

        # a label refining into >=2 components retires; every piece gets a
        # fresh id, ordered by smallest triangle index (deterministic)
        created = []
        cur = state.eff[tri_idx]
        for label in np.unique(cur):
            comp = np.unique(labels[cur == label])
            if len(comp) < 2:
                continue
            firsts = sorted((tri_idx[(cur == label) & (labels == c)][0], c)
                            for c in comp)
            for _, c in firsts:
                state.eff[tri_idx[(cur == label) & (labels == c)]] = next_id
                state.parents.append(face_orig)
                created.append(next_id)
                next_id += 1
        state.cut_info.append({"created": created,
                               "separated": bool(created)})
    return state


def _components(local_pairs, count):
    """Connected-component label per node over undirected edge pairs."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    graph = coo_matrix((np.ones(len(local_pairs), dtype=np.int8),
                        (local_pairs[:, 0], local_pairs[:, 1])),
                       shape=(count, count))
    _, labels = connected_components(graph, directed=False)
    return labels


def cut_path(verts, faces, eff, face_id, start, end, cut_edges=()):
    """Shortest mesh-edge path across a face's interior between two
    boundary vertices. Raises ValueError with a user-facing message on
    every invalid request."""
    start, end = int(start), int(end)
    if start == end:
        raise ValueError("start and end are the same point")
    for v in (start, end):
        if not 0 <= v < len(verts):
            raise ValueError(f"vertex {v} out of range")
    region = np.nonzero(eff == face_id)[0]
    if len(region) == 0:
        raise ValueError(f"face {face_id} does not exist (already split?)")

    # census of the region's triangle edges: seen twice = interior,
    # seen once = boundary (open mesh edge or a different effective face)
    tris = faces[region].astype(np.int64)
    edges = np.stack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]],
                     axis=1).reshape(-1, 2)
    edges.sort(axis=1)
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    boundary_verts = set(uniq[counts == 1].ravel().tolist())
    for name, v in (("start", start), ("end", end)):
        if v not in boundary_verts:
            raise ValueError(
                f"{name} point is not on the face's boundary")

    interior = uniq[counts == 2]
    if len(cut_edges):
        keys = interior[:, 0] << 32 | interior[:, 1]
        cut_keys = np.array([(a << 32) | b for a, b in cut_edges],
                            dtype=np.int64)
        interior = interior[~np.isin(keys, cut_keys)]
    if len(interior) == 0:
        raise ValueError("face has no interior mesh edges left to cut along")

    # Dijkstra over interior edges only — the path cannot run along the
    # boundary, so it always separates something adjacent to it
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra

    node_ids = np.unique(interior)
    local = {int(v): i for i, v in enumerate(node_ids)}
    if start not in local or end not in local:
        raise ValueError("no interior mesh path between these points — "
                         "pick different points")
    lengths = np.linalg.norm(verts[interior[:, 0]] - verts[interior[:, 1]],
                             axis=1)
    row = np.array([local[int(v)] for v in interior[:, 0]])
    col = np.array([local[int(v)] for v in interior[:, 1]])
    graph = coo_matrix((lengths, (row, col)),
                       shape=(len(node_ids), len(node_ids)))
    dist, pred = dijkstra(graph, directed=False, indices=local[start],
                          return_predecessors=True)
    if not np.isfinite(dist[local[end]]):
        raise ValueError("no interior mesh path between these points — "
                         "pick different points")
    path = [end]
    node = local[end]
    while node != local[start]:
        node = pred[node]
        path.append(int(node_ids[node]))
    path.reverse()
    return path


def _paths(workdir):
    return {name: os.path.join(workdir, getattr(pipeline, const))
            for name, const in (("splits", "FACE_SPLITS_FILE"),
                                ("subfaces", "SUBFACES_FILE"),
                                ("edges", "SUBFACE_EDGES_FILE"),
                                ("pairs", "SUBFACE_EDGE_PAIRS_FILE"),
                                ("meta", "SUBFACE_META_FILE"))}


def _load_mesh(workdir):
    verts = np.load(os.path.join(workdir, pipeline.FINE_VERTS_FILE))
    faces = np.load(os.path.join(workdir, pipeline.FINE_FACES_FILE))
    brep_path = os.path.join(workdir, pipeline.BREP_FACES_FILE)
    if not os.path.exists(brep_path):
        raise ValueError("part has no BREP face data — splits need STEP input")
    return verts, faces, np.load(brep_path)


def _load_splits(workdir):
    path = _paths(workdir)["splits"]
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _write_derived(workdir, verts, faces, state):
    """Regenerate every derived sidecar from a replayed state."""
    paths = _paths(workdir)
    np.save(paths["subfaces"], state.eff)

    # same recipe as mesh-time BREP edges, over effective ids — cut edges
    # of separated cuts become boundary segments automatically
    pairs, edge_verts = molding.face_adjacency(faces)
    boundary = state.eff[pairs[:, 0]] != state.eff[pairs[:, 1]]
    segments = verts[edge_verts[boundary]].astype("<f4")
    id_pairs = np.sort(np.stack([state.eff[pairs[boundary, 0]],
                                 state.eff[pairs[boundary, 1]]], axis=1),
                       axis=1).astype("<u4")
    np.save(paths["edges"], segments)
    np.save(paths["pairs"], id_pairs)

    with open(paths["meta"], "w") as f:
        json.dump({"schema": SPLITS_SCHEMA,
                   "mesh_fingerprint": pipeline.mesh_fingerprint(workdir),
                   "n_brep": state.n_brep,
                   "n_effective": state.n_effective,
                   "parents": state.parents,
                   "cut_info": state.cut_info}, f)


def _clear_derived(workdir):
    paths = _paths(workdir)
    for name in ("subfaces", "edges", "pairs", "meta"):
        if os.path.exists(paths[name]):
            os.remove(paths[name])


def add_cut(workdir, face, start, end):
    """Add one cut (two snapped boundary vertices on effective face
    `face`), persist it and regenerate the derived sidecars."""
    verts, faces, brep_ids = _load_mesh(workdir)
    mesh_fp = pipeline.mesh_fingerprint(workdir)
    data = _load_splits(workdir) or {"schema": SPLITS_SCHEMA,
                                     "mesh_fingerprint": mesh_fp, "cuts": []}
    if data["mesh_fingerprint"] != mesh_fp:
        raise StaleSplitsError(
            "face splits reference an older mesh — clear all cuts first")

    state = replay(faces, brep_ids, data["cuts"])
    face = int(face)
    if face < state.n_brep:
        face_orig = face
    elif face - state.n_brep < len(state.parents):
        face_orig = state.parents[face - state.n_brep]
    else:
        raise ValueError(f"face {face} does not exist")

    path = cut_path(verts, faces, state.eff, face, start, end,
                    state.cut_edges.get(face_orig, set()))
    data["cuts"].append({"face_orig": face_orig, "face_at_cut": face,
                         "start": int(start), "end": int(end),
                         "path": path})

    state = replay(faces, brep_ids, data["cuts"])
    with open(_paths(workdir)["splits"], "w") as f:
        json.dump(data, f)
    _write_derived(workdir, verts, faces, state)
    logger.info(f"cut on face {face} ({len(path)} vertices): "
                f"{state.cut_info[-1]}")
    return state


def undo_last(workdir):
    """Remove the most recent cut; replay reproduces the prior labeling."""
    data = _load_splits(workdir)
    if not data or not data["cuts"]:
        raise ValueError("no cuts to undo")
    data["cuts"].pop()
    if not data["cuts"]:
        return clear(workdir)
    verts, faces, brep_ids = _load_mesh(workdir)
    state = replay(faces, brep_ids, data["cuts"])
    with open(_paths(workdir)["splits"], "w") as f:
        json.dump(data, f)
    _write_derived(workdir, verts, faces, state)
    return state


def clear(workdir):
    """Remove every cut and derived sidecar."""
    paths = _paths(workdir)
    if os.path.exists(paths["splits"]):
        os.remove(paths["splits"])
    _clear_derived(workdir)
    return None


def state(workdir):
    """JSON-safe splits state for the API/viewer."""
    brep_path = os.path.join(workdir, pipeline.BREP_FACES_FILE)
    if not os.path.exists(brep_path):
        raise ValueError("part has no BREP face data")
    data = _load_splits(workdir)
    if data is None:
        n_brep = int(np.load(brep_path).max()) + 1
        return {"n_brep": n_brep, "n_effective": n_brep, "parents": [],
                "stale": False, "cuts": []}
    with open(_paths(workdir)["meta"]) as f:
        meta = json.load(f)
    verts = np.load(os.path.join(workdir, pipeline.FINE_VERTS_FILE))
    stale = data["mesh_fingerprint"] != pipeline.mesh_fingerprint(workdir)
    cuts = [{"face_orig": c["face_orig"], "face_at_cut": c["face_at_cut"],
             "start": c["start"], "end": c["end"],
             "polyline": verts[np.asarray(c["path"], dtype=np.int64)].tolist(),
             **meta["cut_info"][i]}
            for i, c in enumerate(data["cuts"])]
    return {"n_brep": meta["n_brep"], "n_effective": meta["n_effective"],
            "parents": meta["parents"], "stale": stale, "cuts": cuts}


def effective_face_ids(workdir):
    """(ids (F,) int32, n_faces, parents) — sub-face labeling when current
    splits exist, plain BREP labeling otherwise, (None, 0, []) on STL."""
    paths = _paths(workdir)
    if os.path.exists(paths["subfaces"]) and os.path.exists(paths["meta"]):
        with open(paths["meta"]) as f:
            meta = json.load(f)
        if meta["mesh_fingerprint"] == pipeline.mesh_fingerprint(workdir):
            return (np.load(paths["subfaces"]), meta["n_effective"],
                    meta["parents"])
    brep_path = os.path.join(workdir, pipeline.BREP_FACES_FILE)
    if not os.path.exists(brep_path):
        return None, 0, []
    ids = np.load(brep_path)
    return ids, int(ids.max()) + 1, []


def sanitize_retired(valid, defaults, ids):
    """Zero out aggregation entries for retired ids.

    brep_validity reads a retired id (absent from `ids`) as covered==total
    (0 == 0) for every feature — sanitize to valid=0 / conflict so any
    consumer indexing by a stale id degrades to the pre-split behavior."""
    live = np.zeros(len(valid), dtype=bool)
    live[np.unique(ids)] = True
    valid[~live] = 0
    defaults[~live] = molding.DEFAULT_CONFLICT
    return valid, defaults
