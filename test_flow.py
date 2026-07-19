"""Validation of the voxel/SDF flow stage against known geometry.

Fixtures with analytically known answers:

- flat plate 20 x 20 x 2 at h = 0.4: every interior voxel carries a
  positive wall distance (pins the meshlib sign convention), the deepest
  voxels sit on the midplane (d = 1), surface vertices map to ridge
  voxels, and a center gate fills everything with arrival ratios matching
  path lengths — zero freeze-off on a healthy plate
- bridge fixture (two 2 mm plates joined by a 0.6 mm web): fills fine
  without skin, freezes the far plate off under aggressive skin growth
- h vs h/2 convergence: normalized arrivals sampled at fixed probes agree
  across grid resolutions — the trust criterion the skeleton graph's
  tessellation-dependent connectivity could never satisfy

Also round-trips both analyses through the store / manifest / binary
field serving layers.

Run from the repo root: python test_flow.py
"""
import os
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm

import pipeline
from analysis import get_mesh_data, subdivide_mesh


def make_plate():
    plate = mm.makeCube(mm.Vector3f(20, 20, 2), mm.Vector3f(-10, -10, 0))
    return subdivide_mesh(plate, 0.8)


def make_bridge():
    """Two 2 mm plates joined by a thin 0.6 mm web across the middle."""
    a = mm.makeCube(mm.Vector3f(10, 10, 2), mm.Vector3f(-15, -5, 0))
    b = mm.makeCube(mm.Vector3f(10, 10, 2), mm.Vector3f(5, -5, 0))
    web = mm.makeCube(mm.Vector3f(10.2, 10, 0.6), mm.Vector3f(-5.1, -5, 0))
    part = mm.boolean(a, b, mm.BooleanOperation.Union).mesh
    part = mm.boolean(part, web, mm.BooleanOperation.Union).mesh
    return subdivide_mesh(part, 0.6)


def prepare_workdir(workdir, mesh):
    verts, faces = get_mesh_data(mesh)
    np.save(os.path.join(workdir, pipeline.FINE_VERTS_FILE), verts)
    np.save(os.path.join(workdir, pipeline.FINE_FACES_FILE), faces)
    return verts, faces


