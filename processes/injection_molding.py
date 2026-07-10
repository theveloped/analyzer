"""Injection molding process.

parting_directions wraps the setup/parting direction search and stores
per-option face coverage masks; thickness and gaps are per-vertex rolling
inscribed-sphere fields (gaps = the same measure on the orientation-flipped
mesh, i.e. the exterior clearance between opposing walls).
"""

import os

import numpy as np

import pipeline
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            load_cached_result, params_hash, store_result)

COVERAGE_OPTIONS = 3  # face masks stored for the top-ranked options


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


def run_parting_directions(workdir, params, progress):
    cached = load_cached_result(workdir, "injection_molding",
                                "parting_directions", params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    result = pipeline.parting_options(
        workdir, slides=params["slides"], count=params["count"],
        slide_tollerance=params["slide_tollerance"], relax=params["relax"],
        relax_tollerance=params["relax_tollerance"],
        relax_samples=params["relax_samples"], progress=progress)

    # face coverage mask per top option: which faces the option's direction
    # union can reach (the old `serve --include` union, now a cached field)
    accessibility = np.load(os.path.join(workdir, pipeline.ACCESSIBILITY_FILE))
    arrays, field_meta = {}, {}
    for rank, option in enumerate(result["options"][:COVERAGE_OPTIONS]):
        mask = np.any(accessibility[option["directions"], :], axis=0)
        name = f"option_{rank}"
        arrays[name] = mask.astype("u1")
        field_meta[name] = {
            "kind": "parting_coverage",
            "association": "face",
            "role": "mask",
            "option": rank,
            "directions": option["directions"],
            "coverage": option["coverage"],
        }

    store_result(workdir, "injection_molding", "parting_directions", params,
                 result, arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=result, fields=list(arrays))


PROCESS = ProcessDef(
    id="injection_molding",
    label="Injection molding",
    description="Parting direction and slide selection over the shared accessibility matrix.",
    analyses=[
        AnalysisDef(
            id="parting_directions",
            label="Parting directions",
            description="Rank two-sided (plus optional slides) direction combinations by face coverage.",
            requires=["prep/directions"],
            params=[
                Param("slides", "int", default=0, min=0, label="Slides"),
                Param("count", "int", default=10, min=1, label="Results to keep"),
                Param("slide_tollerance", "number", default=2e-1, unit="deg",
                      label="Slide angle tolerance"),
                Param("relax", "bool", default=False,
                      label="Relax winning directions"),
                Param("relax_tollerance", "number", default=1.0, unit="deg",
                      label="Relax tolerance"),
                Param("relax_samples", "int", default=4, label="Relax samples"),
            ],
            run=run_parting_directions,
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
