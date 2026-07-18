"""Build the generalized viewer manifest by scanning a part's workdir.

The manifest is rebuilt from disk on every request, so fields computed by
the CLI show up in the UI immediately and vice versa. Field descriptors are
the generic unit the viewer renders: an association (vertex/face), a role
(scalar/mask/category) and structured params identifying what the field is.
"""

import glob
import json
import os
import re

import numpy as np

import pipeline
from processes.base import RESULTS_DIR


def _json_safe(obj):
    """Replace non-finite floats (NaN/Inf) with None so a single stray value
    from one analysis cannot make the whole manifest fail to serialize and
    500 the viewer."""
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj

# same key scheme DirectionCache uses in <workdir>/zcache/dir_<idx>.npz;
# the optional suffix group matches (and skips) legacy voxel-engine caches
CACHE_FILE_RE = re.compile(r"^dir_(\d+)(?:_([a-z]+))?\.npz$")
TIP_KEY_RE = re.compile(r"^tip_([0-9.eE+-]+)_([0-9.eE+-]+)$")
CLEAR_KEY_RE = re.compile(r"^clear_([0-9.eE+-]+)$")
SREQ_KEY_RE = re.compile(r"^sreq_([0-9.eE+-]+)_([0-9.eE+-]+)_([0-9.eE+-]+)$")


def _num(text):
    value = float(text)
    return int(value) if value == int(value) else value


def _zcache_fields(workdir, base_url, vert_count):
    fields = []
    cache_dir = os.path.join(workdir, "zcache")
    cache_files = sorted(os.listdir(cache_dir)) if os.path.isdir(cache_dir) else []
    for name in cache_files:
        match = CACHE_FILE_RE.match(name)
        if not match or match.group(2):
            continue
        stem = name[:-len(".npz")]
        dir_index = int(match.group(1))

        with np.load(os.path.join(cache_dir, name), allow_pickle=False) as stored:
            pixel = float(stored["pixel"][0]) if "pixel" in stored.files else None
            keys = list(stored.files)

        for key in keys:
            tip = TIP_KEY_RE.match(key)
            clear = CLEAR_KEY_RE.match(key)
            sreq = SREQ_KEY_RE.match(key)
            if tip:
                params = {"kind": "tip_gap", "diameter": _num(tip.group(1)),
                          "corner_radius": _num(tip.group(2))}
            elif clear:
                params = {"kind": "clearance", "radius": _num(clear.group(1))}
            elif sreq:
                params = {"kind": "min_stickout", "diameter": _num(sreq.group(1)),
                          "corner_radius": _num(sreq.group(2)),
                          "radius": _num(sreq.group(3))}
            else:
                continue
            params.update({"direction": dir_index, "pixel": pixel})
            fields.append({
                "id": f"{stem}.{key}",
                "association": "vertex",
                "dtype": "f4",
                "role": "scalar",
                "units": "mm",
                "length": vert_count,
                "url": f"{base_url}/fields/{stem}/{key}",
                "params": params,
            })
    return fields


def _accessibility_fields(workdir, base_url, face_count):
    path = os.path.join(workdir, pipeline.ACCESSIBILITY_FILE)
    if not os.path.exists(path):
        return []
    stored = np.load(path, mmap_mode="r")
    if stored.shape[1] != face_count:
        return []  # workdir was re-meshed after directions — masks misaligned
    direction_count = int(stored.shape[0])
    return [{
        "id": f"accessibility.{index}",
        "association": "face",
        "dtype": "u1",
        "role": "mask",
        "length": face_count,
        "url": f"{base_url}/fields/accessibility/{index}",
        "params": {"kind": "accessibility", "direction": index},
    } for index in range(direction_count)]


