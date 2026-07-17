"""Sheet metal process: recognition/roles (detect) and K-factor unfold
(flat_pattern) over the AAG stage artifact."""

import pipeline
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            load_cached_result, store_result)

# keep in sync with frontend/src/processes/sheetmetal/index.ts
SHEET_SCHEMA = 2
# keep in sync with frontend/src/processes/sheetmetal/bendplan.ts
BENDPLAN_SCHEMA = 1


def _cache_params(params):
    return {**params, "schema": SHEET_SCHEMA}


def run_detect(workdir, params, progress):
    cache_params = {**params, "schema": SHEET_SCHEMA,
                    "mesh": pipeline.mesh_fingerprint(workdir),
                    "aag": pipeline.aag_fingerprint(workdir)}
    cached = load_cached_result(workdir, "sheet_metal", "detect", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    import sheet
    result = sheet.detect_sheet(
        workdir, min_thickness=params["min_thickness"],
        max_thickness=params["max_thickness"], progress=progress)

    store_result(workdir, "sheet_metal", "detect", cache_params,
                 result["stats"], arrays=result["arrays"],
                 field_meta=result["field_meta"])
    return AnalysisResult(stats=result["stats"],
                          fields=list(result["arrays"]))


def run_flat_pattern(workdir, params, progress):
    cache_params = {**params, "schema": SHEET_SCHEMA,
                    "mesh": pipeline.mesh_fingerprint(workdir),
                    "aag": pipeline.aag_fingerprint(workdir)}
    cached = load_cached_result(workdir, "sheet_metal", "flat_pattern",
                                cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    import sheet
    result = sheet.flat_pattern(
        workdir, k_factor=params["k_factor"],
        combine_bends=params["combine_bends"],
        min_thickness=params["min_thickness"],
        volume_tolerance=params["volume_tolerance"],
        tollerance=params["tollerance"], progress=progress)

    store_result(workdir, "sheet_metal", "flat_pattern", cache_params,
                 result["stats"], arrays=result["arrays"],
                 field_meta=result["field_meta"])
    return AnalysisResult(stats=result["stats"],
                          fields=list(result["arrays"]))


def run_bend_plan(workdir, params, progress):
    cache_params = {**params, "schema": BENDPLAN_SCHEMA,
                    "mesh": pipeline.mesh_fingerprint(workdir),
                    "aag": pipeline.aag_fingerprint(workdir)}
    cached = load_cached_result(workdir, "sheet_metal", "bend_plan",
                                cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    # pressbrake imports stay function-local so the registry imports
    # without shapely/pyyaml installed
    import os

    import numpy as np

    import aag
    import sheet
    from pressbrake import adapter, report as report_mod
    from pressbrake import machine as machine_mod, plan as plan_mod

    graph, info = adapter.build_kinematic_graph(
        workdir, k_factor=params["k_factor"],
        min_thickness=params["min_thickness"], progress=progress)

    machine = machine_mod.load_machine(params["machine_path"] or None)
    punches = machine_mod.load_punches(params["punches_path"] or None)
    dies = machine_mod.load_dies(params["dies_path"] or None)
    for key, catalogue, label in ((params["punch_id"], punches, "punch"),
                                  (params["die_id"], dies, "die")):
        if key and key not in catalogue:
            raise ValueError(f"unknown {label} '{key}' — catalogue has: "
                             f"{', '.join(sorted(catalogue))}")
    if params["punch_id"]:
        punches = {params["punch_id"]: punches[params["punch_id"]]}
    if params["die_id"]:
        dies = {params["die_id"]: dies[params["die_id"]]}

    if progress is not None:
        progress(0.85, "planning bend actions")
    plan_report = plan_mod.plan_graph(
        graph, machine, punches, dies, margin=params["margin"],
        springback_deg=params["springback_deg"])

    search_result = None
    if params["search"]:
        if progress is not None:
            progress(0.9, "searching bend sequences")
        from pressbrake.sequence import SearchConfig
        # fresh graph: plan_graph already mutated overbend/relaxed on the
        # first one and the search must start from a clean state
        search_graph, _ = adapter.build_kinematic_graph(
            workdir, k_factor=params["k_factor"],
            min_thickness=params["min_thickness"])
        search_result = plan_mod.plan_search(
            search_graph, machine, punches, dies, margin=params["margin"],
            springback_deg=params["springback_deg"],
            config=SearchConfig(max_solutions=params["solutions"],
                                margin=params["margin"]))

    origin = np.asarray(info["origin"], dtype=float)

    # per action: interval pairs + flat-frame display segments
    actions = []
    warnings = []
    for action_result in plan_report.actions:
        dumped = report_mod.dump_action(action_result)
        envelope = action_result.best or (
            action_result.envelopes[0] if action_result.envelopes else None)
        if envelope is not None:
            forbidden = envelope.forbidden_punch.union(
                envelope.forbidden_die).union(envelope.forbidden_machine)
            dumped["display"] = {
                "required_segments": adapter.machine_interval_segments(
                    graph, action_result.bend_ids, action_result.rotation,
                    0.0, report_mod.pairs(envelope.required)),
                "forbidden_segments": adapter.machine_interval_segments(
                    graph, action_result.bend_ids, action_result.rotation,
                    0.0, report_mod.pairs(forbidden)),
            }
        if action_result.collision_summary \
                and "hem" in action_result.collision_summary:
            warnings.append(action_result.collision_summary)
        actions.append(dumped)

    # line arrays for the viewer, shifted to the display origin
    def shift(points):
        return np.asarray(points, dtype=float) - origin

    outline_points = []
    for panel in graph.panels:
        outline_points.append(np.vstack([shift(panel.outline),
                                         shift(panel.outline[:1])]))
        for hole in panel.holes:
            outline_points.append(np.vstack([shift(hole), shift(hole[:1])]))
    bend_axis_points = [
        shift([bend.axis_point,
               bend.axis_point + bend.length * bend.axis_dir])
        for bend in graph.bends]

    # one display action per sister group: the feasible one when any
    chosen = {}
    for dumped in actions:
        group = dumped["sister_group"]
        if group not in chosen or (dumped["feasible"]
                                   and not chosen[group]["feasible"]):
            chosen[group] = dumped
    required_points = []
    forbidden_points = []
    for dumped in chosen.values():
        display = dumped.get("display")
        if not display:
            continue
        for segment in display["required_segments"]:
            required_points.append(shift(segment))
        for segment in display["forbidden_segments"]:
            forbidden_points.append(shift(segment))

    panel_by_face = np.zeros(aag.load_aag(workdir).face_count,
                             dtype=np.uint8)
    for panel_id, faces in info["panel_faces"].items():
        panel_by_face[faces] = panel_id + 1
    for bend_id, faces in info["bend_faces"].items():
        panel_by_face[faces] = graph.bends[bend_id].child_panel + 1
    brep_ids = np.load(os.path.join(workdir, pipeline.BREP_FACES_FILE))

    arrays = {
        "outline_lines": sheet._segments_from_points(outline_points),
        "bend_axis_lines": sheet._segments_from_points(bend_axis_points),
        "required_lines": sheet._segments_from_points(required_points),
        "forbidden_lines": sheet._segments_from_points(forbidden_points),
        "panel_id": panel_by_face[brep_ids].astype("<u1"),
    }
    field_meta = {}
    for name in ("outline_lines", "bend_axis_lines", "required_lines",
                 "forbidden_lines"):
        field_meta[name] = {"kind": "bend_plan", "association": "none",
                            "role": "lines", "dtype": "f4",
                            "length": int(arrays[name].size),
                            "segments": int(arrays[name].size // 6)}
    field_meta["panel_id"] = {
        "kind": "bend_plan_panel", "association": "face",
        "role": "category", "dtype": "u1",
        "labels": ["none"] + [f"panel {p.id}" for p in graph.panels]}

    stats = {
        "feasible": bool(search_result.feasible if search_result is not None
                         else plan_report.feasible),
        "mode": "search" if params["search"] else "plan",
        "machine": machine.name,
        "thickness": float(graph.thickness),
        "k_factor": float(params["k_factor"]),
        "z_offset": float(graph.z_offset),
        "margin": float(params["margin"]),
        "springback_deg": float(params["springback_deg"]),
        "panel_count": int(graph.panel_count),
        "bend_count": int(graph.bend_count),
        "sister_group_count": len(graph.sister_groups()),
        "graph": report_mod.dump_graph(graph),
        "actions": actions,
        "plans": ([report_mod.dump_plan(plan) for plan
                   in search_result.plans]
                  if search_result is not None else []),
        "search_stats": (dict(search_result.stats)
                         if search_result is not None else {}),
        "warnings": sorted(set(warnings)),
        "origin": [float(origin[0]), float(origin[1])],
    }

    store_result(workdir, "sheet_metal", "bend_plan", cache_params, stats,
                 arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=list(arrays))


PROCESS = ProcessDef(
    id="sheet_metal",
    label="Sheet metal",
    description="Sheet recognition (skins, thickness, bends) and K-factor "
                "unfold to a flat pattern with bend lines.",
    analyses=[
        AnalysisDef(
            id="detect",
            label="Detect sheet",
            description="Find the two sheet skins and the uniform thickness "
                        "(normal ray cast from the largest face), classify "
                        "every face as base/opposite/bend/wall, and report "
                        "a sheet / not-sheet verdict with reasons.",
            requires=["prep/aag"],
            params=[
                Param("min_thickness", "number", default=0.1, unit="mm",
                      min=0, label="Minimum sheet thickness"),
                Param("max_thickness", "number", default=None, unit="mm",
                      min=0, label="Maximum sheet thickness (blank = none)"),
            ],
            run=run_detect,
        ),
        AnalysisDef(
            id="flat_pattern",
            label="Flat pattern (unfold)",
            description="K-factor unfold of the sheet skin onto the plane: "
                        "outer contour, holes and bend lines as the flat "
                        "pattern, validated by volume conservation "
                        "(flat area x thickness vs solid volume).",
            requires=["prep/aag"],
            params=[
                Param("k_factor", "number", default=0.5, min=0, max=1,
                      label="K-factor (neutral fiber position)"),
                Param("combine_bends", "bool", default=True,
                      label="Merge C2-connected multi-face bends"),
                Param("min_thickness", "number", default=0.1, unit="mm",
                      min=0, label="Minimum sheet thickness"),
                Param("volume_tolerance", "number", default=0.025, min=0,
                      label="Volume error fraction accepted as valid"),
                Param("tollerance", "number", default=1e-1, unit="mm", min=0,
                      label="Outline gap bridged as filler"),
            ],
            run=run_flat_pattern,
        ),
        AnalysisDef(
            id="bend_plan",
            label="Bend plan (press brake)",
            description="Simulate air-bending on a press brake: per-bend "
                        "REQUIRED/FORBIDDEN tooling intervals against the "
                        "punch/die/machine catalogue, plus a bend-sequence "
                        "search with segmented tooling placement ranked by "
                        "setup changes, sections and installed length.",
            requires=["prep/aag"],
            params=[
                Param("k_factor", "number", default=0.5, min=0, max=1,
                      label="K-factor (must match the unfold allowance)"),
                Param("margin", "number", default=2.0, unit="mm", min=0,
                      label="Collision clearance margin"),
                Param("springback_deg", "number", default=2.0, unit="deg",
                      min=0, label="Springback overbend delta"),
                Param("punch_id", "string", default="",
                      label="Punch id (blank = all catalogue punches)"),
                Param("die_id", "string", default="",
                      label="Die id (blank = all catalogue dies)"),
                Param("machine_path", "string", default="",
                      label="Machine YAML (blank = bundled demo)"),
                Param("punches_path", "string", default="",
                      label="Punch catalogue YAML (blank = bundled demo)"),
                Param("dies_path", "string", default="",
                      label="Die catalogue YAML (blank = bundled demo)"),
                Param("search", "bool", default=True,
                      label="Sequence search + setup optimisation"),
                Param("solutions", "int", default=4, min=1,
                      label="Ranked plans to keep"),
                Param("min_thickness", "number", default=0.1, unit="mm",
                      min=0, label="Minimum sheet thickness"),
            ],
            run=run_bend_plan,
        ),
    ],
)
