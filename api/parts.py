"""Part discovery and registration over a parts root directory.

A part is a working directory (the same one the CLI operates on). Uploaded
parts get a part.json with their metadata; legacy workdirs are recognized by
their fine_verts.npy and synthesized read-only.
"""

import datetime
import json
import os
import re
import shutil

import numpy as np

import pipeline

PART_META_FILE = "part.json"
SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _read_meta(workdir):
    meta_path = os.path.join(workdir, PART_META_FILE)
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            return json.load(f)
    return {}


def _write_meta(workdir, meta):
    with open(os.path.join(workdir, PART_META_FILE), "w") as f:
        json.dump(meta, f, indent=1)


def part_info(root, part_id):
    """Build the Part dict for one working directory, or None."""
    workdir = os.path.join(root, part_id)
    meta = _read_meta(workdir)
    verts_path = os.path.join(workdir, pipeline.FINE_VERTS_FILE)
    faces_path = os.path.join(workdir, pipeline.FINE_FACES_FILE)
    meshed = os.path.exists(verts_path) and os.path.exists(faces_path)
    if not meta and not meshed:
        return None

    counts = None
    if meshed:
        counts = {
            "verts": int(np.load(verts_path, mmap_mode="r").shape[0]),
            "faces": int(np.load(faces_path, mmap_mode="r").shape[0]),
        }

    return {
        "id": part_id,
        "name": meta.get("name", part_id),
        "source": meta.get("source"),
        "status": "meshed" if meshed else "raw",
        "counts": counts,
        "has_directions": os.path.exists(os.path.join(workdir, pipeline.DIRECTIONS_FILE)),
        "created": meta.get("created"),
    }


def list_parts(root):
    parts = []
    for name in sorted(os.listdir(root)):
        workdir = os.path.join(root, name)
        if name.startswith(".") or not os.path.isdir(workdir):
            continue
        info = part_info(root, name)
        if info is not None:
            parts.append(info)
    return parts


def _unique_id(root, stem):
    part_id = SAFE_ID_RE.sub("_", stem) or "part"
    candidate, counter = part_id, 1
    while os.path.exists(os.path.join(root, candidate, PART_META_FILE)) or (
            os.path.isdir(os.path.join(root, candidate))
            and part_info(root, candidate) is not None):
        counter += 1
        candidate = f"{part_id}_{counter}"
    return candidate


def create_part(root, filename, data):
    """Register an uploaded STEP/STL as a new raw part."""
    stem, ext = os.path.splitext(os.path.basename(filename))
    if ext.lower() not in pipeline.MESH_EXTENSIONS:
        raise ValueError(f"unsupported extension {ext}; expected one of {pipeline.MESH_EXTENSIONS}")

    part_id = _unique_id(root, stem)
    workdir = os.path.join(root, part_id)
    os.makedirs(workdir, exist_ok=True)

    source_name = f"source{ext.lower()}"
    with open(os.path.join(workdir, source_name), "wb") as f:
        f.write(data)

    _write_meta(workdir, {
        "name": stem,
        "source": source_name,
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    return part_info(root, part_id)


def register_part_file(root, path):
    """Register an existing STEP/STL file path as a part (used by `view <file>`).

    Reuses a matching existing part when the workdir already holds the same
    source name, so repeated launches do not duplicate parts.
    """
    stem, ext = os.path.splitext(os.path.basename(path))
    if ext.lower() not in pipeline.MESH_EXTENSIONS:
        raise ValueError(f"unsupported extension {ext}; expected one of {pipeline.MESH_EXTENSIONS}")

    part_id = SAFE_ID_RE.sub("_", stem) or "part"
    workdir = os.path.join(root, part_id)
    source_name = f"source{ext.lower()}"
    source_path = os.path.join(workdir, source_name)

    if not os.path.exists(source_path):
        os.makedirs(workdir, exist_ok=True)
        shutil.copyfile(path, source_path)
        meta = _read_meta(workdir)
        meta.setdefault("name", stem)
        meta["source"] = source_name
        meta.setdefault("created",
                        datetime.datetime.now(datetime.timezone.utc).isoformat())
        _write_meta(workdir, meta)
    return part_info(root, part_id)
