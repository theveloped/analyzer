"""Injection molding process.

mold_orientation ranks mold orientations (antipodal plate pair + greedy
perpendicular slides) and stores per-face feature membership bitmasks,
whole-BREP-face validity/defaults and numbered internal undercut regions —
the assignment/parting-line choice is made per BREP face in the viewer.
thickness and gaps are per-vertex rolling inscribed-sphere fields (gaps =
the same measure on the orientation-flipped mesh, i.e. the exterior
clearance between opposing walls).
"""

import numpy as np

import gating
import pipeline
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            load_cached_result, load_result_arrays,
                            params_hash, store_result)

ASSIGNMENT_OPTIONS = 3  # options that get per-face assignment fields
MOLD_SCHEMA = 2  # result schema version, salted into the cache key
SPRUE_SCHEMA = 2  # sprue_proposals schema version, salted into the cache key
SKELETON_SCHEMA = 3  # wall_skeleton schema (3: absorption + mesh spec)
EJECTION_SCHEMA = 2  # ejection_sticking schema version, cache salt

SKELETON_PARAMS = ("max_radius", "min_radius", "cluster_factor",
                   "absorb_factor")


def _field_stats(values, max_radius):
    cap = 2.0 * max_radius
    return {
        "max_radius": max_radius,
        "cap": cap,
        "verts": int(values.size),
        "min": float(values.min()),
        "mean": float(values.mean()),
        "p05": float(np.percentile(values, 5)),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "saturated_fraction": float(np.mean(values >= cap * (1 - 1e-4))),
    }


