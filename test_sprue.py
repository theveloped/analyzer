"""Validation of the automatic sprue/gate proposals against known geometry.

Synthetic parts where the best gate is analytically obvious:

- uniform plate 40 x 40 x 2: the center minimizes flow resistance, so the
  top proposal must sit near the middle and beat any corner candidate
- plate 60 x 20 x 2 with a 10 x 10 x 8 boss near one end: the boss is the
  packing-critical volume, so near-boss candidates must win the packing
  subscore and the top proposal must sit on the boss half
- T-shaped part: a junction gate reaches all three extremities in similar
  times, so it must beat a leg-tip candidate on fill balance

Hard filters (thin gate, forbidden side via a synthetic mold_orientation
result, graceful degradation without one) and the result store / manifest /
binary serving round-trip are exercised the same way test_skeleton.py does.

Run from the repo root: python test_sprue.py
"""
import os
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm

import gating
import pipeline
from analysis import get_mesh_data, subdivide_mesh
from processes.base import apply_defaults, params_hash, store_result
from processes.injection_molding import PROCESS, skeleton_cache_params


def make_plate():
    plate = mm.makeCube(mm.Vector3f(40, 40, 2), mm.Vector3f(-20, -20, 0))
    return subdivide_mesh(plate, 0.8)


def make_boss_plate():
    plate = mm.makeCube(mm.Vector3f(60, 20, 2), mm.Vector3f(-30, -10, 0))
    boss = mm.makeCube(mm.Vector3f(10, 10, 8), mm.Vector3f(17, -5, 0))
    part = mm.boolean(plate, boss, mm.BooleanOperation.Union).mesh
    return subdivide_mesh(part, 0.8)


def make_tee():
    # the stem overlaps 5 mm into the bar: touching-only cubes leave a
    # degenerate coincident-face seam in the boolean union
    bar = mm.makeCube(mm.Vector3f(40, 10, 3), mm.Vector3f(-20, 0, 0))
    stem = mm.makeCube(mm.Vector3f(10, 35, 3), mm.Vector3f(-5, -30, 0))
    part = mm.boolean(bar, stem, mm.BooleanOperation.Union).mesh
    return subdivide_mesh(part, 0.8)


def prepare_workdir(workdir, mesh):
    verts, faces = get_mesh_data(mesh)
    np.save(os.path.join(workdir, pipeline.FINE_VERTS_FILE), verts)
    np.save(os.path.join(workdir, pipeline.FINE_FACES_FILE), faces)
    return verts, faces


def run_analysis(workdir, **overrides):
    analysis = PROCESS.analysis("sprue_proposals")
    params = apply_defaults(analysis, overrides)
    return analysis.run(workdir, params, None), params


def nearest_candidate(result_arrays, target):
    points = result_arrays["candidate_points"].reshape(-1, 3)
    return int(np.argmin(np.linalg.norm(points - np.asarray(target), axis=1)))


def load_arrays(workdir, params):
    from processes.base import load_result_arrays
    cache_params = {**params, "schema": 1}
    return load_result_arrays(workdir, "injection_molding",
                              "sprue_proposals", cache_params)


def synthetic_mold_result(workdir, verts, faces):
    """Store a schema-2 mold_orientation result with membership derived
    from face normals: A reaches +Z-facing faces, B reaches -Z-facing ones,
    near-vertical walls carry both bits (the STL / no-BREP fallback path)."""
    tri = verts[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True),
                          1e-30)
    membership = np.zeros(len(faces), dtype=np.uint32)
    membership[normals[:, 2] > 0.5] |= 1
    membership[normals[:, 2] < -0.5] |= 2
    membership[np.abs(normals[:, 2]) <= 0.5] |= 3
    stats = {"schema": 2, "options": [], "brep": False}
    store_result(workdir, "injection_molding", "mold_orientation",
                 {"synthetic": True}, stats,
                 arrays={"membership_0": membership},
                 field_meta={"membership_0": {"association": "face",
                                              "role": "category",
                                              "dtype": "u4"}})