def _brep_faces_fields(workdir, base_url, face_count):
    path = os.path.join(workdir, pipeline.BREP_FACES_FILE)
    if not os.path.exists(path):
        return []
    count = int(np.load(path, mmap_mode="r").max()) + 1
    fields = [{
        "id": "brep_faces",
        "association": "face",
        "dtype": "u4",
        "role": "category",
        "length": face_count,
        "url": f"{base_url}/fields/brep_faces/0",
        "params": {"kind": "brep_faces", "count": count},
    }]

    edges_path = os.path.join(workdir, pipeline.BREP_EDGES_FILE)
    if os.path.exists(edges_path):
        segments = int(np.load(edges_path, mmap_mode="r").shape[0])
        fields += [{
            "id": "brep_edges",
            "association": "none",
            "dtype": "f4",
            "role": "lines",
            "length": None,
            "url": f"{base_url}/fields/brep_edges/0",
            "params": {"kind": "brep_edges", "segments": segments},
        }, {
            "id": "brep_edge_pairs",
            "association": "none",
            "dtype": "u4",
            "role": "data",
            "length": None,
            "url": f"{base_url}/fields/brep_edge_pairs/0",
            "params": {"kind": "brep_edge_pairs", "segments": segments},
        }]
    return fields


def _subface_fields(workdir, base_url, face_count):
    """Effective sub-face labeling from user cuts (splits.py sidecars).

    Only advertised while the splits reference the current mesh — a
    re-meshed part silently falls back to plain BREP faces."""
    meta_path = os.path.join(workdir, pipeline.SUBFACE_META_FILE)
    if not (os.path.exists(meta_path)
            and os.path.exists(os.path.join(workdir, pipeline.SUBFACES_FILE))):
        return []
    with open(meta_path) as f:
        meta = json.load(f)
    if meta.get("mesh_fingerprint") != pipeline.mesh_fingerprint(workdir):
        return []
    fields = [{
        "id": "subfaces",
        "association": "face",
        "dtype": "u4",
        "role": "category",
        "length": face_count,
        "url": f"{base_url}/fields/subfaces/0",
        "params": {"kind": "subfaces", "count": meta["n_effective"],
                   "n_brep": meta["n_brep"], "parents": meta["parents"]},
    }]
    edges_path = os.path.join(workdir, pipeline.SUBFACE_EDGES_FILE)
    if os.path.exists(edges_path):
        segments = int(np.load(edges_path, mmap_mode="r").shape[0])
        fields += [{
            "id": "subface_edges",
            "association": "none",
            "dtype": "f4",
            "role": "lines",
            "length": None,
            "url": f"{base_url}/fields/subface_edges/0",
            "params": {"kind": "subface_edges", "segments": segments},
        }, {
            "id": "subface_edge_pairs",
            "association": "none",
            "dtype": "u4",
            "role": "data",
            "length": None,
            "url": f"{base_url}/fields/subface_edge_pairs/0",
            "params": {"kind": "subface_edge_pairs", "segments": segments},
        }]
    return fields


def _result_entries(workdir, base_url, face_count, vert_count):
    fields, results = [], []
    current_fingerprint = pipeline.directions_fingerprint(workdir)
    current_mesh = pipeline.mesh_fingerprint(workdir)
    current_splits = pipeline.splits_fingerprint(workdir)
    pattern = os.path.join(workdir, RESULTS_DIR, "*", "*", "*.json")
    # oldest -> newest so "last entry per analysis" means the most recent
    # recompute — the frontend's default result picks rely on this
    for json_path in sorted(glob.glob(pattern), key=os.path.getmtime):
        if json_path.endswith("_overrides.json"):
            continue  # assignment overrides live next to their result
        with open(json_path) as f:
            payload = json.load(f)
        process_id = payload["process"]
        analysis_id = payload["analysis"]
        result_hash = os.path.splitext(os.path.basename(json_path))[0]

        field_ids = []
        for name, meta in payload.get("arrays", {}).items():
            association = meta.get("association", "vertex")
            role = meta.get("role", "scalar")
            # graph-shaped arrays declare their own dtype and flat length;
            # mesh-shaped fields keep the inferred defaults
            dtype = meta.get("dtype") or (
                "u1" if role in ("mask", "category") else "f4")
            length = meta.get("length")
            if length is None and association != "none":
                length = face_count if association == "face" else vert_count
            field_id = f"results.{process_id}.{analysis_id}.{result_hash}.{name}"
            field_ids.append(field_id)
            fields.append({
                "id": field_id,
                "association": association,
                "dtype": dtype,
                "role": role,
                "length": length,
                "url": f"{base_url}/results/{process_id}/{analysis_id}/{result_hash}/{name}",
                "params": meta,
            })

        stats = payload.get("stats", {})
        stored_fingerprint = stats.get("directions_fingerprint")
        stored_mesh = payload.get("params", {}).get("mesh")
        results.append({
            "process": process_id,
            "analysis": analysis_id,
            "hash": result_hash,
            "params": payload.get("params", {}),
            "stats": _json_safe(stats),
            "fields": field_ids,
            # direction indices, face/vertex indexing or sub-face labeling
            # in the result no longer match the workdir — stale, re-run it
            "stale": bool((stored_fingerprint
                           and stored_fingerprint != current_fingerprint)
                          or (stored_mesh and stored_mesh != current_mesh)
                          or ("splits" in payload.get("params", {})
                              and payload["params"]["splits"]
                              != current_splits)),
            "overrides_url": f"{base_url}/results/{process_id}/{analysis_id}/{result_hash}/overrides",
        })
    return fields, results


