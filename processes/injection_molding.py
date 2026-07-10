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

import pipeline
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            load_cached_result, store_result)

ASSIGNMENT_OPTIONS = 3  # options that get per-face assignment fields
MOLD_SCHEMA = 2  # result schema version, salted into the cache key


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
    ],
)
