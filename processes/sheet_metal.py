"""Sheet metal process: recognition/roles (detect) and K-factor unfold
(flat_pattern) over the AAG stage artifact."""

import pipeline
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            load_cached_result, store_result)

# keep in sync with frontend/src/processes/sheetmetal/index.ts
SHEET_SCHEMA = 1


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
    ],
)