def main():
    failures = []

    def check(name, ok, detail):
        status = "OK " if ok else "FAIL"
        print(f"  [{status}] {name}: {detail}")
        if not ok:
            failures.append(f"{name}: {detail}")

    balance_col = gating.METRICS.index("balance")
    packing_col = gating.METRICS.index("packing")

    # --- uniform plate: the center wins --------------------------------
    print("=== plate 40 x 40 x 2 ===")
    with tempfile.TemporaryDirectory() as workdir:
        prepare_workdir(workdir, make_plate())
        result, params = run_analysis(workdir)
        arrays = load_arrays(workdir, params)
        top = result.stats["proposals"][0]

        check("proposals returned", len(result.stats["proposals"]) > 0,
              f"{len(result.stats['proposals'])} proposals, "
              f"{result.stats['candidates']['scored']} scored")
        center_offset = np.linalg.norm(np.asarray(top["point"][:2]))
        check("top proposal near plate center", center_offset < 6.0,
              f"|xy| = {center_offset:.2f} mm at {top['point']}")
        check("plate fills completely", top["raw"]["unreached"] < 1e-3,
              f"unreached {top['raw']['unreached']:.4f}")

        scores = arrays["candidate_score"]
        center = nearest_candidate(arrays, [0, 0, 2])
        corner = nearest_candidate(arrays, [-20, -20, 2])
        check("center candidate outscores corner",
              scores[center] > scores[corner],
              f"center {scores[center]:.3f} vs corner {scores[corner]:.3f}")
        check("no orientation -> degraded confidence",
              result.stats["confidence"] == "no_orientation"
              and not result.stats["orientation"]["used"],
              result.stats["confidence"])
        check("unknown gate style without parting data",
              top["gate_style"] == "unknown", top["gate_style"])

    # --- plate with thick boss: packing access -------------------------
    print("=== plate 60 x 20 x 2 with 10 x 10 x 8 boss ===")
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir, make_boss_plate())
        result, params = run_analysis(workdir)
        arrays = load_arrays(workdir, params)
        top = result.stats["proposals"][0]

        check("thick threshold isolates the boss",
              result.stats["thick"]["radius_threshold"] > 1.5,
              f"r_thick {result.stats['thick']['radius_threshold']:.2f} "
              f"(plate radius ~1)")
        check("top proposal on the boss half", top["point"][0] > 0,
              f"x = {top['point'][0]:.1f}")

        subscores = arrays["candidate_subscores"].reshape(
            -1, len(gating.METRICS))
        near_boss = nearest_candidate(arrays, [22, 0, 8])
        far_corner = nearest_candidate(arrays, [-30, -10, 2])
        check("near-boss candidate wins packing subscore",
              subscores[near_boss, packing_col]
              > subscores[far_corner, packing_col],
              f"boss {subscores[near_boss, packing_col]:.2f} vs "
              f"corner {subscores[far_corner, packing_col]:.2f}")

        # thin-gate hard filter: only the boss is thick enough
        result_thin, params_thin = run_analysis(workdir,
                                                min_gate_thickness=4.0)
        rejected = result_thin.stats["candidates"]["rejected"]
        check("thin filter rejects plate candidates",
              rejected["thin"] > 0, f"rejected {rejected['thin']} thin")
        on_boss = [p["point"][0] > 10 for p in result_thin.stats["proposals"]]
        check("thin-gated proposals all sit at the boss",
              len(on_boss) > 0 and all(on_boss),
              f"{sum(on_boss)}/{len(on_boss)} on the boss")

    # --- T-shaped part: fill balance ------------------------------------
    print("=== T-shaped part (40 x 10 bar + 10 x 30 stem, t=3) ===")
    with tempfile.TemporaryDirectory() as workdir:
        prepare_workdir(workdir, make_tee())
        result, params = run_analysis(workdir)
        arrays = load_arrays(workdir, params)
        top = result.stats["proposals"][0]

        subscores = arrays["candidate_subscores"].reshape(
            -1, len(gating.METRICS))
        scores = arrays["candidate_score"]
        junction = nearest_candidate(arrays, [0, 2, 3])
        mid_bar = nearest_candidate(arrays, [12, 5, 3])
        check("junction beats a mid-bar gate on balance",
              subscores[junction, balance_col]
              > subscores[mid_bar, balance_col],
              f"junction {subscores[junction, balance_col]:.2f} vs "
              f"mid-bar {subscores[mid_bar, balance_col]:.2f}")

        extremities = np.array([[-20, 5], [20, 5], [0, -30]])
        for target in extremities:
            tip = nearest_candidate(arrays, [*target, 1.5])
            check(f"junction outscores tip {target.tolist()}",
                  scores[junction] > scores[tip],
                  f"junction {scores[junction]:.3f} vs tip {scores[tip]:.3f}")
        to_junction = np.linalg.norm(np.asarray(top["point"])[:2]
                                     - np.array([0, 2.5]))
        to_tips = np.linalg.norm(
            extremities - np.asarray(top["point"])[:2], axis=1)
        check("top proposal in the junction region",
              to_junction < 12 and (to_tips > 12).all(),
              f"junction {to_junction:.1f} mm, tips {np.round(to_tips, 1)}")

    # --- forbidden side over a synthetic mold result --------------------
    print("=== forbidden side (synthetic mold_orientation) ===")
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir, make_plate())
        synthetic_mold_result(workdir, verts, faces)
        result, params = run_analysis(workdir, forbid_side="A")

        check("orientation picked up",
              result.stats["orientation"]["used"]
              and result.stats["confidence"] == "full",
              str(result.stats["orientation"]))
        rejected = result.stats["candidates"]["rejected"]
        check("A-side candidates rejected", rejected["side"] > 0,
              f"rejected {rejected['side']} on side A")
        proposal_sides = [p["side"] for p in result.stats["proposals"]]
        check("no proposal on side A",
              len(proposal_sides) > 0
              and all(side != "A" for side in proposal_sides),
              f"sides {sorted(set(proposal_sides))}")

    # --- store / manifest / serving round-trip --------------------------
    print("=== analysis result round-trip ===")
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir, make_plate())
        result, params = run_analysis(workdir)
        again, _ = run_analysis(workdir)
        check("cached rerun identical",
              result.fields == again.fields and result.stats == again.stats,
              f"{len(result.fields)} fields")
        check("skeleton hash binds the sub-run",
              result.stats["skeleton_hash"]
              == params_hash(skeleton_cache_params(params)),
              result.stats["skeleton_hash"])

        from api.manifest import build_manifest
        part = {
            "id": os.path.basename(workdir),
            "status": "meshed",
            "counts": {"verts": int(len(verts)), "faces": int(len(faces))},
        }
        manifest = build_manifest(os.path.dirname(workdir), part)
        by_id = {entry["id"].rsplit(".", 1)[-1]: entry
                 for entry in manifest["fields"]
                 if ".sprue_proposals." in entry["id"]}
        check("manifest exposes all sprue fields",
              set(by_id) == set(result.fields), sorted(by_id))

        arrays = load_arrays(workdir, params)
        scored = result.stats["candidates"]["scored"]
        check("candidate_points is f4 with flat length",
              by_id["candidate_points"]["dtype"] == "f4"
              and by_id["candidate_points"]["length"] == scored * 3,
              str({k: by_id["candidate_points"][k]
                   for k in ("dtype", "length")}))
        check("best_fill covers the clustered graph",
              by_id["best_fill"]["length"] == result.stats["nodes"],
              f"{by_id['best_fill']['length']} vs {result.stats['nodes']}")
        check("subscores carry metric names",
              by_id["candidate_subscores"]["params"]["metrics"]
              == list(gating.METRICS),
              str(by_id["candidate_subscores"]["params"]["metrics"]))

        from api.fields import result_field_bytes
        result_hash = params_hash({**params, "schema": 1})
        for name in ("candidate_points", "proposal_index", "best_fill",
                     "weld_edges_best"):
            entry = by_id[name]
            item = 1 if entry["dtype"] == "u1" else 4
            data, dtype = result_field_bytes(
                workdir, "injection_molding", "sprue_proposals", result_hash,
                name)
            check(f"served bytes for {name}",
                  dtype == "<" + entry["dtype"]
                  and len(data) == entry["length"] * item,
                  f"{dtype} {len(data)} bytes")

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
