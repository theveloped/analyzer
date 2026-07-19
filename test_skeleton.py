"""Validation of the wall-thickness skeleton stage against known geometry.

Two synthetic parts with analytically known medial skeletons:

- flat plate 20 x 20 x 2: inscribed spheres of the top/bottom faces have
  radius 1 and centers on the midplane z = 1
- rib on plate (1 wide x 6 tall rib on a 20 x 20 x 4 plate): rib spheres
  have radius 0.5 centered on the rib midplane x = 0, plate spheres radius
  2 centered on z = 2, and the clustered graph connects rib to plate

Also round-trips the injection molding analysis result through the store /
manifest / binary field serving layers.

Run from the repo root: python test_skeleton.py
"""
import os
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn

import pipeline
from analysis import get_mesh_data, subdivide_mesh


def make_plate():
    plate = mm.makeCube(mm.Vector3f(20, 20, 2), mm.Vector3f(-10, -10, 0))
    return subdivide_mesh(plate, 0.8)


def make_ribbed_plate():
    plate = mm.makeCube(mm.Vector3f(20, 20, 4), mm.Vector3f(-10, -10, 0))
    rib = mm.makeCube(mm.Vector3f(1, 20, 10), mm.Vector3f(-0.5, -10, 0))
    part = mm.boolean(plate, rib, mm.BooleanOperation.Union).mesh
    return subdivide_mesh(part, 0.8)


def prepare_workdir(workdir, mesh):
    verts, faces = get_mesh_data(mesh)
    np.save(os.path.join(workdir, pipeline.FINE_VERTS_FILE), verts)
    np.save(os.path.join(workdir, pipeline.FINE_FACES_FILE), faces)
    return verts, faces


def connected_components(node_count, edges):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components as csgraph_components

    adjacency = coo_matrix(
        (np.ones(len(edges), dtype=np.int8), (edges[:, 0], edges[:, 1])),
        shape=(node_count, node_count))
    count, _ = csgraph_components(adjacency, directed=False)
    return count


