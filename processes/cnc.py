"""CNC machining process: setup-combination search over the shared
accessibility matrix, plus tool-field precompute and composition."""

import pipeline
from processes import resolver
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            load_cached_result, store_result)

SETUPS_SCHEMA = 3  # result schema version, salted into the cache key
FEATURES_SCHEMA = 2  # keep in sync with frontend/src/processes/cnc/features.ts
REACH_STUDY_SCHEMA = 1  # keep in sync with frontend/src/processes/cnc/reach.ts
TURNING_SCHEMA = 2  # keep in sync with frontend/src/processes/cnc/turning.tsx
TURNING_SCAN_SCHEMA = 2  # no frontend mirror: the scan is read per result hash
HULL_SCHEMA = 1  # keep in sync with frontend/src/processes/cnc/hull.ts

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
    cache_params = resolver.cache_key(workdir, "cnc/setups", params)
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
    # rides in the setups store dir (the frontend reads both from cnc/setups,
    # distinguished by stats.verdict); key_extra={"verdict":1} keeps it distinct
    cache_params = resolver.cache_key(workdir, "cnc/setup_verdict", params)
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
    cache_params = resolver.cache_key(workdir, "cnc/features", params)
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


def run_turning(workdir, params, progress):
    cache_params = resolver.cache_key(workdir, "cnc/turning", params)
    cached = load_cached_result(workdir, "cnc", "turning", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    import turning
    result = turning.analyse_turning(
        workdir, tollerance=params["tollerance"],
        profile_bins=params["profile_bins"],
        refine_rounds=params["refine_rounds"],
        max_candidates=params["max_candidates"],
        sample_faces=params["sample_faces"],
        axis_override=params["axis_override"], progress=progress)

    store_result(workdir, "cnc", "turning", cache_params, result["stats"],
                 arrays=result["arrays"], field_meta=result["field_meta"])
    return AnalysisResult(stats=result["stats"],
                          fields=list(result["arrays"]))


def run_turning_scan(workdir, params, progress):
    cache_params = resolver.cache_key(workdir, "cnc/turning_scan", params)
    cached = load_cached_result(workdir, "cnc", "turning_scan", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    import turning
    result = turning.scan_axes(
        workdir, axis_vectors=params["axis_vectors"] or [],
        tollerance=params["tollerance"], progress=progress)

    store_result(workdir, "cnc", "turning_scan", cache_params, result["stats"],
                 arrays=result["arrays"], field_meta=result["field_meta"])
    return AnalysisResult(stats=result["stats"],
                          fields=list(result["arrays"]))


def run_reach_study(workdir, params, progress):
    cache_params = resolver.cache_key(workdir, "cnc/reach_study", params)
    cached = load_cached_result(workdir, "cnc", "reach_study", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    result = pipeline.reach_study(
        workdir,
        directions=[int(i) for i in params["direction_indices"] or []],
        tools=pipeline.parse_tools(params["tools"]),
        tollerance=params["tollerance"],
        wall_tollerance=params["wall_tollerance"], pixel=params["pixel"],
        window=params["window"], progress=progress)

    store_result(workdir, "cnc", "reach_study", cache_params,
                 result["stats"], arrays=result["arrays"],
                 field_meta=result["field_meta"])
    return AnalysisResult(stats=result["stats"],
                          fields=list(result["arrays"]))


def run_hull(workdir, params, progress):
    cache_params = resolver.cache_key(workdir, "cnc/hull", params)
    cached = load_cached_result(workdir, "cnc", "hull", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    result = pipeline.convex_hull_faces(
        workdir, tollerance=params["tollerance"], progress=progress)

    store_result(workdir, "cnc", "hull", cache_params, result["stats"],
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
            # results are per BREP FACE, so this needs the BREP and nothing
            # else — the coarse preview is enough to see them on, and the
            # viewer joins the ids to whichever mesh it is showing
            description="Rule-based machining features from the BREP "
                        "adjacency graph: through/blind holes, counterbores, "
                        "countersinks (coaxial cylinder/cone stacks) and "
                        "best-effort pockets, with diameters, depths and axes.",
            requires=["prep/mesh_coarse", "prep/aag"],
            params=[
                Param("axis_angle_tol", "number", default=1.0, unit="deg",
                      min=0, label="Coaxiality angle tolerance"),
                Param("axis_dist_tol", "number", default=1e-2, unit="mm",
                      min=0, label="Coaxiality axis distance tolerance"),
                Param("include_pockets", "bool", default=True,
                      label="Emit best-effort pockets"),
            ],
            run=run_features,
            schema=FEATURES_SCHEMA,
        ),
        AnalysisDef(
            id="turning",
            label="Turned state",
            # requires only prep/mesh: everything consumed (brep_faces.npy,
            # brep_meta.json, brep_edge_pairs.npy, normals.npy) is written by
            # mesh_part, and demanding prep/aag would make the resolver
            # auto-run it on an STL part, where compute_aag raises
            description="Best-fit turning axis and the maximal turned state "
                        "(the smallest solid of revolution containing the "
                        "part): outer profile, stock and per-face turning "
                        "roles — OD turning, facing, boring — with the "
                        "remainder grouped as milled regions.",
            requires=["prep/mesh"],
            params=[
                Param("tollerance", "number", default=None, unit="deg", min=0,
                      label="Revolution angle tolerance (blank = 1° STEP / 5° STL)"),
                Param("profile_bins", "int", default=512, min=16,
                      label="Profile bins along the axis"),
                Param("refine_rounds", "int", default=3, min=0,
                      label="Axis refinement rounds"),
                Param("max_candidates", "int", default=12, min=1,
                      label="Candidate axes scored"),
                Param("sample_faces", "int", default=100000, min=0,
                      label="Faces sampled for the axis search (0 = all)"),
                Param("axis_override", "number_list", default=[],
                      label="Force the axis: px py pz dx dy dz (blank = search)"),
            ],
            run=run_turning,
            schema=TURNING_SCHEMA,
            # roles are voted per EFFECTIVE face, so a user cut changes the
            # answer and must orphan the old result (as cnc/setups does)
            salts=("splits",),
        ),
        AnalysisDef(
            id="turning_scan",
            label="Turnability by axis",
            description="Score candidate directions as turning axes: the "
                        "share of area that is revolution-compatible and the "
                        "share actually swept. Ranks candidates for the "
                        "directions overview — it does not certify one; "
                        "cnc/turning is the full answer for a chosen axis.",
            # vectors in, so this needs nothing but the mesh — asking whether
            # an axis is turnable must not wait on an accessibility run
            requires=["prep/mesh"],
            params=[
                Param("axis_vectors", "vector_list", default=[],
                      label="Candidate axes (x:y:z, …)"),
                Param("tollerance", "number", default=None, unit="deg", min=0,
                      label="Revolution angle tolerance "
                            "(blank = 1° STEP / 5° STL)"),
            ],
            run=run_turning_scan,
            schema=TURNING_SCAN_SCHEMA,
        ),
        AnalysisDef(
            id="hull",
            label="Convex hull faces",
            description="Faces lying on the part's convex hull — the "
                        "surface an infinitely large mill can machine "
                        "directly from outside.",
            requires=["prep/mesh"],
            params=[
                Param("tollerance", "number", default=None, unit="mm", min=0,
                      label="On-hull distance tolerance "
                            "(blank = from mesh deflection)"),
            ],
            run=run_hull,
            schema=HULL_SCHEMA,
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
            schema=SETUPS_SCHEMA,
            salts=("splits",),
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
            schema=SETUPS_SCHEMA,
            salts=("splits",),
            key_extra={"verdict": 1},
        ),
        AnalysisDef(
            id="reach_study",
            label="Reachability study",
            description="Per-face machinable masks for every (candidate "
                        "direction × tool) pair — the reusable exploration "
                        "computation operation-scoped checks slice into "
                        "per-op and aggregate views without recomputing.",
            requires=["prep/directions"],
            params=[
                # NOT named "directions": prep salt fields would collide with
                # a declared param of that name in the cache key (resolver
                # guards against it)
                Param("direction_indices", "int_list", default=[],
                      label="Direction indices (blank = all sampled)"),
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
            ],
            run=run_reach_study,
            schema=REACH_STUDY_SCHEMA,
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
