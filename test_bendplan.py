"""Checks of the press-brake adapter + bend_plan analysis (OCP required).

Fixtures reuse test_sheet.py's STEP builders. The folded-vs-BREP fixture
is the empirical pin of adapter.ANGLE_SIGN and the z_offset sign: in a
valid fold the child panels land on the MATERIAL side of the base plane
(sign of folded z equals sign of z_offset), parallel wall mid-planes end
at the solid's true spacing, and the folded bounding box matches the
source part.

Run from the repo root: python test_bendplan.py
"""
import json
import math
import os
import sys
import tempfile

import numpy as np

import pipeline
from pressbrake import adapter
from test_sheet import make_l_bracket, make_u_channel, write_step


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:44s} {detail}")
        if not condition:
            failures.append(name)
    return check


def build_workdir(tmp, shape, name):
    path = write_step(tmp, shape, name)
    workdir = os.path.join(tmp, name.replace(".step", ""))
    os.makedirs(workdir)
    pipeline.mesh_part(path, workdir, resolution=2.0, subdivide=2.0)
    pipeline.compute_aag(workdir)
    return workdir


def folded_vertices(graph):
    theta = np.array([bend.angle_target for bend in graph.bends])
    return graph.panel_vertices(theta)


def fixture_l_bracket(check, tmp):
    workdir = build_workdir(tmp, make_l_bracket(), "bp_bracket.step")
    graph, info = adapter.build_kinematic_graph(workdir)

    check("bracket: 2 panels, 1 bend",
          graph.panel_count == 2 and graph.bend_count == 1,
          f"{graph.panel_count}/{graph.bend_count}")
    bend = graph.bends[0]
    check("bracket: bend r3, length 25, k 0.5",
          np.isclose(bend.inner_radius, 3.0, atol=1e-3)
          and np.isclose(bend.length, 25.0, atol=0.1)
          and bend.k_factor == 0.5, f"r{bend.inner_radius} L{bend.length}")
    zone = (math.pi / 2) * (3.0 + 0.5 * 2.0)
    check("bracket: zone width = bend allowance",
          np.isclose(bend.zone_width, zone, atol=1e-3),
          f"{bend.zone_width:.3f} vs {zone:.3f}")
    check("bracket: |z_offset| = t/2",
          np.isclose(abs(graph.z_offset), 1.0, atol=1e-6),
          f"{graph.z_offset}")

    # panels tile the pattern: total area equals volume / thickness
    from shapely.geometry import Polygon
    area = sum(
        Polygon(panel.outline, [h for h in panel.holes]).area
        for panel in graph.panels)
    expected = 56.2832 * 25.0   # analytic flat area of the fixture
    check("bracket: panels tile the flat pattern",
          np.isclose(area, expected, rtol=0.01),
          f"{area:.1f} vs {expected:.1f}")

    # the fold pin: the flange must land on the MATERIAL side of the base
    verts = folded_vertices(graph)
    child_z = verts[1][:, 2]
    check("bracket: fold lands on the material side (ANGLE_SIGN pin)",
          np.sign(np.mean(child_z)) == np.sign(graph.z_offset)
          and np.isclose(np.max(np.abs(child_z)), 20.0 - 1.0, atol=1.0),
          f"child z [{child_z.min():.1f}, {child_z.max():.1f}] "
          f"z_offset {graph.z_offset:+.1f}")

    # folded footprint matches the source legs (a=40 leg along the base)
    base_span = np.ptp(verts[0][:, :2], axis=0).max()
    check("bracket: folded base leg span ~ 40",
          np.isclose(base_span, 40.0, atol=1.5), f"{base_span:.1f}")


