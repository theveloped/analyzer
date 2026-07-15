"""Analytic checks of the rolling-sphere thickness and gaps fields.

The fields are the RAW tangent-at-vertex probe (a valid empty ball at every
vertex — a trustworthy lower bound, never modified). The false-LOW readings
near sharp edges/corners are captured by the exclusion masks instead
(pipeline.edge_excluded over the stored `limit` + `suspect` arrays), so the
probes here assert both the raw values and the flagging behavior.

Probes:
1. A 20x20x2 plate: interior ~= 2.0, auto max_radius = 1.0; the raw rim
   ramps toward 0 but every below-threshold reading is edge-explainable
   (limit ~= 2*d for the 90-degree rim) -> zero thin flags at 1.0 mm. A
   20x20x0.6 control plate DOES flag. sharp_deg=0 leaves the field
   byte-identical with empty masks.
2. Two 20x20x5 blocks 3 mm apart (one mesh): gaps ~= 3.0 on facing walls
   while thickness reads ~= 5.0 there; outward faces saturate at the cap.
3. Saturation: capping max_radius clamps the field at 2*max_radius.
4. Cache round-trip: a second registry run returns the stored result.
5. A 4 mm slot: walls read the raw ~= 4.0 (never more), the floor/wall
   corner ramp reads < 3.0 raw but is excluded by the concave-sign mask ->
   zero gap flags at 3.5 mm on walls+floor.
6. Crossing ribs: the thick junction ball (2*sqrt(2) ~= 2.83) is preserved
   verbatim and is NOT thickness-excluded (concave creases are gap-sign) —
   the thick-region regression guard.

Run from the repo root: python test_thickness.py
"""
import os
import sys
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm

import pipeline
from pipeline import edge_excluded
from analysis import get_mesh_data, subdivide_mesh
from processes.base import load_result_arrays, apply_defaults
import processes


def save_workdir(workdir, part, subdivide=1.0):
    part = subdivide_mesh(part, subdivide)
    verts, faces = get_mesh_data(part)
    np.save(os.path.join(workdir, "fine_verts.npy"), verts)
    np.save(os.path.join(workdir, "fine_faces.npy"), faces)
    return verts, faces


