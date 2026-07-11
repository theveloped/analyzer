"""CNC setup-combination search and per-face setup assignment.

Given the (D, F) accessibility matrix, rank the combinations of machining
setups that could produce the part. A machine is modelled as a tilt cone:
a setup fixes the part once with a primary (untilted spindle) direction and
covers the union of the accessibility rows of every sampled direction
within ``tilt`` degrees of that primary. tilt 0 is a plain 2.5D / 3-axis
setup (exactly one direction); tilt 90 is an indexed 5-axis (3+2) setup
that can swing the head down to horizontal. Options are found by greedy
set cover seeded at every direction, deduplicated, and ranked machine
first (a two-setup job on a plain 3-axis beats booking a 3+2), then fewer
setups, then coverage, preferring the classic flip (antipodal second
setup) among ties.

Cover is the OR of *sampled* directions inside the cone, so it both
underestimates a continuous tilt (finite sampling) and overestimates
reality (no fixture / table occlusion yet — that 3D recheck is a later
stage); ranked options are therefore optimistic per setup.

Assignment mirrors molding.py: per mesh face a bitmask records every setup
whose cover reaches it (feature index == membership bit == setup index), a
setup is valid for a whole BREP face iff it covers every one of its
triangles, and the default assignment is the earliest valid setup (machine
as much as possible early). Faces no setup covers form numbered
unmachinable regions (needs EDM / another process / more setups). The
molding sentinels are reused: 254 = conflict (partial covers only, needs a
split), 255 = unmachinable.
"""

import numpy as np
from loguru import logger

from molding import DEFAULT_CONFLICT, DEFAULT_INTERNAL  # shared sentinels
from utils import log_execution_time

# palette (frontend receives these through field_meta)
SETUP_COLORS = [
    [0.44, 0.64, 0.86], [0.62, 0.80, 0.58], [0.95, 0.66, 0.23],
    [0.60, 0.40, 0.80], [0.30, 0.75, 0.75], [0.80, 0.75, 0.30],
    [0.75, 0.45, 0.30], [0.55, 0.55, 0.90],
]
CATEGORY_COLORS = {
    "unmachinable": [0.88, 0.29, 0.23],
    "conflict": [0.95, 0.35, 0.60],
}

AXIS_NAMES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")


def machine_cover(directions, accessibility, tilt_deg):
    """bool (D, F): faces a setup with primary direction d can cover.

    Row d is the OR of the accessibility rows of every direction within
    tilt_deg of d. tilt 0 degenerates to the accessibility matrix itself;
    the epsilon keeps directions exactly on the cone edge (e.g. the 90°
    equator) deterministically inside, mirroring the wall tolerance of the
    visibility test.
    """
    if tilt_deg <= 0:
        return accessibility
    dots = directions @ directions.T
    inside = dots >= np.cos(np.radians(tilt_deg)) - 1e-9
    cover = np.zeros_like(accessibility)
    for d in range(directions.shape[0]):
        cover[d] = accessibility[inside[d]].any(axis=0)
    return cover


def direction_label(vector):
    """'+Z'-style tag when the vector is (near) a principal axis, else ''."""
    vector = np.asarray(vector, dtype=float)
    axis = int(np.argmax(np.abs(vector)))
    if abs(vector[axis]) >= 0.99:
        return AXIS_NAMES[2 * axis + (0 if vector[axis] > 0 else 1)]
    return ""


def _greedy_setups(cover, seed, max_setups, min_setup_faces):
    """Greedy set cover from one seed direction; returns direction indices.

    Ties in marginal gain prefer the antipode of an already-chosen setup
    (directions are laid out as antipodal pairs, antipode(i) == i ^ 1) —
    the classic flip re-fixture is the cheapest second setup, so among
    equals it should surface.
    """
    chosen = [int(seed)]
    residual = ~cover[seed]
    while residual.any() and len(chosen) < max_setups:
        gains = (cover & residual).sum(axis=1)
        best_gain = int(gains.max())
        if best_gain < min_setup_faces:
            break
        best = int(np.argmax(gains))
        for direction in chosen:
            if gains[direction ^ 1] == best_gain:
                best = direction ^ 1
                break
        chosen.append(best)
        residual &= ~cover[best]
    return chosen


