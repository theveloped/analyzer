"""Sprue/gate candidate generation, screening and scoring.

Pure numpy/scipy kernels (arrays in, arrays out — no workdir I/O) behind the
``sprue_proposals`` pipeline stage. Candidates are surface vertices mapped to
clustered skeleton nodes; screening is a multi-source Dijkstra over the same
``length / r^4`` flow resistances the interactive fill view uses
(frontend/src/processes/injection/skeleton.ts), so a clicked proposal
reproduces the ranked fill exactly.

The candidate -> hard-filter -> screen -> normalize/score shape is generic on
purpose: ejector-pin placement is expected to reuse it with different filters
and metrics.
"""

import numpy as np

NODE_SENTINEL = np.uint32(0xFFFFFFFF)  # vert->node map: vertex has no node

# per-face / per-vertex mold-reachability category bits
CAT_A, CAT_B, CAT_SLIDE, CAT_INTERNAL, CAT_CONFLICT = 1, 2, 4, 8, 16

# scored metrics in candidate_subscores column order; raw values are all
# "higher = worse"
METRICS = ("pressure", "fill_max", "unreached", "balance", "packing",
           "weld", "airtrap")

DEFAULT_WEIGHTS = {
    "pressure": 2.0,   # p95 flow resistance from the gate
    "fill_max": 1.0,   # worst-case flow resistance
    "unreached": 3.0,  # volume fraction never reached
    "balance": 1.0,    # overpack exposure of early-filled volume
    "packing": 2.5,    # thick volume cut off behind narrow channels
    "weld": 1.0,       # late front-meeting indicator
    "airtrap": 0.5,    # dead-end late-fill indicator
}


def edge_geometry(nodes, radii, edges):
    """(length, channel radius, flow resistance) per skeleton edge.

    Must stay identical to the client fill solve (skeleton.ts
    buildAdjacency): resistance = length / max(mean endpoint radius, 1e-3)^4.
    """
    a = edges[:, 0].astype(np.int64)
    b = edges[:, 1].astype(np.int64)
    length = np.linalg.norm(nodes[a] - nodes[b], axis=1)
    radius = np.maximum(0.5 * (radii[a] + radii[b]), 1e-3)
    return length, radius, length / radius ** 4


def _flow_matrix(node_count, edges, weights):
    from scipy.sparse import csr_matrix

    a = edges[:, 0].astype(np.int64)
    b = edges[:, 1].astype(np.int64)
    return csr_matrix(
        (np.concatenate([weights, weights]),
         (np.concatenate([a, b]), np.concatenate([b, a]))),
        shape=(node_count, node_count))


# --------------------------------------------------------------------------
# candidate generation


def _grid_keys(points, vmin, cell):
    q = np.floor((points - vmin) / cell).astype(np.int64)
    dims = q.max(axis=0) + 1
    return (q[:, 0] * dims[1] + q[:, 1]) * dims[2] + q[:, 2]


def _pick_per_key(keys, thickness, pool):
    """One max-thickness representative vertex per unique key.

    Deterministic: lexsort by (key, descending thickness), keep the first
    occurrence of each key.
    """
    order = np.lexsort((-thickness[pool], keys))
    _, first = np.unique(keys[order], return_index=True)
    return pool[order[first]]


def generate_candidates(verts, thickness, vert_node, *, min_gate_thickness,
                        max_candidates=400, thick_diameter=None,
                        cell_fraction=0.02):
    """High-recall, deduped candidate vertices.

    Grid decimation (max-thickness vertex per cell) over all eligible
    surface vertices, densified 2x near thick regions, then deduped to one
    vertex per skeleton node so every candidate is a distinct Dijkstra
    source. Deterministic — no RNG. Returns (candidate vertex indices,
    thin-rejected count).
    """
    mapped = vert_node != NODE_SENTINEL
    finite = np.isfinite(thickness)
    thin = mapped & finite & (thickness < min_gate_thickness)
    pool = np.flatnonzero(mapped & finite & ~thin)
    rejected_thin = int(thin.sum())
    if len(pool) == 0:
        return np.zeros(0, dtype=np.int64), rejected_thin

    vmin = verts.min(axis=0)
    diag = float(np.linalg.norm(verts.max(axis=0) - vmin))
    cell = max(cell_fraction * diag, 1e-9)
    node_ids = vert_node.astype(np.int64)

    for _ in range(8):
        picks = _pick_per_key(_grid_keys(verts[pool], vmin, cell),
                              thickness, pool)
        if thick_diameter is not None:
            thick_pool = pool[thickness[pool] >= thick_diameter]
            if len(thick_pool):
                dense = _pick_per_key(_grid_keys(verts[thick_pool], vmin,
                                                 cell / 2),
                                      thickness, thick_pool)
                picks = np.union1d(picks, dense)
        # one candidate per skeleton node: identical sources are redundant
        picks = _pick_per_key(node_ids[picks], thickness, picks)
        if len(picks) <= max_candidates:
            break
        cell *= 1.5
    return np.sort(picks), rejected_thin


