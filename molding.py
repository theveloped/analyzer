"""Mold orientation search and per-face side assignment.

Given the (D, F) accessibility matrix over antipodal direction pairs, rank
mold orientations: the two mold plates open along an antipodal pair, plus
optional slides preferably perpendicular to the pull axis, chosen by greedy
set cover so every slide's marginal contribution is known. Faces reachable
by neither the pair nor any perpendicular slide are internal undercuts
(inside slides / hand-loads — solved separately later).

Assignment is membership based: per mesh face a bitmask records every
feature (side A, side B, slide j) whose direction reaches it — a face can be
valid for none, one or several. A feature is valid for a whole BREP face iff
it reaches every one of its triangles; BREP faces with partial coverage but
no fully covering feature need a split (conflict), faces reached by nothing
belong to a numbered internal undercut region. The chosen feature per BREP
face (default + user toggles) defines the parting, which therefore runs
along BREP edges.

Feature index == membership bit: 0 = side A, 1 = side B, 2+j = slide j.
"""

import numpy as np
from loguru import logger

from utils import log_execution_time

FEAT_A, FEAT_B, FEAT_SLIDE_BASE = 0, 1, 2  # feature index == membership bit
DEFAULT_CONFLICT, DEFAULT_INTERNAL = 254, 255  # brep_default sentinels