def main():
    failures = []

    def check(name, ok, detail):
        status = "OK " if ok else "FAIL"
        print(f"  [{status}] {name}: {detail}")
        if not ok:
            failures.append(f"{name}: {detail}")

    # --- mesh stage: auto subdivide bounds edge lengths everywhere -------
    print("=== mesh_part auto subdivide ===")
    with tempfile.TemporaryDirectory() as tmp:
        cube = mm.makeCube(mm.Vector3f(20, 20, 20), mm.Vector3f(0, 0, 0))
        stl = os.path.join(tmp, "cube.stl")
        mm.saveMesh(cube, stl)

        target = pipeline.auto_subdivide(np.linalg.norm([20, 20, 20]))
        pipeline.mesh_part(stl, os.path.join(tmp, "auto"))
        verts, faces = pipeline.load_mesh_arrays(os.path.join(tmp, "auto"))
        tri = verts[faces.astype(np.int64)]
        edge_max = float(np.linalg.norm(
            tri - np.roll(tri, -1, axis=1), axis=2).max())
        check("auto subdivide bounds every edge (flats included)",
              edge_max <= target * 1.001,
              f"max edge {edge_max:.3f} mm vs auto target {target:.2f} mm")

        pipeline.mesh_part(stl, os.path.join(tmp, "off"), subdivide=0)
        _, faces_off = pipeline.load_mesh_arrays(os.path.join(tmp, "off"))
        check("subdivide 0 disables refinement",
              len(faces_off) == 12, f"{len(faces_off)} faces")

    # --- flat plate ------------------------------------------------------
    print("=== plate 20 x 20 x 2 ===")
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir, make_plate())
        stats, arrays, field_meta = pipeline.wall_skeleton(workdir)

        thickness = arrays["thickness"]
        vert_node = arrays["raw_vert_node"]
        nodes = arrays["raw_nodes"]

        check("thickness dtype/shape",
              thickness.dtype == np.float32 and thickness.shape == (len(verts),),
              f"{thickness.dtype} {thickness.shape}")
        check("edges dtype", arrays["raw_edges"].dtype == np.uint32,
              str(arrays["raw_edges"].dtype))

        interior = ((np.abs(verts[:, 0]) < 6) & (np.abs(verts[:, 1]) < 6)
                    & (vert_node != pipeline.NODE_SENTINEL))
        interior_thickness = thickness[interior]
        check("interior thickness ~ 2",
              np.allclose(interior_thickness, 2.0, atol=0.15),
              f"mean {interior_thickness.mean():.3f} "
              f"range [{interior_thickness.min():.3f}, {interior_thickness.max():.3f}]")

        interior_z = nodes[vert_node[interior].astype(np.int64), 2]
        check("interior centers on midplane z=1",
              np.allclose(interior_z, 1.0, atol=0.1),
              f"mean {interior_z.mean():.3f} max|err| {np.abs(interior_z - 1).max():.3f}")

        kept = vert_node != pipeline.NODE_SENTINEL
        check("vert->node mapping in range",
              vert_node[kept].max() < stats["raw_nodes"],
              f"max id {vert_node[kept].max()} of {stats['raw_nodes']}")

        components = connected_components(stats["cluster_nodes"],
                                          arrays["cluster_edges"].astype(np.int64))
        check("clustered graph connected", components == 1,
              f"{components} components, {stats['cluster_nodes']} nodes "
              f"(reduced from {stats['raw_nodes']})")
        check("clustering reduces graph",
              stats["cluster_nodes"] < 0.5 * stats["raw_nodes"],
              f"{stats['raw_nodes']} -> {stats['cluster_nodes']}")

    # --- rib on plate -----------------------------------------------------
    print("=== rib 1 x 6 on plate 20 x 20 x 4 ===")
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir, make_ribbed_plate())
        stats, arrays, field_meta = pipeline.wall_skeleton(workdir)

        thickness = arrays["thickness"]
        vert_node = arrays["raw_vert_node"]
        nodes = arrays["raw_nodes"]
        kept = vert_node != pipeline.NODE_SENTINEL

        rib = (kept & (np.abs(verts[:, 0]) < 0.51) & (np.abs(verts[:, 1]) < 6)
               & (verts[:, 2] > 6) & (verts[:, 2] < 9))
        check("rib thickness ~ 1", np.allclose(thickness[rib], 1.0, atol=0.15),
              f"mean {thickness[rib].mean():.3f}")
        rib_x = nodes[vert_node[rib].astype(np.int64), 0]
        check("rib centers on rib midplane x=0",
              np.allclose(rib_x, 0.0, atol=0.1),
              f"max|x| {np.abs(rib_x).max():.3f}")

        plate = (kept & (np.abs(verts[:, 0]) > 4) & (np.abs(verts[:, 0]) < 7)
                 & (np.abs(verts[:, 1]) < 6) & (verts[:, 2] > 3.9))
        check("plate thickness ~ 4", np.allclose(thickness[plate], 4.0, atol=0.2),
              f"mean {thickness[plate].mean():.3f}")
        plate_z = nodes[vert_node[plate].astype(np.int64), 2]
        check("plate centers on plate midplane z=2",
              np.allclose(plate_z, 2.0, atol=0.15),
              f"max|err| {np.abs(plate_z - 2).max():.3f}")

        components = connected_components(stats["cluster_nodes"],
                                          arrays["cluster_edges"].astype(np.int64))
        check("rib connects to plate in clustered graph", components == 1,
              f"{components} components")

        # --- result store / manifest / serving round-trip -----------------
        print("=== analysis result round-trip ===")
        from processes.base import apply_defaults
        from processes.injection_molding import PROCESS
        analysis = PROCESS.analysis("wall_skeleton")
        params = apply_defaults(analysis, {})

        first = analysis.run(workdir, params, None)
        again = analysis.run(workdir, params, None)  # cache hit
        check("cached rerun identical fields",
              first.fields == again.fields and first.stats == again.stats,
              f"{len(first.fields)} fields")

        from api.manifest import build_manifest
        part = {
            "id": os.path.basename(workdir),
            "status": "meshed",
            "counts": {"verts": int(len(verts)), "faces": int(len(faces))},
        }
        manifest = build_manifest(os.path.dirname(workdir), part)
        by_id = {entry["id"].rsplit(".", 1)[-1]: entry
                 for entry in manifest["fields"]
                 if ".wall_skeleton." in entry["id"]}
        check("manifest exposes all skeleton fields",
              set(by_id) == set(first.fields), sorted(by_id))
        check("thickness field is a vertex scalar",
              by_id["thickness"]["dtype"] == "f4"
              and by_id["thickness"]["length"] == len(verts)
              and by_id["thickness"]["association"] == "vertex",
              str({k: by_id['thickness'][k] for k in ('dtype', 'length')}))
        check("edges field is u4 with flat length",
              by_id["raw_edges"]["dtype"] == "u4"
              and by_id["raw_edges"]["length"] == first.stats["raw_edges"] * 2,
              str({k: by_id['raw_edges'][k] for k in ('dtype', 'length')}))
        check("nodes field is f4 with flat length",
              by_id["cluster_nodes"]["dtype"] == "f4"
              and by_id["cluster_nodes"]["length"] == first.stats["cluster_nodes"] * 3,
              str({k: by_id['cluster_nodes'][k] for k in ('dtype', 'length')}))

        from api.fields import result_field_bytes
        from processes import resolver
        from processes.base import params_hash
        cache_key = resolver.cache_key(
            workdir, "injection_molding/wall_skeleton", params)
        check("cache key carries schema 5",
              cache_key["schema"] == 5, f"schema {cache_key['schema']}")
        result_hash = params_hash(cache_key)
        for name, entry in by_id.items():
            data, dtype = result_field_bytes(
                workdir, "injection_molding", "wall_skeleton", result_hash, name)
            expected = entry["length"] * 4  # u4 and f4 are both 4 bytes
            check(f"served bytes for {name}",
                  dtype == "<" + entry["dtype"] and len(data) == expected,
                  f"{dtype} {len(data)} bytes")

    # --- STEP mesh: non-manifold vertex splitting must not break the ----
    # per-vertex contract or hang the inscribed-sphere correction; the
    # drafted variant also exercises the C1-crease support filtering
    candidates = [os.path.join(os.path.dirname(__file__), "tests", name)
                  for name in ("Aligator.STEP", "AligatorDrafted.stp")]
    step = next((path for path in candidates if os.path.exists(path)), None)
    if step:
        print("=== STEP part (welded, non-manifold) ===")
        from processes.base import apply_defaults
        from processes.prep import PROCESS as PREP
        from processes.injection_molding import PROCESS as INJ
        with tempfile.TemporaryDirectory() as tmp:
            wd = os.path.join(tmp, "Aligator")
            os.makedirs(wd)
            import shutil
            shutil.copy(step, wd)
            PREP.analysis("mesh").run(
                wd, apply_defaults(PREP.analysis("mesh"), {}), None)
            verts, faces = pipeline.load_mesh_arrays(wd)

            # meshlib splits non-manifold verts, so the rebuilt mesh has more
            # vertices than the on-disk array — the field must still align
            mesh = mn.meshFromFacesVerts(faces.astype(np.int64), verts)
            check("STEP mesh is non-manifold (splits verts)",
                  mesh.topology.vertSize() > len(verts),
                  f"vertSize {mesh.topology.vertSize()} vs disk {len(verts)}")

            result = INJ.analysis("wall_skeleton").run(
                wd, apply_defaults(INJ.analysis("wall_skeleton"), {}), None)
            from processes import resolver
            from processes.base import load_result_arrays
            skel = load_result_arrays(
                wd, "injection_molding", "wall_skeleton",
                resolver.cache_key(wd, "injection_molding/wall_skeleton",
                                   apply_defaults(
                                       INJ.analysis("wall_skeleton"), {})))
            check("thickness field aligns to disk vertices",
                  len(skel["thickness"]) == len(verts),
                  f"{len(skel['thickness'])} vs {len(verts)}")
            check("skeleton completes on STEP (no findInSphere hang)",
                  result.stats["cluster_nodes"] > 0,
                  f"{result.stats['cluster_nodes']} clusters, "
                  f"{result.stats['penetrating_dropped']} penetrating dropped")
            check("unbounded markers normalized (sane mean thickness)",
                  result.stats["mean_thickness"] < 1e3,
                  f"mean {result.stats['mean_thickness']:.2f}")

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
