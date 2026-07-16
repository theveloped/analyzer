"""Analytic checks of the CNC setup-combination search and assignment.

Fixtures:
A. Cube: no single 3-axis setup covers it; the top option is a 2-setup
   flip; a single 3+2 setup at tilt 90 is (optimistically) feasible and
   ranks after the 3-axis plans (machine-first ranking); tilt 0 cover
   equals raw accessibility.
B. Three pockets on mutually exclusive faces (+X, +Y, -Z): 3-axis needs
   3 setups, one 3+2 setup does it all; setups are presented biggest
   first; every retained setup has exclusive faces.
C. Internal notch (laterally occluded): unreachable by every direction,
   so every option stays infeasible and the faces form a numbered
   unmachinable region.
B2. Through-slot STEP (BREP end-to-end): whole-face validity across two
   flip setups, earliest-setup defaults, and the client setup-boundary
   filter replicated over brep_edge_pairs.
D. Unit checks of setup_membership / setup_defaults / parse_tools /
   face_areas on hand-built arrays.
E. Cache round-trip through the cnc/setups AnalysisDef, plus the
   directions-fingerprint guard (results and zcache invalidate when the
   direction set is regenerated).
F. Tool verdict on a step block: a flat endmill machines the step exactly
   (feasible, nothing lost), a too-short tool loses the wall-adjacent
   band to stickout.

Run from the repo root: python test_setups.py
"""
import os
import sys
import tempfile

import numpy as np
from meshlib import mrmeshpy as mm

import machining
import molding
import pipeline
import processes
from processes.base import apply_defaults
from processes.cnc import SETUPS_SCHEMA
from test_mold import check_factory, make_slotted_step, save_workdir


def load_matrix(workdir):
    directions = np.load(os.path.join(workdir, "directions.npy"))
    accessibility = np.load(os.path.join(workdir, "accessibility.npy"))
    return directions, accessibility


def best(options, machine):
    return next(o for o in options if o["machine"] == machine)


def fixture_cube(check):
    with tempfile.TemporaryDirectory() as workdir:
        block = mm.makeCube(mm.Vector3f(20, 20, 10), mm.Vector3f(-10, -10, -5))
        save_workdir(workdir, block)
        pipeline.compute_directions(workdir, count=4, axes=True)
        directions, accessibility = load_matrix(workdir)

        check("tilt 0 cover is the accessibility matrix",
              machining.machine_cover(directions, accessibility, 0.0)
              is accessibility, "")

        options = machining.setup_search(directions, accessibility)
        top = options[0]
        check("cube: no single 3-axis setup",
              not any(o["feasible"] and len(o["setups"]) == 1
                      for o in options if o["machine"] == "3-axis"), "")
        check("cube: top option is a feasible 3-axis flip",
              top["machine"] == "3-axis" and top["feasible"] and top["flip"]
              and len(top["setups"]) == 2
              and top["setups"][1]["direction"]
              == (top["setups"][0]["direction"] ^ 1),
              f"setups {[s['direction'] for s in top['setups']]}")
        check("cube: full coverage on the flip",
              top["coverage"] == 1.0 and top["counts"]["internal"] == 0, "")
        check("cube: side walls toggleable between the setups",
              top["counts"]["multi"] > 0,
              f"multi {top['counts']['multi']}")

        indexed = best(options, "3+2")
        check("cube: one 3+2 setup covers everything (pre-fixture optimism)",
              indexed["feasible"] and len(indexed["setups"]) == 1,
              f"{len(indexed['setups'])} setup(s)")
        check("cube: 3-axis plans rank before the 3+2 plan",
              options.index(indexed) > options.index(top), "")


