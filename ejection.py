"""Ejector-pin sticking model and 1-DOF ejection stiffness solve.

Pure numpy/scipy kernels (arrays in, arrays out — no workdir I/O, no
meshlib) behind the ``ejection_sticking`` pipeline stage and the interactive
``POST /ejector/simulate`` endpoint.

Sticking: after cooling the part shrinks onto the mold; steep faces (low
draft relative to the pull axis) grip, and the release traction per face is
``p_shrink * area * max(mu*cos(theta) - sin(theta), 0)`` — the classic
ejection-force estimate, with the draft component assisting release.

Stiffness: one unknown per clustered skeleton node — deflection along the
pull axis. Edges resist differential deflection as beam-like springs
(k = 3*E*I/L^3 with I from the local wall radius), assembled into a
weighted graph Laplacian; ejector pins are Dirichlet supports and the
sticking forces are the loads. Units are mm / N / MPa throughout, so
deflections come out in mm — but the spring constant is indicative, not
calibrated: comparative behavior between pin layouts is the product.

``simulate_ejection`` is the reusable solve a future automatic pin-layout
optimizer calls in a candidate loop, the same way sprue screening loops
over gating.py kernels.
"""

import numpy as np

import gating


def _unit(pull):
    pull = np.asarray(pull, dtype=np.float64)
    return pull / max(np.linalg.norm(pull), 1e-30)


def draft_angles(normals, pull):
    """Draft angle per face in degrees relative to the pull axis.

    0 deg = wall parallel to the pull (zero draft, grips the mold),
    90 deg = face perpendicular to the pull (top/bottom).
    """
    sin_t = np.clip(np.abs(normals @ _unit(pull)), 0.0, 1.0)
    return np.degrees(np.arcsin(sin_t))


def sticking_forces(normals, areas, pull, *, grip_deg=15.0, mu=0.5,
                    p_shrink=0.5, scope=None):
    """Grip mask and release force (N) per face.

    ``scope`` optionally restricts gripping to a face subset (the B/core
    side when mold-orientation data exists). Above atan(mu) the friction
    term is zero anyway; grip_deg is the stricter engineering cutoff.
    """
    sin_t = np.clip(np.abs(normals @ _unit(pull)), 0.0, 1.0)
    cos_t = np.sqrt(1.0 - sin_t ** 2)
    grip = np.degrees(np.arcsin(sin_t)) < grip_deg
    if scope is not None:
        grip &= scope
    force = np.where(grip,
                     p_shrink * areas * np.maximum(mu * cos_t - sin_t, 0.0),
                     0.0)
    return grip, force


def vertex_loads(faces, face_force, vert_count):
    """Scatter each face's release force to its corners (N per vertex)."""
    loads = np.zeros(vert_count)
    share = face_force / 3.0
    for corner in range(3):
        np.add.at(loads, faces[:, corner], share)
    return loads


def node_loads(vert_force, vert_node, node_count):
    """Aggregate vertex loads onto skeleton nodes.

    Vertices the skeleton dropped (sentinel) lose their load; the lost
    fraction is reported so results stay honest.
    """
    mapped = vert_node != gating.NODE_SENTINEL
    loads = np.zeros(node_count)
    np.add.at(loads, vert_node[mapped].astype(np.int64), vert_force[mapped])
    total = vert_force.sum()
    lost = float(vert_force[~mapped].sum() / max(total, 1e-30))
    return loads, lost


def edge_stiffness(nodes, radii, edges, E, *, spring_c=3.0):
    """Spring constant per skeleton edge (N/mm).

    k = c * E * I / L^3 with I = (pi/4) r^4 — a beam segment between medial
    nodes (c = 3 is the cantilever tip stiffness). E in MPa, lengths in mm
    give N/mm, so deflections solve to mm.
    """
    length, radius, _ = gating.edge_geometry(nodes, radii, edges)
    inertia = (np.pi / 4.0) * radius ** 4
    return spring_c * E * inertia / np.maximum(length, 1e-3) ** 3


