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


def face_areas(verts, faces):
    """float64[F] triangle areas — the weights behind every setup count.

    Counting triangles instead would bias every number towards finely
    tessellated features (a small fillet outvoting a big flat face).
    """
    tri = verts[faces].astype(np.float64)
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return 0.5 * np.linalg.norm(cross, axis=1)


def face_angles_deg(normals, direction):
    """float64[F] angle between each face normal and an approach direction:
    0 = floor seen straight on, 90 = vertical wall, > 90 = overhang."""
    dots = np.clip(normals @ np.asarray(direction, dtype=float), -1.0, 1.0)
    return np.degrees(np.arccos(dots))


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


def cone_members(directions, tilt_deg):
    """Per direction, the sampled direction indices inside its tilt cone.

    Returns a list of int arrays; tilt 0 degenerates to [d] itself. Same
    epsilon convention as machine_cover.
    """
    if tilt_deg <= 0:
        return [np.array([d]) for d in range(directions.shape[0])]
    dots = directions @ directions.T
    inside = dots >= np.cos(np.radians(tilt_deg)) - 1e-9
    return [np.flatnonzero(inside[d]) for d in range(directions.shape[0])]


def direction_label(vector):
    """'+Z'-style tag when the vector is (near) a principal axis, else ''."""
    vector = np.asarray(vector, dtype=float)
    axis = int(np.argmax(np.abs(vector)))
    if abs(vector[axis]) >= 0.99:
        return AXIS_NAMES[2 * axis + (0 if vector[axis] > 0 else 1)]
    return ""


def _greedy_setups(cover, seed, max_setups, min_gain, weights):
    """Greedy set cover from one seed direction; returns direction indices.

    Gains are area-weighted. Ties prefer the antipode of an already-chosen
    setup (directions are laid out as antipodal pairs, antipode(i) == i ^ 1)
    — the classic flip re-fixture is the cheapest second setup, so among
    equals it should surface. Equal gains come from identical residual face
    sets, so the comparison only needs a rounding-slack epsilon.
    """
    chosen = [int(seed)]
    residual = ~cover[seed]
    while residual.any() and len(chosen) < max_setups:
        masked = weights * residual
        # row-wise dot: one row is promoted to float at a time, never the
        # whole (D, F) matrix
        gains = np.array([np.dot(masked, row) for row in cover])
        best_gain = float(gains.max())
        if best_gain < min_gain:
            break
        best = int(np.argmax(gains))
        for direction in chosen:
            if gains[direction ^ 1] >= best_gain - 1e-9 * max(best_gain, 1.0):
                best = direction ^ 1
                break
        chosen.append(best)
        residual &= ~cover[best]
    return chosen


def _option(machine, machine_rank, tilt_deg, setup_dirs, directions, cover,
            weights):
    """Assemble the JSON-safe option dict for one setup set.

    All counts are area-weighted (mm² for a mm-unit part); feasibility stays
    an exact face-set property so a sliver of uncovered triangles cannot
    round away.
    """
    face_count = cover.shape[1]
    rows = cover[setup_dirs]
    total = float(weights.sum())

    # presentation order: the biggest setup machines the bulk first,
    # regardless of which greedy seed found the set; setups left with no
    # marginal contribution after the reorder (a redundant seed demoted
    # behind the picks that replaced it) are dropped
    order = np.argsort([-float(weights[r].sum()) for r in rows], kind="stable")
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
    uncovered = hit_count == 0
    internal_area = float(weights[uncovered].sum())

    area = lambda mask: round(float(weights[mask].sum()), 3)  # noqa: E731
    setups = []
    residual = np.ones(face_count, dtype=bool)
    for row, direction in zip(rows, setup_dirs):
        setups.append({
            "direction": int(direction),
            "vector": [float(c) for c in directions[direction]],
            "reachable": area(row),
            "exclusive": area(row & (hit_count == 1)),
            "marginal": area(row & residual),
        })
        residual &= ~row

    flip = len(setup_dirs) == 2 and setup_dirs[1] == setup_dirs[0] ^ 1
    return {
        "machine": machine,
        "machine_rank": machine_rank,
        "tilt": float(tilt_deg),
        "setups": setups,
        "coverage": 1.0 - internal_area / total,
        "feasible": not bool(uncovered.any()),
        "flip": flip,
        "counts": {
            "per_setup": [{k: s[k] for k in ("reachable", "exclusive", "marginal")}
                          for s in setups],
            "multi": area(hit_count > 1),
            "internal": round(internal_area, 3),
            "internal_faces": int(uncovered.sum()),
        },
        "arrows": [{"kind": "setup", "index": j, "direction": s["vector"]}
                   for j, s in enumerate(setups)],
    }


