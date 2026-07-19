"""Part preparation pseudo-process: stages shared by every real process."""

import json
import os

import pipeline
from processes.base import (AnalysisDef, AnalysisResult, Param, ProcessDef,
                            load_cached_result, store_result)

# prep/voxels artifact schema — bump if the SDF voxel array layout changes.
# Mirrored client-side as FLOW_SCHEMA in frontend/src/processes/injection/
# voxels.ts, and re-exported as injection_molding.FLOW_SCHEMA; the
# (process, analysis, params.schema) triple is the cross-side contract.
VOXEL_SCHEMA = 1


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


def _read_json(workdir, name):
    path = os.path.join(workdir, name)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# is_current gates for the dependency resolver: True when the on-disk artifact
# is present and not stale w.r.t. its inputs, so the resolver can reuse it as a
# prerequisite instead of recomputing. They deliberately ignore the analysis's
# own params (any valid artifact is reusable — a user who wants a different
# resolution / direction count re-runs the stage explicitly); they only track
# the *upstream* content that would invalidate the result.

def mesh_current(workdir, params):
    """A fine mesh exists and was built from the current source bytes."""
    from utils import file_fingerprint
    if pipeline.mesh_fingerprint(workdir) is None:
        return False
    source = find_source(workdir)
    meta = _read_json(workdir, pipeline.MESH_META_FILE)
    if source is None or meta is None or meta.get("source_fingerprint") is None:
        return True  # legacy workdir without source tracking — trust the mesh
    return meta["source_fingerprint"] == file_fingerprint(source)


def aag_current(workdir, params):
    """The AAG artifact exists and indexes the current mesh."""
    import aag
    meta = _read_json(workdir, pipeline.AAG_META_FILE)
    if meta is None or meta.get("schema") != aag.AAG_SCHEMA:
        return False
    return meta.get("mesh_fingerprint") == pipeline.mesh_fingerprint(workdir)


def directions_current(workdir, params):
    """The accessibility matrix exists and indexes the current mesh."""
    if (pipeline.directions_fingerprint(workdir) is None
            or pipeline.accessibility_fingerprint(workdir) is None):
        return False
    meta = _read_json(workdir, pipeline.DIRECTIONS_META_FILE)
    if meta is None:
        return False
    return meta.get("mesh_fingerprint") == pipeline.mesh_fingerprint(workdir)


def voxels_current(workdir, params):
    """A voxel artifact for these params exists and indexes the current mesh."""
    from processes import resolver
    if pipeline.mesh_fingerprint(workdir) is None:
        return False
    key = resolver.cache_key(workdir, "prep/voxels", params)
    return load_cached_result(workdir, "prep", "voxels", key) is not None


# salt_fields: each prep artifact's fingerprint contribution to a downstream
# results-tier cache key (collected transitively by resolver.cache_key). The
# coarse preview contributes nothing — it co-varies with the fine mesh and is
# display-only.

def mesh_salt(workdir):
    return {"mesh": pipeline.mesh_fingerprint(workdir)}


def directions_salt(workdir):
    return {"directions": pipeline.directions_fingerprint(workdir),
            "accessibility": pipeline.accessibility_fingerprint(workdir)}


def aag_salt(workdir):
    return {"aag": pipeline.aag_fingerprint(workdir)}


def coarse_current(workdir, params):
    """The coarse preview exists and was tessellated from the current source."""
    from utils import file_fingerprint
    if not os.path.exists(os.path.join(workdir, pipeline.COARSE_FACES_FILE)):
        return False
    source = find_source(workdir)
    meta = _read_json(workdir, pipeline.COARSE_META_FILE)
    if source is None or meta is None or meta.get("source_fingerprint") is None:
        return True  # legacy coarse mesh without source tracking — trust it
    return meta["source_fingerprint"] == file_fingerprint(source)


def attributes_current(workdir, params):
    """STEP colors/names/PMI have been extracted (face_attrs.json present)."""
    return os.path.exists(os.path.join(workdir, "face_attrs.json"))


