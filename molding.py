"""Mold orientation search and per-face side assignment.

Given the (D, F) accessibility matrix over antipodal direction pairs, rank
mold orientations: the two mold plates open along an antipodal pair, plus
optional slides preferably perpendicular to the pull axis, chosen by greedy
set cover so every slide's marginal contribution is known. Faces reachable
by neither the pair nor any perpendicular slide are internal undercuts
(inside slides / hand-loads — solved separately later).

Per-face assignment categories (u1 codes, stable):
  0 side A (forced: visible only from directions[i])
  1 side B (forced: visible only from directions[i+1])
  2 either (visible from both — the parting-line choice band)
  3 internal undercut
  4+j slide j (in greedy pick order)
  4+S straddle (BREP-aggregated field only: face contains forced A and B)
"""

import numpy as np
from loguru import logger

from utils import log_execution_time

SIDE_A, SIDE_B, EITHER, INTERNAL, SLIDE_BASE = 0, 1, 2, 3, 4

# side/category palette (frontend receives these through field_meta)
CATEGORY_COLORS = {
    "side_a": [0.44, 0.64, 0.86],
    "side_b": [0.62, 0.80, 0.58],
    "either": [0.87, 0.90, 0.92],
    "internal": [0.88, 0.29, 0.23],
    "straddle": [0.95, 0.35, 0.60],
}
SLIDE_COLORS = [
    [0.95, 0.66, 0.23], [0.60, 0.40, 0.80], [0.30, 0.75, 0.75],
    [0.80, 0.75, 0.30], [0.75, 0.45, 0.30], [0.55, 0.55, 0.90],
]


def perpendicular_candidates(axis, directions, tolerance_deg=2.0):
    """Direction indices within tolerance of perpendicular to the axis.

    abs() keeps a slide and its antipode as independent candidates.
    """
    dots = np.abs(directions @ np.asarray(axis, dtype=float))
    return np.flatnonzero(dots <= np.cos(np.radians(90.0 - tolerance_deg)))


@log_execution_time
def mold_orientation_search(directions, accessibility, *, max_slides=2,
                            slide_tolerance_deg=2.0, min_slide_faces=50):
    """Rank mold orientations: antipodal pair + greedy perpendicular slides.

    Returns the full ranked list of option dicts (JSON-safe).
    """
    face_count = accessibility.shape[1]
    options = []

    for i in range(0, directions.shape[0], 2):
        covered_a = accessibility[i]
        covered_b = accessibility[i + 1]
        residual = ~(covered_a | covered_b)

        candidates = perpendicular_candidates(directions[i], directions,
                                              slide_tolerance_deg)
        slides = []
        while residual.any() and len(slides) < max_slides and len(candidates):
            gains = (accessibility[candidates] & residual).sum(axis=1)
            best = int(np.argmax(gains))
            if gains[best] < min_slide_faces:
                break
            direction_index = int(candidates[best])
            marginal = int(gains[best])
            residual &= ~accessibility[direction_index]
            candidates = np.delete(candidates, best)
            slides.append({
                "direction": direction_index,
                "vector": [float(c) for c in directions[direction_index]],
                "marginal": marginal,
            })

        internal = int(residual.sum())
        arrows = [
            {"kind": "main_a", "direction": [float(c) for c in directions[i]]},
            {"kind": "main_b", "direction": [float(c) for c in directions[i + 1]]},
        ] + [
            {"kind": "slide", "index": j, "direction": s["vector"]}
            for j, s in enumerate(slides)
        ]
        options.append({
            "pair": [i, i + 1],
            "slides": slides,
            "coverage": 1.0 - internal / face_count,
            "feasible": internal == 0,
            "counts": {
                "side_a": int((covered_a & ~covered_b).sum()),
                "side_b": int((covered_b & ~covered_a).sum()),
                "either": int((covered_a & covered_b).sum()),
                "internal": internal,
                "slides": [s["marginal"] for s in slides],
            },
            "arrows": arrows,
        })

    options.sort(key=lambda o: (not o["feasible"], len(o["slides"]),
                                -o["coverage"], o["counts"]["either"],
                                o["pair"][0]))
    return options


