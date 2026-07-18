"""CNC machining process: setup-combination search over the shared
accessibility matrix, plus tool-field precompute and composition."""

import pipeline
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            load_cached_result, store_result)

SETUPS_SCHEMA = 3  # result schema version, salted into the cache key
FEATURES_SCHEMA = 1  # keep in sync with frontend/src/processes/cnc/features.ts

# default library: 3 flat endmills + 2 ball mills, each at its longest
# practical reach (stickout 5xD) with the shank as the holder cylinder
DEFAULT_TOOLS = [
    {"diameter": 16.0, "corner_radius": 0.0, "stickout": 80.0, "holder_radius": 8.0},
    {"diameter": 8.0, "corner_radius": 0.0, "stickout": 40.0, "holder_radius": 4.0},
    {"diameter": 4.0, "corner_radius": 0.0, "stickout": 20.0, "holder_radius": 2.0},
    {"diameter": 10.0, "corner_radius": 5.0, "stickout": 50.0, "holder_radius": 5.0},
    {"diameter": 4.0, "corner_radius": 2.0, "stickout": 20.0, "holder_radius": 2.0},
]


def run_setups(workdir, params, progress):
    cache_params = {**params, "schema": SETUPS_SCHEMA,
                    "directions": pipeline.directions_fingerprint(workdir),
                    "accessibility": pipeline.accessibility_fingerprint(workdir),
                    "mesh": pipeline.mesh_fingerprint(workdir),
                    "splits": pipeline.splits_fingerprint(workdir)}
    cached = load_cached_result(workdir, "cnc", "setups", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    result = pipeline.cnc_setups(
        workdir, indexed=params["indexed"], tilt=params["tilt"],
        max_setups=params["max_setups"],
        min_setup_area=params["min_setup_area"], count=params["count"],
        field_options=params["field_options"], progress=progress)

    store_result(workdir, "cnc", "setups", cache_params, result["stats"],
                 arrays=result["arrays"], field_meta=result["field_meta"])
    return AnalysisResult(stats=result["stats"], fields=list(result["arrays"]))


def run_setup_verdict(workdir, params, progress):
    cache_params = {**params, "schema": SETUPS_SCHEMA, "verdict": 1,
                    "directions": pipeline.directions_fingerprint(workdir),
                    "accessibility": pipeline.accessibility_fingerprint(workdir),
                    "mesh": pipeline.mesh_fingerprint(workdir),
                    "splits": pipeline.splits_fingerprint(workdir)}
    cached = load_cached_result(workdir, "cnc", "setups", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    result = pipeline.setup_verdict(
        workdir, option=params["option"],
        tools=pipeline.parse_tools(params["tools"]),
        tollerance=params["tollerance"],
        wall_tollerance=params["wall_tollerance"], pixel=params["pixel"],
        window=params["window"], indexed=params["indexed"],
        tilt=params["tilt"], max_setups=params["max_setups"],
        min_setup_area=params["min_setup_area"], count=params["count"],
        field_options=params["field_options"], progress=progress)

    store_result(workdir, "cnc", "setups", cache_params, result["stats"],
                 arrays=result["arrays"], field_meta=result["field_meta"])
    return AnalysisResult(stats=result["stats"], fields=list(result["arrays"]))


def run_features(workdir, params, progress):
    cache_params = {**params, "schema": FEATURES_SCHEMA,
                    "mesh": pipeline.mesh_fingerprint(workdir),
                    "aag": pipeline.aag_fingerprint(workdir)}
    cached = load_cached_result(workdir, "cnc", "features", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    import machining_features
    result = machining_features.recognize_features(
        workdir, axis_angle_tol=params["axis_angle_tol"],
        axis_dist_tol=params["axis_dist_tol"],
        include_pockets=params["include_pockets"], progress=progress)

    store_result(workdir, "cnc", "features", cache_params, result["stats"],
                 arrays=result["arrays"], field_meta=result["field_meta"])
    return AnalysisResult(stats=result["stats"],
                          fields=list(result["arrays"]))


def _tips(params):
    """Accept tip specs as 'D:rc' strings or [D, rc] pairs."""
    tips = []
    for entry in params["tips"] or []:
        if isinstance(entry, str):
            tips.extend(pipeline.parse_tips([entry]))
        elif isinstance(entry, dict):
            tips.append((float(entry["diameter"]), float(entry.get("corner_radius", 0.0))))
        else:
            diameter, corner = entry
            tips.append((float(diameter), float(corner)))
    return tips


def run_precompute(workdir, params, progress):
    result = pipeline.precompute_fields(
        workdir, directions=[int(i) for i in params["directions"]],
        pixel=params["pixel"], tips=_tips(params),
        clearances=[float(r) for r in params["clearances"] or []],
        window=params["window"], progress=progress)
    return AnalysisResult(stats=result)


def run_compose(workdir, params, progress):
    cylinders = params["holder"]
    if isinstance(cylinders, str):
        cylinders = pipeline.parse_holder(cylinders)
    elif cylinders:
        cylinders = [(float(r), float(s)) for r, s in cylinders]
    result = pipeline.compose_tool(
        workdir, int(params["direction"]), pixel=params["pixel"],
        tollerance=params["tollerance"], diameter=params["diameter"],
        corner_radius=params["corner_radius"], stickout=params["stickout"],
        cylinders=cylinders, sweep=params["sweep"] or [],
        wall_tollerance=params["wall_tollerance"],
        window=params["window"], progress=progress)
    stats = {key: result[key] for key in ("unreachable", "accessible", "sweep")}
    return AnalysisResult(stats=stats)


PROCESS = ProcessDef(
    id="cnc",
    label="CNC machining",
    description="Setup combinations (3-axis / indexed 3+2) and tool reachability: tip gap, holder clearance and stickout fields per approach direction.",
    analyses=[
        AnalysisDef(
            id="features",
            label="Feature recognition",
            description="Rule-based machining features from the BREP "
                        "adjacency graph: through/blind holes, counterbores, "
                        "countersinks (coaxial cylinder/cone stacks) and "
                        "best-effort pockets, with diameters, depths and axes.",
            requires=["prep/aag"],
            params=[
                Param("axis_angle_tol", "number", default=1.0, unit="deg",
                      min=0, label="Coaxiality angle tolerance"),
                Param("axis_dist_tol", "number", default=1e-2, unit="mm",
                      min=0, label="Coaxiality axis distance tolerance"),
                Param("include_pockets", "bool", default=True,
                      label="Emit best-effort pockets"),
            ],
            run=run_features,
        ),
        AnalysisDef(
            id="setups",
            label="Setup combinations",
            description="Rank setup sequences that cover the part: plain 3-axis setups and indexed 5-axis (3+2) tilt-cone setups; per-BREP-face setup assignment with toggles.",
            requires=["prep/directions"],
            params=[
                Param("indexed", "bool", default=True,
                      label="Include indexed 5-axis (3+2) machine"),
                Param("tilt", "number", default=90.0, unit="deg", min=0,
                      label="3+2 head tilt cone half-angle"),
                Param("max_setups", "int", default=4, min=1,
                      label="Max setups per option"),
                Param("min_setup_area", "number", default=None, unit="mm²",
                      min=0, label="Min area a setup must gain (blank = 0.1% of part)"),
                Param("count", "int", default=10, min=1,
                      label="Ranked options in stats"),
                Param("field_options", "int", default=3, min=1,
                      label="Plans with per-face assignment fields"),
            ],
            run=run_setups,
        ),
        AnalysisDef(
            id="setup_verdict",
            label="Setup plan tool verdict",
            description="Re-verdict one ranked setup plan with a real tool library: per-setup coverage from tip gap + stickout fields; faces no tool reaches become 'lost to tooling' regions.",
            requires=["cnc/setups"],
            params=[
                Param("option", "int", default=0, min=0,
                      label="Ranked plan to verdict (index in the setups result)"),
                Param("tools", "tool_list", default=DEFAULT_TOOLS,
                      label="Tool library (D : rc : stickout : holder radius)"),
                Param("tollerance", "number", default=1e-1, unit="mm", min=0,
                      label="Gap threshold"),
                Param("wall_tollerance", "number", default=1.0, unit="deg",
                      min=0, label="Wall angle tolerance (side-milled)"),
                Param("pixel", "number", default=None, unit="mm", min=0,
                      label="Height map pixel (blank = resolution/5)"),
                Param("window", "number", default=0.3, unit="mm", min=0,
                      label="Exact gap window"),
                Param("indexed", "bool", default=True,
                      label="Include indexed 5-axis (3+2) machine"),
                Param("tilt", "number", default=90.0, unit="deg", min=0,
                      label="3+2 head tilt cone half-angle"),
                Param("max_setups", "int", default=4, min=1,
                      label="Max setups per option"),
                Param("min_setup_area", "number", default=None, unit="mm²",
                      min=0, label="Min area a setup must gain (blank = 0.1% of part)"),
                Param("count", "int", default=10, min=1,
                      label="Ranked options in stats"),
                Param("field_options", "int", default=3, min=1,
                      label="Plans with per-face assignment fields"),
            ],
            run=run_setup_verdict,
        ),
        AnalysisDef(
            id="precompute",
            label="Precompute tool fields",
            description="Cache height maps and per-tip/per-clearance fields for fast interactive composition.",
            requires=["prep/directions"],
            params=[
                Param("directions", "int_list", default=[4],
                      label="Direction indices"),
                Param("pixel", "number", default=None, unit="mm", min=0,
                      label="Height map pixel (blank = resolution/5)"),
                Param("tips", "tip_list", default=[{"diameter": 6, "corner_radius": 0}],
                      label="Tool tips (diameter : corner radius)"),
                Param("clearances", "number_list", default=[], unit="mm",
                      label="Holder/shank clearance radii"),
                Param("window", "number", default=0.3, unit="mm", min=0,
                      label="Exact gap window"),
            ],
            run=run_precompute,
        ),
        AnalysisDef(
            id="compose",
            label="Compose tool verdict",
            description="Evaluate a full tool assembly from precomputed fields; writes highlights.json for CLI parity.",
            requires=["cnc/precompute"],
            params=[
                Param("direction", "int", default=4, label="Direction index"),
                Param("diameter", "number", default=2.0, unit="mm", min=0,
                      label="Tool diameter"),
                Param("corner_radius", "number", default=0.0, unit="mm", min=0,
                      label="Corner radius (0 = flat, D/2 = ball)"),
                Param("tollerance", "number", default=1e-1, unit="mm", min=0,
                      label="Gap threshold"),
                Param("stickout", "number", default=None, unit="mm",
                      label="Stickout"),
                Param("holder", "string", default=None,
                      label="Holder cylinders radius:start,..."),
                Param("sweep", "number_list", default=[], unit="mm",
                      label="Extra stickout sweep values"),
                Param("wall_tollerance", "number", default=1.0, unit="deg",
                      min=0, label="Wall angle tolerance (side-milled)"),
                Param("pixel", "number", default=None, unit="mm", min=0,
                      label="Height map pixel (blank = resolution/5)"),
                Param("window", "number", default=0.3, unit="mm", min=0,
                      label="Exact gap window"),
            ],
            run=run_compose,
        ),
    ],
)
