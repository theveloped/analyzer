"""Tube / profile laser process: straight constant-section profile
classification (round / rectangular / square) with an optional unroll."""

from processes import resolver
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            load_cached_result, store_result)

# keep in sync with frontend/src/processes/tubelaser/index.ts
TUBE_SCHEMA = 2


def run_profile(workdir, params, progress):
    cache_params = resolver.cache_key(workdir, "tube_laser/profile", params)
    cached = load_cached_result(workdir, "tube_laser", "profile",
                                cache_params)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))

    import tube
    result = tube.analyse_profile(
        workdir, unroll=params["unroll"], k_factor=params["k_factor"],
        progress=progress)

    store_result(workdir, "tube_laser", "profile", cache_params,
                 result["stats"], arrays=result["arrays"],
                 field_meta=result["field_meta"])
    return AnalysisResult(stats=result["stats"],
                          fields=list(result["arrays"]))


PROCESS = ProcessDef(
    id="tube_laser",
    label="Tube / profile laser",
    description="Straight constant-section profile recognition (round, "
                "rectangular, square): section dimensions, wall thickness "
                "and length, plus the unrolled cut pattern.",
    analyses=[
        AnalysisDef(
            id="profile",
            label="Profile section",
            description="Classify the profile from the two shells around "
                        "the largest face; report section dimensions and "
                        "optionally unroll the outer shell into the flat "
                        "cut pattern.",
            requires=["prep/mesh", "prep/aag"],
            params=[
                Param("unroll", "bool", default=True,
                      label="Unroll the outer shell (cut pattern)"),
                Param("k_factor", "number", default=0.5, min=0, max=1,
                      label="K-factor (neutral fiber position)"),
            ],
            run=run_profile,
            schema=TUBE_SCHEMA,
        ),
    ],
)