def run_mesh(workdir, params, progress):
    source = find_source(workdir)
    if source is None:
        raise FileNotFoundError(
            "no source STEP/STL found in the working directory; upload one first")
    result = pipeline.mesh_part(
        source, workdir, heal=params["heal"], resolution=params["resolution"],
        subdivide=params["subdivide"], deflection=params["deflection"],
        progress=progress)
    return AnalysisResult(stats=result["counts"])


def run_aag(workdir, params, progress):
    result = pipeline.compute_aag(
        workdir, smooth_angle=params["smooth_angle"],
        tollerance=params["tollerance"], deflection=params["deflection"],
        progress=progress)
    return AnalysisResult(stats=result)


def run_directions(workdir, params, progress):
    result = pipeline.compute_directions(
        workdir, count=params["count"], axes=params["axes"],
        tollerance=params["tollerance"], pixel=params["pixel"],
        progress=progress)
    return AnalysisResult(stats=result)


def run_mesh_coarse(workdir, params, progress):
    source = find_source(workdir)
    if source is None:
        raise FileNotFoundError(
            "no source STEP found in the working directory; upload one first")
    result = pipeline.mesh_part_coarse(
        source, workdir, resolution=params["resolution"],
        deflection=params["deflection"], progress=progress)
    return AnalysisResult(stats=result)


def run_attributes(workdir, params, progress):
    import step_import
    source = find_source(workdir)
    if (source is None or os.path.splitext(source)[1].lower()
            not in pipeline.STEP_EXTENSIONS):
        return AnalysisResult(stats={"skipped": "attributes need a STEP source"})
    try:
        counts = step_import.extract_part_attributes(workdir)
    except ValueError as exc:  # assembly source — explode it instead
        return AnalysisResult(stats={"skipped": str(exc)})
    return AnalysisResult(stats=counts)


# the fixed default first-load bundle: cheap artifacts that let the viewer
# render and take user input immediately (coarse preview + BREP colors/names/
# PMI + the adjacency graph behind sheet/tube/feature recognition). The fine
# mesh is deliberately excluded — it is produced on demand by the resolver when
# an analysis that needs it is requested. STEP-only (STL has no BREP level).
FIRST_LOAD_BUNDLE = ["prep/mesh_coarse", "prep/aag", "prep/attributes"]


def run_bundle(workdir, params, progress):
    """Idempotent: each target is skipped when already current, so this is safe
    to enqueue on every upload (a dedup re-upload no-ops cheaply)."""
    import processes.resolver as resolver
    from processes import get_analysis
    from processes.base import apply_defaults

    source = find_source(workdir)
    is_step = (source is not None and os.path.splitext(source)[1].lower()
               in pipeline.STEP_EXTENSIONS)
    targets = FIRST_LOAD_BUNDLE if is_step else []
    stats = {}
    for index, target in enumerate(targets):
        process_id, analysis_id = target.split("/", 1)
        analysis = get_analysis(process_id, analysis_id)
        if (analysis.is_current is not None
                and analysis.is_current(workdir, apply_defaults(analysis, {}))):
            stats[target] = {"reused": True}
            continue
        if progress is not None:
            lo, hi = index / len(targets), (index + 1) / len(targets)
            sub = lambda f, m, lo=lo, hi=hi: progress(lo + (hi - lo) * f, m)
        else:
            sub = None
        try:
            result = resolver.ensure(workdir, target, {}, sub)
            stats[target] = result.stats
        except Exception as exc:  # keep the coarse preview even if a stage fails
            stats[target] = {"error": f"{type(exc).__name__}: {exc}"}
    return AnalysisResult(stats={"bundle": stats,
                                 "skipped": not is_step})