def fixture_u_channel(check, tmp):
    workdir = build_workdir(tmp, make_u_channel(), "bp_channel.step")
    graph, info = adapter.build_kinematic_graph(workdir)

    check("channel: 3 panels, 2 bends, 2 sister groups",
          graph.panel_count == 3 and graph.bend_count == 2
          and len(graph.sister_groups()) == 2, "")

    verts = folded_vertices(graph)
    wall_ids = [bend.child_panel for bend in graph.bends]
    centroids = [np.mean(verts[panel_id], axis=0) for panel_id in wall_ids]
    check("channel: both walls fold to the material side",
          np.sign(centroids[0][2]) == np.sign(graph.z_offset)
          and np.sign(centroids[1][2]) == np.sign(graph.z_offset), "")

    # wall mid-planes end at the solid's true spacing: outer width 40,
    # walls t=2 -> mid-plane spacing 38
    normals = []
    positions = []
    for panel_id in wall_ids:
        pts = verts[panel_id]
        centered = pts - pts.mean(axis=0)
        _, _, vh = np.linalg.svd(centered)
        normals.append(vh[2])
        positions.append(pts.mean(axis=0))
    spacing = abs(float(np.dot(positions[1] - positions[0], normals[0])))
    check("channel: folded wall mid-plane spacing = 38",
          np.isclose(spacing, 38.0, atol=0.5), f"{spacing:.2f}")
    check("channel: walls parallel when folded",
          abs(float(np.dot(normals[0], normals[1]))) > 0.9999, "")


def fixture_intervals(check, tmp):
    from pressbrake import envelope as envelope_mod
    from pressbrake import kinematics
    from pressbrake.machine import load_dies, load_machine, load_punches
    from pressbrake.model import BendAction

    machine = load_machine()
    punches = load_punches()
    dies = load_dies()

    workdir = os.path.join(tmp, "bp_bracket")
    graph, info = adapter.build_kinematic_graph(workdir)
    bend = graph.bends[0]
    action = BendAction(bend_ids=(0,), flip=bend.angle_target < 0,
                        rotation=0)
    result = envelope_mod.compute_envelope(
        graph, np.zeros(1), action, punches["P.88.R08"], dies["D.V16.88"],
        machine)
    check("bracket: required interval ~ bend length",
          np.isclose(result.required.measure(), 25.0, atol=1.0)
          and result.feasible,
          f"required {result.required.measure():.1f}")
    core = result.required_core.measure()
    check("bracket: required core shrunk by end relief",
          0 < core < result.required.measure(), f"core {core:.1f}")

    # a notch across the bend line splits the required intervals
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    notched_shape = BRepAlgoAPI_Cut(
        make_l_bracket(),
        BRepPrimAPI_MakeBox(gp_Pnt(-1, 10, -1), 8, 5, 8).Shape()).Shape()
    notched_dir = build_workdir(tmp, notched_shape, "bp_notched.step")
    notched, _ = adapter.build_kinematic_graph(notched_dir)
    # the notch splits the bend line into collinear sister segments that
    # form together in one stroke
    groups = notched.sister_groups()
    check("notched bracket: sisters form one group",
          len(groups) == 1 and notched.bend_count == 2,
          f"{notched.bend_count} bends in {len(groups)} groups")
    group = next(iter(groups.values()))
    bend = notched.bends[group[0]]
    action = BendAction(bend_ids=tuple(group),
                        flip=bend.angle_target < 0, rotation=0)
    result = envelope_mod.compute_envelope(
        notched, np.zeros(notched.bend_count), action, punches["P.88.R08"],
        dies["D.V16.88"], machine)
    check("notched bracket: required splits at the notch",
          len(result.required) == 2
          and np.isclose(result.required.measure(), 20.0, atol=1.5),
          f"{result.required.to_pairs()}")