def _option(machine, machine_rank, tilt_deg, setup_dirs, directions, cover):
    """Assemble the JSON-safe option dict for one setup set."""
    face_count = cover.shape[1]
    rows = cover[setup_dirs]

    # presentation order: the biggest setup machines the bulk first,
    # regardless of which greedy seed found the set; setups left with no
    # marginal contribution after the reorder (a redundant seed demoted
    # behind the picks that replaced it) are dropped
    order = np.argsort([-int(r.sum()) for r in rows], kind="stable")
    setup_dirs = [setup_dirs[i] for i in order]
    rows = rows[order]
    residual = np.ones(face_count, dtype=bool)
    keep = []
    for i, row in enumerate(rows):
        if (row & residual).any():
            keep.append(i)
            residual &= ~row
    setup_dirs = [setup_dirs[i] for i in keep]
    rows = rows[keep]

    hit_count = rows.sum(axis=0)
    covered = int((hit_count > 0).sum())
    internal = face_count - covered

    setups = []
    residual = np.ones(face_count, dtype=bool)
    for row, direction in zip(rows, setup_dirs):
        setups.append({
            "direction": int(direction),
            "vector": [float(c) for c in directions[direction]],
            "reachable": int(row.sum()),
            "exclusive": int((row & (hit_count == 1)).sum()),
            "marginal": int((row & residual).sum()),
        })
        residual &= ~row

    flip = len(setup_dirs) == 2 and setup_dirs[1] == setup_dirs[0] ^ 1
    return {
        "machine": machine,
        "machine_rank": machine_rank,
        "tilt": float(tilt_deg),
        "setups": setups,
        "coverage": covered / face_count,
        "feasible": internal == 0,
        "flip": flip,
        "counts": {
            "per_setup": [{k: s[k] for k in ("reachable", "exclusive", "marginal")}
                          for s in setups],
            "multi": int((hit_count > 1).sum()),
            "internal": internal,
        },
        "arrows": [{"kind": "setup", "index": j, "direction": s["vector"]}
                   for j, s in enumerate(setups)],
    }


@log_execution_time
def setup_search(directions, accessibility, *, machines=(("3-axis", 0.0),
                                                         ("3+2", 90.0)),
                 max_setups=4, min_setup_faces=10):
    """Rank setup combinations per machine: greedy cover from every seed.

    ``machines`` is a sequence of (name, tilt_deg); earlier machines rank
    higher at equal setup counts. Returns the full ranked list of option
    dicts (JSON-safe, plus an internal machine_rank used by the sort).
    """
    options = []
    for machine_rank, (machine, tilt_deg) in enumerate(machines):
        cover = machine_cover(directions, accessibility, tilt_deg)
        seen = set()
        for seed in range(directions.shape[0]):
            setup_dirs = _greedy_setups(cover, seed, max_setups,
                                        min_setup_faces)
            if frozenset(setup_dirs) in seen:
                continue
            option = _option(machine, machine_rank, tilt_deg, setup_dirs,
                             directions, cover)
            final = frozenset(s["direction"] for s in option["setups"])
            fresh = final not in seen
            seen.update((frozenset(setup_dirs), final))
            if fresh:
                options.append(option)
        logger.debug(f"{machine}: {len(seen)} distinct setup combinations")

    options.sort(key=lambda o: (not o["feasible"], o["machine_rank"],
                                len(o["setups"]), -o["coverage"],
                                not o["flip"], o["setups"][0]["direction"]))
    return options


def setup_membership(setup_dirs, cover):
    """u4[F] bitmask: bit s set iff setup s's cover reaches the face.

    Cover rows, not raw accessibility — a 3+2 setup counts everything its
    tilt cone reaches; 0 means no setup machines the face.
    """
    membership = np.zeros(cover.shape[1], dtype=np.uint32)
    for s, direction in enumerate(setup_dirs):
        membership |= cover[direction].astype(np.uint32) << s
    return membership


def setup_defaults(membership, brep_valid, brep_ids):
    """u1[n_brep] default setup per BREP face: the earliest valid setup.

    Machining as much as possible early keeps later setups short. 254 =
    conflict (partially covered, no setup covers the whole face — needs a
    split), 255 = unmachinable (nothing covers any triangle).
    """
    n_brep = len(brep_valid)
    ids = brep_ids.astype(np.int64)
    default = np.full(n_brep, DEFAULT_CONFLICT, dtype=np.uint8)

    touched = np.bincount(ids[membership > 0], minlength=n_brep) > 0
    default[(brep_valid == 0) & ~touched] = DEFAULT_INTERNAL

    valid = brep_valid.astype(np.int64)
    has_valid = valid > 0
    lowest = np.round(np.log2(np.maximum(valid & -valid, 1)))
    default[has_valid] = lowest.astype(np.uint8)[has_valid]
    return default


def setup_labels_colors(vectors):
    """(labels, colors) indexed by setup: 'setup 1 (+Z)' where axis-clean."""
    labels, colors = [], []
    for j, vector in enumerate(vectors):
        axis = direction_label(vector)
        labels.append(f"setup {j + 1}" + (f" ({axis})" if axis else ""))
        colors.append(SETUP_COLORS[j % len(SETUP_COLORS)])
    return labels, colors