def fixture_pockets(check):
    with tempfile.TemporaryDirectory() as workdir:
        block = mm.makeCube(mm.Vector3f(20, 20, 20), mm.Vector3f(-10, -10, -10))
        part = block
        for pocket in (mm.makeCube(mm.Vector3f(4, 6, 6), mm.Vector3f(7, -3, -3)),
                       mm.makeCube(mm.Vector3f(6, 4, 6), mm.Vector3f(-3, 7, -3)),
                       mm.makeCube(mm.Vector3f(6, 6, 4), mm.Vector3f(-3, -3, -11))):
            part = mm.boolean(part, pocket,
                              mm.BooleanOperation.DifferenceAB).mesh
        verts, faces = save_workdir(workdir, part)
        pipeline.compute_directions(workdir, count=4, axes=True)
        directions, accessibility = load_matrix(workdir)

        options = machining.setup_search(directions, accessibility)
        three_axis = best(options, "3-axis")
        indexed = best(options, "3+2")
        check("pockets: 3-axis needs 3 setups",
              three_axis["feasible"] and len(three_axis["setups"]) == 3
              and {s["direction"] for s in three_axis["setups"]} == {0, 2, 5},
              f"setups {[s['direction'] for s in three_axis['setups']]}")
        check("pockets: one 3+2 setup does it all",
              indexed["feasible"] and len(indexed["setups"]) == 1, "")
        check("pockets: 3-axis plans keep ranking first (machine-first)",
              options.index(three_axis) < options.index(indexed), "")
        reachable = [s["reachable"] for s in three_axis["setups"]]
        check("pockets: biggest setup presented first",
              reachable == sorted(reachable, reverse=True), f"{reachable}")
        check("pockets: every setup has exclusive faces",
              all(s["exclusive"] > 0 for s in three_axis["setups"]),
              f"{[s['exclusive'] for s in three_axis['setups']]}")

        # membership over the winning 3-axis option: each pocket floor is
        # owned by exactly its setup's bit
        cover = machining.machine_cover(directions, accessibility, 0.0)
        setup_dirs = [s["direction"] for s in three_axis["setups"]]
        membership = machining.setup_membership(setup_dirs, cover)
        centroids = verts[faces].mean(axis=1)
        floor_x = (np.abs(centroids[:, 0] - 7.0) < 0.05) \
            & (np.abs(centroids[:, 1]) < 2.9) & (np.abs(centroids[:, 2]) < 2.9)
        bit_x = 1 << setup_dirs.index(0)
        check("pockets: +X pocket floor owned by the +X setup alone",
              int(floor_x.sum()) > 0
              and np.all(membership[floor_x] == bit_x),
              f"{int(floor_x.sum())} faces")
        check("pockets: no face is unmachinable",
              np.all(membership > 0), "")


def fixture_internal_notch(check):
    with tempfile.TemporaryDirectory() as workdir:
        block = mm.makeCube(mm.Vector3f(20, 20, 10), mm.Vector3f(-10, -10, -10))
        pocket = mm.makeCube(mm.Vector3f(8, 8, 6), mm.Vector3f(-4, -4, -6))
        part = mm.boolean(block, pocket, mm.BooleanOperation.DifferenceAB).mesh
        notch = mm.makeCube(mm.Vector3f(3, 4, 2), mm.Vector3f(4, -2, -5))
        part = mm.boolean(part, notch, mm.BooleanOperation.DifferenceAB).mesh
        verts, faces = save_workdir(workdir, part)
        pipeline.compute_directions(workdir, count=4, axes=True)
        directions, accessibility = load_matrix(workdir)

        options = machining.setup_search(directions, accessibility)
        check("notch: every option infeasible on every machine",
              not any(o["feasible"] for o in options),
              f"{len(options)} options")

        top = options[0]
        cover = machining.machine_cover(directions, accessibility,
                                        top["tilt"])
        membership = machining.setup_membership(
            [s["direction"] for s in top["setups"]], cover)
        centroids = verts[faces].mean(axis=1)
        notch_back = (np.abs(centroids[:, 0] - 7.0) < 0.05) \
            & (np.abs(centroids[:, 1]) < 1.9) & (centroids[:, 2] > -4.9) \
            & (centroids[:, 2] < -3.1)
        check("notch back wall has empty membership",
              int(notch_back.sum()) > 0
              and np.all(membership[notch_back] == 0),
              f"{int(notch_back.sum())} faces")

        pairs, _ = molding.face_adjacency(faces)
        region, counts = molding.internal_regions(membership, pairs,
                                                  len(faces))
        check("unmachinable region numbered and complete",
              len(counts) >= 1
              and np.all(region[notch_back] == region[notch_back][0])
              and int(region[notch_back][0]) > 0
              and sum(counts) == int((membership == 0).sum()),
              f"{len(counts)} region(s), counts {counts[:4]}")