def fixture_analysis(check, tmp):
    import processes
    from processes.base import apply_defaults, load_result_arrays
    from processes.sheet_metal import BENDPLAN_SCHEMA

    workdir = os.path.join(tmp, "bp_bracket")
    analysis = processes.get_analysis("sheet_metal", "bend_plan")
    merged = apply_defaults(analysis, {})
    stats = analysis.run(workdir, merged, None).stats

    check("analysis: L-bracket plan feasible with demo catalogue",
          stats["feasible"] and stats["mode"] == "search"
          and stats["plans"], "")
    best = stats["plans"][0]
    check("analysis: one setup, one step",
          len(best["setups"]) == 1 and len(best["steps"]) == 1
          and best["setups"][0]["punch"]["runs"], "")
    feasible_actions = [a for a in stats["actions"] if a["feasible"]]
    check("analysis: actions carry display segments",
          feasible_actions
          and feasible_actions[0]["display"]["required_segments"], "")

    cache_params = {**merged, "schema": BENDPLAN_SCHEMA,
                    "mesh": pipeline.mesh_fingerprint(workdir),
                    "aag": pipeline.aag_fingerprint(workdir)}
    arrays = load_result_arrays(workdir, "sheet_metal", "bend_plan",
                                cache_params)
    fine_count = len(np.load(os.path.join(workdir, "fine_faces.npy")))
    check("analysis: line arrays + panel field stored",
          arrays["outline_lines"].size > 0
          and arrays["bend_axis_lines"].size > 0
          and arrays["required_lines"].size > 0
          and len(arrays["panel_id"]) == fine_count
          and set(np.unique(arrays["panel_id"])) == {0, 1, 2}, "")

    # cache round-trip: identical stats, no recompute
    second = analysis.run(workdir, merged, None).stats
    check("analysis: cache round-trip byte-identical",
          json.dumps(stats, sort_keys=True)
          == json.dumps(second, sort_keys=True), "")

    # U-channel: search finds a single-setup two-step plan
    workdir = os.path.join(tmp, "bp_channel")
    stats = analysis.run(workdir, apply_defaults(analysis, {}), None).stats
    best = stats["plans"][0]
    check("analysis: U-channel single setup, two steps",
          stats["feasible"] and len(best["setups"]) == 1
          and len(best["steps"]) == 2, f"{len(best['setups'])} setups")


