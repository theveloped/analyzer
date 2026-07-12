"""Interactive ejector-pin simulation core.

Loads a stored ejection_sticking result plus the wall_skeleton graph it was
computed against, runs the pure ejection.py stiffness solve, and returns a
JSON-safe payload. Kept out of app.py so tests call it directly (no HTTP
client dependency); the FastAPI route only validates and translates errors.

Touches only cached npz arrays + scipy — no meshlib — so it is safe to run
inline in a request handler, outside the single-thread job worker.
"""

import json
import math
import os

import numpy as np

import ejection
from processes.injection_molding import EJECTION_SCHEMA


def _load_result(workdir, analysis, result_hash):
    base = os.path.join(workdir, "results", "injection_molding", analysis,
                        result_hash)
    if not os.path.exists(base + ".json"):
        raise FileNotFoundError(f"no {analysis} result {result_hash}")
    with open(base + ".json") as f:
        payload = json.load(f)
    if not os.path.exists(base + ".npz"):
        raise FileNotFoundError(f"{analysis} result {result_hash} has no arrays")
    return payload, np.load(base + ".npz", allow_pickle=False)


def simulate(workdir, result_hash, pins, E=2000.0, allowable_pressure=80.0):
    """Solve one pin layout against a stored sticking result.

    ``pins``: [{"point": [x,y,z], "diameter": mm}], at least one. Raises
    FileNotFoundError for missing/stale results and ValueError for invalid
    inputs — the route maps those to 404/400.
    """
    if not pins:
        raise ValueError("at least one pin is required")
    for pin in pins:
        point = pin.get("point")
        if (not isinstance(point, (list, tuple)) or len(point) != 3
                or not all(math.isfinite(float(c)) for c in point)):
            raise ValueError("pin point must be a finite xyz triple")
        if not (float(pin.get("diameter", 0)) > 0):
            raise ValueError("pin diameter must be positive")
    if not (E > 0 and allowable_pressure > 0):
        raise ValueError("E and allowable pressure must be positive")

    payload, arrays = _load_result(workdir, "ejection_sticking", result_hash)
    stats = payload.get("stats", {})
    if stats.get("schema") != EJECTION_SCHEMA:
        raise FileNotFoundError("unsupported ejection_sticking schema")
    skeleton_hash = stats["skeleton_hash"]
    try:
        _, skeleton = _load_result(workdir, "wall_skeleton", skeleton_hash)
    except FileNotFoundError:
        raise FileNotFoundError(
            "the wall_skeleton result this analysis used is gone — "
            "re-run ejection_sticking")

    nodes = np.asarray(skeleton["cluster_nodes"], dtype=np.float64)
    radii = np.asarray(skeleton["cluster_radii"], dtype=np.float64)
    edges = np.asarray(skeleton["cluster_edges"], dtype=np.int64)
    loads = np.asarray(arrays["node_load"], dtype=np.float64)

    pin_maps = ejection.map_pins_to_nodes(nodes, pins)
    pin_nodes = np.unique(np.concatenate(
        [mapped["footprint"] for mapped in pin_maps]))
    sim = ejection.simulate_ejection(nodes, radii, edges, loads, pin_nodes,
                                     E=float(E))
    reports = ejection.pin_report(sim, pin_maps,
                                  [float(p["diameter"]) for p in pins],
                                  float(allowable_pressure))

    deflection = sim["deflection"]
    finite = deflection[np.isfinite(deflection)]
    return {
        "result_hash": result_hash,
        "skeleton_hash": skeleton_hash,
        "nodes": int(len(radii)),
        # NaN (unsupported components) must become null: json rejects NaN
        "deflection": [None if math.isnan(w) else float(w)
                       for w in deflection],
        "pins": reports,
        "stats": {
            "total_sticking_n": float(loads.sum()),
            "supported_load_n": sim["supported_load"],
            "max_deflection_mm": sim["max_deflection"],
            "p95_deflection_mm": (float(np.percentile(finite, 95))
                                  if len(finite) else 0.0),
            "unsupported": [{"nodes": entry["nodes"],
                             "load_n": entry["load"]}
                            for entry in sim["unsupported"]],
            "E_mpa": float(E),
            "allowable_pressure_mpa": float(allowable_pressure),
        },
    }
