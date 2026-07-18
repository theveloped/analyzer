"""Checks of the press-brake planning core (pressbrake/, pure — no OCP).

Consolidated port of the upstream pytest suites onto the repo's
plain-script convention: interval algebra, fold kinematics on the
synthetic builders, catalogue loading, the sampled collision oracle, the
analytic interval envelope (including the envelope-contains-oracle
cross-validation), the segmented-tooling knapsack, sequence search and
report serialization determinism.

Run from the repo root: python test_pressbrake.py
"""
import json
import math
import sys

import numpy as np
from shapely.geometry import Polygon

from pressbrake import collision, envelope, kinematics, plan, report, sequence, tooling
from pressbrake import builders
from pressbrake.intervals import IntervalSet
from pressbrake.machine import (CatalogueSection, ToolProfile, load_dies,
                                load_machine, load_punches)
from pressbrake.model import BendAction
from pressbrake.sequence import SearchConfig


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:44s} {detail}")
        if not condition:
            failures.append(name)
    return check


def folded_outline(graph, theta, panel_id):
    return graph.panel_vertices(np.asarray(theta, dtype=float))[panel_id]


def action_for(graph, bend_id, rotation=0, x_offset=0.0):
    flip = graph.bends[bend_id].angle_target < 0
    return BendAction(bend_ids=(bend_id,), flip=flip, rotation=rotation,
                      x_offset=x_offset)


def fixture_intervals(check):
    check("normalization merges touching/overlapping",
          IntervalSet([(5, 7), (0, 2), (2, 3), (6, 9)]).to_pairs()
          == [(0, 3), (5, 9)], "")
    check("empty and inverted inputs dropped",
          IntervalSet([(3, 3), (5, 4)]).is_empty()
          and IntervalSet().is_empty(), "")
    a = IntervalSet([(0, 2), (6, 8)])
    b = IntervalSet([(1, 3), (4, 5)])
    check("union / intersect / complement / difference",
          a.union(b).to_pairs() == [(0, 3), (4, 5), (6, 8)]
          and IntervalSet([(0, 4), (6, 10)]).intersect(
              IntervalSet([(2, 7), (9, 12)])).to_pairs()
          == [(2, 4), (6, 7), (9, 10)]
          and IntervalSet([(2, 4), (6, 8)]).complement(0, 10).to_pairs()
          == [(0, 2), (4, 6), (8, 10)]
          and IntervalSet([(0, 10)]).difference(
              IntervalSet([(2, 3), (5, 7)])).to_pairs()
          == [(0, 2), (3, 5), (7, 10)], "")
    c = IntervalSet([(2, 3), (5, 6)])
    check("buffer / translate / contains",
          np.isclose(c.buffer(1.0).measure(), 6.0)
          and c.translate(10).to_pairs() == [(12, 13), (15, 16)]
          and c.buffer(-0.6).is_empty()
          and IntervalSet([(0, 5), (7, 9)]).contains(
              IntervalSet([(1, 2), (8, 9)]))
          and not IntervalSet([(0, 5)]).contains_point(6.0), "")

    rng = np.random.default_rng(42)
    ok = True
    for _ in range(50):
        a = IntervalSet(rng.uniform(0, 100, (6, 2)))
        b = IntervalSet(rng.uniform(0, 100, (6, 2)))
        inter = a.intersect(b)
        ok &= np.isclose(a.union(b).measure(),
                         a.measure() + b.measure() - inter.measure())
        ok &= np.isclose(a.difference(b).measure() + inter.measure(),
                         a.measure())
        ok &= a.complement(-1.0, 101.0).complement(-1.0, 101.0) == a
    check("algebra round-trip properties (50 random)", ok, "")


