"""Shared pipeline stages behind the CLI and the HTTP API.

Each function is a lift of a main.py subcommand body operating on a part
working directory (the on-disk cache). Both entry points call these, so a
field computed from the UI is byte-identical to one computed from the CLI.

Every long-running stage accepts an optional ``progress(fraction, message)``
callback (injected by the API job worker, ignored by the CLI).
"""

import json
import os

import numpy as np
from loguru import logger
from meshlib import mrmeshnumpy as mn

from analysis import (
    load_mesh,
    save_mesh,
    get_mesh_data,
    subdivide_mesh,
    sample_unity_vector_pairs,
    compute_accessibility,
    relax_accessibility,
    find_combinations_matching_best,
)
from utils import has_valid_extension, ensure_directory

FINE_MESH_FILE = "fine_mesh.obj"
FINE_VERTS_FILE = "fine_verts.npy"
FINE_FACES_FILE = "fine_faces.npy"
DIRECTIONS_FILE = "directions.npy"
ACCESSIBILITY_FILE = "accessibility.npy"
HIGHLIGHT_FILE = "highlights.json"

MESH_EXTENSIONS = [".stl", ".stp", ".step"]


def _report(progress, fraction, message):
    if progress is not None:
        progress(fraction, message)


def parse_tips(specs):
    """Parse tool tip specs 'diameter:corner_radius' into (D, rc) tuples."""
    tips = []
    for spec in specs:
        diameter, _, corner = str(spec).partition(":")
        tips.append((float(diameter), float(corner or 0.0)))
    return tips


def parse_holder(spec):
    """Parse a holder stack 'radius:start,radius:start,...' into tuples."""
    if not spec:
        return None
    cylinders = []
    for part in str(spec).split(","):
        radius, _, start = part.partition(":")
        cylinders.append((float(radius), float(start or 0.0)))
    return cylinders


def write_highlights(workdir, face_indices):
    """Persist the legacy per-face highlight result for a workdir."""
    highlight_path = os.path.join(workdir, HIGHLIGHT_FILE)
    with open(highlight_path, "w") as f:
        json.dump({"faces": list(face_indices)}, f)
    return highlight_path


def load_mesh_arrays(workdir):
    verts = np.load(os.path.join(workdir, FINE_VERTS_FILE))
    faces = np.load(os.path.join(workdir, FINE_FACES_FILE))
    return verts, faces


def mesh_part(input_path, workdir=None, *, heal=False, subdivide=None, offset=None,
              tollerance=1e-1, progress=None):
    """Canonicalize an input STL/STEP into a part working directory.

    Writes fine_mesh.obj + fine_verts.npy + fine_faces.npy (the stable face
    indexing every later stage refers to). Returns the workdir and counts.
    """
    has_valid_extension(input_path, MESH_EXTENSIONS)

    _report(progress, 0.0, "loading mesh")
    mesh = load_mesh(input_path, heal=heal, offset=offset, tollerance=tollerance)

    if subdivide:
        _report(progress, 0.5, "subdividing mesh")
        mesh = subdivide_mesh(mesh, subdivide)
    verts, faces = get_mesh_data(mesh)

    if not workdir:
        input_name = os.path.basename(input_path)
        input_name = input_name.rsplit(".", 1)[0]
        workdir = os.path.join(os.path.abspath("."), input_name)

    ensure_directory(workdir)
    obj_path = os.path.join(workdir, FINE_MESH_FILE)
    verts_path = os.path.join(workdir, FINE_VERTS_FILE)
    faces_path = os.path.join(workdir, FINE_FACES_FILE)

    _report(progress, 0.8, "storing mesh")
    logger.debug(f"Storing verts: {verts_path}")
    np.save(verts_path, verts)

    logger.debug(f"Storing faces: {faces_path}")
    np.save(faces_path, faces)

    logger.debug(f"Storing obj file: {obj_path}")
    save_mesh(mesh, obj_path)

    return {
        "workdir": workdir,
        "counts": {"verts": int(len(verts)), "faces": int(len(faces))},
    }


def compute_directions(workdir, *, count=64, axes=False, relax=False,
                       relax_tollerance=1.0, relax_samples=4, progress=None):
    """Sample approach directions and compute the accessibility matrix."""
    logger.debug(f"Computing {count} directions")
    directions = sample_unity_vector_pairs(count)

    if axes:
        # principal axes as antipodal pairs, matching the pair layout
        principal = np.array([
            [1.0, 0.0, 0.0], [-1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0], [0.0, -1.0, 0.0],
            [0.0, 0.0, 1.0], [0.0, 0.0, -1.0],
        ])
        directions = np.vstack([principal, directions])

    logger.debug("Cheking accessibility per direction")
    verts, faces = load_mesh_arrays(workdir)
    mesh = mn.meshFromFacesVerts(faces, verts)

    face_count = len(faces)
    _report(progress, 0.1, f"accessibility for {directions.shape[0]} directions")
    accessibility = compute_accessibility(mesh, directions, face_count)

    if relax:
        for direction_index in range(directions.shape[0]):
            _report(progress, 0.5 + 0.5 * direction_index / directions.shape[0],
                    f"relaxing direction {direction_index}")
            relaxed = relax_accessibility(
                mesh, accessibility[direction_index, :], directions[direction_index],
                tolerance_degrees=relax_tollerance, n=relax_samples)
            accessibility[direction_index, :] = relaxed

    directions_path = os.path.join(workdir, DIRECTIONS_FILE)
    accessibility_path = os.path.join(workdir, ACCESSIBILITY_FILE)

    logger.debug(f"Storing directions at: {directions_path}")
    np.save(directions_path, directions)

    logger.debug(f"Storing accessibility at: {accessibility_path}")
    np.save(accessibility_path, accessibility)

    return {
        "directions": int(directions.shape[0]),
        "faces": int(face_count),
    }


