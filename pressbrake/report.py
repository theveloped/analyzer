"""Plain-dict JSON serialization of graphs, envelopes and plans.

Replaces the upstream marshmallow serialize.py: the analyzer stores results
as JSON via processes.base.store_result, so everything here returns native
Python types only (float/int/bool/str/list/dict — never numpy scalars).
The field surface mirrors the upstream schemas so downstream consumers of
the JSON stay compatible.
"""


def _f(value):
    return float(value)


def _points(points):
    return [[_f(x), _f(y)] for x, y in points]


def pairs(interval_set):
    """An IntervalSet as [[start, end], ...] (empty list for None)."""
    if interval_set is None:
        return []
    return [[_f(start), _f(end)] for start, end in interval_set.to_pairs()]


def dump_panel(panel):
    return {
        "id": int(panel.id),
        "outline": _points(panel.outline),
        "holes": [_points(hole) for hole in panel.holes],
        "brep_faces": [int(f) for f in panel.face_hashes],
    }


def dump_bend(bend):
    return {
        "id": int(bend.id),
        "axis_point": _points([bend.axis_point])[0],
        "axis_dir": _points([bend.axis_dir])[0],
        "angle_target": _f(bend.angle_target),
        "angle_overbend": _f(bend.angle_overbend),
        "angle_relaxed": _f(bend.angle_relaxed),
        "inner_radius": _f(bend.inner_radius),
        "k_factor": _f(bend.k_factor),
        "length": _f(bend.length),
        "zone_width": _f(bend.zone_width),
        "parent_panel": int(bend.parent_panel),
        "child_panel": int(bend.child_panel),
        "moving_mask": int(bend.moving_mask),
        "sister_group": int(bend.sister_group),
        "brep_faces": [int(f) for f in bend.face_hashes],
    }


def dump_graph(graph):
    return {
        "source": graph.source,
        "thickness": _f(graph.thickness),
        "z_offset": _f(graph.z_offset),
        "base_panel": int(graph.base_panel),
        "panels": [dump_panel(panel) for panel in graph.panels],
        "bends": [dump_bend(bend) for bend in graph.bends],
    }


def dump_envelope(envelope):
    return {
        "punch": envelope.punch_id,
        "die": envelope.die_id,
        "feasible": bool(envelope.feasible),
        "margin": _f(envelope.margin),
        "x_range": [_f(envelope.x_range[0]), _f(envelope.x_range[1])],
        "required": pairs(envelope.required),
        "required_core": pairs(envelope.required_core),
        "forbidden_punch": pairs(envelope.forbidden_punch),
        "forbidden_die": pairs(envelope.forbidden_die),
        "forbidden_machine": pairs(envelope.forbidden_machine),
    }


def dump_action(action):
    return {
        "bend_ids": [int(b) for b in action.bend_ids],
        "sister_group": int(action.sister_group),
        "rotation": int(action.rotation),
        "flip": bool(action.flip),
        "feasible": bool(action.feasible),
        "collision_summary": action.collision_summary,
        "envelopes": [dump_envelope(env) for env in action.envelopes],
        "best": (dump_envelope(action.best)
                 if action.best is not None else None),
    }


def dump_report(report):
    return {
        "source": report.graph.source,
        "machine": report.machine,
        "feasible": bool(report.feasible),
        "actions": [dump_action(action) for action in report.actions],
    }


def dump_placement(placement):
    if placement is None:
        return None
    return {
        "tool": placement.tool_id,
        "kind": placement.kind,
        "feasible": bool(placement.feasible),
        "reason": placement.reason,
        "section_count": int(placement.section_count),
        "total_length": _f(placement.total_length),
        "total_mass": _f(placement.total_mass),
        "runs": [{
            "x_start": _f(run.x_start),
            "x_end": _f(run.x_end),
            "length": _f(run.length),
            "sections": [{
                "length": _f(section.length),
                "x_start": _f(section.x_start),
                "x_end": _f(section.x_end),
                "horn": section.horn,
            } for section in run.sections],
        } for run in placement.runs],
    }


def dump_setup(setup):
    return {
        "punch_id": setup.punch_id,
        "die_id": setup.die_id,
        "step_indices": [int(i) for i in setup.step_indices],
        "feasible": bool(setup.feasible),
        "reason": setup.reason,
        "punch": dump_placement(setup.punch_placement),
        "die": dump_placement(setup.die_placement),
    }


def dump_plan(plan):
    return {
        "feasible": bool(plan.feasible),
        "objective": [_f(value) for value in plan.objective],
        "steps": [{
            "bend_ids": [int(b) for b in step.bend_ids],
            "sister_group": int(step.sister_group),
            "rotation": int(step.action.rotation),
            "flip": bool(step.action.flip),
        } for step in plan.steps],
        "setups": [dump_setup(setup) for setup in plan.setups],
    }


def dump_search_report(result, graph, machine_name=None):
    return {
        "source": graph.source,
        "machine": machine_name,
        "feasible": bool(result.feasible),
        "exhaustive": bool(result.exhaustive),
        "stats": {key: (int(value) if hasattr(value, "__index__") else value)
                  for key, value in result.stats.items()},
        "plans": [dump_plan(plan) for plan in result.plans],
    }