def fixture_kinematics(check):
    graph = builders.l_bracket(width=100, leg=50, flange=30)
    got = {tuple(np.round(v, 6))
           for v in folded_outline(graph, [math.pi / 2], 1)}
    check("l_bracket +90: flange rises to +Z",
          got == {(0.0, 50.0, 0.0), (100.0, 50.0, 0.0),
                  (100.0, 50.0, 30.0), (0.0, 50.0, 30.0)}, "")

    graph = builders.l_bracket()
    graph.bends[0].angle_target = -math.pi / 2
    verts = folded_outline(graph, [-math.pi / 2], 1)
    check("l_bracket -90 goes down",
          bool(np.all(verts[:, 2] <= 1e-9))
          and np.isclose(np.min(verts[:, 2]), -30.0), "")

    graph = builders.hat_profile(width=100, top=40.0, wall=30.0, foot=20.0)
    theta = [b.angle_target for b in graph.bends]
    foot_a = folded_outline(graph, theta, 3)
    foot_b = folded_outline(graph, theta, 4)
    check("hat profile chained transforms",
          bool(np.allclose(foot_a[:, 2], -30.0, atol=1e-9))
          and bool(np.allclose(foot_b[:, 2], -30.0, atol=1e-9))
          and np.isclose(np.min(foot_a[:, 1]), -20.0)
          and np.isclose(np.max(foot_b[:, 1]), 60.0), "")
    check("hat moving masks",
          graph.bends[0].moving_mask == (1 << 1) | (1 << 3)
          and graph.bends[1].moving_mask == (1 << 2) | (1 << 4)
          and graph.bends[2].moving_mask == 1 << 3, "")

    graph = builders.u_channel()
    bend = graph.bends[0]
    normal = kinematics.normal_2d(bend.axis_dir)
    child = graph.panels[bend.child_panel].centroid()
    check("axis normalized toward the child",
          float(np.dot(normal, child - bend.axis_point)) > 0, "")
    check("sister groups: tabs share, u_channel walls do not",
          builders.tabbed_flange().bends[0].sister_group
          == builders.tabbed_flange().bends[1].sister_group
          and graph.bends[0].sister_group != graph.bends[1].sister_group, "")

    action = BendAction(bend_ids=(0,), flip=False, rotation=0)
    transforms = kinematics.relative_transforms(
        graph, np.zeros(2), action, math.pi / 4)
    check("relative transforms move only the subtree",
          bool(np.allclose(transforms[0], np.eye(4)))
          and bool(np.allclose(transforms[2], np.eye(4)))
          and not np.allclose(transforms[1], np.eye(4)), "")

    graph = builders.l_bracket(width=100, leg=50, flange=30)
    poses = kinematics.machine_transforms(
        graph, np.zeros(1), action, [0.0, math.pi / 2])
    flat_ok = all(np.allclose(kinematics.transform_points(
        poses[0, p.id], kinematics.panel_points_3d(p))[:, 2], 0.0, atol=1e-9)
        for p in graph.panels)
    lifted = [kinematics.transform_points(
        poses[1, p.id], kinematics.panel_points_3d(p)) for p in graph.panels]
    tips = [v[np.argmax(np.abs(v[:, 1]))] for v in lifted]
    check("machine transforms: flat at phi=0, both wings lift +-phi/2",
          flat_ok
          and np.isclose(tips[0][2], 50 * math.sin(math.pi / 4))
          and np.isclose(tips[1][2], 30 * math.sin(math.pi / 4))
          and tips[0][1] * tips[1][1] < 0, "")

    graph = builders.l_bracket()
    graph.bends[0].angle_target = -math.pi / 2
    actions = kinematics.enumerate_actions(graph, [0])
    poses = kinematics.machine_transforms(
        graph, np.zeros(1), actions[0], [math.pi / 2])
    check("negative bend forms upward via flip",
          all(action.flip for action in actions)
          and all(np.min(kinematics.transform_points(
              poses[0, p.id],
              kinematics.panel_points_3d(p))[:, 2]) >= -1e-9
              for p in graph.panels), "")

    graph = builders.l_bracket()
    action_b = BendAction(bend_ids=(0,), flip=False, rotation=0, x_offset=25.0)
    pose_a = kinematics.machine_transforms(
        graph, np.zeros(1), BendAction(bend_ids=(0,), flip=False, rotation=0),
        [0.3])
    pose_b = kinematics.machine_transforms(graph, np.zeros(1), action_b, [0.3])
    delta = (kinematics.transform_points(
        pose_b[0, 1], kinematics.panel_points_3d(graph.panels[1]))
        - kinematics.transform_points(
            pose_a[0, 1], kinematics.panel_points_3d(graph.panels[1])))
    check("x_offset translates along machine X",
          bool(np.allclose(delta, [25.0, 0.0, 0.0])), "")

    # bend deduction: with zone_width = BA the child slides away from the
    # hinge by 2*(r+t/2)*tan(theta/2) - BA (panels are short relative to a
    # rigid rotation about the virtual corner)
    graph = builders.l_bracket(width=100, leg=50, flange=30,
                               inner_radius=3.0, thickness=2.0)
    r, t, k = 3.0, 2.0, 0.5
    zone = (math.pi / 2) * (r + k * t)
    graph.bends[0].zone_width = zone
    verts = folded_outline(graph, [math.pi / 2], 1)
    deduction = 2.0 * (r + t / 2) * math.tan(math.pi / 4) - zone
    check("bend deduction slides the folded flange",
          np.isclose(np.max(verts[:, 2]), 30.0 + deduction, atol=1e-6)
          and bool(np.allclose(verts[:, 1], 50.0, atol=1e-6)),
          f"z {np.max(verts[:, 2]):.3f} vs {30 + deduction:.3f}")

    state = builders.u_channel().flat_state()
    following = state.with_bend_done(builders.u_channel(), 1)
    check("fold state bitmask helpers",
          state.done_mask == 0 and following.done_mask == 0b10
          and builders.u_channel().folded_state().done_mask == 0b11, "")