def assignment_band(pair, slide_dirs, accessibility):
    """Per-face category field with the 'either' band kept explicit."""
    covered_a = accessibility[pair[0]]
    covered_b = accessibility[pair[1]]
    band = np.full(accessibility.shape[1], INTERNAL, dtype=np.uint8)
    band[covered_a & ~covered_b] = SIDE_A
    band[covered_b & ~covered_a] = SIDE_B
    band[covered_a & covered_b] = EITHER

    residual = ~(covered_a | covered_b)
    for j, direction_index in enumerate(slide_dirs):
        newly = residual & accessibility[direction_index]
        band[newly] = SLIDE_BASE + j
        residual &= ~accessibility[direction_index]
    return band


def face_adjacency(faces):
    """Edge-adjacent face pairs and the shared edge's vertex ids.

    Returns (pairs (M,2) int32 face indices, edge_verts (M,2) int32).
    """
    f = np.asarray(faces, dtype=np.int64)
    edges = np.stack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]],
                     axis=1).reshape(-1, 2)
    edges.sort(axis=1)
    owner = np.repeat(np.arange(len(f), dtype=np.int64), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owner = edges[order], owner[order]
    same = (edges[1:] == edges[:-1]).all(axis=1)
    pairs = np.stack([owner[:-1][same], owner[1:][same]], axis=1).astype(np.int32)
    edge_verts = edges[:-1][same].astype(np.int32)
    return pairs, edge_verts


def resolve_either(band, pairs):
    """Assign 'either' faces by growing regions from forced A/B neighbors.

    Multi-source BFS over face adjacency; slides/internal act as walls.
    Ties and enclosed islands fall to side A.
    """
    resolved = band.copy()
    undecided = resolved == EITHER
    u, v = pairs[:, 0], pairs[:, 1]

    while undecided.any():
        new_a = np.zeros(len(resolved), dtype=bool)
        new_b = np.zeros(len(resolved), dtype=bool)
        for a, b in ((u, v), (v, u)):
            mask = undecided[b]
            new_a[b[mask & (resolved[a] == SIDE_A)]] = True
            new_b[b[mask & (resolved[a] == SIDE_B)]] = True
        grew = undecided & (new_a | new_b)
        if not grew.any():
            break
        resolved[grew & new_a] = SIDE_A            # ties go to A
        resolved[grew & new_b & ~new_a] = SIDE_B
        undecided &= ~grew

    resolved[undecided] = SIDE_A                   # unreached islands
    return resolved


def aggregate_brep(band, resolved, brep_ids):
    """Whole-BREP-face assignment: straddle where a face is forced both ways,
    otherwise the mode of the resolved categories over the face's triangles."""
    n_brep = int(brep_ids.max()) + 1
    n_codes = int(max(resolved.max(), band.max())) + 1
    straddle_code = n_codes  # one past the last used code (== SLIDE_BASE + S)

    has_a = np.zeros(n_brep, dtype=bool)
    has_b = np.zeros(n_brep, dtype=bool)
    has_a[np.unique(brep_ids[band == SIDE_A])] = True
    has_b[np.unique(brep_ids[band == SIDE_B])] = True

    votes = np.zeros(n_brep * n_codes, dtype=np.int64)
    np.add.at(votes, brep_ids.astype(np.int64) * n_codes + resolved, 1)
    per_face = votes.reshape(n_brep, n_codes).argmax(axis=1).astype(np.uint8)
    per_face[has_a & has_b] = straddle_code

    return per_face[brep_ids], straddle_code


def parting_line_segments(resolved, pairs, edge_verts, verts):
    """Coordinates of mesh edges where the resolved assignment flips A<->B.

    Slide/internal boundaries are painted regions, not the parting line.
    """
    a, b = resolved[pairs[:, 0]], resolved[pairs[:, 1]]
    flip = ((a == SIDE_A) & (b == SIDE_B)) | ((a == SIDE_B) & (b == SIDE_A))
    return verts[edge_verts[flip]].astype("<f4")


def category_labels_colors(n_slides, straddle=False):
    """labels/colors lists indexed by category code, for field_meta."""
    labels = ["side A", "side B", "either", "internal undercut"]
    colors = [CATEGORY_COLORS["side_a"], CATEGORY_COLORS["side_b"],
              CATEGORY_COLORS["either"], CATEGORY_COLORS["internal"]]
    for j in range(n_slides):
        labels.append(f"slide {j + 1}")
        colors.append(SLIDE_COLORS[j % len(SLIDE_COLORS)])
    if straddle:
        labels.append("straddle (needs split)")
        colors.append(CATEGORY_COLORS["straddle"])
    return labels, colors