def fixture_slot_brep(check):
    with tempfile.TemporaryDirectory() as tmp:
        path, _ = make_slotted_step(tmp)
        workdir = os.path.join(tmp, "wd")
        os.makedirs(workdir)
        pipeline.mesh_part(path, workdir, subdivide=1.0)
        pipeline.compute_directions(workdir, count=4, axes=True)

        result = pipeline.cnc_setups(workdir, indexed=True, count=10,
                                     field_options=3)
        stats = result["stats"]
        check("stats carry the schema and brep",
              stats["schema"] == pipeline.SETUPS_STATS_SCHEMA
              and stats["brep"], "")
        check("stats carry the directions fingerprint",
              stats["directions_fingerprint"]
              == pipeline.directions_fingerprint(workdir), "")
        # 20x20x10 block, 4x4 through-slot along X: outer skin minus the
        # two slot apertures plus the four 4x20 tunnel walls
        expected_area = (2 * 400 + 2 * 200 + 2 * (200 - 16)) + 4 * 4 * 20
        check("counts are area-weighted (exact block area)",
              abs(stats["total_area"] - expected_area) < 2.0,
              f"total {stats['total_area']:.1f} / {expected_area} mm²")
        check("field_options index into the reported options",
              stats["field_options"]
              and all(0 <= i < len(stats["options"])
                      for i in stats["field_options"])
              and len({(stats["options"][i]["machine"],
                        len(stats["options"][i]["setups"]))
                       for i in stats["field_options"]})
              == len(stats["field_options"]), f"{stats['field_options']}")
        check("a 3+2 plan is among the field options",
              any(stats["options"][i]["machine"] == "3+2"
                  for i in stats["field_options"]),
              f"{[stats['options'][i]['machine'] for i in stats['field_options']]}")

        # the through-slot runs along X: the top plan is the ±X flip
        k, option = next(
            (k, stats["options"][index])
            for k, index in enumerate(stats["field_options"])
            if stats["options"][index]["machine"] == "3-axis")
        check("slot: 3-axis plan is a ±X flip",
              option["feasible"] and option["flip"]
              and {s["direction"] for s in option["setups"]} == {0, 1},
              f"setups {[s['direction'] for s in option['setups']]}")

        valid = result["arrays"][f"brep_valid_{k}"]
        default = result["arrays"][f"brep_default_{k}"]
        membership = result["arrays"][f"membership_{k}"]
        brep_ids = np.load(os.path.join(workdir, "brep_faces.npy"))
        verts = np.load(os.path.join(workdir, "fine_verts.npy"))
        faces = np.load(os.path.join(workdir, "fine_faces.npy"))
        centroids = verts[faces].mean(axis=1)

        def brep_of(mask):
            return np.unique(brep_ids[mask])

        first = next(i for i, s in enumerate(option["setups"])
                     if s["direction"] == 0)
        second = 1 - first
        plus_x = brep_of(np.abs(centroids[:, 0] - 10.0) < 0.01)
        minus_x = brep_of(np.abs(centroids[:, 0] + 10.0) < 0.01)
        slot = brep_of((np.abs(np.abs(centroids[:, 1]) - 2.0) < 0.05)
                       & (np.abs(centroids[:, 2]) < 1.9))
        top = brep_of(np.abs(centroids[:, 2] - 5.0) < 0.01)

        check("end faces valid for exactly their setup",
              all(int(valid[b]) == 1 << first for b in plus_x)
              and all(int(valid[b]) == 1 << second for b in minus_x), "")
        check("slot and top faces valid for both setups (toggleable)",
              all(int(valid[b]) == 3 for b in np.concatenate([slot, top])),
              f"{len(slot) + len(top)} faces")
        check("defaults pick the earliest setup",
              all(default[b] == 0 for b in np.concatenate([slot, top]))
              and all(default[b] == second for b in minus_x), "")
        check("no conflict or unmachinable faces",
              not np.any(default >= molding.DEFAULT_CONFLICT)
              and np.all(membership > 0), "")

        # replicate the client setup-boundary filter over the BREP edges
        id_pairs = np.load(os.path.join(workdir, "brep_edge_pairs.npy"))
        a, b = default[id_pairs[:, 0]], default[id_pairs[:, 1]]
        keep = (a != b) & (a < molding.DEFAULT_CONFLICT) \
            & (b < molding.DEFAULT_CONFLICT)
        segments = np.load(os.path.join(workdir, "brep_edges.npy"))[keep]
        check("setup boundary lands on BREP edges", len(segments) > 0,
              f"{len(segments)} segments")