def parting_options(workdir, *, slides=0, count=10, slide_tollerance=2e-1,
                    relax=False, relax_tollerance=1.0, relax_samples=4,
                    progress=None):
    """Rank setup/parting direction combinations by face coverage."""
    logger.debug(f"Computing preferred options with {slides} slides")

    verts, faces = load_mesh_arrays(workdir)
    mesh = mn.meshFromFacesVerts(faces, verts)

    directions = np.load(os.path.join(workdir, DIRECTIONS_FILE))
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))

    _report(progress, 0.1, "searching direction combinations")
    matching_combinations = find_combinations_matching_best(
        directions, accessibility, max_slides=slides, max_results=count,
        tolerance_degrees=slide_tollerance)

    if relax:
        # Compute the unique open closed directions
        unique_directions = set()
        for option, performance in matching_combinations[:count]:
            unique_directions.update(option)

        for i, direction_index in enumerate(unique_directions):
            _report(progress, 0.5 + 0.5 * i / max(len(unique_directions), 1),
                    f"relaxing direction {direction_index}")
            relaxed = relax_accessibility(
                mesh, accessibility[direction_index, :], directions[direction_index],
                tolerance_degrees=relax_tollerance, n=relax_samples)
            accessibility[direction_index, :] = relaxed

        np.save(os.path.join(workdir, ACCESSIBILITY_FILE), accessibility)

    options = [
        {"directions": [int(i) for i in option], "coverage": float(performance)}
        for option, performance in matching_combinations[:count]
    ]
    return {"options": options}


def highlight_union(workdir, include=(), exclude=()):
    """Union of accessibility rows (or its inverse) as highlighted faces."""
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))

    if include:
        union = np.any(accessibility[list(include), :], axis=0)
    elif exclude:
        union = np.any(accessibility[list(exclude), :], axis=0)
        union = np.invert(union)
    else:
        union = np.zeros(accessibility.shape[1], dtype=bool)

    indices = np.where(union)[0].tolist()
    logger.debug(f"Highlighting {len(indices)} faces")
    write_highlights(workdir, indices)
    return indices


def precompute_fields(workdir, *, directions, pixel=1e-1, tips=(), clearances=(),
                      engine="zmap", window=0.3, progress=None):
    """Cache height maps and per-tip/per-clearance fields for directions."""
    from zmap import DirectionCache

    verts, faces = load_mesh_arrays(workdir)
    tips = [tips_entry if isinstance(tips_entry, tuple) else tuple(tips_entry)
            for tips_entry in tips]

    computed = []
    for step, direction_index in enumerate(directions):
        logger.info(f"Direction {direction_index}")
        _report(progress, step / max(len(directions), 1),
                f"direction {direction_index}")
        cache = DirectionCache(workdir, direction_index, verts=verts, faces=faces,
                               pixel=pixel, window=window, engine=engine)
        for diameter, corner_radius in tips:
            cache.tip_gap(diameter, corner_radius)
        for radius in clearances:
            cache.clearance(radius)
        if engine == "zmap":
            # tip-aware holder stickout fields per (tip, cylinder radius)
            for diameter, corner_radius in tips:
                for radius in clearances:
                    cache.tip_min_stickout(diameter, corner_radius, radius)
        computed.append(int(direction_index))

    return {
        "directions": computed,
        "tips": [list(tip) for tip in tips],
        "clearances": [float(r) for r in clearances],
        "engine": engine,
    }


def compose_tool(workdir, direction, *, pixel=1e-1, tollerance=1e-1, diameter=2.0,
                 corner_radius=0.0, stickout=None, cylinders=None, sweep=(),
                 engine="zmap", window=0.3, progress=None):
    """Evaluate a full tool assembly from precomputed fields."""
    from zmap import DirectionCache, compose_unreachable

    verts, faces = load_mesh_arrays(workdir)
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))

    _report(progress, 0.1, "composing tool verdict")
    cache = DirectionCache(workdir, direction, verts=verts, faces=faces,
                           pixel=pixel, window=window, engine=engine)
    unreachable_faces, gap, min_stick = compose_unreachable(
        cache, faces, diameter, corner_radius, tollerance,
        stickout=stickout, cylinders=cylinders,
    )

    # Keep only the faces that are accessible
    unreachable_faces = unreachable_faces[accessibility[direction, unreachable_faces]]

    accessible_count = int(accessibility[direction].sum())
    logger.info(f"Tool D={diameter} rc={corner_radius} stickout={stickout} cannot reach {len(unreachable_faces)} of {accessible_count} accessible faces")

    # A stickout sweep is free: threshold the cached per-vertex field
    sweep_results = []
    if sweep and min_stick is not None:
        for sweep_stickout in sweep:
            blocked = (gap > tollerance) | (min_stick > sweep_stickout + tollerance)
            swept = np.where(blocked[faces].all(axis=1))[0]
            swept = swept[accessibility[direction, swept]]
            logger.info(f"  stickout {sweep_stickout:8.2f}: {len(swept)} unreachable faces")
            sweep_results.append({"stickout": float(sweep_stickout),
                                  "unreachable": int(len(swept))})

    unreachable_faces = unreachable_faces.tolist()
    write_highlights(workdir, unreachable_faces)

    return {
        "unreachable": len(unreachable_faces),
        "accessible": accessible_count,
        "sweep": sweep_results,
        "faces": unreachable_faces,
    }
