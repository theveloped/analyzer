"""CNC 3-axis machining process: tool-field precompute and composition."""

import pipeline
from processes.base import AnalysisDef, AnalysisResult, Param, ProcessDef


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
        engine=params["engine"], window=params["window"], progress=progress)
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
        engine=params["engine"], window=params["window"], progress=progress)
    stats = {key: result[key] for key in ("unreachable", "accessible", "sweep")}
    return AnalysisResult(stats=stats)


PROCESS = ProcessDef(
    id="cnc",
    label="CNC machining (3-axis)",
    description="Tool reachability: tip gap, holder clearance and stickout fields per approach direction.",
    analyses=[
        AnalysisDef(
            id="precompute",
            label="Precompute tool fields",
            description="Cache height maps and per-tip/per-clearance fields for fast interactive composition.",
            requires=["prep/directions"],
            params=[
                Param("directions", "int_list", default=[4],
                      label="Direction indices"),
                Param("pixel", "number", default=1e-1, unit="mm", min=0,
                      label="Height map pixel size"),
                Param("tips", "tip_list", default=[{"diameter": 6, "corner_radius": 0}],
                      label="Tool tips (diameter : corner radius)"),
                Param("clearances", "number_list", default=[], unit="mm",
                      label="Holder/shank clearance radii"),
                Param("engine", "select", default="zmap", options=["zmap", "voxel"],
                      label="Field engine"),
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
                Param("pixel", "number", default=1e-1, unit="mm", min=0,
                      label="Height map pixel size"),
                Param("engine", "select", default="zmap", options=["zmap", "voxel"],
                      label="Field engine"),
                Param("window", "number", default=0.3, unit="mm", min=0,
                      label="Exact gap window"),
            ],
            run=run_compose,
        ),
    ],
)
