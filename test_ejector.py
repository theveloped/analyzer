"""Validation of the ejection sticking model and pin-simulation solve.

Analytic fixtures:

- box 40 x 40 x 10, pull +Z: only the four vertical walls grip, so the
  total sticking force is p * mu * wall area = 0.5 * 0.5 * 1600 = 400 N,
  and the draft field reads ~0 deg on walls / ~90 deg on top and bottom
- strip 80 x 10 x 2 with rim sticking loads: a center pin beats an end
  pin, two pins beat one, and a third pin between two others reduces the
  max deflection; pin reactions always sum to the supported load
- hand-built 3-node chain: deflection matches the k = 3*E*(pi/4)*r^4/L^3
  spring analytically; a loaded pinless component reports as unsupported
- a synthetic mold_orientation result restricts gripping to B-reachable
  faces and supplies the pull axis

Run from the repo root: python test_ejector.py
"""
import os
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm

import ejection
import pipeline
from analysis import get_mesh_data, subdivide_mesh
from api.ejector import simulate
from processes.base import (apply_defaults, params_hash, store_result)
from processes.injection_molding import (EJECTION_SCHEMA, PROCESS,
                                         skeleton_cache_params)


def make_box():
    box = mm.makeCube(mm.Vector3f(40, 40, 10), mm.Vector3f(-20, -20, 0))
    return subdivide_mesh(box, 0.8)


def make_strip():
    strip = mm.makeCube(mm.Vector3f(80, 10, 2), mm.Vector3f(-40, -5, 0))
    return subdivide_mesh(strip, 0.8)


def make_rounded_plate():
    """Plate with rounded rims: a positive offset rounds every convex edge,
    creating the curvature-artifact nodes the absorption pass must absorb."""
    from analysis import offset_mesh
    plate = mm.makeCube(mm.Vector3f(30, 30, 2), mm.Vector3f(-15, -15, 0))
    return subdivide_mesh(offset_mesh(plate, 0.5, tollerance=0.1), 0.8)


def make_web_bridge():
    """Two thick plates bridged by a thin web — the web must SURVIVE
    absorption (only its junction nodes overlap the plates' spheres)."""
    a = mm.makeCube(mm.Vector3f(20, 20, 4), mm.Vector3f(-25, -10, 0))
    b = mm.makeCube(mm.Vector3f(20, 20, 4), mm.Vector3f(5, -10, 0))
    web = mm.makeCube(mm.Vector3f(12, 8, 0.6), mm.Vector3f(-6, -4, 0))
    part = mm.boolean(a, b, mm.BooleanOperation.Union).mesh
    part = mm.boolean(part, web, mm.BooleanOperation.Union).mesh
    return subdivide_mesh(part, 0.4)


def make_plate_at(subdivide):
    plate = mm.makeCube(mm.Vector3f(40, 40, 2), mm.Vector3f(-20, -20, 0))
    return subdivide_mesh(plate, subdivide)


def prepare_workdir(workdir, mesh):
    verts, faces = get_mesh_data(mesh)
    np.save(os.path.join(workdir, pipeline.FINE_VERTS_FILE), verts)
    np.save(os.path.join(workdir, pipeline.FINE_FACES_FILE), faces)
    return verts, faces


def run_analysis(workdir, **overrides):
    analysis = PROCESS.analysis("ejection_sticking")
    params = apply_defaults(analysis, overrides)
    result = analysis.run(workdir, params, None)
    return result, params, params_hash(
        {**params, "schema": EJECTION_SCHEMA,
         "mesh": pipeline.mesh_fingerprint(workdir)})


def pin(x, y, z, diameter=4.0):
    return {"point": [x, y, z], "diameter": diameter}