def fixture_catalogue(check):
    machine = load_machine()
    punches = load_punches()
    dies = load_dies()
    check("demo catalogue loads",
          len(punches) >= 3 and len(dies) >= 3 and machine.x_length > 1000,
          f"{machine.name}: {len(punches)} punches, {len(dies)} dies")
    punch = punches["P.88.R08"]
    die = dies["D.V16.88"]
    check("thickness/angle gates",
          die.fits_thickness(2.0) and punch.fits_angle(math.pi / 2)
          and not punch.fits_angle(math.radians(170)), "")
    shifted = die.transformed_profile(thickness=2.0)
    check("die profile shifts down by t/2",
          np.isclose(np.max(shifted[:, 1]),
                     np.max(np.asarray(die.profile)[:, 1]) - 1.0), "")


def fixture_collision(check):
    machine = load_machine()
    punches = load_punches()
    dies = load_dies()

    graph = builders.l_bracket(width=100, leg=50, flange=30)
    clear = collision.check_action(
        graph, np.zeros(1), action_for(graph, 0),
        punch=punches["P.88.R08"], die=dies["D.V16.88"], machine=machine)
    check("plain 90-deg bend is clear", not clear.collided,
          clear.summary() if clear.collided else "")

    graph = builders.offset_lip(base=60, wall=30, lip=20)
    state = np.array([0.0, math.pi / 2])
    straight_hits = [collision.check_action(
        graph, state, action_for(graph, 0, rotation),
        punch=punches["P.88.R08"], die=dies["D.V16.88"]).collided
        for rotation in (0, 1)]
    gooseneck_clear = [not collision.check_action(
        graph, state, action_for(graph, 0, rotation),
        punch=punches["P.88.GN"], die=dies["D.V16.88"]).collided
        for rotation in (0, 1)]
    check("formed lip: straight punch collides, gooseneck clears one side",
          all(straight_hits) and any(gooseneck_clear)
          and not all(gooseneck_clear), "")

    graph = builders.u_channel(base=30, wall=200)
    tall = collision.check_action(
        graph, np.array([math.pi / 2, 0.0]), action_for(graph, 1),
        punch=punches["P.88.R08"], die=dies["D.V16.88"])
    check("tall formed wall hits the punch body",
          tall.collided
          and any(hit.obstacle == "punch" for hit in tall.hits), "")

    tight = builders.box(corner_gap=0.0)
    state = np.array([math.pi / 2] * 3 + [0.0])
    hits = collision.check_self_collision(
        tight, state, action_for(tight, 3), margin=2.0)
    roomy = builders.box(corner_gap=6.0)
    clear_hits = collision.check_self_collision(
        roomy, state, action_for(roomy, 3), margin=2.0)
    check("box corner self-collision at gap 0, clear at 6",
          bool(hits) and not clear_hits, "")

    z_graph = builders.z_profile()
    z_report = collision.check_action(
        z_graph, np.zeros(2), action_for(z_graph, 0),
        punch=punches["P.88.R08"], die=dies["D.V16.88"], machine=machine)
    check("z-profile negative bend clears via flip", not z_report.collided,
          z_report.summary() if z_report.collided else "")


