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
MOLD_SCHEMA = 3  # result schema version, salted into the cache key
SPRUE_SCHEMA = 2  # sprue_proposals schema version, salted into the cache key
SKELETON_SCHEMA = 5  # wall_skeleton schema (5: unbounded-marker normalization)
EJECTION_SCHEMA = 2  # ejection_sticking schema version, cache salt
FLOW_SCHEMA = 1  # flow_voxels / flow_fill schema version, cache salt
SLENDER_SCHEMA = 1  # slenderness schema version, cache salt
SPAN_SCHEMA = 1  # thin_span schema version, cache salt

SKELETON_PARAMS = ("max_radius", "min_radius", "cluster_factor",
                   "absorb_factor")
FLOW_VOXEL_PARAMS = ("voxel",)


def _field_stats(values, max_radius, excluded):
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
        "excluded_fraction": float(np.mean(excluded)),
    }


def _run_sphere_field(workdir, analysis_id, member, kind, inverted, params,
                      progress):
    cache_params = {**params, "mesh": pipeline.mesh_fingerprint(workdir)}
    cached = load_cached_result(workdir, "injection_molding", analysis_id,
                                cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"], fields=list(cached["arrays"]))

    values, max_radius, masks = pipeline.compute_thickness(
        workdir, max_radius=params["max_radius"], inverted=inverted,
        sharp_deg=params["sharp_deg"],
        contact_angles=params["contact_angles"], progress=progress)

    excluded = pipeline.edge_excluded(values, masks["band_lo"],
                                      masks["band_hi"], masks["suspect"])
    stats = _field_stats(values, max_radius, excluded)
    stats["edge_floor"] = masks["floor"]
    stats["edge_tol"] = masks["tol"]
    data_meta = {"kind": kind, "association": "vertex", "role": "data",
                 "units": "mm"}
    field_meta = {member: {"kind": kind, "association": "vertex",
                           "role": "scalar", "units": "mm",
                           "max_radius": max_radius},
                  # edge-explainable band: readings inside [lo, hi] are what
                  # the nearest sharp edge alone would produce (excluded
                  # from thin flags); limit is the nominal 2*d*tan(Omega/2)
                  # for display (-1 = no sharp features)
                  "limit": data_meta, "band_lo": data_meta,
                  "band_hi": data_meta,
                  # penetrating-center crease mask (u1 per vertex)
                  "suspect": {"kind": kind, "association": "vertex",
                              "role": "mask", "dtype": "u1"}}
    arrays = {member: values, "limit": masks["limit"],
              "band_lo": masks["band_lo"], "band_hi": masks["band_hi"],
              "suspect": masks["suspect"].astype(np.uint8)}
    if masks["angle"] is not None:
        # separation angle per ball: wall ~180 deg, N-degree corner ~N,
        # edge ~0, saturated NaN — the contact-angle view modes' field
        arrays["contact_angle"] = masks["angle"]
        field_meta["contact_angle"] = {"kind": kind, "association": "vertex",
                                       "role": "data", "units": "deg"}
    store_result(workdir, "injection_molding", analysis_id, cache_params,
                 stats, arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=list(arrays))


def run_thickness(workdir, params, progress):
    return _run_sphere_field(workdir, "thickness", "thickness", "thickness",
                             False, params, progress)


def run_gaps(workdir, params, progress):
    return _run_sphere_field(workdir, "gaps", "gap", "gap",
                             True, params, progress)


def slenderness_cache_params(workdir, params):
    """slenderness cache key: declared params + schema/directions/
    accessibility/mesh salts (the direction index is only meaningful for one
    directions.npy, and off-half vertices are zeroed via accessibility)."""
    return {**params, "schema": SLENDER_SCHEMA,
            "directions": pipeline.directions_fingerprint(workdir),
            "accessibility": pipeline.accessibility_fingerprint(workdir),
            "mesh": pipeline.mesh_fingerprint(workdir)}


def run_slenderness(workdir, params, progress):
    cache_params = slenderness_cache_params(workdir, params)
    cached = load_cached_result(workdir, "injection_molding", "slenderness",
                                cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    ratio, width, stats = pipeline.pocket_slenderness(
        workdir, direction=params["direction"],
        max_diameter=params["max_diameter"], ladder=params["ladder"],
        progress=progress)

    field_meta = {
        "slenderness": {"kind": "slenderness", "association": "vertex",
                        "role": "scalar", "units": ""},
        # the ladder diameter that realised each vertex's max ratio — the
        # local pocket width the steel core has to fill (0 = no pocket)
        "critical_width": {"kind": "slenderness", "association": "vertex",
                           "role": "data", "units": "mm"},
    }
    arrays = {"slenderness": ratio, "critical_width": width}
    store_result(workdir, "injection_molding", "slenderness", cache_params,
                 stats, arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=list(arrays))


def span_cache_params(workdir, params):
    """thin_span cache key: declared params + schema/mesh salts (no
    directions — the field is direction-independent)."""
    return {**params, "schema": SPAN_SCHEMA,
            "mesh": pipeline.mesh_fingerprint(workdir)}


def run_thin_span(workdir, params, progress):
    cache_params = span_cache_params(workdir, params)
    cached = load_cached_result(workdir, "injection_molding", "thin_span",
                                cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    def scaled(lo, hi):
        if progress is None:
            return None
        return lambda f, m: progress(lo + (hi - lo) * f, m)

    # the thickness field is a cache-aware sub-run: default params -> the
    # result is shared with a plain thickness analysis run
    thick_params = {"max_radius": params["max_radius"], "sharp_deg": 25.0,
                    "contact_angles": False}
    run_thickness(workdir, thick_params, scaled(0.0, 0.7))
    thick_cache = {**thick_params, "mesh": pipeline.mesh_fingerprint(workdir)}
    thick_arrays = load_result_arrays(workdir, "injection_molding",
                                      "thickness", thick_cache)

    ratio, critical, stats = pipeline.thin_span(
        workdir, thickness=thick_arrays["thickness"],
        band_lo=thick_arrays["band_lo"], band_hi=thick_arrays["band_hi"],
        suspect=thick_arrays["suspect"],
        max_thickness=params["max_thickness"], ladder=params["ladder"],
        contrast=params["contrast"], max_span=params["max_span"],
        progress=scaled(0.7, 1.0))

    field_meta = {
        "span_ratio": {"kind": "thin_span", "association": "vertex",
                       "role": "scalar", "units": ""},
        # the thickness scale that realised each vertex's max ratio — the
        # support thickness the span is measured against (0 = no reading)
        "critical_thickness": {"kind": "thin_span", "association": "vertex",
                               "role": "data", "units": "mm"},
    }
    arrays = {"span_ratio": ratio, "critical_thickness": critical}
    store_result(workdir, "injection_molding", "thin_span", cache_params,
                 stats, arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=list(arrays))


def run_mold_orientation(workdir, params, progress):
    cache_params = {**params, "schema": MOLD_SCHEMA,
                    "directions": pipeline.directions_fingerprint(workdir),
                    "accessibility": pipeline.accessibility_fingerprint(workdir),
                    "mesh": pipeline.mesh_fingerprint(workdir)}
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


def skeleton_cache_params(workdir, params):
    """wall_skeleton cache key: declared params + schema/mesh salts."""
    return {**{name: params[name] for name in SKELETON_PARAMS},
            "schema": SKELETON_SCHEMA,
            "mesh": pipeline.mesh_fingerprint(workdir)}


def run_wall_skeleton(workdir, params, progress):
    cache_params = skeleton_cache_params(workdir, params)
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
    cache_params = {**params, "schema": SPRUE_SCHEMA,
                    "mesh": pipeline.mesh_fingerprint(workdir)}
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
    skel_cache = skeleton_cache_params(workdir, params)
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
    cache_params = {**params, "schema": EJECTION_SCHEMA,
                    "mesh": pipeline.mesh_fingerprint(workdir)}
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
    skel_cache = skeleton_cache_params(workdir, params)
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


def flow_voxel_cache_params(workdir, params):
    """flow_voxels cache key: the voxel params + schema/mesh salts, so
    flow_fill's extra params still share the voxel result."""
    return {**{name: params[name] for name in FLOW_VOXEL_PARAMS},
            "schema": FLOW_SCHEMA,
            "mesh": pipeline.mesh_fingerprint(workdir)}


def run_flow_voxels(workdir, params, progress):
    cache_params = flow_voxel_cache_params(workdir, params)
    cached = load_cached_result(workdir, "injection_molding",
                                "flow_voxels", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    stats, arrays, field_meta = pipeline.flow_voxels(
        workdir, voxel=params["voxel"], progress=progress)

    store_result(workdir, "injection_molding", "flow_voxels", cache_params,
                 stats, arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=list(arrays))


def run_flow_fill(workdir, params, progress):
    if not params.get("gate"):
        raise ValueError("flow_fill needs a gate point: gate = [x, y, z] "
                         "(click the part in the flow fill view or pass "
                         "--gate on the CLI)")
    cache_params = {**params, "schema": FLOW_SCHEMA,
                    "mesh": pipeline.mesh_fingerprint(workdir)}
    cached = load_cached_result(workdir, "injection_molding",
                                "flow_fill", cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    def scaled(lo, hi):
        if progress is None:
            return None
        return lambda f, m: progress(lo + (hi - lo) * f, m)

    # the voxel grid is a cache-aware sub-run: shared params -> shared result
    voxel_result = run_flow_voxels(workdir, params, scaled(0.0, 0.4))
    voxel_cache = flow_voxel_cache_params(workdir, params)
    voxels = load_result_arrays(workdir, "injection_molding", "flow_voxels",
                                voxel_cache)

    stats, arrays, field_meta = pipeline.flow_fill(
        workdir, voxels=voxels, grid=voxel_result.stats["grid"],
        voxels_hash=params_hash(voxel_cache),
        gate=params["gate"], delta0=params["delta0"],
        skin_coef=params["skin_coef"], fill_time=params["fill_time"],
        iterations=params["iterations"],
        neighborhood=int(params["neighborhood"]),
        resolution_spec=voxel_result.stats.get("resolution"),
        progress=scaled(0.4, 1.0))

    store_result(workdir, "injection_molding", "flow_fill", cache_params,
                 stats, arrays=arrays, field_meta=field_meta)
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
                Param("sharp_deg", "number", default=25.0, unit="deg",
                      min=0, max=90,
                      label="Sharp edge threshold (0 = no exclusions)"),
                Param("contact_angles", "bool", default=False,
                      label="Store contact angles"),
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
                Param("sharp_deg", "number", default=25.0, unit="deg",
                      min=0, max=90,
                      label="Sharp edge threshold (0 = no exclusions)"),
                Param("contact_angles", "bool", default=False,
                      label="Store contact angles"),
            ],
            run=run_gaps,
        ),
        AnalysisDef(
            id="thin_span",
            label="Thin span / stiffness proxy",
            description="Distance to supporting thick material over local thickness scale, direction-free — long thin bridges and large unsupported panels read high (bending compliance ~ ratio^3).",
            requires=["prep/mesh"],
            params=[
                Param("max_radius", "number", default=None, unit="mm", min=0,
                      label="Max sphere radius (thickness, blank = auto)"),
                Param("max_thickness", "number", default=None, unit="mm",
                      min=0,
                      label="Max support thickness (blank = auto p99)"),
                Param("ladder", "number", default=1.5, min=1.05, max=2.0,
                      label="Thickness sweep step (finer = smoother, slower)"),
                Param("contrast", "number", default=1.5, min=1.1, max=3.0,
                      label="Support contrast (support >= this x own thickness)"),
                Param("max_span", "number", default=None, unit="mm", min=0,
                      label="Span saturation (blank = bbox diagonal)"),
            ],
            run=run_thin_span,
        ),
        AnalysisDef(
            id="slenderness",
            label="Steel slenderness (pocket depth/width)",
            description="Pocket depth/width ratio along one pull direction — the slenderness of the mold-steel core each pocket needs (thin steel above ~2-3).",
            requires=["prep/directions"],
            params=[
                Param("direction", "int", default=4, min=0,
                      label="Pull direction index"),
                Param("max_diameter", "number", default=None, unit="mm",
                      min=0,
                      label="Max pocket width (blank = auto from bbox)"),
                Param("ladder", "number", default=1.5, min=1.05, max=2.0,
                      label="Width sweep step (finer = smoother, slower)"),
            ],
            run=run_slenderness,
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
            id="flow_voxels",
            label="Flow voxels (SDF)",
            description="Signed-distance voxelization of the part interior — the mesh-independent basis for fill, freeze-off and cooling estimates.",
            requires=[],
            params=[
                Param("voxel", "number", default=None, unit="mm", min=0.05,
                      label="Voxel size (blank = auto from resolution)"),
            ],
            run=run_flow_voxels,
        ),
        AnalysisDef(
            id="flow_fill",
            label="Flow fill (voxel)",
            description="Gate-seeded Hele-Shaw fill over the voxel grid with frozen-skin hesitation — arrival times, weld/short-shot risk regions.",
            requires=[],  # flow_voxels computed internally
            params=[
                Param("voxel", "number", default=None, unit="mm", min=0.05,
                      label="Voxel size (blank = auto from resolution)"),
                Param("gate", "number_list", default=None,
                      label="Gate point x, y, z (click in the fill view)"),
                Param("delta0", "number", default=0.0, unit="mm", min=0,
                      label="Initial skin thickness"),
                Param("skin_coef", "number", default=0.12,
                      unit="mm/sqrt(s)", min=0,
                      label="Frozen-skin growth coefficient (0 = off)"),
                Param("fill_time", "number", default=2.0, unit="s", min=0.01,
                      label="Nominal fill time"),
                Param("iterations", "int", default=3, min=1, max=8,
                      label="Frozen-skin fixed-point passes"),
                Param("neighborhood", "select", default="26",
                      options=["26", "6"],
                      label="Grid neighborhood (26 = isotropic, 6 = fast)"),
            ],
            run=run_flow_fill,
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