@log_execution_time
def setup_search(directions, accessibility, *, weights=None,
                 machines=(("3-axis", 0.0), ("3+2", 90.0)),
                 max_setups=4, min_setup_gain=0.0):
    """Rank setup combinations per machine: greedy cover from every seed.

    ``machines`` is a sequence of (name, tilt_deg); earlier machines rank
    higher at equal setup counts. ``weights`` (default: uniform) weight the
    gains, counts and coverage — pass triangle areas so numbers read in mm²
    instead of tessellation-biased triangle counts; ``min_setup_gain`` is in
    the same unit. Returns the full ranked list of option dicts (JSON-safe,
    plus an internal machine_rank used by the sort).
    """
    if weights is None:
        weights = np.ones(accessibility.shape[1])
    weights = np.asarray(weights, dtype=np.float64)

    options = []
    for machine_rank, (machine, tilt_deg) in enumerate(machines):
        cover = machine_cover(directions, accessibility, tilt_deg)
        seen = set()
        for seed in range(directions.shape[0]):
            setup_dirs = _greedy_setups(cover, seed, max_setups,
                                        min_setup_gain, weights)
            if frozenset(setup_dirs) in seen:
                continue
            option = _option(machine, machine_rank, tilt_deg, setup_dirs,
                             directions, cover, weights)
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


def membership_from_rows(rows):
    """u4[F] bitmask: bit s set iff coverage row s reaches the face."""
    membership = np.zeros(len(rows[0]), dtype=np.uint32)
    for s, row in enumerate(rows):
        membership |= row.astype(np.uint32) << s
    return membership


def setup_membership(setup_dirs, cover):
    """u4[F] bitmask: bit s set iff setup s's cover reaches the face.

    Cover rows, not raw accessibility — a 3+2 setup counts everything its
    tilt cone reaches; 0 means no setup machines the face.
    """
    return membership_from_rows([cover[d] for d in setup_dirs])


def reweight_option(option, rows, weights):
    """Copy of an option with its counts recomputed from new coverage rows.

    Used by the tool verdict: the plan (setup directions, order, arrows)
    stays exactly as ranked, only reachability changed — so no reordering
    or pruning, a setup that lost all its faces to tooling should show a
    zero, not vanish.
    """
    rows = [np.asarray(row) for row in rows]
    hit_count = np.sum(rows, axis=0)
    uncovered = hit_count == 0
    total = float(weights.sum())

    area = lambda mask: round(float(weights[mask].sum()), 3)  # noqa: E731
    out = {**option, "setups": [dict(s) for s in option["setups"]]}
    residual = np.ones(len(hit_count), dtype=bool)
    for setup, row in zip(out["setups"], rows):
        setup["reachable"] = area(row)
        setup["exclusive"] = area(row & (hit_count == 1))
        setup["marginal"] = area(row & residual)
        residual &= ~row

    internal_area = float(weights[uncovered].sum())
    out["coverage"] = 1.0 - internal_area / total
    out["feasible"] = not bool(uncovered.any())
    out["counts"] = {
        "per_setup": [{k: s[k] for k in ("reachable", "exclusive", "marginal")}
                      for s in out["setups"]],
        "multi": area(hit_count > 1),
        "internal": round(internal_area, 3),
        "internal_faces": int(uncovered.sum()),
    }
    return out


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