def fixture_envelope(check):
    machine = load_machine()
    punches = load_punches()
    dies = load_dies()
    punch = punches["P.88.R08"]
    die = dies["D.V16.88"]

    graph = builders.l_bracket(width=100)
    result = envelope.compute_envelope(
        graph, np.zeros(1), action_for(graph, 0), punch, die, machine)
    check("plain bend fully required and feasible",
          result.feasible and result.forbidden_punch.is_empty()
          and np.isclose(result.required.measure(), 100.0, atol=1.0),
          f"required {result.required.measure():.1f}")

    graph = builders.notched_bend(width=100, notch=(40.0, 60.0))
    result = envelope.compute_envelope(
        graph, np.zeros(1), action_for(graph, 0), punch, die, machine)
    check("notch drops out of required, stays optional",
          np.isclose(result.required.measure(), 80.0, atol=1.5)
          and not result.required.intersect(
              IntervalSet([(41.0, 59.0)])).measure()
          and result.optional_for(result.forbidden_punch).contains(
              IntervalSet([(41.0, 59.0)])), "")

    graph = builders.tabbed_flange(width=100, gap=(40.0, 60.0))
    result = envelope.compute_envelope(
        graph, np.zeros(2), BendAction(bend_ids=(0, 1), flip=False,
                                       rotation=0), punch, die, machine)
    check("sister tabs: two required spans",
          result.feasible and len(result.required) == 2
          and np.isclose(result.required.measure(), 80.0, atol=1.5), "")

    graph = builders.offset_lip(width=100)
    result = envelope.compute_envelope(
        graph, np.array([0.0, math.pi / 2]), action_for(graph, 0), punch, die)
    check("formed lip forbids the straight punch",
          not result.forbidden_punch.is_empty() and not result.feasible, "")

    graph = builders.notched_bend()
    base = envelope.compute_envelope(
        graph, np.zeros(1), action_for(graph, 0), punch, die)
    shifted = envelope.compute_envelope(
        graph, np.zeros(1), action_for(graph, 0, x_offset=500.0), punch, die)
    check("x_offset translates the envelope",
          shifted.required == base.required.translate(500.0), "")

    from shapely.geometry import Point
    swept = envelope.swept_region(
        [((50.0, 0.0), (100.0, 0.0))], 1.0, 0.0, math.pi / 4)
    covered = all(
        swept.contains(Point(radius * math.cos(angle),
                             radius * math.sin(angle)))
        for angle in np.linspace(0, math.pi / 4, 100)
        for radius in (50.0, 75.0, 100.0))
    check("swept region covers all rotations, no huge overshoot",
          covered and not swept.contains(Point(0.0, -10.0))
          and not swept.contains(Point(103.0, -3.0)), "")

    # cross-validation: every sampled oracle hit lies inside the envelope
    cases = [
        (builders.offset_lip(width=100), np.array([0.0, math.pi / 2]), 0),
        (builders.u_channel(base=30, wall=200),
         np.array([math.pi / 2, 0.0]), 1),
        (builders.notched_bend(), np.array([0.0]), 0),
    ]
    contained = True
    hits_seen = 0
    for graph, state, bend_id in cases:
        action = action_for(graph, bend_id)
        result = envelope.compute_envelope(
            graph, state, action, punch, die, machine)
        oracle = collision.check_action(
            graph, state, action, punch=punch, die=die, machine=machine)
        for hit in oracle.hits:
            if math.isnan(hit.x):
                continue
            hits_seen += 1
            forbidden = {
                "punch": result.forbidden_punch,
                "die": result.forbidden_die,
                "ram": result.forbidden_machine,
                "table": result.forbidden_machine,
            }[hit.obstacle]
            contained &= forbidden.contains_point(hit.x)
    check("envelope contains every sampled oracle hit",
          contained and hits_seen > 0, f"{hits_seen} hits cross-validated")

    # precomputed sweep must reproduce the inline result exactly
    graph = builders.offset_lip(width=100)
    state = np.array([0.0, math.pi / 2])
    action = action_for(graph, 0)
    direct = envelope.compute_envelope(
        graph, state, action, punch, die, machine)
    reused = envelope.compute_envelope(
        graph, state, action, punch, die, machine,
        sweep=envelope.compute_sweep(graph, state, action))
    check("sweep reuse: identical envelope",
          direct.required == reused.required
          and direct.forbidden_punch == reused.forbidden_punch
          and direct.forbidden_die == reused.forbidden_die
          and direct.forbidden_machine == reused.forbidden_machine
          and direct.x_range == reused.x_range, "")

    # analytic vs legacy shapely path: same verdicts on the fixtures, and
    # the plain bend (wings hugging the punch flanks in exact tangency)
    # must stay feasible despite the strict-interior predicate
    agree = True
    for graph, state, bend_id, machine_arg, expect_feasible in [
            (builders.l_bracket(width=100), np.zeros(1), 0, machine, True),
            (builders.offset_lip(width=100), np.array([0.0, math.pi / 2]),
             0, None, False)]:
        action = action_for(graph, bend_id)
        analytic = envelope.compute_envelope(
            graph, state, action, punch, die, machine_arg,
            sweep=envelope.compute_sweep(graph, state, action, analytic=True))
        legacy = envelope.compute_envelope(
            graph, state, action, punch, die, machine_arg,
            sweep=envelope.compute_sweep(graph, state, action, analytic=False))
        agree &= analytic.feasible == legacy.feasible == expect_feasible
    check("analytic path matches shapely path verdicts", agree, "")

    # analytic sector predicate against a square off the +Y=0 axis
    square = Polygon([(10.0, -1.0), (12.0, -1.0), (12.0, 1.0), (10.0, 1.0)])
    arrays = envelope._obstacle_arrays(square)
    predicate_ok = (
        envelope._sectors_intersect(np.array([[9.0, 13.0, -0.2, 0.2]]), arrays)
        and not envelope._sectors_intersect(
            np.array([[9.0, 13.0, 1.0, 2.0]]), arrays)
        and envelope._sectors_intersect(                   # full annulus
            np.array([[9.0, 13.0, -0.2, 6.4]]), arrays)
        and envelope._sectors_intersect(                   # sector inside
            np.array([[10.5, 11.0, -0.01, 0.01]]), arrays)
        and envelope._sectors_intersect(                   # pie contains it
            np.array([[0.0, 50.0, -3.0, 3.0]]), arrays)
        and not envelope._sectors_intersect(               # radially clear
            np.array([[1.0, 8.0, -3.0, 3.0]]), arrays))
    check("analytic sector predicate cases", predicate_ok, "")

    # batched plane cut reproduces the per-plane oracle slicing
    outline = np.array([[0.0, 0.0, 0.0], [80.0, 0.0, 5.0],
                        [80.0, 40.0, 5.0], [0.0, 40.0, 0.0]])
    hole = np.array([[20.0, 10.0, 1.25], [40.0, 10.0, 2.5],
                     [40.0, 30.0, 2.5], [20.0, 30.0, 1.25]])
    loops = [outline, hole]
    xs = list(np.linspace(1.0, 79.0, 57))
    starts, ends = envelope._loop_edges(loops)
    batched = envelope._batched_plane_cut(starts, ends, xs, block=16)
    slicing_ok = True
    for index, x in enumerate(xs):
        single = collision._plane_cut_segments(loops, x)
        got = batched[index]
        slicing_ok &= len(single) == len(got) and all(
            np.allclose(a, b, atol=1e-12)
            for a, b in zip(np.asarray(single).reshape(-1, 2),
                            np.asarray(got).reshape(-1, 2)))
    check("batched slicing matches per-plane slicing", slicing_ok,
          f"{len(xs)} planes")


