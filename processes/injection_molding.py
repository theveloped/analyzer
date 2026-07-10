"""Injection molding process (skeleton with one real analysis).

parting_directions wraps the existing setup/parting direction search and
stores per-option face coverage masks so the viewer can paint them.
"""

import os

import numpy as np

import pipeline
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            load_cached_result, params_hash, store_result)

COVERAGE_OPTIONS = 3  # face masks stored for the top-ranked options


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


def run_wall_skeleton(workdir, params, progress):
    cached = load_cached_result(workdir, "injection_molding",
                                "wall_skeleton", params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    stats, arrays, field_meta = pipeline.wall_skeleton(
        workdir, max_radius=params["max_radius"],
        min_radius=params["min_radius"],
        cluster_factor=params["cluster_factor"], progress=progress)

    store_result(workdir, "injection_molding", "wall_skeleton", params,
                 stats, arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=list(arrays))


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
            ],
            run=run_wall_skeleton,
        ),
    ],
)
