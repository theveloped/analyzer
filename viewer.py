"""Export cached analysis fields as a browser-friendly bundle.

The interactive viewer (viewer.html) recomputes every mask client side —
tolerance sliders, stickout sweeps, holder stacks — from the same per-vertex
scalar fields the compose command uses. This module dumps those fields from
<workdir>/zcache/*.npz into <workdir>/viewer/ as raw little-endian binaries
plus a manifest.json describing what is available:

- faces.bin                       uint32  F*3   vertex indices per face
- access_<dir>.bin                uint8   F     accessibility per face
- gap_<dir>_<D>_<rc>[_voxel].bin  float32 V     tip gap per vertex
- clear_<dir>_<r>[_voxel].bin     float32 V     clearance per vertex
"""

import json
import os
import re
import shutil

import numpy as np
from loguru import logger

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_FILES = ["three.min.js", "OrbitControls.js", "OBJLoader.js"]

CACHE_FILE_RE = re.compile(r"^dir_(\d+)(?:_([a-z]+))?\.npz$")
TIP_KEY_RE = re.compile(r"^tip_([0-9.eE+-]+)_([0-9.eE+-]+)$")
CLEAR_KEY_RE = re.compile(r"^clear_([0-9.eE+-]+)$")
SREQ_KEY_RE = re.compile(r"^sreq_([0-9.eE+-]+)_([0-9.eE+-]+)_([0-9.eE+-]+)$")


def _num(text):
    value = float(text)
    return int(value) if value == int(value) else value


def export_viewer_bundle(workdir):
    """Collect mesh, accessibility and all cached zcache fields into
    <workdir>/viewer/. Returns the manifest dict."""
    out_dir = os.path.join(workdir, "viewer")
    os.makedirs(out_dir, exist_ok=True)

    # the bundle carries its own three.js so the viewer works offline
    for name in STATIC_FILES:
        shutil.copyfile(os.path.join(STATIC_DIR, name), os.path.join(out_dir, name))

    verts = np.load(os.path.join(workdir, "fine_verts.npy"))
    faces = np.load(os.path.join(workdir, "fine_faces.npy"))
    directions = np.load(os.path.join(workdir, "directions.npy"))

    faces.astype("<u4").tofile(os.path.join(out_dir, "faces.bin"))

    # unit face normals: the angle to any approach direction is then a client
    # side dot product (surface classification, wall detection)
    tri = verts[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.maximum(lengths, 1e-30)
    normals.astype("<f4").tofile(os.path.join(out_dir, "normals.bin"))

    manifest = {
        "counts": {"verts": int(len(verts)), "faces": int(len(faces))},
        "directions": [[float(c) for c in d] for d in directions],
        "sources": [],
    }

    access_path = os.path.join(workdir, "accessibility.npy")
    accessibility = None
    if os.path.exists(access_path):
        accessibility = np.load(access_path)

    cache_dir = os.path.join(workdir, "zcache")
    cache_files = sorted(os.listdir(cache_dir)) if os.path.isdir(cache_dir) else []
    for name in cache_files:
        match = CACHE_FILE_RE.match(name)
        if not match:
            continue
        dir_index = int(match.group(1))
        engine = match.group(2) or "zmap"
        suffix = "" if engine == "zmap" else f"_{engine}"

        stored = np.load(os.path.join(cache_dir, name), allow_pickle=False)
        source = {
            "direction": dir_index,
            "engine": engine,
            "pixel": float(stored["pixel"][0]) if "pixel" in stored.files else None,
            "tips": [],
            "clearances": [],
        }

        sreqs = {}
        for key in stored.files:
            tip = TIP_KEY_RE.match(key)
            clear = CLEAR_KEY_RE.match(key)
            sreq = SREQ_KEY_RE.match(key)
            if tip:
                diameter, corner = _num(tip.group(1)), _num(tip.group(2))
                fname = f"gap_{dir_index}_{diameter}_{corner}{suffix}.bin"
                stored[key].astype("<f4").tofile(os.path.join(out_dir, fname))
                source["tips"].append({"diameter": diameter, "corner_radius": corner, "file": fname})
            elif clear:
                radius = _num(clear.group(1))
                fname = f"clear_{dir_index}_{radius}{suffix}.bin"
                stored[key].astype("<f4").tofile(os.path.join(out_dir, fname))
                source["clearances"].append({"radius": radius, "file": fname})
            elif sreq:
                diameter, corner, radius = (_num(sreq.group(i)) for i in (1, 2, 3))
                fname = f"sreq_{dir_index}_{diameter}_{corner}_{radius}{suffix}.bin"
                stored[key].astype("<f4").tofile(os.path.join(out_dir, fname))
                sreqs.setdefault((diameter, corner), []).append({"radius": radius, "file": fname})

        # attach tip-aware stickout fields to their tips
        for tip_entry in source["tips"]:
            entry_key = (tip_entry["diameter"], tip_entry["corner_radius"])
            tip_entry["stickouts"] = sorted(sreqs.get(entry_key, []), key=lambda s: s["radius"])

        if accessibility is not None and dir_index < accessibility.shape[0]:
            fname = f"access_{dir_index}.bin"
            accessibility[dir_index].astype("<u1").tofile(os.path.join(out_dir, fname))
            source["accessibility"] = fname

        source["tips"].sort(key=lambda t: (t["diameter"], t["corner_radius"]))
        source["clearances"].sort(key=lambda c: c["radius"])
        manifest["sources"].append(source)

    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    n_fields = sum(len(s["tips"]) + len(s["clearances"]) for s in manifest["sources"])
    logger.info(f"Exported viewer bundle: {len(manifest['sources'])} direction caches, {n_fields} fields -> {out_dir}")
    return manifest