def _make_tool(lengths_counts, tool_id="T", kind="punch", mass=None):
    profile = [[0.0, 0.0], [5.0, 10.0], [5.0, 50.0], [-5.0, 50.0],
               [-5.0, 10.0]]
    return ToolProfile(
        id=tool_id, kind=kind, profile=profile, height=50.0,
        tip_angle=math.radians(88), mass_kg_per_m=mass,
        sections=[CatalogueSection(length, count)
                  for length, count in lengths_counts.items()])


def _solve(tool, required, forbidden=(), domain=(-50.0, 200.0),
           machine_x=None):
    return tooling.solve_tool_placement(
        tool, IntervalSet(required), IntervalSet(forbidden), domain,
        machine_x_length=machine_x)


def fixture_tooling(check):
    placement = _solve(_make_tool({100.0: 1}), [(0, 100)])
    check("exact single-section cover",
          placement.feasible and placement.section_count == 1
          and np.isclose(placement.runs[0].x_start, 0.0), "")

    placement = _solve(_make_tool({10.0: 4, 15.0: 4, 20.0: 4}), [(0, 35)])
    check("min count beats min length",
          placement.feasible and placement.section_count == 2
          and np.isclose(placement.total_length, 35.0), "")

    placement = _solve(_make_tool({50.0: 1, 25.0: 2}), [(0, 50)])
    check("count-first lexicographic",
          placement.feasible and placement.section_count == 1
          and np.isclose(placement.runs[0].sections[0].length, 50.0), "")

    roomy = _solve(_make_tool({50.0: 1}), [(0, 30)])
    tight = _solve(_make_tool({50.0: 1}), [(0, 30)],
                   forbidden=[(-100, -5), (35, 100)], domain=(-100, 100))
    check("overshoot allowed with room, infeasible when tight",
          roomy.feasible and np.isclose(roomy.total_length, 50.0)
          and not tight.feasible, "")

    exhausted = _solve(_make_tool({40.0: 1}), [(0, 80)])
    check("inventory quantity limits",
          _solve(_make_tool({40.0: 2}), [(0, 80)]).feasible
          and not exhausted.feasible, "")

    tool = _make_tool({40.0: 2, 100.0: 1})
    split = _solve(tool, [(0, 40), (60, 100)], forbidden=[(45, 55)])
    merged = _solve(tool, [(0, 40), (60, 100)])
    check("forbidden gap splits runs, absence merges",
          split.feasible and len(split.runs) == 2
          and merged.feasible and merged.section_count == 1, "")

    conflicted = _solve(_make_tool({100.0: 1}), [(0, 100)],
                        forbidden=[(40, 50)])
    check("required meeting forbidden rejected",
          not conflicted.feasible and "forbidden" in conflicted.reason, "")

    wide = _solve(_make_tool({100.0: 2}), [(0, 90), (1900, 1990)],
                  domain=(-100, 2100), machine_x=1000.0)
    check("machine width post-check",
          not wide.feasible and "wider than the machine" in wide.reason, "")

    check("empty required trivially feasible",
          _solve(_make_tool({50.0: 1}), []).feasible, "")

    from dataclasses import dataclass, field

    @dataclass
    class FakeEnvelope:
        required: IntervalSet
        forbidden_punch: IntervalSet = field(default_factory=IntervalSet)
        forbidden_die: IntervalSet = field(default_factory=IntervalSet)
        forbidden_machine: IntervalSet = field(default_factory=IntervalSet)
        x_range: tuple = (0.0, 100.0)

    punch = _make_tool({100.0: 1, 40.0: 2}, tool_id="P")
    die = _make_tool({100.0: 1, 40.0: 2}, tool_id="D", kind="die")
    setup = tooling.solve_setup(
        [FakeEnvelope(required=IntervalSet([(0, 40)])),
         FakeEnvelope(required=IntervalSet([(60, 100)]))], punch, die)
    check("setup union: one section covers both bends",
          setup.feasible and setup.punch_placement.section_count == 1, "")

    conflict = tooling.solve_setup(
        [FakeEnvelope(required=IntervalSet([(0, 100)])),
         FakeEnvelope(required=IntervalSet([(150, 200)]),
                      forbidden_punch=IntervalSet([(20, 80)]),
                      x_range=(0.0, 250.0))], punch, die)
    check("setup union conflict rejected fast",
          not conflict.feasible
          and "union required meets union forbidden" in conflict.reason, "")


