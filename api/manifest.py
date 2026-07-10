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

# same key scheme DirectionCache uses in <workdir>/zcache/dir_<idx>[_engine].npz
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
        if not match:
            continue
        stem = name[:-len(".npz")]
        dir_index = int(match.group(1))
        engine = match.group(2) or "zmap"

        stored = np.load(os.path.join(cache_dir, name), allow_pickle=False)
        pixel = float(stored["pixel"][0]) if "pixel" in stored.files else None

        for key in stored.files:
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
            params.update({"direction": dir_index, "engine": engine, "pixel": pixel})
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
    direction_count = int(np.load(path, mmap_mode="r").shape[0])
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


def _result_entries(workdir, base_url, face_count, vert_count):
    fields, results = [], []
    pattern = os.path.join(workdir, RESULTS_DIR, "*", "*", "*.json")
    for json_path in sorted(glob.glob(pattern)):
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
            field_id = f"results.{process_id}.{analysis_id}.{result_hash}.{name}"
            field_ids.append(field_id)
            fields.append({
                "id": field_id,
                "association": association,
                "dtype": meta.get("dtype")
                         or ("u1" if role in ("mask", "category") else "f4"),
                "role": role,
                "length": (face_count if association == "face"
                           else vert_count if association == "vertex" else None),
                "url": f"{base_url}/results/{process_id}/{analysis_id}/{result_hash}/{name}",
                "params": meta,
            })

        results.append({
            "process": process_id,
            "analysis": analysis_id,
            "hash": result_hash,
            "params": payload.get("params", {}),
            "stats": payload.get("stats", {}),
            "fields": field_ids,
            "overrides_url": f"{base_url}/results/{process_id}/{analysis_id}/{result_hash}/overrides",
        })
    return fields, results


def build_manifest(root, part):
    workdir = os.path.join(root, part["id"])
    base_url = f"/api/parts/{part['id']}"
    counts = part.get("counts") or {}
    vert_count = counts.get("verts")
    face_count = counts.get("faces")

    manifest = {
        "part": part,
        "mesh": None,
        "directions": [],
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

    manifest["fields"] = (
        _zcache_fields(workdir, base_url, vert_count)
        + _accessibility_fields(workdir, base_url, face_count)
        + _brep_faces_fields(workdir, base_url, face_count)
    )
    result_fields, results = _result_entries(workdir, base_url, face_count, vert_count)
    manifest["fields"] += result_fields
    manifest["results"] = results

    if os.path.exists(os.path.join(workdir, pipeline.HIGHLIGHT_FILE)):
        manifest["highlights_url"] = f"{base_url}/highlights"

    return manifest