# --------------------------------------------------------------------------
# mold-reachability categories (hard filters + side tags)


def face_categories_from_defaults(face_feat):
    """Per-mesh-face category bits from brep_default feature codes
    (0=A, 1=B, 2+j=slide, 254=conflict, 255=internal)."""
    cats = np.full(face_feat.shape, CAT_SLIDE, dtype=np.uint8)
    cats[face_feat == 0] = CAT_A
    cats[face_feat == 1] = CAT_B
    cats[face_feat == 254] = CAT_CONFLICT
    cats[face_feat == 255] = CAT_INTERNAL
    return cats


def face_categories_from_membership(membership):
    """Per-mesh-face category bits from the reachability bitmask
    (bit0=A, bit1=B, bit2+j=slide j). A face both sides reach carries both
    bits ("mixed"); slides only count when no side reaches the face (sides
    win assignment defaults)."""
    membership = np.asarray(membership)
    cats = np.zeros(membership.shape, dtype=np.uint8)
    cats[(membership & 1) > 0] |= CAT_A
    cats[(membership & 2) > 0] |= CAT_B
    cats[((membership & 3) == 0) & (membership > 0)] |= CAT_SLIDE
    cats[membership == 0] |= CAT_INTERNAL
    return cats


def vertex_categories(faces, face_cats, vert_count):
    """OR of the category bits of every face incident to each vertex."""
    vert_cats = np.zeros(vert_count, dtype=np.uint8)
    for corner in range(3):
        np.bitwise_or.at(vert_cats, faces[:, corner], face_cats)
    return vert_cats


def side_labels(vert_cats):
    """'A' / 'B' / 'mixed' / 'unknown' per entry."""
    a = (vert_cats & CAT_A) > 0
    b = (vert_cats & CAT_B) > 0
    labels = np.full(vert_cats.shape, "unknown", dtype=object)
    labels[a & ~b] = "A"
    labels[b & ~a] = "B"
    labels[a & b] = "mixed"
    return labels


# --------------------------------------------------------------------------
# screening metrics