def run_voxels(workdir, params, progress):
    """Signed-distance voxelization of the part interior, cached as a shared
    prep artifact (results/prep/voxels/<hash>) any process can reuse."""
    from processes import resolver
    key = resolver.cache_key(workdir, "prep/voxels", params)
    cached = load_cached_result(workdir, "prep", "voxels", key)
    if cached is not None:
        return AnalysisResult(stats=cached["stats"],
                              fields=list(cached["arrays"]))
    stats, arrays, field_meta = pipeline.flow_voxels(
        workdir, voxel=params["voxel"], progress=progress)
    store_result(workdir, "prep", "voxels", key, stats,
                 arrays=arrays, field_meta=field_meta)
    return AnalysisResult(stats=stats, fields=list(arrays))


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
                Param("resolution", "number", default=None, unit="mm", min=0,
                      label="Analysis resolution (blank = auto from part size)"),
                Param("heal", "bool", default=False,
                      label="Heal (voxel remesh at resolution/5, for dirty STL)"),
                Param("subdivide", "number", default=None, unit="mm", min=0,
                      label="Subdivide override (blank = resolution, 0 = off)"),
                Param("deflection", "number", default=None, unit="mm", min=0,
                      label="BREP deflection override (blank = resolution/8)"),
            ],
            run=run_mesh,
            is_current=mesh_current,
            salt_fields=mesh_salt,
        ),
        AnalysisDef(
            id="mesh_coarse",
            label="Coarse preview mesh",
            description="Cheap first-load BREP tessellation (no fine "
                        "subdivision) — a display-only preview the viewer can "
                        "render before the slow fine mesh exists (STEP only).",
            requires=[],
            params=[
                Param("resolution", "number", default=None, unit="mm", min=0,
                      label="Analysis resolution (blank = auto from part size)"),
                Param("deflection", "number", default=None, unit="mm", min=0,
                      label="BREP deflection override (blank = resolution/8)"),
            ],
            run=run_mesh_coarse,
            is_current=coarse_current,
        ),
        AnalysisDef(
            id="attributes",
            label="STEP colors / names / PMI",
            description="Extract per-face colors, names and semantic PMI from "
                        "the STEP (face_attrs.json / pmi.json) — first-load "
                        "ready, no meshing required (single STEP parts only).",
            requires=[],
            params=[],
            run=run_attributes,
            is_current=attributes_current,
        ),
        AnalysisDef(
            id="bundle",
            label="First-load bundle",
            description="Compute the cheap default first-load artifacts "
                        "(coarse preview + AAG + colors/names/PMI) in one job "
                        "so the viewer renders and takes input immediately.",
            requires=[],
            params=[],
            run=run_bundle,
        ),
        AnalysisDef(
            id="aag",
            label="Face adjacency graph",
            description="Attributed adjacency graph over the BREP: face "
                        "convexity, edge tangency continuity and dihedral "
                        "angles — the shared stage behind sheet metal, tube "
                        "and machining-feature recognition (STEP parts only).",
            # depends on the coarse level only — AAG rebuilds from the source
            # BREP (it reads brep_meta, not the mesh geometry), so it is
            # available at first load without waiting for the fine mesh
            requires=["prep/mesh_coarse"],
            params=[
                Param("smooth_angle", "number", default=None, unit="deg",
                      min=0, label="Tangency angle tolerance (blank = 0.57)"),
                Param("tollerance", "number", default=1e-6, min=0,
                      label="Geometric tolerance"),
                Param("deflection", "number", default=None, unit="mm", min=0,
                      label="Edge polyline deflection (blank = resolution/5)"),
            ],
            run=run_aag,
            is_current=aag_current,
            salt_fields=aag_salt,
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
                      label="Visibility map pixel (blank = resolution/5)"),
            ],
            run=run_directions,
            is_current=directions_current,
            salt_fields=directions_salt,
        ),
        AnalysisDef(
            id="voxels",
            label="SDF voxels",
            description="Signed-distance voxelization of the part interior — "
                        "the mesh-independent basis for injection fill, "
                        "freeze-off and cooling estimates, shared across "
                        "processes.",
            requires=["prep/mesh"],
            schema=VOXEL_SCHEMA,
            params=[
                Param("voxel", "number", default=None, unit="mm", min=0.05,
                      label="Voxel size (blank = auto from resolution)"),
            ],
            run=run_voxels,
            is_current=voxels_current,
        ),
    ],
)