def fixture_unit(check):
    # cover: 3 directions x 6 faces
    cover = np.array([
        [1, 1, 0, 0, 1, 0],
        [0, 0, 1, 1, 1, 0],
        [1, 0, 0, 1, 0, 0],
    ], dtype=bool)
    membership = machining.setup_membership([0, 1], cover)
    check("unit: membership ORs shifted cover rows",
          membership.tolist() == [1, 1, 2, 2, 3, 0], f"{membership.tolist()}")

    # 4 brep faces x 2 triangles; setups: bit0, bit1
    brep_ids = np.repeat(np.arange(4, dtype=np.int32), 2)
    membership = np.array([
        2, 2,    # face 0: fully setup 2      -> default setup 2
        3, 3,    # face 1: both setups        -> earliest (setup 1) wins
        1, 2,    # face 2: split covers       -> conflict
        0, 0,    # face 3: nothing            -> unmachinable
    ], dtype=np.uint32)
    valid = molding.brep_validity(membership, brep_ids, 2)
    default = machining.setup_defaults(membership, valid, brep_ids)
    check("unit: single-setup face", valid[0] == 2 and default[0] == 1, "")
    check("unit: earliest setup wins", valid[1] == 3 and default[1] == 0, "")
    check("unit: split face is conflict",
          valid[2] == 0 and default[2] == molding.DEFAULT_CONFLICT, "")
    check("unit: uncovered face is unmachinable",
          valid[3] == 0 and default[3] == molding.DEFAULT_INTERNAL, "")

    check("unit: axis labels",
          machining.setup_labels_colors([[0, 0, 1], [0.6, 0.6, 0.52]])[0]
          == ["setup 1 (+Z)", "setup 2"], "")

    verts = np.array([[0, 0, 0], [2, 0, 0], [0, 3, 0]], dtype=np.float32)
    areas = machining.face_areas(verts, np.array([[0, 1, 2]]))
    check("unit: face areas", abs(areas[0] - 3.0) < 1e-6, f"{areas[0]}")

    tools = pipeline.parse_tools([
        "6", "8:1", "4:2:20", "10:0:50:5",
        {"diameter": 3, "stickout": 12, "holder_radius": 1.5},
    ])
    check("unit: parse_tools",
          tools[0] == {"diameter": 6.0, "corner_radius": 0.0,
                       "stickout": None, "holder_radius": None}
          and tools[1]["corner_radius"] == 1.0
          and tools[2]["stickout"] == 20.0 and tools[2]["holder_radius"] is None
          and tools[3] == {"diameter": 10.0, "corner_radius": 0.0,
                           "stickout": 50.0, "holder_radius": 5.0}
          and tools[4] == {"diameter": 3.0, "corner_radius": 0.0,
                           "stickout": 12.0, "holder_radius": 1.5}, "")


def cache_round_trip(check):
    with tempfile.TemporaryDirectory() as workdir:
        block = mm.makeCube(mm.Vector3f(20, 20, 10), mm.Vector3f(-10, -10, -5))
        save_workdir(workdir, block, subdivide=2.0)
        pipeline.compute_directions(workdir, count=4, axes=True)

        analysis = processes.get_analysis("cnc", "setups")
        merged = apply_defaults(analysis, {})
        first = analysis.run(workdir, merged, None)
        calls = []
        second = analysis.run(workdir, merged,
                              lambda fraction, message: calls.append(message))
        check("cache round-trip (no recompute)",
              second.stats == first.stats and not calls,
              f"progress calls on 2nd run: {len(calls)}")
        check("schema and membership fields present",
              first.stats["schema"] == SETUPS_SCHEMA
              and "membership_0" in first.fields
              and "internal_region_0" in first.fields, "")
        check("top option is the 3-axis flip",
              first.stats["options"][0]["machine"] == "3-axis"
              and first.stats["options"][0]["flip"], "")


def fixture_fingerprint(check):
    with tempfile.TemporaryDirectory() as workdir:
        block = mm.makeCube(mm.Vector3f(20, 20, 10), mm.Vector3f(-10, -10, -5))
        verts, faces = save_workdir(workdir, block, subdivide=2.0)
        pipeline.compute_directions(workdir, count=4, axes=True)
        first_fp = pipeline.directions_fingerprint(workdir)

        from zmap import DirectionCache
        cache = DirectionCache(workdir, 4, verts=verts, faces=faces, pixel=0.2)
        cache.tip_gap(4.0, 0.0)

        analysis = processes.get_analysis("cnc", "setups")
        merged = apply_defaults(analysis, {})
        analysis.run(workdir, merged, None)

        pipeline.compute_directions(workdir, count=8, axes=True)
        second_fp = pipeline.directions_fingerprint(workdir)
        check("fingerprint changes with the direction set",
              first_fp and second_fp and first_fp != second_fp,
              f"{first_fp} -> {second_fp}")

        # the zcache from the old set must be discarded, not reused
        fresh = DirectionCache(workdir, 4, verts=verts, faces=faces, pixel=0.2)
        check("stale zcache discarded on direction change",
              "tip_4_0" not in fresh._fields, "")

        # the setups result must not be served from the stale cache
        calls = []
        analysis.run(workdir, merged,
                     lambda fraction, message: calls.append(message))
        check("stale setups result recomputed, not reused", len(calls) > 0,
              f"{len(calls)} progress calls")