def simulate_ejection(nodes, radii, edges, loads, pin_nodes, *,
                      E=2000.0, spring_c=3.0):
    """1-DOF deflection along the pull axis over the clustered skeleton.

    ``loads``: (N,) sticking force magnitudes (N, >= 0). ``pin_nodes``:
    Dirichlet node indices (w = 0, deduplicated here). Returns a dict:

    - ``deflection`` (N,) f8 mm — NaN on components with no pin
    - ``node_reaction`` (N,) f8 N — the force each support node delivers
    - ``supported`` (N,) bool — node participated in the solve
    - ``unsupported``: [{"nodes": int, "load": float}] loaded pinless comps
    - ``solved``, ``max_deflection``, ``supported_load``

    Nonnegative loads + zero Dirichlet values give w >= 0 and reactions
    >= 0 (discrete maximum principle), and Laplacian row sums are zero, so
    the reactions always sum to the supported load.
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.sparse.linalg import spsolve

    node_count = len(radii)
    loads = np.asarray(loads, dtype=np.float64)
    edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
    pin_nodes = np.unique(np.asarray(pin_nodes, dtype=np.int64))
    if len(pin_nodes) == 0:
        raise ValueError("at least one pin node is required")
    if pin_nodes.min() < 0 or pin_nodes.max() >= node_count:
        raise ValueError("pin node index out of range")

    a, b = edges[:, 0], edges[:, 1]
    k = edge_stiffness(nodes, radii, edges, E, spring_c=spring_c)
    diagonal = (np.bincount(a, weights=k, minlength=node_count)
                + np.bincount(b, weights=k, minlength=node_count))
    stiffness = csr_matrix(
        (np.concatenate([diagonal, -k, -k]),
         (np.concatenate([np.arange(node_count), a, b]),
          np.concatenate([np.arange(node_count), b, a]))),
        shape=(node_count, node_count))

    adjacency = csr_matrix((np.ones(len(edges)), (a, b)),
                           shape=(node_count, node_count))
    _, component = connected_components(adjacency, directed=False)
    pinned = np.zeros(component.max() + 1, dtype=bool)
    pinned[component[pin_nodes]] = True
    supported = pinned[component]

    free = supported.copy()
    free[pin_nodes] = False
    free_index = np.flatnonzero(free)

    deflection = np.full(node_count, np.nan)
    deflection[pin_nodes] = 0.0
    if len(free_index):
        system = stiffness[free_index][:, free_index].tocsc()
        deflection[free_index] = spsolve(system, loads[free_index])

    # equilibrium K w = loads + reactions with w = 0 at pins; unsupported
    # components carry w = NaN -> 0 here, and their K rows never touch pins
    full = np.where(np.isnan(deflection), 0.0, deflection)
    node_reaction = np.zeros(node_count)
    node_reaction[pin_nodes] = (loads[pin_nodes]
                                - stiffness[pin_nodes] @ full)

    unsupported = []
    for comp in np.flatnonzero(~pinned):
        members = component == comp
        load = float(loads[members].sum())
        if load > 1e-9:
            unsupported.append({"nodes": int(members.sum()), "load": load})

    finite = deflection[supported]
    return {
        "deflection": deflection,
        "node_reaction": node_reaction,
        "supported": supported,
        "unsupported": unsupported,
        "solved": int(supported.sum()),
        "max_deflection": float(finite.max()) if len(finite) else 0.0,
        "supported_load": float(loads[supported].sum()),
    }


def map_pins_to_nodes(nodes, pins):
    """Skeleton node(s) per pin: the nearest node plus every node within
    the pin radius. The nearest-node distance is returned so callers can
    sanity-check a click that landed far from any medial node."""
    from scipy.spatial import cKDTree

    tree = cKDTree(nodes)
    mapped = []
    for pin in pins:
        point = np.asarray(pin["point"], dtype=np.float64)
        distance, nearest = tree.query(point)
        ball = tree.query_ball_point(point, r=float(pin["diameter"]) / 2.0)
        footprint = np.unique(np.append(
            np.asarray(ball, dtype=np.int64), int(nearest)))
        mapped.append({"node": int(nearest), "distance": float(distance),
                       "footprint": footprint})
    return mapped


def pin_report(sim, pin_maps, diameters, allowable_pressure):
    """Per-pin force/pressure summary from a simulate_ejection result.

    Footprint nodes shared by several pins split their reaction equally
    (deterministic). pressure = force / (pi d^2 / 4) versus the allowable.
    """
    reaction = sim["node_reaction"]
    owners = np.zeros(len(reaction), dtype=np.int64)
    for mapped in pin_maps:
        owners[mapped["footprint"]] += 1

    reports = []
    for index, (mapped, diameter) in enumerate(zip(pin_maps, diameters)):
        footprint = mapped["footprint"]
        share = reaction[footprint] / np.maximum(owners[footprint], 1)
        force = float(share.sum())
        pressure = force / (np.pi * diameter ** 2 / 4.0)
        reports.append({
            "index": index,
            "node": mapped["node"],
            "footprint": int(len(footprint)),
            "distance": float(mapped["distance"]),
            "force_n": force,
            "pressure_mpa": pressure,
            "utilization": pressure / max(allowable_pressure, 1e-30),
            "over_limit": bool(pressure > allowable_pressure),
        })
    return reports
