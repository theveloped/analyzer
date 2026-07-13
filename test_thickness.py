"""Analytic checks of the rolling-sphere thickness and gaps fields.

Probes:
1. A 20x20x2 plate reads inside thickness ~= 2.0 on its big faces, with the
   auto max_radius derived as 0.5 * min(bbox dims) = 1.0.
2. Two 20x20x5 blocks 3 mm apart (one mesh): the gaps (inverted-orientation)
   run reads ~= 3.0 on the facing walls while the thickness run still reads
   ~= 5.0 there — both maps are complete per vertex (a single signed run
   would lose whichever sphere is larger). Outward faces saturate at the cap.
3. Saturation: capping max_radius clamps the field at 2*max_radius and the
   stats report the saturated fraction.
4. Cache round-trip: a second run with the same params returns the stored
   result without recomputing.

Run from the repo root: python test_thickness.py
"""
import os
import sys
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm

import pipeline
from analysis import get_mesh_data, subdivide_mesh
from processes.base import load_result_arrays, apply_defaults
import processes


def save_workdir(workdir, part):
    part = subdivide_mesh(part, 1.0)
    verts, faces = get_mesh_data(part)
    np.save(os.path.join(workdir, "fine_verts.npy"), verts)
    np.save(os.path.join(workdir, "fine_faces.npy"), faces)
    return verts, faces


def main():
    failures = []

    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:36s} {detail}")
        if not condition:
            failures.append(name)

    # --- probe 1 + 3 + 4: plate ------------------------------------------
    with tempfile.TemporaryDirectory() as workdir:
        plate = mm.makeCube(mm.Vector3f(20, 20, 2), mm.Vector3f(-10, -10, -1))
        verts, _ = save_workdir(workdir, plate)

        values, max_radius = pipeline.compute_thickness(workdir)
        check("auto max_radius = 0.5*min(bbox)", abs(max_radius - 1.0) < 1e-6,
              f"max_radius {max_radius:.3f}")

        big_faces = (np.abs(np.abs(verts[:, 2]) - 1.0) < 0.01) \
            & (np.abs(verts[:, 0]) < 8) & (np.abs(verts[:, 1]) < 8)
        frac = np.mean(np.abs(values[big_faces] - 2.0) < 0.05)
        check("plate thickness ~= 2.0", frac > 0.99,
              f"within 0.05 mm on {frac * 100:.1f}% of {int(big_faces.sum())} verts")

        # saturation at a small cap
        capped, _ = pipeline.compute_thickness(workdir, max_radius=0.5)
        check("field capped at 2*max_radius", capped.max() <= 1.0 * (1 + 1e-3),
              f"max {capped.max():.3f}")

        # registry run + cache round-trip
        analysis = processes.get_analysis("injection_molding", "thickness")
        merged = apply_defaults(analysis, {})
        first = analysis.run(workdir, merged, None)
        calls = []
        second = analysis.run(workdir, merged,
                              lambda fraction, message: calls.append(message))
        check("cache round-trip (no recompute)",
              second.stats == first.stats and not calls,
              f"progress calls on 2nd run: {len(calls)}")
        stored = load_result_arrays(
            workdir, "injection_molding", "thickness",
            {**merged, "mesh": pipeline.mesh_fingerprint(workdir)})
        check("stored f4 field of length verts",
              stored is not None and stored["thickness"].dtype == np.float32
              and stored["thickness"].shape == (len(verts),), "")

    # --- probe 2: two blocks, 3 mm gap -----------------------------------
    with tempfile.TemporaryDirectory() as workdir:
        lower = mm.makeCube(mm.Vector3f(20, 20, 5), mm.Vector3f(-10, -10, -8))
        upper = mm.makeCube(mm.Vector3f(20, 20, 5), mm.Vector3f(-10, -10, 0))
        part = mm.boolean(lower, upper, mm.BooleanOperation.Union).mesh
        verts, _ = save_workdir(workdir, part)

        thickness, _ = pipeline.compute_thickness(workdir, max_radius=8.0)
        gap, _ = pipeline.compute_thickness(workdir, max_radius=8.0, inverted=True)

        facing = ((np.abs(verts[:, 2] + 3.0) < 0.01) | (np.abs(verts[:, 2]) < 0.01)) \
            & (np.abs(verts[:, 0]) < 7) & (np.abs(verts[:, 1]) < 7)
        outer = (np.abs(verts[:, 2] - 5.0) < 0.01) \
            & (np.abs(verts[:, 0]) < 7) & (np.abs(verts[:, 1]) < 7)

        frac = np.mean(np.abs(gap[facing] - 3.0) < 0.1)
        check("facing walls gap ~= 3.0", frac > 0.99,
              f"within 0.1 mm on {frac * 100:.1f}% of {int(facing.sum())} verts")
        frac = np.mean(np.abs(thickness[facing] - 5.0) < 0.1)
        check("facing walls thickness ~= 5.0", frac > 0.99,
              f"within 0.1 mm on {frac * 100:.1f}% (both maps complete per vertex)")
        check("open side saturates at cap", np.all(gap[outer] >= 16.0 * (1 - 1e-3)),
              f"outer gap min {gap[outer].min():.2f} (cap 16.0)")

    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