def nearest_voxel_arrival(grid, voxel_index, arrival, points):
    """Arrival at the interior voxel nearest each probe point."""
    origin = np.asarray(grid["origin"])
    h = grid["voxel"]
    nx, ny, nz = grid["dims"]
    lin = voxel_index.astype(np.int64)
    centers = origin + (np.stack(
        [lin // (ny * nz), (lin // nz) % ny, lin % nz],
        axis=1).astype(np.float64) + 0.5) * h
    out = np.empty(len(points))
    for i, point in enumerate(points):
        out[i] = arrival[np.argmin(((centers - point) ** 2).sum(axis=1))]
    return out


def main():
    failures = []

    def check(name, ok, detail):
        status = "OK " if ok else "FAIL"
        print(f"  [{status}] {name}: {detail}")
        if not ok:
            failures.append(f"{name}: {detail}")

    # --- flat plate: sign convention, distances, vertex mapping ----------
    print("=== plate 20 x 20 x 2, h = 0.4 ===")
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir, make_plate())
        stats, arrays, field_meta = pipeline.flow_voxels(workdir, voxel=0.4)
        dist = arrays["voxel_dist"]
        half = arrays["vert_half_thickness"]

        check("interior voxels found", stats["interior_voxels"] > 5000,
              f"{stats['interior_voxels']} voxels")
        check("sign convention: every interior distance positive",
              len(dist) and bool((dist > 0).all()),
              f"min {dist.min():.3f}")
        check("deepest voxel on the midplane (d = 1)",
              abs(float(dist.max()) - 1.0) <= 0.4,
              f"max d {dist.max():.3f} vs 1.0 (± h)")
        check("interior volume matches the mesh volume",
              abs(stats["interior_volume_mm3"] / stats["mesh_volume_mm3"] - 1)
              < 0.1,
              f"{stats['interior_volume_mm3']:.0f} vs "
              f"{stats['mesh_volume_mm3']:.0f} mm3 "
              f"(sign check {stats['sign_check']})")

        interior = (np.abs(verts[:, 0]) < 6) & (np.abs(verts[:, 1]) < 6)
        interior_half = half[interior]
        interior_half = interior_half[np.isfinite(interior_half)]
        check("vertex half-thickness ~ 1 away from the rim",
              np.allclose(interior_half, 1.0, atol=0.25),
              f"mean {interior_half.mean():.3f} range "
              f"[{interior_half.min():.3f}, {interior_half.max():.3f}]")
        check("nearly all vertices map to a ridge voxel",
              stats["unmapped_vertex_fraction"] < 0.05,
              f"unmapped {100 * stats['unmapped_vertex_fraction']:.1f}%")
        check("resolution gate ok at 5 voxels through the wall",
              stats["resolution"]["status"] == "ok",
              str(stats["resolution"]))

        # --- center gate: full fill, no freeze-off, analytic ratios ------
        fstats, farrays, _ = pipeline.flow_fill(
            workdir, voxels=arrays, grid=stats["grid"],
            voxels_hash="0" * 12, gate=[0.0, 0.0, 1.0])
        arrival = farrays["vert_arrival"]
        resolvable = farrays["vert_frozen"] != 254
        check("healthy plate surface fills at default skin",
              bool(np.isfinite(arrival[resolvable]).all()),
              f"{100 * np.isfinite(arrival[resolvable]).mean():.1f}% of "
              f"judgeable vertices reached "
              f"({100 * fstats['reached_volume_fraction']:.1f}% of voxels — "
              "near-wall skin solidifying is expected)")
        check("no freeze-off on a healthy plate",
              fstats["freeze_off"]["surface_fraction"] == 0.0,
              str(fstats["freeze_off"]))
        check("gate snaps into the material",
              fstats["gate"]["snap_distance_mm"] < 2 * 0.4,
              f"snap {fstats['gate']['snap_distance_mm']:.2f} mm")

        # arrival ratio edge-mid vs corner ~ path ratio 10 : 14.14; the
        # frozen-skin default slows late (far) regions, so solve skinless
        sstats, sarrays, _ = pipeline.flow_fill(
            workdir, voxels=arrays, grid=stats["grid"],
            voxels_hash="0" * 12, gate=[0.0, 0.0, 1.0], skin_coef=0.0,
            iterations=1)
        svals = sarrays["vert_arrival"]
        edge = (np.abs(verts[:, 0]) > 9.5) & (np.abs(verts[:, 1]) < 0.5)
        corner = (np.abs(verts[:, 0]) > 9.5) & (np.abs(verts[:, 1]) > 9.5)
        ratio = (np.nanmean(svals[corner]) / np.nanmean(svals[edge]))
        check("arrival ratio corner/edge matches path lengths",
              abs(ratio - np.sqrt(2)) < 0.15 * np.sqrt(2),
              f"ratio {ratio:.3f} vs sqrt(2) = 1.414")

    # --- bridge fixture: freeze-off behavior -----------------------------
    print("=== bridge: 2 mm plates joined by a 0.6 mm web ===")
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = prepare_workdir(workdir, make_bridge())
        stats, arrays, field_meta = pipeline.flow_voxels(workdir, voxel=0.15)
        gate = [-10.0, 0.0, 1.0]
        far = verts[:, 0] > 6

        def fill(**kwargs):
            fstats, farrays, _ = pipeline.flow_fill(
                workdir, voxels=arrays, grid=stats["grid"],
                voxels_hash="0" * 12, gate=gate, **kwargs)
            return fstats, farrays

        no_skin, no_skin_arrays = fill(skin_coef=0.0)
        reached_far = np.isfinite(no_skin_arrays["vert_arrival"][far]).mean()
        check("web passes melt without skin",
              no_skin["reached_volume_fraction"] > 0.99 and reached_far > 0.9,
              f"{100 * no_skin['reached_volume_fraction']:.1f}% reached, "
              f"far plate {100 * reached_far:.0f}%")
        check("no freeze-off without skin",
              no_skin["freeze_off"]["surface_fraction"] == 0.0,
              str(no_skin["freeze_off"]))

        skin, skin_arrays = fill(skin_coef=0.35)
        reached_far = np.isfinite(skin_arrays["vert_arrival"][far]).mean()
        check("aggressive skin freezes the web shut",
              reached_far < 0.05,
              f"far plate {100 * reached_far:.1f}% reached")
        check("freeze-off reported on the judgeable surface",
              skin["freeze_off"]["surface_fraction"] > 0.2,
              str(skin["freeze_off"]))
        check("frozen field records the lost passes",
              len(skin["skin"]["lost_per_pass"]) > 0
              or (skin_arrays["frozen"] == 0).any(),
              f"lost per pass {skin['skin']['lost_per_pass']}")

        huge, huge_arrays = fill(delta0=10.0, skin_coef=0.0)
        check("skin thicker than every wall handled gracefully",
              huge["reached_volume_fraction"] == 0.0
              and not np.isfinite(huge_arrays["vert_arrival"]).any(),
              f"{100 * huge['reached_volume_fraction']:.1f}% reached")

        # --- h vs h/2 convergence (the trust criterion) ------------------
        print("=== h vs h/2 convergence ===")
        coarse_stats, coarse_arrays, _ = pipeline.flow_voxels(workdir,
                                                              voxel=0.3)
        cf_stats, cf_arrays, _ = pipeline.flow_fill(
            workdir, voxels=coarse_arrays, grid=coarse_stats["grid"],
            voxels_hash="0" * 12, gate=gate, skin_coef=0.0)

        check("median half-thickness agrees across resolutions",
              abs(coarse_stats["median_half_thickness"]
                  - stats["median_half_thickness"])
              <= 0.5 * coarse_stats["grid"]["voxel"],
              f"{coarse_stats['median_half_thickness']:.3f} (h=0.3) vs "
              f"{stats['median_half_thickness']:.3f} (h=0.15)")

        # fixed probes on the plate midplanes, away from rims and the web
        xs = np.concatenate([np.linspace(-14, -6.5, 8),
                             np.linspace(6.5, 14, 8)])
        probes = np.stack([xs, np.zeros_like(xs), np.ones_like(xs)], axis=1)
        fine = nearest_voxel_arrival(
            stats["grid"], arrays["voxel_index"],
            no_skin_arrays["arrival"].astype(np.float64), probes)
        coarse = nearest_voxel_arrival(
            coarse_stats["grid"], coarse_arrays["voxel_index"],
            cf_arrays["arrival"].astype(np.float64), probes)
        # both are scaled to the same fill_time axis; compare directly
        finite = np.isfinite(fine) & np.isfinite(coarse)
        error = np.abs(fine[finite] - coarse[finite]) / max(fine[finite].max(),
                                                            1e-9)
        check("every probe reached at both resolutions",
              bool(finite.all()), f"{finite.sum()}/{len(probes)} probes")
        check("normalized arrivals converge (median error < 10%)",
              float(np.median(error)) < 0.10,
              f"median {100 * np.median(error):.1f}%, "
              f"max {100 * error.max():.1f}%")

        # --- result store / manifest / serving round-trip ----------------
        print("=== analysis result round-trip ===")
        from processes import resolver
        from processes.base import apply_defaults, params_hash
        from processes.injection_molding import PROCESS

        def voxel_cache(wd, voxel_params):
            return resolver.cache_key(wd, "prep/voxels", voxel_params)

        voxel_analysis = PROCESS.analysis("flow_voxels")
        voxel_params = apply_defaults(voxel_analysis, {"voxel": 0.15})
        first = voxel_analysis.run(workdir, voxel_params, None)
        again = voxel_analysis.run(workdir, voxel_params, None)  # cache hit
        check("cached rerun identical fields",
              first.fields == again.fields and first.stats == again.stats,
              f"{len(first.fields)} fields")

        fill_analysis = PROCESS.analysis("flow_fill")
        try:
            fill_analysis.run(workdir, apply_defaults(fill_analysis,
                                                      {"voxel": 0.15}), None)
            check("missing gate rejected", False, "no error raised")
        except ValueError as err:
            check("missing gate rejected", "gate" in str(err),
                  str(err)[:60])
        fill_params = apply_defaults(fill_analysis,
                                     {"voxel": 0.15, "gate": gate})
        fill_result = fill_analysis.run(workdir, fill_params, None)
        voxel_hash = params_hash(voxel_cache(workdir, voxel_params))
        check("fill result binds the voxel grid by hash",
              fill_result.stats["voxels_hash"] == voxel_hash,
              fill_result.stats["voxels_hash"])

        from api.manifest import build_manifest
        part = {
            "id": os.path.basename(workdir),
            "status": "meshed",
            "counts": {"verts": int(len(verts)), "faces": int(len(faces))},
        }
        manifest = build_manifest(os.path.dirname(workdir), part)
        by_id = {}
        for entry in manifest["fields"]:
            # voxels live in the shared prep/voxels stage now; fill stays in
            # injection_molding/flow_fill
            if ".voxels." in entry["id"] or ".flow_fill." in entry["id"]:
                by_id[entry["id"].rsplit(".", 1)[-1]] = entry
        expected = set(first.fields) | set(fill_result.fields)
        check("manifest exposes all flow fields",
              expected <= set(by_id), sorted(by_id))
        interior = first.stats["interior_voxels"]
        check("voxel_index is a free u4 array with flat length",
              by_id["voxel_index"]["association"] == "none"
              and by_id["voxel_index"]["dtype"] == "u4"
              and by_id["voxel_index"]["length"] == interior,
              str({k: by_id["voxel_index"][k]
                   for k in ("association", "dtype", "length")}))
        check("grid meta rides in the field descriptor",
              by_id["voxel_index"]["params"].get("grid", {}).get("dims")
              == first.stats["grid"]["dims"],
              str(by_id["voxel_index"]["params"].get("grid")))
        check("vert_arrival is a vertex scalar",
              by_id["vert_arrival"]["association"] == "vertex"
              and by_id["vert_arrival"]["dtype"] == "f4"
              and by_id["vert_arrival"]["length"] == len(verts),
              str({k: by_id["vert_arrival"][k]
                   for k in ("association", "dtype", "length")}))
        check("vert_frozen is a vertex mask",
              by_id["vert_frozen"]["association"] == "vertex"
              and by_id["vert_frozen"]["dtype"] == "u1",
              str({k: by_id["vert_frozen"][k]
                   for k in ("association", "dtype")}))

        from api.fields import result_field_bytes
        sizes = {"u1": 1, "u4": 4, "f4": 4}
        for process_id, analysis_id, result, params in (
                ("prep", "voxels", first,
                 voxel_cache(workdir, voxel_params)),
                ("injection_molding", "flow_fill", fill_result,
                 {**fill_params, "schema": 1,
                  "mesh": pipeline.mesh_fingerprint(workdir)})):
            result_hash = params_hash(params)
            for name in result.fields:
                entry = by_id[name]
                data, dtype = result_field_bytes(
                    workdir, process_id, analysis_id, result_hash,
                    name)
                ok = (dtype == "<" + entry["dtype"]
                      and len(data) == entry["length"] * sizes[entry["dtype"]])
                check(f"served bytes for {name}", ok,
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