def fixture_fold_mesh(check, tmp):
    from pressbrake import foldmesh

    for name, label in (("bp_bracket", "bracket"),
                        ("bp_channel", "channel"),
                        ("bp_notched", "notched")):
        workdir = os.path.join(tmp, name)
        graph, info = adapter.build_kinematic_graph(workdir,
                                                    keep_unfold=True)
        fold = adapter.compute_fold_mesh(workdir, graph, info)
        check(f"{label}: fold mesh available", fold["available"],
              str(fold.get("reason")))
        if not fold["available"]:
            continue

        vertex_panel = fold["vertex_panel"]
        vertex_bend = fold["vertex_bend"]
        orphans = int(np.count_nonzero((vertex_panel == 0)
                                       & (vertex_bend == 0)))
        check(f"{label}: every vertex assigned",
              fold["unassigned"] == 0 and orphans == 0,
              f"{orphans} orphans")

        flat = fold["flat_verts"].astype(float)
        height = flat[:, 2] * np.sign(graph.z_offset)
        check(f"{label}: flat heights within the sheet",
              float(height.min()) > -0.2
              and float(height.max()) < graph.thickness + 0.2,
              f"[{height.min():.2f}, {height.max():.2f}]")

        # THE invariant: refolding the flat coordinates at the target
        # angles reproduces the source mesh (base-aligned)
        verts = np.load(os.path.join(workdir, "fine_verts.npy")) \
            .astype(float)
        theta = np.array([bend.angle_target for bend in graph.bends])
        posed = foldmesh.pose_vertices(graph, flat, vertex_panel,
                                       vertex_bend, theta)
        base = np.asarray(fold["base_transform"])
        expected = verts @ base[:3, :3].T + base[:3, 3]
        deviation = np.linalg.norm(posed - expected, axis=1)
        check(f"{label}: refold reproduces the mesh (<0.2mm)",
              float(deviation.max()) < 0.2,
              f"max {deviation.max():.3f} mm")

    # partial-fold model: zone edges rigid with their panels, the arc
    # continuous across the zone, at every stroke fraction incl. overbend
    workdir = os.path.join(tmp, "bp_bracket")
    graph, info = adapter.build_kinematic_graph(workdir, keep_unfold=True)
    bend = graph.bends[0]
    normal = np.array([-bend.axis_dir[1], bend.axis_dir[0]])
    mid = bend.axis_point + bend.axis_dir * bend.length / 2.0
    edge_parent = bend.zone_width / 2.0 + bend.zone_shift
    edge_child = bend.zone_width / 2.0 - bend.zone_shift

    edges_ok = True
    smooth_ok = True
    samples = np.linspace(0.0, bend.zone_width, 200)
    for fraction in (0.25, 0.5, 0.75, 1.0, 1.1):
        theta = np.array([bend.angle_target * fraction])
        transforms = graph.fold_transforms(theta)
        for zeta in (-graph.thickness / 2, 0.0, graph.thickness / 2):
            z = graph.z_offset + zeta
            flat = np.column_stack([
                mid[0] + (samples - edge_parent) * normal[0],
                mid[1] + (samples - edge_parent) * normal[1],
                np.full(len(samples), z)])
            posed = foldmesh.pose_vertices(
                graph, flat, np.zeros(len(flat), dtype=int),
                np.full(len(flat), bend.id + 1, dtype=int), theta)
            for pose, index in ((transforms[bend.parent_panel], 0),
                                (transforms[bend.child_panel], -1)):
                rigid = flat[index] @ pose[:3, :3].T + pose[:3, 3]
                edges_ok &= bool(np.linalg.norm(rigid - posed[index]) < 1e-6)
            steps = np.linalg.norm(np.diff(posed, axis=0), axis=1)
            smooth_ok &= bool(steps.max() < 2.5 * bend.zone_width / 199)
    check("bracket: zone edges rigid with their panels", edges_ok, "")
    check("bracket: arc continuous across the zone", smooth_ok, "")

    # step poses: rigid placements and a full theta trail
    from processes.base import apply_defaults
    import processes
    analysis = processes.get_analysis("sheet_metal", "bend_plan")
    stats = analysis.run(workdir, apply_defaults(analysis, {}), None).stats
    steps = stats["plans"][0]["steps"]
    rigid_ok = True
    for step in steps:
        placement = np.asarray(step["placement"]).reshape(4, 4)
        rotation = placement[:3, :3]
        rigid_ok &= bool(
            np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-9)
            and np.isclose(np.linalg.det(rotation), 1.0, atol=1e-9)
            and abs(step["lift_sign"]) == 1.0
            and step["phi_target"] > 0
            and len(step["theta_before"]) == graph.bend_count)
    check("plan steps carry rigid machine placements",
          bool(steps) and rigid_ok, f"{len(steps)} steps")
    check("fold mesh stats stored",
          stats["fold_mesh"]["available"]
          and stats["fold_mesh"]["unassigned"] == 0
          and stats["tooling"]["punches"] and stats["tooling"]["dies"], "")


def main():
    failures = []
    check = check_factory(failures)

    with tempfile.TemporaryDirectory() as tmp:
        print("=== fixture 1: L-bracket kinematic graph ===")
        fixture_l_bracket(check, tmp)
        print("=== fixture 2: U-channel folded-vs-BREP ===")
        fixture_u_channel(check, tmp)
        print("=== fixture 3: tooling intervals on extracted parts ===")
        fixture_intervals(check, tmp)
        print("=== fixture 4: bend_plan analysis end to end ===")
        fixture_analysis(check, tmp)
        print("=== fixture 5: fold mesh + partial-fold model ===")
        fixture_fold_mesh(check, tmp)

    if failures:
        print(f"{len(failures)} CHECKS FAILED: {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