def make_step_block(workdir):
    """20x20x10 block with a 10-wide, 5-deep full-width step: every concave
    feature is a single horizontal edge, so a flat endmill machines it
    exactly — the analytic 'feasible with tools' fixture."""
    block = mm.makeCube(mm.Vector3f(20, 20, 10), mm.Vector3f(-10, -10, -5))
    cut = mm.makeCube(mm.Vector3f(12, 24, 7), mm.Vector3f(0, -12, 0))
    part = mm.boolean(block, cut, mm.BooleanOperation.DifferenceAB).mesh
    return save_workdir(workdir, part)


def fixture_tool_verdict(check):
    with tempfile.TemporaryDirectory() as workdir:
        verts, faces = make_step_block(workdir)
        pipeline.compute_directions(workdir, count=4, axes=True)

        base = pipeline.cnc_setups(workdir, indexed=False, count=10)
        options = base["stats"]["options"]
        option = next(i for i, o in enumerate(options)
                      if {s["direction"] for s in o["setups"]} == {4, 5})
        check("step block: +-Z flip is feasible on visibility",
              options[option]["feasible"] and options[option]["flip"], "")

        # holder fatter than the tool: flank contact on a wall puts the
        # holder rim 2 mm inside the wall, so depth genuinely costs stickout
        good = pipeline.setup_verdict(
            workdir, option=option,
            tools=[{"diameter": 4, "stickout": 30, "holder_radius": 4}],
            pixel=0.2, indexed=False)
        opt = good["stats"]["options"][0]
        check("flat endmill machines the step exactly",
              opt["feasible"] and opt["verdict"]["lost"] < 1.0,
              f"lost {opt['verdict']['lost']} mm²")
        check("verdict stats mirror the setups schema",
              good["stats"]["schema"] == pipeline.SETUPS_STATS_SCHEMA
              and good["stats"]["verdict"]
              and good["stats"]["field_options"] == [0]
              and "membership_0" in good["arrays"], "")

        short = pipeline.setup_verdict(
            workdir, option=option,
            tools=[{"diameter": 4, "stickout": 1, "holder_radius": 4}],
            pixel=0.2, indexed=False)
        opt_short = short["stats"]["options"][0]
        membership = short["arrays"]["membership_0"]
        centroids = verts[faces].mean(axis=1)
        # deep on the step wall (x = 0 plane, z in [0, 5]): the holder needs
        # nearly the full step depth of stickout there
        wall_deep = (np.abs(centroids[:, 0]) < 0.05) \
            & (centroids[:, 2] > 0.5) & (centroids[:, 2] < 2.0) \
            & (np.abs(centroids[:, 1]) < 8.0)
        check("short tool loses the step to stickout",
              not opt_short["feasible"] and opt_short["verdict"]["lost"] > 10.0
              and int(wall_deep.sum()) > 0
              and np.all(membership[wall_deep] == 0),
              f"lost {opt_short['verdict']['lost']:.0f} mm², "
              f"{int(wall_deep.sum())} deep wall faces")


def main():
    failures = []
    check = check_factory(failures)
    print("=== fixture A: cube (flip vs 3+2) ===")
    fixture_cube(check)
    print("=== fixture B: three exclusive pockets ===")
    fixture_pockets(check)
    print("=== fixture C: internal notch ===")
    fixture_internal_notch(check)
    print("=== fixture B2: through-slot (BREP end-to-end) ===")
    fixture_slot_brep(check)
    print("=== fixture D: unit checks ===")
    fixture_unit(check)
    print("=== fixture E: cache round-trip ===")
    cache_round_trip(check)
    print("=== fixture E2: directions fingerprint guard ===")
    fixture_fingerprint(check)
    print("=== fixture F: tool verdict on the step block ===")
    fixture_tool_verdict(check)
    print("ALL CHECKS PASSED" if not failures else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