def synthetic_mold_result(workdir, verts, faces, *, a_only_nx=False):
    """Schema-2 mold_orientation result with membership from face normals
    (A reaches +Z, B reaches -Z, walls both) and pull arrows. With
    a_only_nx, the +X wall becomes A-only so the B-side scope excludes it."""
    tri = verts[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True),
                          1e-30)
    membership = np.zeros(len(faces), dtype=np.uint32)
    membership[normals[:, 2] > 0.5] |= 1
    membership[normals[:, 2] < -0.5] |= 2
    membership[np.abs(normals[:, 2]) <= 0.5] |= 3
    if a_only_nx:
        membership[normals[:, 0] > 0.5] = 1
    stats = {"schema": pipeline.MOLD_STATS_SCHEMA, "brep": False, "options": [{
        "pair": [0, 1], "slides": [], "feasible": True, "coverage": 1.0,
        "arrows": [{"kind": "main_a", "direction": [0.0, 0.0, 1.0]},
                   {"kind": "main_b", "direction": [0.0, 0.0, -1.0]}],
    }]}
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

    # --- kernel analytics (no meshlib) ----------------------------------
    print("=== stiffness kernel on a 3-node chain ===")
    nodes = np.array([[0, 0, 0], [10, 0, 0], [20, 0, 0]], dtype=float)
    radii = np.array([1.0, 1.0, 1.0])
    edges = np.array([[0, 1], [1, 2]])
    loads = np.array([0.0, 0.0, 5.0])
    stiffness = ejection.edge_stiffness(nodes, radii, edges, 2000.0)
    expected_k = 3 * 2000.0 * (np.pi / 4) / 1000.0
    check("edge spring constant", np.allclose(stiffness, expected_k),
          f"{stiffness[0]:.5f} vs {expected_k:.5f} N/mm")
    sim = ejection.simulate_ejection(nodes, radii, edges, loads, [0],
                                     E=2000.0)
    tip = 5.0 * (1 / stiffness[0] + 1 / stiffness[1])
    check("chain tip deflection analytic",
          np.allclose(sim["deflection"][2], tip),
          f"{sim['deflection'][2]:.5f} vs {tip:.5f} mm")
    check("single pin carries the whole load",
          np.allclose(sim["node_reaction"][0], loads.sum()),
          f"{sim['node_reaction'][0]:.5f} N")

    # symmetric 5-node chain, symmetric pins -> mirror-symmetric deflection
    nodes5 = np.array([[x, 0, 0] for x in (-20, -10, 0, 10, 20)], float)
    radii5 = np.ones(5)
    edges5 = np.array([[0, 1], [1, 2], [2, 3], [3, 4]])
    loads5 = np.array([1.0, 0.0, 2.0, 0.0, 1.0])
    sim5 = ejection.simulate_ejection(nodes5, radii5, edges5, loads5,
                                      [1, 3], E=2000.0)
    check("symmetric layout -> symmetric deflection",
          np.allclose(sim5["deflection"][0], sim5["deflection"][4])
          and np.allclose(sim5["node_reaction"][1],
                          sim5["node_reaction"][3]),
          f"w {sim5['deflection'][0]:.5f}/{sim5['deflection'][4]:.5f}")

    # loaded component without a pin: NaN + unsupported, rest unaffected
    nodes7 = np.vstack([nodes5, [[100, 0, 0], [110, 0, 0]]])
    radii7 = np.ones(7)
    edges7 = np.vstack([edges5, [[5, 6]]])
    loads7 = np.append(loads5, [3.0, 0.0])
    sim7 = ejection.simulate_ejection(nodes7, radii7, edges7, loads7,
                                      [1, 3], E=2000.0)
    check("pinless component excluded",
          np.isnan(sim7["deflection"][5])
          and sim7["unsupported"] == [{"nodes": 2, "load": 3.0}]
          and np.allclose(sim7["supported_load"], loads5.sum()),
          str(sim7["unsupported"]))

    # --- box: analytic sticking total, degraded path --------------------
    print("=== box 40 x 40 x 10, pull +Z, no orientation ===")
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir, make_box())
        result, params, result_hash = run_analysis(workdir)
        stats = result.stats

        expected = 0.5 * 0.5 * (2 * (40 + 40) * 10)
        total = stats["totals"]["sticking_force_n"]
        check("total sticking = p*mu*wall area",
              abs(total - expected) < 0.05 * expected,
              f"{total:.1f} N vs {expected:.1f} N")
        check("degraded confidence without orientation",
              stats["confidence"] == "no_orientation"
              and stats["pull"] == [0.0, 0.0, 1.0],
              stats["confidence"])

        from processes.base import load_result_arrays
        arrays = load_result_arrays(workdir, "injection_molding",
                                    "ejection_sticking",
                                    {**params, "schema": EJECTION_SCHEMA,
                                     "mesh": pipeline.mesh_fingerprint(workdir)})
        draft = arrays["draft_deg"]
        # facet normals: the fixture is a synthetic BREP-less box, so the
        # facets are the ground truth (same rule as load_face_normals)
        tri = verts[faces.astype(np.int64)].astype(np.float64)
        normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        normals /= np.maximum(
            np.linalg.norm(normals, axis=1, keepdims=True), 1e-30)
        walls = np.abs(normals[:, 2]) < 0.01
        tops = np.abs(normals[:, 2]) > 0.99
        check("draft ~0 on walls, ~90 on top/bottom",
              draft[walls].max() < 1.0 and draft[tops].min() > 89.0,
              f"walls max {draft[walls].max():.2f}, "
              f"tops min {draft[tops].min():.2f}")
        check("only walls grip",
              arrays["grip_faces"][walls].all()
              and not arrays["grip_faces"][tops].any(),
              f"{int(arrays['grip_faces'].sum())} gripping faces")

        # pressure identity + over-limit flip on a tiny pin
        response = simulate(workdir, result_hash, [pin(0, 0, 10, 4.0)])
        force = response["pins"][0]["force_n"]
        pressure = response["pins"][0]["pressure_mpa"]
        check("pin pressure = F / (pi d^2 / 4)",
              abs(pressure - force / (np.pi * 16 / 4)) < 1e-9
              and not response["pins"][0]["over_limit"],
              f"{pressure:.2f} MPa from {force:.1f} N")
        tiny = simulate(workdir, result_hash, [pin(0, 0, 10, 0.5)])
        check("tiny pin flips over_limit",
              tiny["pins"][0]["over_limit"],
              f"{tiny['pins'][0]['pressure_mpa']:.0f} MPa > 80 MPa")

    # --- strip: pin layout comparisons ----------------------------------
    print("=== strip 80 x 10 x 2: pin layouts ===")
    with tempfile.TemporaryDirectory() as workdir:
        prepare_workdir(workdir, make_strip())
        result, params, result_hash = run_analysis(workdir)

        center = simulate(workdir, result_hash, [pin(0, 0, 2)])
        end = simulate(workdir, result_hash, [pin(38, 0, 2)])
        check("center pin beats end pin",
              center["stats"]["max_deflection_mm"]
              < end["stats"]["max_deflection_mm"],
              f"center {center['stats']['max_deflection_mm']:.4f} vs "
              f"end {end['stats']['max_deflection_mm']:.4f} mm")

        two = simulate(workdir, result_hash, [pin(-20, 0, 2), pin(20, 0, 2)])
        check("two pins beat one center pin",
              two["stats"]["max_deflection_mm"]
              < center["stats"]["max_deflection_mm"],
              f"two {two['stats']['max_deflection_mm']:.4f} vs "
              f"one {center['stats']['max_deflection_mm']:.4f} mm")

        wide = simulate(workdir, result_hash, [pin(-30, 0, 2), pin(30, 0, 2)])
        three = simulate(workdir, result_hash,
                         [pin(-30, 0, 2), pin(0, 0, 2), pin(30, 0, 2)])
        check("third pin between two reduces max deflection",
              three["stats"]["max_deflection_mm"]
              < wide["stats"]["max_deflection_mm"],
              f"three {three['stats']['max_deflection_mm']:.4f} vs "
              f"two {wide['stats']['max_deflection_mm']:.4f} mm")

        for name, response in (("one", center), ("two", two),
                               ("three", three)):
            total_pins = sum(p["force_n"] for p in response["pins"])
            supported = response["stats"]["supported_load_n"]
            check(f"equilibrium ({name} pin layout)",
                  abs(total_pins - supported) < 1e-6 * max(supported, 1),
                  f"pins {total_pins:.6f} N vs supported {supported:.6f} N")

    # --- rounded rims: absorption kills curvature slivers ----------------
    print("=== rounded-rim plate: absorption ===")
    with tempfile.TemporaryDirectory() as workdir:
        prepare_workdir(workdir, make_rounded_plate())
        result, params, result_hash = run_analysis(workdir)
        from processes.base import load_result_arrays
        skeleton = load_result_arrays(workdir, "injection_molding",
                                      "wall_skeleton",
                                      skeleton_cache_params(workdir, params))
        radii = skeleton["cluster_radii"]
        median_r = float(np.median(radii))
        sliver_fraction = float(np.mean(radii < 0.3 * median_r))
        check("rim slivers absorbed into the walls",
              sliver_fraction < 0.05,
              f"{100 * sliver_fraction:.1f}% of nodes below 0.3x median "
              f"radius ({median_r:.2f} mm)")

        response = simulate(workdir, result_hash, [pin(0, 0, 3)])
        max_w = response["stats"]["max_deflection_mm"]
        p95_w = response["stats"]["p95_deflection_mm"]
        check("no sliver deflection blow-up",
              max_w <= 5 * max(p95_w, 1e-12),
              f"max {max_w:.4f} mm vs p95 {p95_w:.4f} mm")

    # --- thin web between thick plates survives absorption ---------------
    print("=== thin web bridge: flexibility preserved ===")
    with tempfile.TemporaryDirectory() as workdir:
        prepare_workdir(workdir, make_web_bridge())
        result, params, result_hash = run_analysis(workdir)
        from processes.base import load_result_arrays
        skeleton = load_result_arrays(workdir, "injection_molding",
                                      "wall_skeleton",
                                      skeleton_cache_params(workdir, params))
        nodes = skeleton["cluster_nodes"]
        radii = skeleton["cluster_radii"]
        web = (np.abs(nodes[:, 0]) < 3) & (radii < 0.5)
        check("web interior nodes survive absorption", web.sum() > 0,
              f"{int(web.sum())} web nodes (r < 0.5) at |x| < 3")

        response = simulate(workdir, result_hash, [pin(-15, 0, 4)])
        w = np.array([x if x is not None else np.nan
                      for x in response["deflection"]])
        near = np.nanmean(w[nodes[:, 0] < -5])
        far = np.nanmean(w[nodes[:, 0] > 5])
        check("far plate hangs on the flexible web",
              far > 5 * max(near, 1e-12),
              f"far plate {far:.4f} mm vs pinned plate {near:.6f} mm")

    # --- mesh-resolution invariance + spec statuses ----------------------
    print("=== resolution invariance and mesh spec ===")
    medians, deflections, statuses = [], [], []
    for subdivide in (0.6, 1.2):
        with tempfile.TemporaryDirectory() as workdir:
            prepare_workdir(workdir, make_plate_at(subdivide))
            result, params, result_hash = run_analysis(workdir)
            from processes.base import load_result_arrays
            skeleton = load_result_arrays(workdir, "injection_molding",
                                          "wall_skeleton",
                                          skeleton_cache_params(workdir, params))
            medians.append(float(np.median(skeleton["cluster_radii"])))
            response = simulate(workdir, result_hash, [pin(0, 0, 2)])
            deflections.append(response["stats"]["max_deflection_mm"])
            statuses.append(result.stats["mesh"]["status"])
    check("median radius resolution-invariant",
          abs(medians[0] - medians[1]) < 0.1 * max(medians),
          f"{medians[0]:.3f} vs {medians[1]:.3f} mm")
    ratio = deflections[0] / max(deflections[1], 1e-12)
    check("deflection resolution-invariant (±35%)",
          1 / 1.35 < ratio < 1.35,
          f"{deflections[0]:.4f} vs {deflections[1]:.4f} mm (x{ratio:.2f})")
    check("fine meshes pass the spec", all(s == "ok" for s in statuses),
          str(statuses))

    # under-resolved meshes get flagged (subdivide is a max edge length, so
    # actual edges land well below it — pick values that cross the bands)
    for subdivide, expected in ((2.5, "marginal"), (6.0, "coarse")):
        with tempfile.TemporaryDirectory() as workdir:
            prepare_workdir(workdir, make_plate_at(subdivide))
            result, _, _ = run_analysis(workdir)
            spec = result.stats["mesh"]
            check(f"subdivide {subdivide} flagged {expected}",
                  spec["status"] == expected,
                  f"ratio {spec['edge_thickness_ratio']:.2f} -> "
                  f"{spec['status']} (in ejection stats)")

    # --- B-side scope over a synthetic orientation ----------------------
    print("=== B-side scope (synthetic mold_orientation) ===")
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir, make_box())
        synthetic_mold_result(workdir, verts, faces, a_only_nx=True)
        result, params, result_hash = run_analysis(workdir)
        stats = result.stats

        # the +X wall (40 x 10) is A-only and must not grip
        expected = 0.5 * 0.5 * ((2 * (40 + 40) - 40) * 10)
        total = stats["totals"]["sticking_force_n"]
        check("A-only wall excluded from gripping",
              abs(total - expected) < 0.05 * expected,
              f"{total:.1f} N vs {expected:.1f} N")
        check("orientation picked up (pull from arrows)",
              stats["confidence"] == "full"
              and stats["pull"] == [0.0, 0.0, -1.0],
              f"{stats['orientation']} pull {stats['pull']}")

    # --- round-trip + error paths ---------------------------------------
    print("=== result round-trip and endpoint errors ===")
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir, make_box())
        result, params, result_hash = run_analysis(workdir)
        again, _, _ = run_analysis(workdir)
        check("cached rerun identical",
              result.fields == again.fields and result.stats == again.stats,
              f"{len(result.fields)} fields")
        check("skeleton hash binds the sub-run",
              result.stats["skeleton_hash"]
              == params_hash(skeleton_cache_params(workdir, params)),
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
                 if ".ejection_sticking." in entry["id"]}
        check("manifest exposes all sticking fields",
              set(by_id) == set(result.fields), sorted(by_id))
        check("draft_deg is a face scalar",
              by_id["draft_deg"]["association"] == "face"
              and by_id["draft_deg"]["dtype"] == "f4"
              and by_id["draft_deg"]["length"] == len(faces),
              str({k: by_id["draft_deg"][k] for k in ("dtype", "length")}))
        check("node_load covers the clustered graph",
              by_id["node_load"]["length"] == result.stats["nodes"],
              f"{by_id['node_load']['length']} vs {result.stats['nodes']}")

        from api.fields import result_field_bytes
        for name in result.fields:
            entry = by_id[name]
            item = 1 if entry["dtype"] == "u1" else 4
            data, dtype = result_field_bytes(
                workdir, "injection_molding", "ejection_sticking",
                result_hash, name)
            check(f"served bytes for {name}",
                  dtype == "<" + entry["dtype"]
                  and len(data) == entry["length"] * item,
                  f"{dtype} {len(data)} bytes")

        # a JSON round-trip must survive unsupported-component NaNs
        import json
        response = simulate(workdir, result_hash, [pin(0, 0, 10)])
        check("response is JSON-serializable",
              isinstance(json.dumps(response), str),
              f"{len(response['deflection'])} deflection entries")

        try:
            simulate(workdir, "0" * 12, [pin(0, 0, 10)])
            check("bad hash raises", False, "no error")
        except FileNotFoundError as error:
            check("bad hash raises", True, str(error))
        try:
            simulate(workdir, result_hash, [])
            check("empty pins raise", False, "no error")
        except ValueError as error:
            check("empty pins raise", True, str(error))

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