def _run_sphere_field(workdir, analysis_id, member, kind, inverted, params,
                      progress):
    cached = load_cached_result(workdir, "injection_molding", analysis_id, params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"], fields=list(cached["arrays"]))

    values, max_radius = pipeline.compute_thickness(
        workdir, max_radius=params["max_radius"], inverted=inverted,
        progress=progress)

    stats = _field_stats(values, max_radius)
    field_meta = {member: {"kind": kind, "association": "vertex",
                           "role": "scalar", "units": "mm",
                           "max_radius": max_radius}}
    store_result(workdir, "injection_molding", analysis_id, params, stats,
                 arrays={member: values}, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=[member])


def run_thickness(workdir, params, progress):
    return _run_sphere_field(workdir, "thickness", "thickness", "thickness",
                             False, params, progress)


def run_gaps(workdir, params, progress):
    return _run_sphere_field(workdir, "gaps", "gap", "gap",
                             True, params, progress)


def run_mold_orientation(workdir, params, progress):
    cache_params = {**params, "schema": MOLD_SCHEMA}
    cached = load_cached_result(workdir, "injection_molding",
                                "mold_orientation", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    result = pipeline.mold_orientation(
        workdir, max_slides=params["max_slides"],
        slide_tollerance=params["slide_tollerance"], count=params["count"],
        min_slide_faces=params["min_slide_faces"],
        field_options=ASSIGNMENT_OPTIONS, progress=progress)

    store_result(workdir, "injection_molding", "mold_orientation", cache_params,
                 result["stats"], arrays=result["arrays"],
                 field_meta=result["field_meta"])
    return AnalysisResult(stats=result["stats"], fields=list(result["arrays"]))


def skeleton_cache_params(params):
    """wall_skeleton cache key: declared params + schema salt."""
    return {**{name: params[name] for name in SKELETON_PARAMS},
            "schema": SKELETON_SCHEMA}


def run_wall_skeleton(workdir, params, progress):
    cache_params = skeleton_cache_params(params)
    cached = load_cached_result(workdir, "injection_molding",
                                "wall_skeleton", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    stats, arrays, field_meta = pipeline.wall_skeleton(
        workdir, max_radius=params["max_radius"],
        min_radius=params["min_radius"],
        cluster_factor=params["cluster_factor"],
        absorb_factor=params["absorb_factor"], progress=progress)

    store_result(workdir, "injection_molding", "wall_skeleton", cache_params,
                 stats, arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=list(arrays))


def run_sprue_proposals(workdir, params, progress):
    cache_params = {**params, "schema": SPRUE_SCHEMA}
    cached = load_cached_result(workdir, "injection_molding",
                                "sprue_proposals", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    def scaled(lo, hi):
        if progress is None:
            return None
        return lambda f, m: progress(lo + (hi - lo) * f, m)

    # the skeleton is a cache-aware sub-run: shared params -> shared result
    skel_result = run_wall_skeleton(workdir, params, scaled(0.0, 0.4))
    skel_cache = skeleton_cache_params(params)
    skeleton = load_result_arrays(workdir, "injection_molding",
                                  "wall_skeleton", skel_cache)

    weights = {name: params[f"w_{name}"] for name in gating.METRICS}
    stats, arrays, field_meta = pipeline.sprue_proposals(
        workdir, skeleton=skeleton, skeleton_hash=params_hash(skel_cache),
        mesh_spec=skel_result.stats.get("mesh"),
        min_gate_thickness=params["min_gate_thickness"],
        max_candidates=params["max_candidates"],
        thick_percentile=params["thick_percentile"],
        pack_factor=params["pack_factor"],
        edge_gate_distance=params["edge_gate_distance"],
        forbid_side=params["forbid_side"],
        orientation_option=params["orientation_option"],
        top_n=params["top_n"], weights=weights, progress=scaled(0.4, 1.0))

    store_result(workdir, "injection_molding", "sprue_proposals",
                 cache_params, stats, arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=list(arrays))


def run_ejection_sticking(workdir, params, progress):
    cache_params = {**params, "schema": EJECTION_SCHEMA}
    cached = load_cached_result(workdir, "injection_molding",
                                "ejection_sticking", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    def scaled(lo, hi):
        if progress is None:
            return None
        return lambda f, m: progress(lo + (hi - lo) * f, m)

    # the skeleton is a cache-aware sub-run: shared params -> shared result
    skel_result = run_wall_skeleton(workdir, params, scaled(0.0, 0.6))
    skel_cache = skeleton_cache_params(params)
    skeleton = load_result_arrays(workdir, "injection_molding",
                                  "wall_skeleton", skel_cache)

    stats, arrays, field_meta = pipeline.ejection_sticking(
        workdir, skeleton=skeleton, skeleton_hash=params_hash(skel_cache),
        mesh_spec=skel_result.stats.get("mesh"),
        grip_deg=params["grip_deg"], mu=params["mu"],
        p_shrink=params["p_shrink"],
        orientation_option=params["orientation_option"],
        progress=scaled(0.6, 1.0))

    store_result(workdir, "injection_molding", "ejection_sticking",
                 cache_params, stats, arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=list(arrays))


PROCESS = ProcessDef(
    id="injection_molding",
    label="Injection molding",
    description="Parting direction and slide selection over the shared accessibility matrix.",
    analyses=[
        AnalysisDef(
            id="mold_orientation",
            label="Mold orientation",
            description="Main pull axis + greedy perpendicular slides; per-face side assignment, internal undercuts and the parting line.",
            requires=["prep/directions"],
            params=[
                Param("max_slides", "int", default=2, min=0, label="Max slides"),
                Param("slide_tollerance", "number", default=2.0, unit="deg",
                      min=0, label="Slide perpendicularity tolerance"),
                Param("count", "int", default=10, min=1,
                      label="Ranked options in stats"),
                Param("min_slide_faces", "int", default=50, min=1,
                      label="Min faces a slide must gain"),
            ],
            run=run_mold_orientation,
        ),
        AnalysisDef(
            id="thickness",
            label="Wall thickness",
            description="Maximal inscribed (rolling) sphere diameter per vertex — local wall thickness.",
            requires=["prep/mesh"],
            params=[
                Param("max_radius", "number", default=None, unit="mm", min=0,
                      label="Max sphere radius (blank = auto from bbox)"),
            ],
            run=run_thickness,
        ),
        AnalysisDef(
            id="gaps",
            label="Wall gaps / clearance",
            description="Rolling sphere on the inverted shape: the gap between opposing outside walls per vertex.",
            requires=["prep/mesh"],
            params=[
                Param("max_radius", "number", default=None, unit="mm", min=0,
                      label="Max sphere radius (blank = auto from bbox)"),
            ],
            run=run_gaps,
        ),
        AnalysisDef(
            id="wall_skeleton",
            label="Wall thickness skeleton",
            description="Inscribed-sphere wall thickness plus a medial skeleton graph for fill-flow estimation.",
            requires=[],
            params=[
                Param("max_radius", "number", default=5.0, unit="mm",
                      label="Max sphere radius"),
                Param("min_radius", "number", default=0.1, unit="mm",
                      label="Min node radius"),
                Param("cluster_factor", "number", default=1.0, min=0.1,
                      label="Cluster radius factor"),
                Param("absorb_factor", "number", default=0.5, min=0,
                      label="Rim absorption ratio (0 = off)"),
            ],
            run=run_wall_skeleton,
        ),
        AnalysisDef(
            id="sprue_proposals",
            label="Sprue / gate proposals",
            description="Ranked automatic injection gate locations: skeleton fill screening, moldability filters and an explainable score.",
            requires=[],  # skeleton computed internally; mold_orientation optional
            params=[
                Param("max_radius", "number", default=5.0, unit="mm",
                      label="Max sphere radius (skeleton)"),
                Param("min_radius", "number", default=0.1, unit="mm",
                      label="Min node radius (skeleton)"),
                Param("cluster_factor", "number", default=1.0, min=0.1,
                      label="Cluster radius factor (skeleton)"),
                Param("absorb_factor", "number", default=0.5, min=0,
                      label="Rim absorption ratio (skeleton)"),
                Param("min_gate_thickness", "number", default=0.8, unit="mm",
                      min=0, label="Min wall thickness at the gate"),
                Param("max_candidates", "int", default=400, min=10,
                      label="Max screened candidates"),
                Param("thick_percentile", "number", default=85.0, min=50,
                      max=100, label="Thick-region volume percentile"),
                Param("pack_factor", "number", default=0.5, min=0.05,
                      label="Packing channel factor (× thick radius)"),
                Param("edge_gate_distance", "number", default=5.0, unit="mm",
                      min=0, label="Edge-gate distance to parting line"),
                Param("forbid_side", "select", default="none",
                      options=["none", "A", "B"], label="Forbidden gate side"),
                Param("orientation_option", "int", default=0, min=0,
                      max=ASSIGNMENT_OPTIONS - 1,
                      label="Mold orientation option"),
                Param("top_n", "int", default=10, min=1,
                      label="Proposals to return"),
                Param("w_pressure", "number", default=2.0, min=0,
                      label="Weight: fill pressure (p95)"),
                Param("w_fill_max", "number", default=1.0, min=0,
                      label="Weight: worst-case flow path"),
                Param("w_unreached", "number", default=3.0, min=0,
                      label="Weight: unreached volume"),
                Param("w_balance", "number", default=1.0, min=0,
                      label="Weight: fill balance"),
                Param("w_packing", "number", default=2.5, min=0,
                      label="Weight: thick-region packing access"),
                Param("w_weld", "number", default=1.0, min=0,
                      label="Weight: weld-line indicator"),
                Param("w_airtrap", "number", default=0.5, min=0,
                      label="Weight: air-trap indicator"),
            ],
            run=run_sprue_proposals,
        ),
        AnalysisDef(
            id="ejection_sticking",
            label="Ejection sticking",
            description="Draft-scaled mold sticking forces per face and per skeleton node — the loads the interactive ejector-pin simulation solves against.",
            requires=[],  # skeleton computed internally; mold_orientation optional
            params=[
                Param("max_radius", "number", default=5.0, unit="mm",
                      label="Max sphere radius (skeleton)"),
                Param("min_radius", "number", default=0.1, unit="mm",
                      label="Min node radius (skeleton)"),
                Param("cluster_factor", "number", default=1.0, min=0.1,
                      label="Cluster radius factor (skeleton)"),
                Param("absorb_factor", "number", default=0.5, min=0,
                      label="Rim absorption ratio (skeleton)"),
                Param("grip_deg", "number", default=15.0, unit="deg", min=0,
                      max=90, label="Grip draft threshold"),
                Param("mu", "number", default=0.5, min=0,
                      label="Steel-polymer friction coefficient"),
                Param("p_shrink", "number", default=0.5, unit="MPa", min=0,
                      label="Shrinkage contact pressure"),
                Param("orientation_option", "int", default=0, min=0,
                      max=ASSIGNMENT_OPTIONS - 1,
                      label="Mold orientation option"),
            ],
            run=run_ejection_sticking,
        ),
    ],
)
