"""Part preparation pseudo-process: stages shared by every real process."""

import os

import pipeline
from processes.base import AnalysisDef, AnalysisResult, Param, ProcessDef


def find_source(workdir):
    """Locate the part's original STEP/STL inside its working directory."""
    import json
    meta_path = os.path.join(workdir, "part.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            source = json.load(f).get("source")
        if source:
            path = source if os.path.isabs(source) else os.path.join(workdir, source)
            if os.path.exists(path):
                return path
    for name in sorted(os.listdir(workdir)):
        if os.path.splitext(name)[1].lower() in pipeline.MESH_EXTENSIONS:
            return os.path.join(workdir, name)
    return None


def run_mesh(workdir, params, progress):
    source = find_source(workdir)
    if source is None:
        raise FileNotFoundError(
            "no source STEP/STL found in the working directory; upload one first")
    result = pipeline.mesh_part(
        source, workdir, heal=params["heal"], subdivide=params["subdivide"],
        offset=params["offset"], tollerance=params["tollerance"],
        deflection=params["deflection"], progress=progress)
    return AnalysisResult(stats=result["counts"])


def run_directions(workdir, params, progress):
    result = pipeline.compute_directions(
        workdir, count=params["count"], axes=params["axes"],
        tollerance=params["tollerance"], pixel=params["pixel"],
        relax=params["relax"], relax_tollerance=params["relax_tollerance"],
        relax_samples=params["relax_samples"], progress=progress)
    return AnalysisResult(stats=result)


PROCESS = ProcessDef(
    id="prep",
    label="Part preparation",
    description="Mesh canonicalization and approach-direction sampling shared by all processes.",
    analyses=[
        AnalysisDef(
            id="mesh",
            label="Mesh / heal",
            description="Load the STEP/STL and store the canonical mesh with stable face indexing.",
            requires=[],
            params=[
                Param("heal", "bool", default=False,
                      label="Heal (voxel remesh, for dirty STL)"),
                Param("subdivide", "number", default=None, unit="mm", min=0,
                      label="Subdivide max edge length (clean STEP)"),
                Param("offset", "number", default=None, unit="mm",
                      label="Offset before storing"),
                Param("tollerance", "number", default=1e-1, min=0,
                      label="Voxel tolerance"),
                Param("deflection", "number", default=0.5, unit="mm", min=0,
                      label="BREP tessellation deflection (STEP)"),
            ],
            run=run_mesh,
        ),
        AnalysisDef(
            id="directions",
            label="Approach directions",
            description="Sample sphere directions and compute per-direction accessibility.",
            requires=["prep/mesh"],
            params=[
                Param("count", "int", default=64, min=1, label="Direction count"),
                Param("axes", "bool", default=True,
                      label="Prepend principal ±X/±Y/±Z"),
                Param("tollerance", "number", default=0.1, unit="deg", min=0,
                      label="Wall relaxation tolerance"),
                Param("pixel", "number", default=None, unit="mm", min=0,
                      label="Visibility map pixel (empty = auto)"),
                Param("relax", "bool", default=False, label="Relax near-vertical walls"),
                Param("relax_tollerance", "number", default=1.0, unit="deg",
                      label="Relax tolerance"),
                Param("relax_samples", "int", default=4, label="Relax samples"),
            ],
            run=run_directions,
        ),
    ],
)
