"""Analytic checks of the ray-cast thickness and gap fields.

pipeline.compute_ray_thickness casts meshlib's per-vertex ray along -normal
(inward wall thickness) and, on the orientation-flipped mesh, along +normal
(outward gap). It is a single-sided distance (not a sphere diameter), reading
exactly the along-normal distance, so it needs none of the rolling sphere's
edge-band/suspect masks. Misses (FLT_MAX, no opposing wall) and readings
beyond max_distance saturate to max_distance.

Probes (values pre-verified against the installed meshlib):
1. A 20x20x2 plate: interior top verts read ~= 2.0; auto max_distance is the
   bbox diagonal sqrt(20^2+20^2+2^2) ~= 28.35.
2. Two 20x20x5 blocks 3 mm apart (one mesh): facing walls read thickness
   ~= 5.0 and gap ~= 3.0; the open outer face has no opposing wall so its
   gap saturates at the cap.
3. Saturation: a small max_distance clamps the field.
4. Registry + cache round-trip for ray_thickness and ray_gap: a second run
   returns the stored result, and each stored field is f4 of length verts.

Run from the repo root: python test_ray_thickness.py
"""
import os
import sys
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm

import pipeline
import processes
from processes.base import load_result_arrays, apply_defaults
from analysis import get_mesh_data, subdivide_mesh


def save_workdir(workdir, part, subdivide=1.0):
    part = subdivide_mesh(part, subdivide)
    verts, faces = get_mesh_data(part)
    np.save(os.path.join(workdir, "fine_verts.npy"), verts)
    np.save(os.path.join(workdir, "fine_faces.npy"), faces)
    return verts, faces


def main():
    failures = []

    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:40s} {detail}")
        if not condition:
            failures.append(name)

    # --- probe 1 + 3 + 4: plate ------------------------------------------
    with tempfile.TemporaryDirectory() as workdir:
        plate = mm.makeCube(mm.Vector3f(20, 20, 2), mm.Vector3f(-10, -10, -1))
        verts, _ = save_workdir(workdir, plate)

        vals, maxd = pipeline.compute_ray_thickness(workdir)
        diag = float(np.sqrt(20 ** 2 + 20 ** 2 + 2 ** 2))
        check("auto max_distance = bbox diagonal", abs(maxd - diag) < 0.1,
              f"max_distance {maxd:.3f} (diag {diag:.3f})")

        on_top = np.abs(np.abs(verts[:, 2]) - 1.0) < 0.01
        outer = np.maximum(np.abs(verts[:, 0]), np.abs(verts[:, 1])) < 8
        interior = on_top & outer
        frac = np.mean(np.abs(vals[interior] - 2.0) < 0.05)
        check("plate ray thickness ~= 2.0", frac > 0.99,
              f"within 0.05 mm on {frac * 100:.1f}% of {int(interior.sum())} verts")

        # saturation at a small cap
        capped, _ = pipeline.compute_ray_thickness(workdir, max_distance=0.5)
        check("field capped at max_distance", capped.max() <= 0.5 * (1 + 1e-3),
              f"max {capped.max():.3f}")

        # registry run + cache round-trip (ray_thickness)
        analysis = processes.get_analysis("injection_molding", "ray_thickness")
        merged = apply_defaults(analysis, {})
        first = analysis.run(workdir, merged, None)
        calls = []
        second = analysis.run(workdir, merged,
                              lambda fraction, message: calls.append(message))
        check("ray_thickness cache round-trip",
              second.stats == first.stats and not calls,
              f"progress calls on 2nd run: {len(calls)}")
        stored = load_result_arrays(
            workdir, "injection_molding", "ray_thickness",
            {**merged, "schema": 1, "mesh": pipeline.mesh_fingerprint(workdir)})
        check("ray_thickness stored f4 length verts",
              stored is not None and stored["ray_thickness"].dtype == np.float32
              and stored["ray_thickness"].shape == (len(verts),), "")

        # registry run + stored field (ray_gap)
        analysis_g = processes.get_analysis("injection_molding", "ray_gap")
        merged_g = apply_defaults(analysis_g, {})
        analysis_g.run(workdir, merged_g, None)
        stored_g = load_result_arrays(
            workdir, "injection_molding", "ray_gap",
            {**merged_g, "schema": 1, "mesh": pipeline.mesh_fingerprint(workdir)})
        check("ray_gap stored f4 length verts",
              stored_g is not None and stored_g["ray_gap"].dtype == np.float32
              and stored_g["ray_gap"].shape == (len(verts),), "")

    # --- probe 2: two blocks, 3 mm gap -----------------------------------
    with tempfile.TemporaryDirectory() as workdir:
        lower = mm.makeCube(mm.Vector3f(20, 20, 5), mm.Vector3f(-10, -10, -8))
        upper = mm.makeCube(mm.Vector3f(20, 20, 5), mm.Vector3f(-10, -10, 0))
        part = mm.boolean(lower, upper, mm.BooleanOperation.Union).mesh
        verts, _ = save_workdir(workdir, part)

        thick, _ = pipeline.compute_ray_thickness(workdir, max_distance=16.0)
        gap, _ = pipeline.compute_ray_thickness(
            workdir, inverted=True, max_distance=16.0)

        facing = ((np.abs(verts[:, 2] + 3.0) < 0.01) | (np.abs(verts[:, 2]) < 0.01)) \
            & (np.abs(verts[:, 0]) < 7) & (np.abs(verts[:, 1]) < 7)
        outer = (np.abs(verts[:, 2] - 5.0) < 0.01) \
            & (np.abs(verts[:, 0]) < 7) & (np.abs(verts[:, 1]) < 7)

        frac = np.mean(np.abs(gap[facing] - 3.0) < 0.1)
        check("facing walls gap ~= 3.0", frac > 0.99,
              f"within 0.1 mm on {frac * 100:.1f}% of {int(facing.sum())} verts")
        frac = np.mean(np.abs(thick[facing] - 5.0) < 0.1)
        check("facing walls thickness ~= 5.0", frac > 0.99,
              f"within 0.1 mm on {frac * 100:.1f}%")
        check("open side gap saturates at cap",
              np.all(gap[outer] >= 16.0 * (1 - 1e-3)),
              f"outer gap min {gap[outer].min():.2f} (cap 16.0)")

    print()
    if failures:
        print(f"{len(failures)} assertion(s) failed: {failures}")
    else:
        print("assertions passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