def screen_candidates(nodes, radii, edges, cand_nodes, *, thick_radius,
                      pack_factor=0.5, chunk_ops=2e7, progress=None):
    """Raw screening metrics per candidate (all "higher = worse").

    One multi-source Dijkstra over the clustered skeleton (chunked to bound
    memory) plus a candidate-independent wide-channel decomposition for the
    packing metric. Returns ({metric: (K,) array}, reached volume fraction
    per candidate — the disconnected hard filter's input).
    """
    from scipy.sparse.csgraph import connected_components, dijkstra

    node_count = len(radii)
    cand_nodes = np.asarray(cand_nodes, dtype=np.int64)
    count = len(cand_nodes)
    length, radius, resistance = edge_geometry(nodes, radii, edges)
    matrix = _flow_matrix(node_count, edges, resistance)
    volume = radii.astype(np.float64) ** 3
    total_volume = max(volume.sum(), 1e-30)
    edge_volume = length * radius ** 2
    total_edge_volume = max(edge_volume.sum(), 1e-30)

    # packing: which thick volume stays connected to the gate through
    # channels at least pack_factor * thick_radius wide (candidate
    # independent decomposition, O(1) lookup per candidate)
    thick = radii >= thick_radius
    thick_volume_total = float(volume[thick].sum())
    wide = radius >= pack_factor * thick_radius
    _, labels = connected_components(
        _flow_matrix(node_count, edges[wide], resistance[wide]),
        directed=False)
    component_thick_volume = np.bincount(
        labels[thick], weights=volume[thick], minlength=labels.max() + 1)

    raws = {name: np.zeros(count) for name in METRICS}
    reached = np.zeros(count)
    e0 = edges[:, 0].astype(np.int64)
    e1 = edges[:, 1].astype(np.int64)

    chunk = max(1, int(chunk_ops / max(node_count, 1)))
    for start in range(0, count, chunk):
        stop = min(start + chunk, count)
        if progress is not None:
            progress(start / max(count, 1),
                     f"screening candidates ({start}/{count})")
        dist, pred = dijkstra(matrix, directed=True,
                              indices=cand_nodes[start:stop],
                              return_predecessors=True)
        finite = np.isfinite(dist)
        dist_nan = np.where(finite, dist, np.nan)

        with np.errstate(all="ignore"):
            fill_max = np.nanmax(dist_nan, axis=1)
            raws["fill_max"][start:stop] = fill_max
            raws["pressure"][start:stop] = np.nanpercentile(dist_nan, 95,
                                                            axis=1)
            reached_volume = np.where(finite, volume, 0.0).sum(axis=1)
            reached[start:stop] = reached_volume / total_volume
            raws["unreached"][start:stop] = 1.0 - reached[start:stop]

        if thick_volume_total > 0:
            raws["packing"][start:stop] = (
                1.0 - component_thick_volume[labels[cand_nodes[start:stop]]]
                / thick_volume_total)

        if len(edges):
            # weld indicator: edges where two fronts meet (neither endpoint
            # reached through the edge), weighted by edge volume, weighted
            # late (cold fronts weld worse)
            du, dv = dist[:, e0], dist[:, e1]
            with np.errstate(invalid="ignore"):  # inf - inf on unreached
                meet = (np.isfinite(du) & np.isfinite(dv)
                        & (np.abs(du - dv) < resistance * (1 - 1e-6))
                        & (pred[:, e1] != e0) & (pred[:, e0] != e1))
                severity = (0.5 * (du + dv)
                            / np.maximum(fill_max, 1e-30)[:, None])
            raws["weld"][start:stop] = (
                np.where(meet, severity * edge_volume, 0.0).sum(axis=1)
                / total_edge_volume)

            for row in range(stop - start):
                d = dist[row]

                # air-trap indicator: arrival local maxima = dead-end late
                # pockets; the one unavoidable global last-fill maximum
                # contributes exactly 1 and is subtracted
                is_max = np.isfinite(d)
                np.logical_and.at(is_max, e0, d[e0] >= d[e1])
                np.logical_and.at(is_max, e1, d[e1] >= d[e0])
                is_max[cand_nodes[start + row]] = False
                total = d[is_max].sum() / max(fill_max[row], 1e-30)
                raws["airtrap"][start + row] = max(0.0, total - 1.0)

                # balance: overpack exposure — the window between the
                # volume-weighted mean arrival and the volume-weighted p95
                # arrival (the bulk finishing, robust against the huge
                # r^4 resistance of negligible-volume corner nodes);
                # early-filled volume sits packed for that long
                fin = np.isfinite(d)
                arrive = d[fin]
                weight = volume[fin]
                by_arrival = np.argsort(arrive)
                cum = np.cumsum(weight[by_arrival])
                if len(cum) and cum[-1] > 0:
                    p95 = arrive[by_arrival][np.searchsorted(
                        cum, 0.95 * cum[-1])]
                    mean = float((arrive * weight).sum() / cum[-1])
                    raws["balance"][start + row] = max(0.0, p95 - mean)

    return raws, reached


def fill_and_weld(nodes, radii, edges, source):
    """Fill distances from one gate node + its meeting-edge mask.

    Same solve as screen_candidates, for the winning proposal's stored
    visualization fields.
    """
    from scipy.sparse.csgraph import dijkstra

    length, radius, resistance = edge_geometry(nodes, radii, edges)
    matrix = _flow_matrix(len(radii), edges, resistance)
    dist, pred = dijkstra(matrix, directed=True, indices=[int(source)],
                          return_predecessors=True)
    dist, pred = dist[0], pred[0]
    if len(edges):
        e0 = edges[:, 0].astype(np.int64)
        e1 = edges[:, 1].astype(np.int64)
        du, dv = dist[e0], dist[e1]
        with np.errstate(invalid="ignore"):  # inf - inf on unreached
            meet = (np.isfinite(du) & np.isfinite(dv)
                    & (np.abs(du - dv) < resistance * (1 - 1e-6))
                    & (pred[e1] != e0) & (pred[e0] != e1))
    else:
        meet = np.zeros(0, dtype=bool)
    return dist, meet.astype(np.uint8)


