"""Part discovery and registration over a parts root directory.

A part is a working directory (the same one the CLI operates on). Uploaded
parts are stored content-addressed: id = sha1(source bytes)[:12], workdir
under <root>/parts/ — the same STEP always lands in the same folder and
re-uploads dedupe. Legacy workdirs directly in the root (CLI runs, committed
samples) are recognized by their part.json or fine_verts.npy and served
unchanged.
"""

import datetime
import hashlib
import json
import os
import shutil

import numpy as np

import pipeline

PART_META_FILE = "part.json"
PARTS_DIR = "parts"  # content-addressed upload workdirs live here


def content_part_id(data):
    """Deterministic part id from the source file bytes."""
    return hashlib.sha1(data).hexdigest()[:12]


def workdir_for(root, part_id):
    """Resolve a part id to its working directory.

    Content-hash ids live under <root>/parts/; anything else (legacy
    stem-named uploads, CLI workdirs, committed samples) sits directly in
    the root.
    """
    hashed = os.path.join(root, PARTS_DIR, part_id)
    if os.path.isdir(hashed):
        return hashed
    return os.path.join(root, part_id)


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
    workdir = workdir_for(root, part_id)
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
    """All parts: legacy workdirs in the root plus content-hash uploads in
    parts/. When both hold the same id, the parts/ entry wins (matching
    workdir_for)."""
    by_id = {}
    for name in sorted(os.listdir(root)):
        if name.startswith(".") or name == PARTS_DIR:
            continue
        if not os.path.isdir(os.path.join(root, name)):
            continue
        info = part_info(root, name)
        if info is not None:
            by_id[name] = info
    hashed_root = os.path.join(root, PARTS_DIR)
    if os.path.isdir(hashed_root):
        for name in sorted(os.listdir(hashed_root)):
            info = part_info(root, name)
            if info is not None:
                by_id[name] = info
    return list(by_id.values())


def _register(root, name, ext, data):
    """Store source bytes in their content-addressed workdir (idempotent)."""
    part_id = content_part_id(data)
    existing = part_info(root, part_id)
    if existing is not None:
        return existing  # same bytes already registered; keep the first name

    workdir = os.path.join(root, PARTS_DIR, part_id)
    os.makedirs(workdir, exist_ok=True)
    source_name = f"source{ext.lower()}"
    with open(os.path.join(workdir, source_name), "wb") as f:
        f.write(data)
    _write_meta(workdir, {
        "name": name,
        "source": source_name,
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    return part_info(root, part_id)


def create_part(root, filename, data):
    """Register an uploaded STEP/STL; same bytes always yield the same part."""
    stem, ext = os.path.splitext(os.path.basename(filename))
    if ext.lower() not in pipeline.MESH_EXTENSIONS:
        raise ValueError(f"unsupported extension {ext}; expected one of {pipeline.MESH_EXTENSIONS}")
    return _register(root, stem or "part", ext, data)


def reprocess_part(root, part_id):
    """Wipe every cached artifact in a part's workdir, keeping only the
    original source file and its part.json.

    The cache resolver invalidates on input content and params, not on
    algorithm *code* changes; reprocessing forces a from-scratch rebuild so
    edits to the meshing/analysis code take effect on an already-imported
    part. Manual sidecars (face splits, assignment overrides) are cleared
    too — they index into a mesh the rebuild may re-cut. Returns the source
    file's lowercased extension so callers can decide whether to kick the
    STEP first-load bundle.
    """
    workdir = workdir_for(root, part_id)
    meta = _read_meta(workdir)
    source = meta.get("source")
    if not source or not os.path.exists(os.path.join(workdir, source)):
        raise ValueError("part has no stored source to reprocess")
    keep = {source, PART_META_FILE}
    for name in os.listdir(workdir):
        if name in keep:
            continue
        path = os.path.join(workdir, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    return os.path.splitext(source)[1].lower()


def register_part_file(root, path):
    """Register an existing STEP/STL file path as a part (used by `view <file>`).

    Content-addressed like uploads, so repeated launches (and uploads of the
    same file) all resolve to one part.
    """
    stem, ext = os.path.splitext(os.path.basename(path))
    if ext.lower() not in pipeline.MESH_EXTENSIONS:
        raise ValueError(f"unsupported extension {ext}; expected one of {pipeline.MESH_EXTENSIONS}")
    with open(path, "rb") as f:
        data = f.read()
    return _register(root, stem or "part", ext, data)