def build_manifest(root, part):
    from api.parts import workdir_for
    workdir = workdir_for(root, part["id"])
    base_url = f"/api/parts/{part['id']}"
    counts = part.get("counts") or {}
    vert_count = counts.get("verts")
    face_count = counts.get("faces")

    manifest = {
        "part": part,
        "mesh": None,
        "directions": [],
        "directions_stale": False,
        "fields": [],
        "results": [],
        "highlights_url": None,
    }

    if part["status"] != "meshed":
        return manifest

    manifest["mesh"] = {
        "counts": counts,
        "verts_url": f"{base_url}/mesh/verts",
        "faces_url": f"{base_url}/mesh/faces",
        "normals_url": f"{base_url}/mesh/normals",
    }

    directions_path = os.path.join(workdir, pipeline.DIRECTIONS_FILE)
    if os.path.exists(directions_path):
        directions = np.load(directions_path)
        manifest["directions"] = [[float(c) for c in d] for d in directions]
        meta_path = os.path.join(workdir, pipeline.DIRECTIONS_META_FILE)
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                stored_mesh = json.load(f).get("mesh_fingerprint")
            manifest["directions_stale"] = bool(
                stored_mesh and stored_mesh != pipeline.mesh_fingerprint(workdir))

    manifest["fields"] = (
        _zcache_fields(workdir, base_url, vert_count)
        + _accessibility_fields(workdir, base_url, face_count)
        + _brep_faces_fields(workdir, base_url, face_count)
        + _subface_fields(workdir, base_url, face_count)
    )
    result_fields, results = _result_entries(workdir, base_url, face_count, vert_count)
    manifest["fields"] += result_fields
    manifest["results"] = results

    if os.path.exists(os.path.join(workdir, pipeline.HIGHLIGHT_FILE)):
        manifest["highlights_url"] = f"{base_url}/highlights"

    # STEP import artifacts (step_import.py): presence flags + fetch URLs
    if os.path.exists(os.path.join(workdir, "face_attrs.json")):
        manifest["face_attrs_url"] = f"{base_url}/face_attrs"
    if os.path.exists(os.path.join(workdir, "pmi.json")):
        manifest["pmi_url"] = f"{base_url}/pmi"
    if os.path.exists(os.path.join(workdir, "assembly.json")):
        manifest["assembly_url"] = f"{base_url}/assembly"

    # AAG stage artifact (aag.py): stats + staleness for consumers
    aag_meta_path = os.path.join(workdir, pipeline.AAG_META_FILE)
    if os.path.exists(aag_meta_path):
        with open(aag_meta_path) as f:
            aag_meta = json.load(f)
        manifest["aag"] = {
            "schema": aag_meta.get("schema"),
            "stats": _json_safe(aag_meta.get("stats", {})),
            "stale": bool(
                aag_meta.get("mesh_fingerprint")
                and aag_meta["mesh_fingerprint"]
                != pipeline.mesh_fingerprint(workdir)),
        }

    return manifest