# --------------------------------------------------------------------------
# scoring


def normalize_scores(raws, weights):
    """Robust p5-p95 min-max normalization -> subscores + weighted score.

    Subscore = 1 - clipped norm (higher = better). Metrics with no spread
    across the surviving candidates score 1 everywhere and are reported as
    degenerate. Returns (subscores (K, len(METRICS)) f4, score (K,) f4,
    degenerate metric names).
    """
    count = len(next(iter(raws.values()))) if raws else 0
    subscores = np.ones((count, len(METRICS)), dtype=np.float32)
    degenerate = []
    for column, name in enumerate(METRICS):
        values = np.asarray(raws[name], dtype=np.float64)
        finite = np.isfinite(values)
        if not finite.all():
            worst = values[finite].max() if finite.any() else 0.0
            values = np.where(finite, values, worst)
        lo, hi = np.percentile(values, [5, 95]) if count else (0.0, 0.0)
        span = hi - lo
        if span <= 1e-12 * max(abs(hi), abs(lo), 1.0):
            degenerate.append(name)
            continue
        subscores[:, column] = 1.0 - np.clip((values - lo) / span, 0.0, 1.0)

    weight_row = np.array([weights[name] for name in METRICS])
    score = (subscores * weight_row).sum(axis=1) / max(weight_row.sum(), 1e-30)
    return subscores, score.astype(np.float32), degenerate


_PROS = {
    "pressure": lambda r: f"low fill-pressure proxy (p95 resistance {r:.3g})",
    "fill_max": lambda r: f"short worst-case flow path (resistance {r:.3g})",
    "unreached": lambda r: ("fills the entire skeleton" if r < 1e-3 else
                            f"reaches {100 * (1 - r):.0f}% of the volume"),
    "balance": lambda r: "branches fill nearly simultaneously "
                         f"(overpack exposure {r:.3g})",
    "packing": lambda r: ("wide-channel access to "
                          f"{100 * (1 - r):.0f}% of the thick volume"),
    "weld": lambda r: "flow fronts meet early (low weld indicator)",
    "airtrap": lambda r: "few late-filling dead ends",
}
_CONS = {
    "pressure": lambda r: f"high fill-pressure proxy (p95 resistance {r:.3g})",
    "fill_max": lambda r: f"long worst-case flow path (resistance {r:.3g})",
    "unreached": lambda r: f"{100 * r:.0f}% of the volume never fills",
    "balance": lambda r: "early-filled regions overpack while the last "
                         f"branch fills (exposure {r:.3g})",
    "packing": lambda r: (f"{100 * r:.0f}% of the thick volume sits behind "
                          "narrow channels (packing risk)"),
    "weld": lambda r: "fronts meet late — weld-line indicator high",
    "airtrap": lambda r: f"late-filling dead ends (indicator {r:.2f})",
}


def proposal_reasons(subscores, raw, *, side, parting_distance, gate_style,
                     degenerate=()):
    """Human-readable pros/cons for one proposal (explainability output)."""
    scored = [name for name in METRICS if name not in degenerate]
    ranked = sorted(scored, key=lambda name: -subscores[name])
    pros = [_PROS[name](raw[name]) for name in ranked[:2]
            if subscores[name] >= 0.5]
    cons = [_CONS[name](raw[name])
            for name in sorted(scored, key=lambda name: subscores[name])
            if subscores[name] < 0.5][:2]

    if side == "B":
        pros.append("gate on non-cosmetic side B")
    elif side == "A":
        cons.append("gate mark on cosmetic side A")
    if gate_style == "edge":
        pros.append(f"{parting_distance:.1f} mm from the parting line — "
                    "edge/tab gate suitable")
    elif gate_style == "hot_tip":
        cons.append("far from the parting line — needs a hot-tip style gate")
    return {"pros": pros[:3], "cons": cons[:3]}