def excluded_of(values, masks):
    return edge_excluded(values, masks["band_lo"], masks["band_hi"],
                         masks["suspect"])


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

        values, max_radius, masks = pipeline.compute_thickness(workdir)
        check("auto max_radius = 0.5*min(bbox)", abs(max_radius - 1.0) < 1e-6,
              f"max_radius {max_radius:.3f}")

        on_top = np.abs(np.abs(verts[:, 2]) - 1.0) < 0.01
        outer = np.maximum(np.abs(verts[:, 0]), np.abs(verts[:, 1]))
        inner = np.minimum(np.abs(verts[:, 0]), np.abs(verts[:, 1]))
        big_faces = on_top & (outer < 8)
        frac = np.mean(np.abs(values[big_faces] - 2.0) < 0.05)
        check("plate thickness ~= 2.0", frac > 0.99,
              f"within 0.05 mm on {frac * 100:.1f}% of {int(big_faces.sum())} verts")

        # the raw tangent field genuinely ramps at the 90-degree rim...
        rim = on_top & (outer > 8)
        check("raw rim ramps (artifact present)", values[rim].min() < 1.0,
              f"rim min {values[rim].min():.2f}")

        # ...and the stored limit explains it: 2*d*tan(45deg) = 2*d
        sel = on_top & (outer > 8.2) & (outer < 9.8) & (inner < 7)
        expected = 2.0 * (10.0 - outer[sel])
        frac = np.mean(np.abs(masks["limit"][sel] - expected) < 0.5)
        check("rim limit ~= 2*d", frac > 0.9,
              f"within 0.5 mm on {frac * 100:.1f}% of {int(sel.sum())} verts")

        excluded = excluded_of(values, masks)
        thin = (values < 1.0) & ~excluded
        check("no thin flags on the 2mm plate", int(thin.sum()) == 0,
              f"{int(thin.sum())} flagged of {int((values < 1.0).sum())} below")
        check("interior not excluded", not excluded[big_faces].any(),
              f"{int(excluded[big_faces].sum())} excluded interior verts")

        # escape hatch: field is untouched by design, masks empty
        raw_vals, _, raw_masks = pipeline.compute_thickness(
            workdir, sharp_deg=0)
        check("sharp_deg=0 field byte-identical",
              np.array_equal(raw_vals, values), "")
        check("sharp_deg=0 masks empty",
              (raw_masks["limit"] < 0).all()
              and not excluded_of(raw_vals, raw_masks).any(), "")

        # contact angles: interior balls are doubly tangent (~180 deg),
        # the rim's bitangent band reads its ~90 deg corner. max_radius
        # gets headroom: readings AT the cap are saturated (NaN angle),
        # and the plate's walls sit exactly at the auto cap
        _, _, angle_masks = pipeline.compute_thickness(
            workdir, max_radius=2.0, contact_angles=True)
        angle = angle_masks["angle"]
        frac = np.mean(np.abs(angle[big_faces] - 180.0) < 15.0)
        check("interior contact angle ~= 180", frac > 0.95,
              f"within 15 deg on {frac * 100:.1f}%")
        # angles measure against contact normals, so the 90 deg rim corner
        # reads cleanly even at coarse mesh resolution
        band = on_top & (outer > 9.1) & (outer < 9.4) & (inner < 7)
        frac = np.mean(np.abs(angle[band] - 90.0) < 20.0)
        check("rim bitangent band ~= 90", frac > 0.9,
              f"within 20 deg on {frac * 100:.1f}% of {int(band.sum())} verts")

        # saturation at a small cap
        capped, _, _ = pipeline.compute_thickness(workdir, max_radius=0.5)
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
        check("stored band arrays f4 + suspect u1",
              all(stored[name].dtype == np.float32
                  and stored[name].shape == (len(verts),)
                  for name in ("limit", "band_lo", "band_hi"))
              and stored["suspect"].dtype == np.uint8
              and stored["suspect"].shape == (len(verts),), "")
        check("default result stores no angle",
              "contact_angle" not in stored, str(list(stored)))
        merged_a = apply_defaults(analysis, {"contact_angles": True})
        analysis.run(workdir, merged_a, None)
        stored_a = load_result_arrays(
            workdir, "injection_molding", "thickness",
            {**merged_a, "mesh": pipeline.mesh_fingerprint(workdir)})
        check("angle field stored on request",
              stored_a["contact_angle"].dtype == np.float32
              and stored_a["contact_angle"].shape == (len(verts),), "")

    # --- probe 1b: a genuinely thin plate must still flag -----------------
    with tempfile.TemporaryDirectory() as workdir:
        plate = mm.makeCube(mm.Vector3f(20, 20, 0.6),
                            mm.Vector3f(-10, -10, -0.3))
        verts, _ = save_workdir(workdir, plate, subdivide=0.5)

        values, _, masks = pipeline.compute_thickness(workdir)
        on_top = np.abs(np.abs(verts[:, 2]) - 0.3) < 0.01
        big_faces = on_top & (np.maximum(np.abs(verts[:, 0]),
                                         np.abs(verts[:, 1])) < 8)
        thin = (values < 1.0) & ~excluded_of(values, masks)
        frac = np.mean(thin[big_faces])
        check("0.6mm plate flags as thin", frac > 0.9,
              f"{frac * 100:.1f}% of {int(big_faces.sum())} interior verts")

    # --- probe 2: two blocks, 3 mm gap -----------------------------------
    with tempfile.TemporaryDirectory() as workdir:
        lower = mm.makeCube(mm.Vector3f(20, 20, 5), mm.Vector3f(-10, -10, -8))
        upper = mm.makeCube(mm.Vector3f(20, 20, 5), mm.Vector3f(-10, -10, 0))
        part = mm.boolean(lower, upper, mm.BooleanOperation.Union).mesh
        verts, _ = save_workdir(workdir, part)

        thickness, _, _ = pipeline.compute_thickness(workdir, max_radius=8.0)
        gap, _, gmasks = pipeline.compute_thickness(
            workdir, max_radius=8.0, inverted=True, contact_angles=True)

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
        gangle = gmasks["angle"]
        frac = np.mean(np.abs(gangle[facing] - 180.0) < 15.0)
        check("facing gap contact angle ~= 180", frac > 0.95,
              f"within 15 deg on {frac * 100:.1f}%")
        check("saturated balls have NaN angle",
              np.isnan(gangle[outer]).all(),
              f"{int(np.isnan(gangle[outer]).sum())}/{int(outer.sum())} NaN")

    # --- probe 5: internal corners of a slot (gaps-side artifact) ---------
    with tempfile.TemporaryDirectory() as workdir:
        block = mm.makeCube(mm.Vector3f(20, 20, 8), mm.Vector3f(-10, -10, -8))
        cutter = mm.makeCube(mm.Vector3f(22, 4, 5), mm.Vector3f(-11, -2, -4))
        part = mm.boolean(block, cutter,
                          mm.BooleanOperation.DifferenceAB).mesh
        verts, _ = save_workdir(workdir, part)

        gap, _, masks = pipeline.compute_thickness(
            workdir, max_radius=8.0, inverted=True, contact_angles=True)
        excluded = excluded_of(gap, masks)

        on_wall = (np.abs(np.abs(verts[:, 1]) - 2.0) < 0.01) \
            & (np.abs(verts[:, 0]) < 8) & (verts[:, 2] < -0.5)
        upper = on_wall & (verts[:, 2] > -1.3)
        frac = np.mean(np.abs(gap[upper] - 4.0) < 0.2)
        check("slot walls gap ~= 4.0", frac > 0.95,
              f"within 0.2 mm on {frac * 100:.1f}% of {int(upper.sum())} verts")
        check("raw gap never over-reads the width",
              gap[on_wall].max() <= 4.3,
              f"wall gap max {gap[on_wall].max():.2f}")

        corner = (np.abs(np.abs(verts[:, 1]) - 2.0) < 0.01) \
            & (np.abs(verts[:, 2] + 4.0) < 0.01) & (np.abs(verts[:, 0]) < 8)
        check("raw corner artifact present", gap[corner].min() < 3.0,
              f"corner gap min {gap[corner].min():.2f}")
        check("corner readings excluded", excluded[corner].all(),
              f"{int(excluded[corner].sum())}/{int(corner.sum())} excluded")

        floor_band = (np.abs(verts[:, 1]) < 1.9) \
            & (np.abs(verts[:, 2] + 4.0) < 0.01) & (np.abs(verts[:, 0]) < 8)
        thin = (gap < 3.5) & ~excluded
        scope = on_wall | corner | floor_band
        check("no gap flags on walls/floor at 3.5", int(thin[scope].sum()) == 0,
              f"{int(thin[scope].sum())} flagged of "
              f"{int((gap[scope] < 3.5).sum())} below")

        # contact angles: upper walls doubly tangent (~180), the floor
        # corner's ramp balls bitangent at the 90 deg internal corner
        gangle = masks["angle"]
        frac = np.mean(np.abs(gangle[upper] - 180.0) < 15.0)
        check("slot wall contact angle ~= 180", frac > 0.9,
              f"within 15 deg on {frac * 100:.1f}%")
        ramp = (np.abs(np.abs(verts[:, 1]) - 2.0) < 0.01) \
            & (verts[:, 2] > -3.5) & (verts[:, 2] < -2.6) \
            & (np.abs(verts[:, 0]) < 8)
        frac = np.mean(np.abs(gangle[ramp] - 90.0) < 20.0)
        check("corner ramp contact angle ~= 90", frac > 0.8,
              f"within 20 deg on {frac * 100:.1f}% of {int(ramp.sum())} verts")

    # --- probe 6: crossing ribs — thick junction must survive -------------
    with tempfile.TemporaryDirectory() as workdir:
        rib1 = mm.makeCube(mm.Vector3f(2, 20, 6), mm.Vector3f(-1, -10, 0))
        rib2 = mm.makeCube(mm.Vector3f(20, 2, 5), mm.Vector3f(-10, -1, 0.5))
        part = mm.boolean(rib1, rib2, mm.BooleanOperation.Union).mesh
        verts, _ = save_workdir(workdir, part, subdivide=0.5)

        values, max_radius, masks = pipeline.compute_thickness(
            workdir, contact_angles=True)
        check("auto max_radius = 3.0", abs(max_radius - 3.0) < 1e-6,
              f"max_radius {max_radius:.3f}")

        junction = (np.abs(np.abs(verts[:, 0]) - 1.0) < 0.02) \
            & (np.abs(np.abs(verts[:, 1]) - 1.0) < 0.02) \
            & (verts[:, 2] > 2.0) & (verts[:, 2] < 4.0)
        check("junction verts found", junction.sum() > 0,
              f"{int(junction.sum())} verts")
        check("junction ball preserved (2*sqrt(2))",
              values[junction].min() >= 2.6,
              f"junction min {values[junction].min():.2f}")
        # the junction ball touches opposite corner lines through its
        # center — wall-like 180, unlike a genuine 90 deg corner ball
        jangle = masks["angle"][junction]
        frac = np.mean(jangle >= 150.0)
        check("junction contact angle wall-like", frac > 0.85,
              f">= 150 deg on {frac * 100:.1f}%")

        # (junction verts may be penetration-suspect — boolean creases tilt
        # the reconstructed normals — but exclusion is only consulted for
        # readings BELOW the thin threshold, so the thick reading itself is
        # what matters and it is asserted above)
        excluded = excluded_of(values, masks)

        walls = (np.abs(np.abs(verts[:, 0]) - 1.0) < 0.01) \
            & (np.abs(verts[:, 1]) > 4) & (np.abs(verts[:, 1]) < 8) \
            & (verts[:, 2] > 1) & (verts[:, 2] < 5)
        frac = np.mean(np.abs(values[walls] - 2.0) < 0.2)
        check("rib walls ~= 2.0 (no smear)", frac > 0.95,
              f"within 0.2 mm on {frac * 100:.1f}% of {int(walls.sum())} verts")

        thin = (values < 1.0) & ~excluded
        check("no thin flags on the ribs", int(thin.sum()) == 0,
              f"{int(thin.sum())} flagged of {int((values < 1.0).sum())} below")

        raw_vals, _, _ = pipeline.compute_thickness(workdir, sharp_deg=0)
        check("field equals the raw probe", np.array_equal(raw_vals, values),
              "")

    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