# palette (frontend receives these through field_meta)
CATEGORY_COLORS = {
    "side_a": [0.44, 0.64, 0.86],
    "side_b": [0.62, 0.80, 0.58],
    "internal": [0.88, 0.29, 0.23],
    "conflict": [0.95, 0.35, 0.60],
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

    Every pull pair contributes one option per slide count 0..max_slides
    (the greedy picks are incremental, so the k-slide option is the first
    k picks). Ranking is purely by coverage — feasible options sit at 1.0
    and tie-break to fewer slides, so the cheapest feasible mold still
    wins. Returns the full ranked list of option dicts (JSON-safe).
    """
    face_count = accessibility.shape[1]
    options = []

    for i in range(0, directions.shape[0], 2):
        covered_a = accessibility[i]
        covered_b = accessibility[i + 1]
        residual = ~(covered_a | covered_b)

        base_counts = {
            "side_a": int((covered_a & ~covered_b).sum()),
            "side_b": int((covered_b & ~covered_a).sum()),
            "either": int((covered_a & covered_b).sum()),
        }

        def emit(slides, internal):
            arrows = [
                {"kind": "main_a",
                 "direction": [float(c) for c in directions[i]]},
                {"kind": "main_b",
                 "direction": [float(c) for c in directions[i + 1]]},
            ] + [
                {"kind": "slide", "index": j, "direction": s["vector"]}
                for j, s in enumerate(slides)
            ]
            options.append({
                "pair": [i, i + 1],
                "slides": list(slides),
                "coverage": 1.0 - internal / face_count,
                "feasible": internal == 0,
                "counts": {
                    **base_counts,
                    "internal": internal,
                    "slides": [s["marginal"] for s in slides],
                },
                "arrows": arrows,
            })

        candidates = perpendicular_candidates(directions[i], directions,
                                              slide_tolerance_deg)
        slides = []
        emit(slides, int(residual.sum()))
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
            emit(slides, int(residual.sum()))

    options.sort(key=lambda o: (-o["coverage"], len(o["slides"]),
                                o["counts"]["either"], o["pair"][0]))
    return options


def membership_field(pair, slide_dirs, accessibility):
    """u4[F] reachability bitmask: bit0=A, bit1=B, bit(2+j)=slide j.

    Raw accessibility rows per feature — a face reachable by side A and
    slide 0 carries both bits; 0 means unreachable by every feature.
    """
    membership = (accessibility[pair[0]].astype(np.uint32)
                  | (accessibility[pair[1]].astype(np.uint32) << FEAT_B))
    for j, direction_index in enumerate(slide_dirs):
        membership |= (accessibility[direction_index].astype(np.uint32)
                       << (FEAT_SLIDE_BASE + j))
    return membership


def internal_regions(membership, pairs, n_faces):
    """Numbered connected components of unreachable faces.

    Returns (region u4[F] with 0 = not internal and r = 1..K, counts) where
    regions are ordered by descending triangle count (ties: smallest member
    face index).
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    internal = membership == 0
    n_internal = int(internal.sum())
    region = np.zeros(n_faces, dtype=np.uint32)
    if n_internal == 0:
        return region, []

    compact = np.full(n_faces, -1, dtype=np.int64)
    compact[internal] = np.arange(n_internal)
    both = internal[pairs[:, 0]] & internal[pairs[:, 1]]
    u = compact[pairs[both, 0]]
    v = compact[pairs[both, 1]]
    graph = coo_matrix((np.ones(len(u), dtype=np.int8), (u, v)),
                       shape=(n_internal, n_internal))
    n_components, labels = connected_components(graph, directed=False)

    sizes = np.bincount(labels, minlength=n_components)
    first = np.full(n_components, n_faces, dtype=np.int64)
    np.minimum.at(first, labels, np.flatnonzero(internal))
    order = np.lexsort((first, -sizes))  # descending size, then first face
    rank = np.empty(n_components, dtype=np.uint32)
    rank[order] = np.arange(1, n_components + 1)

    region[internal] = rank[labels]
    counts = [int(sizes[i]) for i in order]
    return region, counts


def brep_validity(membership, brep_ids, n_features):
    """u4[n_brep]: bit f set iff feature f reaches EVERY triangle of the face."""
    n_brep = int(brep_ids.max()) + 1
    ids = brep_ids.astype(np.int64)
    total = np.bincount(ids, minlength=n_brep)
    valid = np.zeros(n_brep, dtype=np.uint32)
    for f in range(n_features):
        covered = np.bincount(ids[((membership >> f) & 1) == 1],
                              minlength=n_brep)
        valid |= ((covered == total).astype(np.uint32) << f)
    return valid


def brep_defaults(membership, brep_valid, brep_ids):
    """u1[n_brep] default feature per BREP face.

    Sides beat slides (plates are free, slides cost mechanism); A-vs-B ties
    break by which side exclusively reaches more of the face's triangles,
    then to A. 254 = conflict (partially reachable, no full cover),
    255 = internal (nothing reaches any triangle).
    """
    n_brep = len(brep_valid)
    ids = brep_ids.astype(np.int64)
    default = np.full(n_brep, DEFAULT_CONFLICT, dtype=np.uint8)

    touched = np.bincount(ids[membership > 0], minlength=n_brep) > 0
    default[(brep_valid == 0) & ~touched] = DEFAULT_INTERNAL

    # lowest valid slide (applied first so sides overwrite below)
    slide_bits = (brep_valid >> FEAT_SLIDE_BASE).astype(np.int64)
    has_slide = slide_bits > 0
    if has_slide.any():
        lowest = np.round(np.log2(np.maximum(slide_bits & -slide_bits, 1)))
        default[has_slide] = (FEAT_SLIDE_BASE + lowest.astype(np.uint8))[has_slide]

    valid_a = (brep_valid & (1 << FEAT_A)) > 0
    valid_b = (brep_valid & (1 << FEAT_B)) > 0
    only_a = np.bincount(ids[(membership & 3) == 1], minlength=n_brep)
    only_b = np.bincount(ids[(membership & 3) == 2], minlength=n_brep)

    default[valid_b] = FEAT_B
    default[valid_a] = FEAT_A
    both = valid_a & valid_b
    default[both & (only_b > only_a)] = FEAT_B  # A wins ties
    return default


def feature_labels_colors(n_slides):
    """(labels, colors) indexed by feature: side A, side B, slide 1..S."""
    labels = ["side A", "side B"]
    colors = [CATEGORY_COLORS["side_a"], CATEGORY_COLORS["side_b"]]
    for j in range(n_slides):
        labels.append(f"slide {j + 1}")
        colors.append(SLIDE_COLORS[j % len(SLIDE_COLORS)])
    return labels, colors


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
