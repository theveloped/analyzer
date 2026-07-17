"""
Bend-plan orchestration: the engine behind the sheet_metal/bend_plan
analysis and the ``bendplan`` CLI command.

``plan_graph`` evaluates every sister-bend group (in the given sequence)
against every compatible punch/die combination and reports per-action
collision envelopes and feasibility; ``plan_search`` adds the sequence
search + setup-minimising assignment.  Graph construction lives in
``pressbrake.adapter``; result serialization in ``pressbrake.report``.

v1 planning model: bends are processed in the given sequence (default: bend
id order, sister groups merged); each action is evaluated with all previous
bends at their relaxed angle and the remaining bends flat.

NOTE: both entry points MUTATE the given graph's ``angle_overbend`` /
``angle_relaxed`` via ``_apply_springback``.  The mutation is idempotent
(recomputed from ``angle_target``), but callers who cache results should
build a fresh graph per invocation so stored reports stay a pure function
of (artifacts, params).
"""

import math

import numpy as np

from pressbrake import collision, envelope, kinematics


class ActionResult:

    def __init__(self, bend_ids, sister_group, rotation, flip):
        self.bend_ids = list(bend_ids)
        self.sister_group = sister_group
        self.rotation = rotation
        self.flip = flip
        self.envelopes = []
        self.best = None
        self.collision_summary = None

    @property
    def feasible(self):
        return self.best is not None


class PlanReport:

    def __init__(self, graph, machine_name):
        self.graph = graph
        self.source = graph.source
        self.machine = machine_name
        self.actions = []

    @property
    def feasible(self):
        """
        Every sister group must have at least one feasible action.
        """
        groups = {}
        for action in self.actions:
            groups.setdefault(action.sister_group, []).append(action)
        return bool(groups) and all(
            any(action.feasible for action in group)
            for group in groups.values()
        )


def plan_graph(graph, machine=None, punches=None, dies=None, margin=2.0,
               sequence=None, springback_deg=2.0):
    """
    Evaluate all bend actions of a graph against the catalogue.
    """
    punches = punches or {}
    dies = dies or {}

    _apply_springback(graph, math.radians(springback_deg))

    groups = graph.sister_groups()
    if sequence is None:
        ordered_groups = [groups[key] for key in sorted(groups.keys())]
    else:
        ordered_groups = _groups_for_sequence(graph, groups, sequence)

    report = PlanReport(graph, machine.name if machine else None)
    theta = np.zeros(graph.bend_count)

    for group in ordered_groups:
        primary = graph.bends[group[0]]
        hem = abs(primary.angle_target) > math.radians(150)
        candidates = _tool_candidates(graph, primary, punches, dies)

        for action in kinematics.enumerate_actions(graph, group):
            result = ActionResult(group, primary.sister_group,
                                  action.rotation, action.flip)
            if hem:
                result.collision_summary = (
                    "hem bend (angle {:.0f} deg) is outside the air-bend "
                    "model".format(math.degrees(abs(primary.angle_target))))
                report.actions.append(result)
                continue
            if not candidates:
                result.collision_summary = "no punch/die in the catalogue fits"
                report.actions.append(result)
                continue

            self_hits = collision.check_self_collision(
                graph, theta, action, margin=margin, stop_on_first=True)
            if self_hits:
                result.collision_summary = (
                    "self-collision: panel {} vs {}".format(
                        self_hits[0].panel, self_hits[0].obstacle))
                report.actions.append(result)
                continue

            for punch, die in candidates:
                candidate_envelope = envelope.compute_envelope(
                    graph, theta, action, punch, die, machine, margin=margin)
                result.envelopes.append(candidate_envelope)
                if candidate_envelope.feasible and (
                        result.best is None or _better(candidate_envelope,
                                                       result.best)):
                    result.best = candidate_envelope
            if result.best is None and result.envelopes:
                result.collision_summary = "no feasible punch/die combination"
            report.actions.append(result)

        for bend_id in group:
            theta[bend_id] = graph.bends[bend_id].angle_relaxed

    return report


def plan_search(graph, machine=None, punches=None, dies=None, margin=2.0,
                springback_deg=2.0, config=None):
    """
    Sequence search + setup-minimising assignment: ranked ProcessPlans.
    """
    from pressbrake import sequence

    _apply_springback(graph, math.radians(springback_deg))
    return sequence.search_sequences(
        graph, machine, punches or {}, dies or {}, config)


def _apply_springback(graph, springback):
    """
    Kinematic springback triple: command an overbend beyond the target,
    relax back to the target.  A constant per-material delta in v1.
    """
    for bend in graph.bends:
        sign = 1.0 if bend.angle_target >= 0 else -1.0
        bend.angle_overbend = bend.angle_target + sign * springback
        bend.angle_relaxed = bend.angle_target


def _tool_candidates(graph, bend, punches, dies):
    candidates = []
    for punch in punches.values():
        if not punch.fits_angle(bend.angle_target):
            continue
        for die in dies.values():
            if not die.fits_thickness(graph.thickness):
                continue
            if not die.fits_angle(bend.angle_target):
                continue
            candidates.append((punch, die))
    return candidates


def _better(envelope_a, envelope_b):
    """
    Among feasible envelopes prefer the one leaving fewer forbidden gaps
    (less segmented tooling later), then the wider die-safe coverage.
    """
    forbidden_a = envelope_a.forbidden_punch.union(envelope_a.forbidden_die)
    forbidden_b = envelope_b.forbidden_punch.union(envelope_b.forbidden_die)
    return (len(forbidden_a), forbidden_a.measure()) < \
           (len(forbidden_b), forbidden_b.measure())


def _groups_for_sequence(graph, groups, sequence):
    ordered = []
    used = set()
    for bend_id in sequence:
        group_key = None
        for key, members in groups.items():
            if bend_id in members:
                group_key = key
                break
        if group_key is None or group_key in used:
            continue
        used.add(group_key)
        ordered.append(groups[group_key])
    for key, members in groups.items():
        if key not in used:
            ordered.append(members)
    return ordered