def fixture_plan_sequence(check):
    machine = load_machine()
    punches = load_punches()
    dies = load_dies()

    graph = builders.hat_profile()
    rep = plan.plan_graph(graph, machine, punches, dies,
                          sequence=[2, 3, 0, 1])
    walls_first = plan.plan_graph(builders.hat_profile(), machine, punches,
                                  dies, sequence=[0, 1, 2, 3])
    check("hat: feet-first feasible, walls-first not",
          rep.feasible and not walls_first.feasible, "")
    check("springback applied as overbend triple",
          all(np.isclose(b.angle_overbend,
                         b.angle_target
                         + math.copysign(math.radians(2.0), b.angle_target))
              and b.angle_relaxed == b.angle_target
              for b in graph.bends), "")

    hem = builders.l_bracket(angle=math.radians(170))
    hem_result = sequence.search_sequences(hem, machine, punches, dies)
    check("hem rejected by the search",
          not hem_result.feasible
          and "hem" in hem_result.stats.get("reason", ""), "")

    graph = builders.hat_profile()
    result = sequence.search_sequences(
        graph, machine, punches, dies, SearchConfig(max_solutions=8))
    groups = graph.sister_groups()
    group_of = {bend: key for key, bends in groups.items() for bend in bends}
    orders_ok = all(
        [step.sister_group for step in p.steps].index(group_of[2])
        < [step.sister_group for step in p.steps].index(group_of[0])
        for p in result.plans)
    check("search discovers feet-before-walls on the hat",
          result.feasible and orders_ok
          and result.stats["dead_states"] > 0, "")

    graph = builders.offset_lip()
    result = sequence.search_sequences(graph, machine, punches, dies)
    best = result.plans[0]
    check("offset lip: lip first, relief punch chosen",
          result.feasible
          and [step.bend_ids for step in best.steps] == [(1,), (0,)]
          and any(setup.punch_id in ("P.88.GN", "P.30.R08")
                  for setup in best.setups), "")

    straight_only = sequence.search_sequences(
        builders.offset_lip(), machine,
        {"P.88.R08": punches["P.88.R08"]}, dies)
    check("offset lip unsolvable with the straight punch only",
          not straight_only.feasible and straight_only.exhaustive, "")

    result = sequence.search_sequences(
        builders.u_channel(), machine, punches, dies,
        SearchConfig(max_solutions=20))
    best = result.plans[0]
    check("u_channel: one setup, exhaustive search",
          result.feasible and result.exhaustive
          and best.objective[0] == 0 and len(best.setups) == 1, "")

    # envelope cache: every (state, action, tools) evaluated at most once
    calls = []
    original = envelope.compute_envelope

    def counting(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    sequence.envelope.compute_envelope = counting
    try:
        sequence.search_sequences(
            builders.u_channel(), machine, punches, dies,
            SearchConfig(max_solutions=20))
    finally:
        sequence.envelope.compute_envelope = original
    keys = {(tuple(np.round(args[1], 6)), tuple(args[2].bend_ids),
             args[2].rotation, args[3].id, args[4].id) for args in calls}
    check("envelope cache: no recomputation",
          len(calls) == len(keys), f"{len(calls)} calls")


def fixture_report(check):
    machine = load_machine()
    punches = load_punches()
    dies = load_dies()

    def build_payload():
        graph = builders.u_channel()
        result = sequence.search_sequences(
            graph, machine, punches, dies, SearchConfig(max_solutions=20))
        return json.dumps(
            report.dump_search_report(result, graph, machine.name),
            sort_keys=True)

    first = build_payload()
    second = build_payload()
    data = json.loads(first)
    check("search report serializes and round-trips",
          data["feasible"] is True and data["plans"]
          and data["plans"][0]["setups"][0]["punch"]["runs"]
          and len(data["plans"][0]["steps"]) == 2, "")
    check("report JSON deterministic across fresh runs", first == second,
          "")

    graph = builders.l_bracket()
    rep = plan.plan_graph(graph, machine, punches, dies)
    payload = json.dumps(report.dump_report(rep), sort_keys=True)
    check("plan report JSON-safe", json.loads(payload)["feasible"] is True,
          "")


def main():
    failures = []
    check = check_factory(failures)

    print("=== intervals ===")
    fixture_intervals(check)
    print("=== kinematics ===")
    fixture_kinematics(check)
    print("=== catalogue ===")
    fixture_catalogue(check)
    print("=== collision oracle ===")
    fixture_collision(check)
    print("=== interval envelope ===")
    fixture_envelope(check)
    print("=== tooling solver ===")
    fixture_tooling(check)
    print("=== plan + sequence search ===")
    fixture_plan_sequence(check)
    print("=== report serialization ===")
    fixture_report(check)

    if failures:
        print(f"{len(failures)} CHECKS FAILED: {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
