"""Shared pipeline stages behind the CLI and the HTTP API.

Each function is a lift of a main.py subcommand body operating on a part
working directory (the on-disk cache). Both entry points call these, so a
field computed from the UI is byte-identical to one computed from the CLI.

Every long-running stage accepts an optional ``progress(fraction, message)``
callback (injected by the API job worker, ignored by the CLI).
"""

import json
import os

import numpy as np
from loguru import logger
from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn

from analysis import (
    load_mesh,
    save_mesh,
    get_mesh_data,
    subdivide_mesh,
    sample_unity_vector_pairs,
    compute_accessibility,
    relax_accessibility,
)
from utils import (ensure_directory, file_fingerprint, files_fingerprint,
                   has_valid_extension)

FINE_MESH_FILE = "fine_mesh.obj"
FINE_VERTS_FILE = "fine_verts.npy"
FINE_FACES_FILE = "fine_faces.npy"
MESH_META_FILE = "mesh_meta.json"
DIRECTIONS_FILE = "directions.npy"
DIRECTIONS_META_FILE = "directions_meta.json"
ACCESSIBILITY_FILE = "accessibility.npy"
BREP_FACES_FILE = "brep_faces.npy"
BREP_META_FILE = "brep_meta.json"
BREP_EDGES_FILE = "brep_edges.npy"
BREP_EDGE_PAIRS_FILE = "brep_edge_pairs.npy"
HIGHLIGHT_FILE = "highlights.json"

MESH_EXTENSIONS = [".stl", ".stp", ".step"]


def _report(progress, fraction, message):
    if progress is not None:
        progress(fraction, message)


def parse_tips(specs):
    """Parse tool tip specs 'diameter:corner_radius' into (D, rc) tuples."""
    tips = []
    for spec in specs:
        diameter, _, corner = str(spec).partition(":")
        tips.append((float(diameter), float(corner or 0.0)))
    return tips


def directions_fingerprint(workdir):
    """Short content hash of directions.npy (None before prep/directions).

    Every artifact keyed by direction index (zcache fields, setup and mold
    results) salts this in, so regenerating the direction set invalidates
    caches instead of silently renumbering them.
    """
    return file_fingerprint(os.path.join(workdir, DIRECTIONS_FILE))


def mesh_fingerprint(workdir):
    """Short content hash of fine_verts + fine_faces (None before prep/mesh).

    Every stored result and zcache field is expressed over this mesh's
    face/vertex indexing; salting the fingerprint into cache keys keeps a
    re-meshed workdir from silently serving misaligned artifacts. Both
    files matter: an offset re-mesh moves only the vertices, a re-index
    only the faces.
    """
    return files_fingerprint([os.path.join(workdir, FINE_VERTS_FILE),
                              os.path.join(workdir, FINE_FACES_FILE)])


def parse_tools(entries):
    """Normalize tool library entries into dicts.

    Accepts dicts {diameter, corner_radius, stickout, holder_radius} or
    'D[:rc[:stickout[:holder_radius]]]' strings. corner_radius defaults to 0
    (flat endmill); stickout/holder_radius may be None (no length / holder
    check for that tool).
    """
    tools = []
    for entry in entries or []:
        if isinstance(entry, dict):
            tool = {
                "diameter": float(entry["diameter"]),
                "corner_radius": float(entry.get("corner_radius") or 0.0),
                "stickout": (None if entry.get("stickout") is None
                             else float(entry["stickout"])),
                "holder_radius": (None if entry.get("holder_radius") is None
                                  else float(entry["holder_radius"])),
            }
        else:
            parts = [p.strip() for p in str(entry).split(":")]
            parts += [""] * (4 - len(parts))
            tool = {
                "diameter": float(parts[0]),
                "corner_radius": float(parts[1] or 0.0),
                "stickout": float(parts[2]) if parts[2] else None,
                "holder_radius": float(parts[3]) if parts[3] else None,
            }
        tools.append(tool)
    return tools


def parse_holder(spec):
    """Parse a holder stack 'radius:start,radius:start,...' into tuples."""
    if not spec:
        return None
    cylinders = []
    for part in str(spec).split(","):
        radius, _, start = part.partition(":")
        cylinders.append((float(radius), float(start or 0.0)))
    return cylinders


def write_highlights(workdir, face_indices):
    """Persist the legacy per-face highlight result for a workdir."""
    highlight_path = os.path.join(workdir, HIGHLIGHT_FILE)
    with open(highlight_path, "w") as f:
        json.dump({"faces": list(face_indices)}, f)
    return highlight_path


def load_mesh_arrays(workdir):
    verts = np.load(os.path.join(workdir, FINE_VERTS_FILE))
    faces = np.load(os.path.join(workdir, FINE_FACES_FILE))
    return verts, faces


def _per_vertex(values, vert_count):
    """Trim a meshlib per-vertex array to the stored vertex count.

    meshFromFacesVerts splits non-manifold vertices (common on STEP meshes
    welded along shared BREP edges), so meshlib's per-vertex outputs can be
    longer than the on-disk fine_verts array. The original vertices are
    preserved in place at indices [0, vert_count); the appended duplicates
    are extra copies at those same positions. Trimming realigns any
    per-vertex field to the stable fine_verts / fine_faces indexing every
    analysis and the viewer share.
    """
    return np.asarray(values)[:vert_count]


def auto_subdivide(diagonal):
    """Default analysis-mesh edge target from the part size.

    Every analysis anchors on vertices (thickness, skeleton nodes), so the
    canonical mesh needs bounded, well-spaced edges everywhere — including
    the large flat faces a curvature-driven tessellation leaves nearly
    empty. 0.5% of the bounding-box diagonal, clamped to a practical
    range; the wall_skeleton mesh-spec gate warns when thin walls call for
    a finer manual setting.
    """
    return float(np.clip(0.005 * diagonal, 0.3, 2.0))


def part_resolution(workdir):
    """The analysis resolution the part was meshed at (None for legacy
    workdirs that predate mesh_meta.json)."""
    meta_path = os.path.join(workdir, MESH_META_FILE)
    if not os.path.exists(meta_path):
        return None
    with open(meta_path) as f:
        resolution = json.load(f).get("resolution")
    return float(resolution) if resolution else None


def resolve_pixel(workdir, pixel, fallback=1e-1):
    """Tool-field zmap pixel: explicit value, else resolution/5, else the
    legacy fixed default."""
    if pixel is not None:
        return float(pixel)
    resolution = part_resolution(workdir)
    if resolution:
        pixel = resolution / 5.0
        logger.info(f"pixel {pixel:.3f} mm from resolution {resolution:.2f} mm")
        return pixel
    return fallback


def mesh_part(input_path, workdir=None, *, heal=False, resolution=None,
              subdivide=None, offset=None, tollerance=1e-1, deflection=None,
              progress=None):
    """Canonicalize an input STL/STEP into a part working directory.

    Writes fine_mesh.obj + fine_verts.npy + fine_faces.npy (the stable face
    indexing every later stage refers to). STEP input tessellates through
    the BREP (brep.mesh_shape) so every fine face carries its source BREP
    face id (brep_faces.npy) — heal/offset destroy the surfaces and fall
    back to the anonymous meshlib path, as does STL input.

    ``resolution`` is the single analysis-resolution knob: it defaults the
    subdivide edge target (= resolution), the BREP tessellation sag
    (= resolution / 8, so curved faces carry their true shape at analysis
    scale while planes stay coarse) and, via mesh_meta.json, the zmap pixel
    of every later stage (= resolution / 5). ``subdivide``/``deflection``
    remain expert overrides. Returns the workdir and counts.
    """
    has_valid_extension(input_path, MESH_EXTENSIONS)

    is_step = os.path.splitext(input_path)[1].lower() in (".stp", ".step")
    brep_ids = None
    surface_types = None

    if is_step and not heal and offset is None:
        import brep

        shape = brep.load_step_shape(input_path)
        if resolution is None:
            resolution = auto_subdivide(brep.shape_diagonal(shape))
        if deflection is None:
            deflection = resolution / 8.0
        if subdivide is None:  # blank = resolution; 0 disables explicitly
            subdivide = resolution
        logger.info(f"resolution {resolution:.2f} mm -> deflection "
                    f"{deflection:.3f} mm, subdivide {subdivide:.2f} mm")

        _report(progress, 0.0, "tessellating BREP")
        verts, faces, brep_ids, surface_types = brep.mesh_shape(
            shape, deflection=deflection)

        if subdivide:
            _report(progress, 0.4, "subdividing mesh (tag preserving)")
            verts, faces, brep_ids = brep.subdivide_tagged(
                verts, faces, brep_ids, subdivide)

        verts = verts.astype(np.float32)
        mesh = mn.meshFromFacesVerts(faces, verts)
    else:
        _report(progress, 0.0, "loading mesh")
        mesh = load_mesh(input_path, heal=heal, offset=offset, tollerance=tollerance)

        deflection = None  # no BREP tessellation on this path
        if resolution is None:
            box = mesh.computeBoundingBox()
            resolution = auto_subdivide((box.max - box.min).length())
        if subdivide is None:  # blank = resolution; 0 disables explicitly
            subdivide = resolution
        logger.info(f"resolution {resolution:.2f} mm -> subdivide "
                    f"{subdivide:.2f} mm")
        if subdivide:
            _report(progress, 0.5, "subdividing mesh")
            mesh = subdivide_mesh(mesh, subdivide)
        verts, faces = get_mesh_data(mesh)

    if not workdir:
        input_name = os.path.basename(input_path)
        input_name = input_name.rsplit(".", 1)[0]
        workdir = os.path.join(os.path.abspath("."), input_name)

    ensure_directory(workdir)
    obj_path = os.path.join(workdir, FINE_MESH_FILE)
    verts_path = os.path.join(workdir, FINE_VERTS_FILE)
    faces_path = os.path.join(workdir, FINE_FACES_FILE)

    _report(progress, 0.8, "storing mesh")
    logger.debug(f"Storing verts: {verts_path}")
    np.save(verts_path, verts)

    logger.debug(f"Storing faces: {faces_path}")
    np.save(faces_path, faces)

    logger.debug(f"Storing obj file: {obj_path}")
    save_mesh(mesh, obj_path)

    mesh_meta = {
        "resolution": float(resolution),
        "deflection": None if deflection is None else float(deflection),
        "subdivide": float(subdivide),
        "diagonal": float(np.linalg.norm(verts.max(0) - verts.min(0))),
    }
    with open(os.path.join(workdir, MESH_META_FILE), "w") as f:
        json.dump(mesh_meta, f)

    counts = {"verts": int(len(verts)), "faces": int(len(faces)), **mesh_meta}
    if brep_ids is not None:
        np.save(os.path.join(workdir, BREP_FACES_FILE), brep_ids)
        with open(os.path.join(workdir, BREP_META_FILE), "w") as f:
            json.dump({"face_count": len(surface_types),
                       "surface_types": surface_types}, f)
        counts["brep_faces"] = len(surface_types)

        # BREP edge geometry: mesh edges between different BREP faces,
        # grouped by their unordered face-id pair — the discretized BREP
        # edges the parting line snaps to
        import molding
        pairs, edge_verts = molding.face_adjacency(faces)
        boundary = brep_ids[pairs[:, 0]] != brep_ids[pairs[:, 1]]
        segments = verts[edge_verts[boundary]].astype("<f4")
        id_pairs = np.sort(np.stack([brep_ids[pairs[boundary, 0]],
                                     brep_ids[pairs[boundary, 1]]], axis=1),
                           axis=1).astype("<u4")
        np.save(os.path.join(workdir, BREP_EDGES_FILE), segments)
        np.save(os.path.join(workdir, BREP_EDGE_PAIRS_FILE), id_pairs)
        counts["brep_edge_segments"] = int(len(segments))

    return {"workdir": workdir, "counts": counts}


def compute_directions(workdir, *, count=64, axes=False, tollerance=0.1,
                       pixel=None, relax=False, relax_tollerance=1.0,
                       relax_samples=4, progress=None):
    """Sample approach directions and compute the accessibility matrix.

    ``tollerance`` is the angular relaxation (degrees) of the visibility
    test: faces within it of perpendicular still count as front-facing, so
    near-vertical walls classify deterministically. ``pixel`` sets the
    visibility height-map resolution (None = resolution/5 from mesh_meta,
    falling back to auto from the bounding box on legacy workdirs).
    """
    if pixel is None:
        resolution = part_resolution(workdir)
        if resolution:
            pixel = resolution / 5.0
            logger.info(f"pixel {pixel:.3f} mm from resolution {resolution:.2f} mm")

    logger.debug(f"Computing {count} directions")
    directions = sample_unity_vector_pairs(count)

    if axes:
        # principal axes as antipodal pairs, matching the pair layout
        principal = np.array([
            [1.0, 0.0, 0.0], [-1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0], [0.0, -1.0, 0.0],
            [0.0, 0.0, 1.0], [0.0, 0.0, -1.0],
        ])
        directions = np.vstack([principal, directions])

    logger.debug("Cheking accessibility per direction")
    verts, faces = load_mesh_arrays(workdir)
    mesh = mn.meshFromFacesVerts(faces, verts)

    face_count = len(faces)
    _report(progress, 0.1, f"accessibility for {directions.shape[0]} directions")
    accessibility = compute_accessibility(mesh, directions, face_count,
                                          tolerance_deg=tollerance, pixel=pixel)

    if relax:
        for direction_index in range(directions.shape[0]):
            _report(progress, 0.5 + 0.5 * direction_index / directions.shape[0],
                    f"relaxing direction {direction_index}")
            relaxed = relax_accessibility(
                mesh, accessibility[direction_index, :], directions[direction_index],
                tolerance_degrees=relax_tollerance, n=relax_samples)
            accessibility[direction_index, :] = relaxed

    directions_path = os.path.join(workdir, DIRECTIONS_FILE)
    accessibility_path = os.path.join(workdir, ACCESSIBILITY_FILE)

    logger.debug(f"Storing directions at: {directions_path}")
    np.save(directions_path, directions)

    logger.debug(f"Storing accessibility at: {accessibility_path}")
    np.save(accessibility_path, accessibility)

    # which mesh the accessibility rows index — the manifest flags the
    # directions stale when the workdir is re-meshed afterwards
    with open(os.path.join(workdir, DIRECTIONS_META_FILE), "w") as f:
        json.dump({"mesh_fingerprint": mesh_fingerprint(workdir),
                   "pixel": None if pixel is None else float(pixel)}, f)

    return {
        "directions": int(directions.shape[0]),
        "faces": int(face_count),
    }


def compute_thickness(workdir, *, max_radius=None, inverted=False,
                      max_iters=1000, progress=None):
    """Per-vertex maximal inscribed ("rolling") sphere diameter.

    inverted=False measures wall thickness inside the part. inverted=True
    runs the same search on an orientation-flipped copy of the mesh, so the
    exterior becomes the inside and the value is the local gap between
    opposing walls — on the same vertex indexing, no boolean inversion or
    cross-mesh mapping needed. Values cap at 2*max_radius (saturated = no
    opposing wall worth considering); max_radius=None derives meshlib's
    recommended 0.5 * min(bbox dims).

    Returns (values float32[verts], max_radius).
    """
    verts, faces = load_mesh_arrays(workdir)
    mesh = mn.meshFromFacesVerts(faces, verts)

    if max_radius is None:
        size = mesh.computeBoundingBox().size()
        max_radius = 0.5 * min(size.x, size.y, size.z)
        logger.debug(f"Auto inscribed sphere max radius: {max_radius:.3f}")

    if inverted:
        mesh.topology.flipOrientation()

    settings = mm.InSphereSearchSettings()
    settings.insideAndOutside = False
    settings.maxRadius = float(max_radius)
    settings.maxIters = int(max_iters)
    settings.minShrinkage = 1e-6

    _report(progress, 0.2, "rolling inscribed spheres")
    result = mm.computeInSphereThicknessAtVertices(mesh, settings)
    values = _per_vertex(np.array(result.vec, dtype=np.float32), len(verts))
    # meshlib returns an unbounded (inf) radius where no sphere is limited by
    # an opposing wall — degenerate/open spots common on welded STEP meshes.
    # Saturate those to the documented cap so the field, its stats and the
    # heatmap stay finite (cap = "no opposing wall worth considering").
    cap = np.float32(2.0 * max_radius)
    values = np.where(np.isfinite(values), np.minimum(values, cap), cap)
    _report(progress, 1.0, "thickness field done")
    return values, float(max_radius)


def mold_orientation(workdir, *, max_slides=2, slide_tollerance=2.0, count=10,
                     min_slide_faces=50, field_options=3, progress=None):
    """Search mold orientations and derive per-face assignment fields.

    Returns {"stats": <JSON-safe>, "arrays": {...}, "field_meta": {...}}
    with band/resolved/brep category fields and parting-line segments for
    the top `field_options` options. The brep fields need brep_faces.npy
    (STEP-meshed parts); they are skipped otherwise.
    """
    import molding

    verts, faces = load_mesh_arrays(workdir)
    directions = np.load(os.path.join(workdir, DIRECTIONS_FILE))
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))

    brep_path = os.path.join(workdir, BREP_FACES_FILE)
    brep_ids = np.load(brep_path) if os.path.exists(brep_path) else None

    _report(progress, 0.1, "searching mold orientations")
    options = molding.mold_orientation_search(
        directions, accessibility, max_slides=max_slides,
        slide_tolerance_deg=slide_tollerance, min_slide_faces=min_slide_faces)

    _report(progress, 0.5, "deriving membership fields")
    pairs, _ = molding.face_adjacency(faces)

    arrays, field_meta = {}, {}
    for k, option in enumerate(options[:field_options]):
        slide_dirs = [s["direction"] for s in option["slides"]]
        n_features = 2 + len(slide_dirs)
        membership = molding.membership_field(option["pair"], slide_dirs,
                                              accessibility)
        region, region_counts = molding.internal_regions(membership, pairs,
                                                         len(faces))
        labels, colors = molding.feature_labels_colors(len(slide_dirs))

        common = {"kind": "mold_membership", "option": k,
                  "features": n_features, "labels": labels, "colors": colors,
                  "conflict_color": molding.CATEGORY_COLORS["conflict"],
                  "internal_color": molding.CATEGORY_COLORS["internal"]}
        arrays[f"membership_{k}"] = membership
        field_meta[f"membership_{k}"] = {
            **common, "variant": "membership", "association": "face",
            "role": "category", "dtype": "u4"}
        arrays[f"internal_region_{k}"] = region
        field_meta[f"internal_region_{k}"] = {
            **common, "variant": "internal_region", "association": "face",
            "role": "category", "dtype": "u4", "regions": len(region_counts),
            "region_counts": region_counts}

        if brep_ids is not None:
            valid = molding.brep_validity(membership, brep_ids, n_features)
            arrays[f"brep_valid_{k}"] = valid
            field_meta[f"brep_valid_{k}"] = {
                **common, "variant": "brep_valid", "association": "none",
                "role": "data", "dtype": "u4", "count": int(len(valid))}
            defaults = molding.brep_defaults(membership, valid, brep_ids)
            arrays[f"brep_default_{k}"] = defaults
            field_meta[f"brep_default_{k}"] = {
                **common, "variant": "brep_default", "association": "none",
                "role": "data", "dtype": "u1", "count": int(len(defaults))}

    stats = {
        "schema": 2,
        "face_count": int(accessibility.shape[1]),
        "direction_count": int(directions.shape[0]),
        "directions_fingerprint": directions_fingerprint(workdir),
        "brep": brep_ids is not None,
        "options": options[:count],
    }
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}


SETUPS_STATS_SCHEMA = 2  # area-weighted counts + directions fingerprint


def _setup_machines(indexed, tilt):
    machines = [("3-axis", 0.0)]
    if indexed:
        machines.append(("3+2", float(tilt)))
    return machines


def _ranked_setup_options(directions, accessibility, weights, *, machines,
                          max_setups, min_setup_area, count, field_options):
    """Shared search + report shaping for cnc_setups and setup_verdict.

    Returns (reported, picked): the reported option list (top `count` plus
    any appended signature-best plans) and the field-option indices into it
    — the best plan of each distinct (machine, setup count) signature.
    min_setup_area=None defaults to 0.1% of the total surface area.
    """
    import machining

    if min_setup_area is None:
        min_setup_area = 1e-3 * float(weights.sum())
    options = machining.setup_search(
        directions, accessibility, weights=weights, machines=machines,
        max_setups=max_setups, min_setup_gain=min_setup_area)

    reported = options[:count]
    picked, signatures = [], set()
    for index, option in enumerate(options):
        signature = (option["machine"], len(option["setups"]))
        if signature in signatures:
            continue
        signatures.add(signature)
        if index >= count:
            # a signature's best plan ranked past the report cut — append
            # it so every field option is present in stats["options"]
            reported.append(option)
            index = len(reported) - 1
        picked.append(index)
        if len(picked) == field_options:
            break

    for option in options:
        option.pop("machine_rank", None)
    return reported, picked


def _membership_fields(k, option_index, option, membership, pairs, faces,
                       brep_ids, arrays, field_meta):
    """Derive the per-face assignment fields of one option's membership."""
    import machining
    import molding

    setup_count = len(option["setups"])
    region, region_counts = molding.internal_regions(membership, pairs,
                                                     len(faces))
    labels, colors = machining.setup_labels_colors(
        [s["vector"] for s in option["setups"]])

    common = {"kind": "setup_membership", "option": option_index,
              "machine": option["machine"], "features": setup_count,
              "labels": labels, "colors": colors,
              "conflict_color": machining.CATEGORY_COLORS["conflict"],
              "internal_color": machining.CATEGORY_COLORS["unmachinable"]}
    arrays[f"membership_{k}"] = membership
    field_meta[f"membership_{k}"] = {
        **common, "variant": "membership", "association": "face",
        "role": "category", "dtype": "u4"}
    arrays[f"internal_region_{k}"] = region
    field_meta[f"internal_region_{k}"] = {
        **common, "variant": "internal_region", "association": "face",
        "role": "category", "dtype": "u4", "regions": len(region_counts),
        "region_counts": region_counts}

    if brep_ids is not None:
        valid = molding.brep_validity(membership, brep_ids, setup_count)
        arrays[f"brep_valid_{k}"] = valid
        field_meta[f"brep_valid_{k}"] = {
            **common, "variant": "brep_valid", "association": "none",
            "role": "data", "dtype": "u4", "count": int(len(valid))}
        defaults = machining.setup_defaults(membership, valid, brep_ids)
        arrays[f"brep_default_{k}"] = defaults
        field_meta[f"brep_default_{k}"] = {
            **common, "variant": "brep_default", "association": "none",
            "role": "data", "dtype": "u1", "count": int(len(defaults))}


def cnc_setups(workdir, *, indexed=True, tilt=90.0, max_setups=4,
               min_setup_area=None, count=10, field_options=3, progress=None):
    """Search CNC setup combinations and derive per-face assignment fields.

    Machines searched: a plain 3-axis (one direction per setup) and, with
    ``indexed``, a 3+2 whose setups cover a ``tilt``-degree cone. All counts
    are area-weighted (mm²); ``min_setup_area`` defaults to 0.1% of the
    total surface area. Returns {"stats": <JSON-safe>, "arrays": {...},
    "field_meta": {...}} with membership/region/brep fields for up to
    `field_options` options — picked as the best of each distinct (machine,
    setup count) signature, so a single-setup 3+2 plan is explorable next
    to the 3-axis flips instead of buried under them.
    stats["field_options"] maps field index k -> index into
    stats["options"]. The brep fields need brep_faces.npy (STEP-meshed
    parts); they are skipped otherwise.
    """
    import machining
    import molding

    verts, faces = load_mesh_arrays(workdir)
    directions = np.load(os.path.join(workdir, DIRECTIONS_FILE))
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))
    weights = machining.face_areas(verts, faces)

    brep_path = os.path.join(workdir, BREP_FACES_FILE)
    brep_ids = np.load(brep_path) if os.path.exists(brep_path) else None

    _report(progress, 0.1, "searching setup combinations")
    reported, picked = _ranked_setup_options(
        directions, accessibility, weights,
        machines=_setup_machines(indexed, tilt), max_setups=max_setups,
        min_setup_area=min_setup_area, count=count,
        field_options=field_options)

    _report(progress, 0.5, "deriving membership fields")
    pairs, _ = molding.face_adjacency(faces)

    covers = {}
    arrays, field_meta = {}, {}
    for k, index in enumerate(picked):
        option = reported[index]
        if option["machine"] not in covers:
            covers[option["machine"]] = machining.machine_cover(
                directions, accessibility, option["tilt"])
        cover = covers[option["machine"]]
        membership = machining.setup_membership(
            [s["direction"] for s in option["setups"]], cover)
        _membership_fields(k, index, option, membership, pairs, faces,
                           brep_ids, arrays, field_meta)

    stats = {
        "schema": SETUPS_STATS_SCHEMA,
        "face_count": int(accessibility.shape[1]),
        "direction_count": int(directions.shape[0]),
        "total_area": round(float(weights.sum()), 3),
        "directions_fingerprint": directions_fingerprint(workdir),
        "brep": brep_ids is not None,
        "options": reported,
        "field_options": picked,
    }
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}


def setup_verdict(workdir, *, option=0, tools=(), tollerance=0.1,
                  wall_tollerance=1.0, pixel=None, window=0.3, indexed=True,
                  tilt=90.0, max_setups=4, min_setup_area=None, count=10,
                  field_options=3, progress=None):
    """Re-verdict one ranked setup plan with a real tool library.

    The funnel's second stage: the visibility-only search proposes plans
    cheaply; this recomputes the same ranked list (same parameters ->
    identical ranking), takes plan ``option`` and rebuilds its per-setup
    coverage from actual tool reachability — a face counts iff some tool in
    the library reaches it (tip gap within tolerance, walls side-milled,
    required stickout within the tool's length) from a direction the setup
    can use (the primary for 3-axis, every sampled direction inside the
    tilt cone for 3+2). Fields (gap + tip-aware stickout per direction and
    tool) come from the zmap DirectionCache, computed lazily and persisted,
    so repeated verdicts are cheap.

    Faces the visibility search covered but no tool reaches become
    membership-0 "lost to tooling" regions; the stored result mirrors a
    setups result (schema, membership/brep fields) with a single option
    carrying tool-aware counts plus a "verdict" block, so the viewer's
    setups mode renders it unchanged.
    """
    import machining
    import molding
    from zmap import DirectionCache, tool_face_verdict

    tools = parse_tools(tools)
    if not tools:
        raise ValueError("setup_verdict needs at least one tool")

    pixel = resolve_pixel(workdir, pixel)
    verts, faces = load_mesh_arrays(workdir)
    directions = np.load(os.path.join(workdir, DIRECTIONS_FILE))
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))
    weights = machining.face_areas(verts, faces)
    normals = machining.face_unit_normals(verts, faces)

    brep_path = os.path.join(workdir, BREP_FACES_FILE)
    brep_ids = np.load(brep_path) if os.path.exists(brep_path) else None

    _report(progress, 0.02, "ranking setup plans")
    reported, _ = _ranked_setup_options(
        directions, accessibility, weights,
        machines=_setup_machines(indexed, tilt), max_setups=max_setups,
        min_setup_area=min_setup_area, count=count,
        field_options=field_options)
    if not 0 <= option < len(reported):
        raise ValueError(f"option {option} out of range (0..{len(reported) - 1})")
    plan = reported[option]

    # per-setup tool coverage: OR over (cone direction x tool) verdicts
    setup_dirs = [s["direction"] for s in plan["setups"]]
    cone = machining.cone_members(directions, plan["tilt"])
    direction_sets = [cone[d] for d in setup_dirs]
    total_steps = max(sum(len(ds) for ds in direction_sets), 1)

    step = 0
    rows = []
    for s, members in enumerate(direction_sets):
        row = np.zeros(len(faces), dtype=bool)
        for d in members:
            _report(progress, 0.05 + 0.85 * step / total_steps,
                    f"setup {s + 1}: tool fields for direction {d}")
            step += 1
            visible = accessibility[d]
            if not visible.any():
                continue
            cache = DirectionCache(workdir, int(d), verts=verts, faces=faces,
                                   pixel=pixel, window=window, engine="zmap")
            angles = machining.face_angles_deg(normals, directions[d])
            for tool in tools:
                cylinders = ([(tool["holder_radius"], 0.0)]
                             if tool["holder_radius"] else None)
                machinable, _, _ = tool_face_verdict(
                    cache, faces, angles, diameter=tool["diameter"],
                    corner_radius=tool["corner_radius"],
                    stickout=tool["stickout"], cylinders=cylinders,
                    tollerance=tollerance, wall_tollerance=wall_tollerance)
                row |= machinable & visible
        rows.append(row)

    _report(progress, 0.92, "deriving membership fields")
    verdict_option = machining.reweight_option(plan, rows, weights)
    visible_any = machining.setup_membership(
        setup_dirs, machining.machine_cover(directions, accessibility,
                                            plan["tilt"])) > 0
    membership = machining.membership_from_rows(rows)
    lost = round(float(weights[visible_any & (membership == 0)].sum()), 3)
    verdict_option["verdict"] = {
        "tools": tools,
        "tollerance": float(tollerance),
        "wall_tollerance": float(wall_tollerance),
        "pixel": float(pixel),
        "base_option": int(option),
        "base_coverage": plan["coverage"],
        "lost": lost,
    }

    pairs, _ = molding.face_adjacency(faces)
    arrays, field_meta = {}, {}
    _membership_fields(0, 0, verdict_option, membership, pairs, faces,
                       brep_ids, arrays, field_meta)

    stats = {
        "schema": SETUPS_STATS_SCHEMA,
        "verdict": True,
        "face_count": int(accessibility.shape[1]),
        "direction_count": int(directions.shape[0]),
        "total_area": round(float(weights.sum()), 3),
        "directions_fingerprint": directions_fingerprint(workdir),
        "brep": brep_ids is not None,
        "options": [verdict_option],
        "field_options": [0],
    }
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}


def thickness_highlights(faces, thickness, hi=1.3):
    """Face indices whose three vertices all exceed hi * mean thickness."""
    mask = thickness > hi * float(np.mean(thickness))
    return np.where(mask[faces].all(axis=1))[0].tolist()


NODE_SENTINEL = np.uint32(0xFFFFFFFF)  # vert->node mapping: vertex has no node


def _in_sphere_settings(max_radius):
    from meshlib import mrmeshpy as mm

    settings = mm.InSphereSearchSettings()
    settings.insideAndOutside = False
    settings.maxRadius = float(max_radius)
    settings.maxIters = 1000
    settings.minShrinkage = 1e-6
    return settings


def _skeleton_centers(mesh, verts, faces, radii, settings, progress):
    """Inscribed-sphere centers per vertex.

    meshlib constrains each sphere's center to the inward normal at the
    vertex, so centers reconstruct vectorized as p - n*r — exact on smooth
    regions. Two suspect classes are handled without meshlib's per-vertex
    findInSphere (which can hang indefinitely on the degenerate, split
    topology of welded STEP meshes and cannot be interrupted from Python):

    - penetrating: the reconstructed center sits closer to the surface than
      its own radius, so the estimate is unreliable — those vertices are
      dropped (radius -> nan) rather than seeding a bad skeleton node. They
      are ~1% at most and the clustered graph stays connected.
    - sharp crease: the averaged normal is a fine medial direction there and
      the downstream absorption pass folds rim/crease nodes into their wall,
      so the estimate is kept as-is.
    """
    from meshlib import mrmeshpy as mm
    from scipy.spatial import cKDTree

    normals = _per_vertex(
        mn.toNumpyArray(mm.computePerVertNormals(mesh)), len(verts))
    centers = verts.astype(np.float64) - normals * radii[:, None]

    tri = verts[faces].astype(np.float64)
    edge_lengths = np.linalg.norm(tri - np.roll(tri, -1, axis=1), axis=2)
    mean_edge = float(edge_lengths.mean())

    # penetration flag: a valid center keeps its radius from the surface
    # (vertex samples of it; mean_edge covers sampling slack)
    surface_distance, _ = cKDTree(verts).query(centers, workers=-1)
    tolerance = np.maximum(0.05 * radii, 0.5 * mean_edge)
    penetrating = (surface_distance < radii - tolerance) & np.isfinite(radii)

    radii = radii.copy()
    radii[penetrating] = np.nan
    dropped = int(penetrating.sum())
    if dropped > 0.1 * len(verts):
        logger.warning(
            f"{dropped}/{len(verts)} penetrating sphere centers dropped; "
            "mesh may be under-resolved or degenerate")
    return centers, radii, dropped


def _cluster_nodes(nodes, radii, cluster_factor):
    """Merge nodes into radius-scaled grid cells.

    Nodes merge when they share a cell sized to their radius octave
    (cell = cluster_factor * 2^k for radii in [2^k, 2^(k+1))). The earlier
    connected-components merge of overlapping spheres chained transitively:
    a uniform midplane sheet collapsed into one giant cluster, making every
    flow distance across it zero. Grid cells bound the cluster extent by
    its members' own radius scale instead, so clustered edge lengths stay
    meaningful while the reduction is still radius-proportional.

    Returns per-node cluster labels and the representative (max radius)
    member index per cluster; averaging positions instead would drift off
    the medial axis in thin features.
    """
    count = len(nodes)
    if count == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)

    octave = np.floor(np.log2(np.maximum(radii, 1e-9)))
    cell = cluster_factor * np.exp2(octave)
    quantized = np.floor(nodes / cell[:, None]).astype(np.int64)
    keys = np.concatenate([octave.astype(np.int64)[:, None], quantized],
                          axis=1)
    _, labels = np.unique(keys, axis=0, return_inverse=True)
    labels = labels.astype(np.int64)

    cluster_count = int(labels.max()) + 1
    representative = np.zeros(cluster_count, dtype=np.int64)
    best_radius = np.full(cluster_count, -1.0)
    np.maximum.at(best_radius, labels, radii)
    is_best = radii >= best_radius[labels]
    representative[labels[is_best]] = np.where(is_best)[0]
    return labels, representative


def _absorb_clusters(nodes, radii, labels, representative, raw_edges,
                     absorb_factor, max_rounds=8):
    """Merge curvature-artifact clusters into the member they belong to.

    At a convex rounded rim or fillet the inscribed sphere measures the
    local edge curvature, not the wall thickness, so walls grow chains of
    tiny-radius nodes along their rims. Those clusters are absorbed into a
    graph neighbor when their sphere is much smaller than AND overlaps the
    neighbor's sphere — rim nodes hug their parent wall, while genuinely
    thin members (hinges, webs) extend away from their thick neighbors and
    only lose their junction nodes. Iterates because chains absorb layer by
    layer. Returns (labels, representative, absorbed_count) with labels
    compacted to the surviving clusters.
    """
    cluster_count = len(representative)
    if absorb_factor <= 0 or cluster_count == 0:
        return labels, representative, 0

    rep_radius = radii[representative]
    rep_pos = nodes[representative]
    parent = np.arange(cluster_count)

    def find(cluster):
        while parent[cluster] != cluster:
            parent[cluster] = parent[parent[cluster]]
            cluster = parent[cluster]
        return cluster

    pairs = labels[raw_edges.astype(np.int64)]
    pairs = np.unique(np.sort(pairs, axis=1), axis=0)
    pairs = pairs[pairs[:, 0] != pairs[:, 1]]

    absorbed = 0
    for _ in range(max_rounds):
        roots = np.fromiter((find(c) for c in range(cluster_count)),
                            dtype=np.int64, count=cluster_count)
        live = roots[pairs]
        live = np.unique(np.sort(live[live[:, 0] != live[:, 1]], axis=1),
                         axis=0)
        merged = 0
        for a, b in live:
            root_a, root_b = find(a), find(b)
            if root_a == root_b:
                continue
            small, big = ((root_a, root_b)
                          if rep_radius[root_a] <= rep_radius[root_b]
                          else (root_b, root_a))
            if rep_radius[small] >= absorb_factor * rep_radius[big]:
                continue
            span = np.linalg.norm(rep_pos[small] - rep_pos[big])
            if span <= rep_radius[small] + rep_radius[big]:
                parent[small] = big
                merged += 1
        if merged == 0:
            break
        absorbed += merged

    roots = np.fromiter((find(c) for c in range(cluster_count)),
                        dtype=np.int64, count=cluster_count)
    survivors, compact = np.unique(roots, return_inverse=True)
    return compact[labels].astype(np.int64), representative[survivors], absorbed


def wall_skeleton(workdir, *, max_radius=5.0, min_radius=0.1,
                  cluster_factor=1.0, absorb_factor=0.5, progress=None):
    """Wall thickness + medial skeleton graphs from inscribed spheres.

    Every vertex gets its maximal inscribed ("rolling") sphere; the sphere
    centers become skeleton nodes carrying the local wall radius, connected
    by the mesh edge adjacency (raw graph) and additionally merged into a
    reduced clustered graph, with curvature-artifact rim clusters absorbed
    into their walls (_absorb_clusters). Stats include a mesh-resolution
    spec (p95 edge length vs the median measured wall thickness) — the
    workdir's single canonical mesh is shared by every analysis and
    visualization, and this gate validates that its resolution is adequate
    for thickness/skeleton work. Returns (stats, arrays, field_meta) —
    storing the result is the caller's job.
    """
    from meshlib import mrmeshpy as mm

    verts, faces = load_mesh_arrays(workdir)
    faces = faces.astype(np.int64, copy=False)
    mesh = mn.meshFromFacesVerts(faces, verts)
    vert_count = len(verts)

    _report(progress, 0.05, "inscribed sphere thickness")
    settings = _in_sphere_settings(max_radius)
    thickness = _per_vertex(
        np.array(mm.computeInSphereThicknessAtVertices(mesh, settings).vec),
        vert_count)
    radii = thickness / 2.0

    _report(progress, 0.3, "sphere centers")
    centers, radii, corrected = _skeleton_centers(
        mesh, verts, faces, radii, settings, progress)
    thickness = radii * 2.0

    # nodes: one per vertex with a meaningful sphere; tiny corner spheres
    # sit off the medial axis and only add noise
    keep = np.isfinite(radii) & (radii >= min_radius)
    node_count = int(keep.sum())
    vert_node = np.full(vert_count, NODE_SENTINEL, dtype=np.uint32)
    vert_node[keep] = np.arange(node_count, dtype=np.uint32)
    raw_nodes = centers[keep]
    raw_radii = radii[keep]

    # raw edges: unique mesh edges between kept vertices
    edges = np.concatenate(
        [faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    edges = vert_node[edges]
    raw_edges = edges[(edges != NODE_SENTINEL).all(axis=1)].astype(np.uint32)

    # prune non-physical edges: an edge spanning far beyond its endpoint
    # spheres comes from a degenerate sliver triangle in the tessellation —
    # it cannot represent contiguous material at that radius, and one such
    # edge can tether a whole region to the graph through a near-zero
    # stiffness / huge flow resistance artifact
    tri = verts[faces]
    edge_lengths = np.linalg.norm(tri - np.roll(tri, -1, axis=1), axis=2)
    mesh_edge = float(edge_lengths.mean())
    a = raw_edges[:, 0].astype(np.int64)
    b = raw_edges[:, 1].astype(np.int64)
    span = np.linalg.norm(raw_nodes[a] - raw_nodes[b], axis=1)
    reach = 4.0 * (raw_radii[a] + raw_radii[b])
    physical = span <= np.maximum(reach, 4.0 * mesh_edge)
    pruned = int((~physical).sum())
    raw_edges = raw_edges[physical]

    _report(progress, 0.7, "clustering skeleton nodes")
    labels, representative = _cluster_nodes(raw_nodes, raw_radii,
                                            cluster_factor)
    labels, representative, absorbed = _absorb_clusters(
        raw_nodes, raw_radii, labels, representative, raw_edges,
        absorb_factor)
    cluster_nodes = raw_nodes[representative]
    cluster_radii = raw_radii[representative]
    cluster_edges = labels[raw_edges.astype(np.int64)]
    cluster_edges = np.unique(np.sort(cluster_edges, axis=1), axis=0)
    cluster_edges = cluster_edges[
        cluster_edges[:, 0] != cluster_edges[:, 1]].astype(np.uint32)
    cluster_vert_node = np.full(vert_count, NODE_SENTINEL, dtype=np.uint32)
    cluster_vert_node[keep] = labels[vert_node[keep].astype(np.int64)]

    # stray nodes (every mesh neighbor was dropped, so no adjacency edge
    # survived) reconnect through sphere overlap: two inscribed spheres that
    # intersect sit in connected material even without a mesh edge
    degree = np.bincount(cluster_edges.astype(np.int64).ravel(),
                         minlength=len(cluster_nodes))
    isolated = np.flatnonzero(degree == 0)
    if len(isolated) and len(cluster_nodes) > 1:
        from scipy.spatial import cKDTree

        span, neighbor = cKDTree(cluster_nodes).query(
            cluster_nodes[isolated], k=2, workers=-1)
        other = neighbor[:, 1]  # first hit is the node itself
        overlap = span[:, 1] <= cluster_radii[isolated] + cluster_radii[other]
        extra = np.stack([isolated[overlap], other[overlap]], axis=1)
        extra = extra[extra[:, 0] != extra[:, 1]].astype(np.uint32)
        cluster_edges = np.concatenate([cluster_edges, extra])

    def graph_meta(role, graph, array, dtype):
        return {"kind": "skeleton", "association": "graph", "role": role,
                "graph": graph, "dtype": dtype, "length": int(array.size),
                "count": int(array.shape[0])}

    arrays = {
        "thickness": thickness.astype("f4"),
        "raw_nodes": raw_nodes.astype("f4"),
        "raw_radii": raw_radii.astype("f4"),
        "raw_edges": raw_edges,
        "raw_vert_node": vert_node,
        "cluster_nodes": cluster_nodes.astype("f4"),
        "cluster_radii": cluster_radii.astype("f4"),
        "cluster_edges": cluster_edges,
        "cluster_vert_node": cluster_vert_node,
    }
    field_meta = {
        "thickness": {"kind": "wall_thickness", "association": "vertex",
                      "role": "scalar", "units": "mm"},
    }
    for graph in ("raw", "cluster"):
        graph_name = "clustered" if graph == "cluster" else "raw"
        field_meta[f"{graph}_nodes"] = graph_meta(
            "nodes", graph_name, arrays[f"{graph}_nodes"], "f4")
        field_meta[f"{graph}_radii"] = graph_meta(
            "radii", graph_name, arrays[f"{graph}_radii"], "f4")
        field_meta[f"{graph}_edges"] = graph_meta(
            "edges", graph_name, arrays[f"{graph}_edges"], "u4")
        field_meta[f"{graph}_vert_node"] = graph_meta(
            "vert_map", graph_name, arrays[f"{graph}_vert_node"], "u4")

    # mesh-resolution spec: a vertex-anchored skeleton needs mesh edges
    # comfortably below the wall thickness it measures
    finite = thickness[np.isfinite(thickness)]
    p95_edge = float(np.percentile(edge_lengths, 95))
    median_thickness = float(np.median(finite)) if len(finite) else 0.0
    ratio = p95_edge / max(median_thickness, 1e-9)
    mesh_spec = {
        "p95_edge_mm": p95_edge,
        "median_thickness_mm": median_thickness,
        "edge_thickness_ratio": ratio,
        "status": "ok" if ratio <= 0.5 else
                  "marginal" if ratio <= 1.0 else "coarse",
    }

    stats = {
        "verts": vert_count,
        "raw_nodes": node_count,
        "raw_edges": int(len(raw_edges)),
        "cluster_nodes": int(len(cluster_nodes)),
        "cluster_edges": int(len(cluster_edges)),
        "absorbed": int(absorbed),
        "pruned_edges": pruned,
        "mesh": mesh_spec,
        "mean_thickness": float(finite.mean()) if len(finite) else None,
        "min_thickness": float(finite.min()) if len(finite) else None,
        "penetrating_dropped": int(corrected),
        "dropped": int(vert_count - node_count),
    }
    logger.info(
        f"wall skeleton: {node_count} raw / {len(cluster_nodes)} clustered "
        f"nodes, mean thickness {stats['mean_thickness']}")
    return stats, arrays, field_meta


def _latest_mold_orientation(workdir):
    """Newest schema-2 mold_orientation result: (hash, payload, npz) or Nones.

    "results" mirrors processes.base.RESULTS_DIR — importing processes here
    would be circular (processes imports pipeline).
    """
    import glob

    pattern = os.path.join(workdir, "results", "injection_molding",
                           "mold_orientation", "*.json")
    best = None
    for json_path in glob.glob(pattern):
        if json_path.endswith("_overrides.json"):
            continue
        with open(json_path) as f:
            payload = json.load(f)
        if payload.get("stats", {}).get("schema") != 2:
            continue
        mtime = os.path.getmtime(json_path)
        if best is None or mtime > best[0]:
            best = (mtime, json_path, payload)
    if best is None:
        return None, None, None
    result_hash = os.path.splitext(os.path.basename(best[1]))[0]
    npz_path = best[1][:-len(".json")] + ".npz"
    arrays = None
    if os.path.exists(npz_path):
        with np.load(npz_path, allow_pickle=False) as stored:
            arrays = {name: stored[name] for name in stored.files}
    return result_hash, best[2], arrays


def _parting_tree(workdir, brep_default):
    """cKDTree over sampled parting-line points, or None.

    Parting segments are the BREP edges whose two faces carry different
    default features (both real, not conflict/internal) — the same rule the
    viewer applies (frontend/src/processes/injection/index.tsx), except over
    defaults: user overrides are ignored in v1.
    """
    from scipy.spatial import cKDTree

    edges_path = os.path.join(workdir, BREP_EDGES_FILE)
    pairs_path = os.path.join(workdir, BREP_EDGE_PAIRS_FILE)
    if not (os.path.exists(edges_path) and os.path.exists(pairs_path)):
        return None
    segments = np.load(edges_path).reshape(-1, 2, 3)
    pairs = np.load(pairs_path).reshape(-1, 2).astype(np.int64)
    feat = brep_default[pairs]
    keep = (feat[:, 0] != feat[:, 1]) & (feat < 254).all(axis=1)
    segments = segments[keep]
    if not len(segments):
        return None
    samples = np.concatenate(
        [segments[:, 0], segments[:, 1], segments.mean(axis=1)])
    return cKDTree(samples)


def sprue_proposals(workdir, *, skeleton, skeleton_hash, mesh_spec=None,
                    min_gate_thickness=0.8, max_candidates=400,
                    thick_percentile=85.0, pack_factor=0.5,
                    edge_gate_distance=5.0, forbid_side="none",
                    orientation_option=0, top_n=10, weights=None,
                    progress=None):
    """Ranked automatic injection-gate proposals over the wall skeleton.

    Candidate surface vertices -> hard moldability filters -> multi-source
    Dijkstra screening on the clustered skeleton (same length/r^4
    resistances as the interactive fill view) -> normalized weighted score
    with per-proposal explanations. ``skeleton`` maps the wall_skeleton
    result arrays; ``skeleton_hash`` its cache hash so the viewer binds the
    identical graph. A mold_orientation result is used when present
    (slide/undercut rejection, side tags, parting distance) and skipped
    gracefully otherwise. Returns (stats, arrays, field_meta) — storing is
    the caller's job.
    """
    import gating

    verts, faces = load_mesh_arrays(workdir)
    faces = faces.astype(np.int64, copy=False)
    thickness = np.asarray(skeleton["thickness"], dtype=np.float64)
    nodes = np.asarray(skeleton["cluster_nodes"], dtype=np.float64)
    radii = np.asarray(skeleton["cluster_radii"], dtype=np.float64)
    edges = np.asarray(skeleton["cluster_edges"],
                       dtype=np.int64).reshape(-1, 2)
    vert_node = np.asarray(skeleton["cluster_vert_node"])
    weights = {**gating.DEFAULT_WEIGHTS, **(weights or {})}

    # thick-region threshold: volume-weighted radius percentile — a plain
    # node-count percentile collapses to the thin-wall radius whenever thin
    # nodes dominate the graph, hiding a lone thick boss from the packing
    # metric
    volume = radii ** 3
    if len(radii):
        by_radius = np.argsort(radii)
        cumulative = np.cumsum(volume[by_radius])
        pick = np.searchsorted(cumulative,
                               thick_percentile / 100.0 * cumulative[-1])
        thick_radius = float(radii[by_radius][min(pick, len(radii) - 1)])
        thick_fraction = float(volume[radii >= thick_radius].sum()
                               / max(volume.sum(), 1e-30))
    else:
        thick_radius, thick_fraction = 0.0, 0.0

    _report(progress, 0.05, "generating gate candidates")
    cands, rejected_thin = gating.generate_candidates(
        verts, thickness, vert_node, min_gate_thickness=min_gate_thickness,
        max_candidates=max_candidates, thick_diameter=1.9 * thick_radius)
    generated = int(len(cands))
    rejected = {"thin": rejected_thin, "slide": 0, "internal": 0,
                "conflict": 0, "side": 0, "disconnected": 0}

    # optional mold-orientation context: category filters, side, parting
    mold_hash, mold_payload, mold_arrays = _latest_mold_orientation(workdir)
    orientation = {"used": False, "reason": "no mold_orientation result"}
    sides = np.full(len(cands), "unknown", dtype=object)
    parting_tree = None
    if mold_payload is not None and mold_arrays is not None:
        option = int(orientation_option)
        if f"membership_{option}" not in mold_arrays:
            option = 0
    if (mold_payload is not None and mold_arrays is not None
            and f"membership_{option}" in mold_arrays):
        brep_path = os.path.join(workdir, BREP_FACES_FILE)
        brep_ids = np.load(brep_path) if os.path.exists(brep_path) else None
        default_name = f"brep_default_{option}"
        use_brep = brep_ids is not None and default_name in mold_arrays
        if use_brep:
            defaults = mold_arrays[default_name]
            face_cats = gating.face_categories_from_defaults(
                defaults[brep_ids.astype(np.int64)])
            parting_tree = _parting_tree(workdir, defaults)
        else:
            face_cats = gating.face_categories_from_membership(
                mold_arrays[f"membership_{option}"])
        vert_cats = gating.vertex_categories(faces, face_cats, len(verts))
        orientation = {"used": True, "hash": mold_hash, "option": option,
                       "brep": bool(use_brep), "parting_from": "defaults"}

        cats = vert_cats[cands]
        internal = (cats & gating.CAT_INTERNAL) > 0
        conflict = ((cats & gating.CAT_CONFLICT) > 0) & ~internal
        slide = ((cats & gating.CAT_SLIDE) > 0) & ~internal & ~conflict
        rejected["internal"] = int(internal.sum())
        rejected["conflict"] = int(conflict.sum())
        rejected["slide"] = int(slide.sum())
        cands = cands[~(internal | conflict | slide)]
        sides = gating.side_labels(vert_cats[cands])
        if forbid_side in ("A", "B"):
            banned = sides == forbid_side
            rejected["side"] = int(banned.sum())
            cands, sides = cands[~banned], sides[~banned]

    _report(progress, 0.2, f"screening {len(cands)} candidates")
    cand_nodes = vert_node[cands].astype(np.int64)
    raws, reached = gating.screen_candidates(
        nodes, radii, edges, cand_nodes, thick_radius=thick_radius,
        pack_factor=pack_factor,
        progress=(lambda f, m: _report(progress, 0.2 + 0.6 * f, m)))
    connected = reached >= 0.5
    rejected["disconnected"] = int((~connected).sum())
    cands, cand_nodes, sides = (cands[connected], cand_nodes[connected],
                                sides[connected])
    raws = {name: values[connected] for name, values in raws.items()}

    _report(progress, 0.85, "scoring candidates")
    subscores, score, degenerate = gating.normalize_scores(raws, weights)
    order = np.argsort(-score, kind="stable")

    parting_dist = None
    if parting_tree is not None and len(cands):
        parting_dist, _ = parting_tree.query(verts[cands], workers=-1)

    # any incident face works as the candidate's representative face
    vert_face = np.zeros(len(verts), dtype=np.int64)
    for corner in range(3):
        vert_face[faces[:, corner]] = np.arange(len(faces))

    if len(cands):
        best_fill, weld_best = gating.fill_and_weld(
            nodes, radii, edges, cand_nodes[order[0]])
    else:
        best_fill = np.full(len(radii), np.inf)
        weld_best = np.zeros(len(edges), dtype=np.uint8)

    proposals = []
    for rank, idx in enumerate(order[:top_n]):
        idx = int(idx)
        sub = {name: float(subscores[idx, col])
               for col, name in enumerate(gating.METRICS)}
        raw = {name: float(raws[name][idx]) for name in gating.METRICS}
        distance = (float(parting_dist[idx])
                    if parting_dist is not None else None)
        style = ("unknown" if distance is None
                 else "edge" if distance <= edge_gate_distance else "hot_tip")
        proposals.append({
            "rank": rank,
            "vertex": int(cands[idx]),
            "face": int(vert_face[cands[idx]]),
            "node": int(cand_nodes[idx]),
            "point": [float(c) for c in verts[cands[idx]]],
            "score": float(score[idx]),
            "subscores": sub,
            "raw": raw,
            "side": str(sides[idx]),
            "parting_distance": distance,
            "gate_style": style,
            "reasons": gating.proposal_reasons(
                sub, raw, side=str(sides[idx]), parting_distance=distance,
                gate_style=style, degenerate=degenerate),
        })

    def sprue_meta(role, graph, array, dtype, **extra):
        return {"kind": "sprue", "association": "graph", "graph": graph,
                "role": role, "dtype": dtype, "length": int(array.size),
                "count": int(array.shape[0]), **extra}

    arrays = {
        "candidate_points": verts[cands].astype("f4").reshape(-1, 3),
        "candidate_node": cand_nodes.astype(np.uint32),
        "candidate_vertex": cands.astype(np.uint32),
        "candidate_face": vert_face[cands].astype(np.uint32),
        "candidate_score": score.astype("f4"),
        "candidate_subscores": subscores.astype("f4"),
        "proposal_index": order[:top_n].astype(np.uint32),
        "best_fill": best_fill.astype("f4"),
        "weld_edges_best": weld_best.astype(np.uint8),
    }
    field_meta = {
        "candidate_points": sprue_meta("nodes", "sprue",
                                       arrays["candidate_points"], "f4"),
        "candidate_node": sprue_meta("data", "sprue",
                                     arrays["candidate_node"], "u4"),
        "candidate_vertex": sprue_meta("data", "sprue",
                                       arrays["candidate_vertex"], "u4"),
        "candidate_face": sprue_meta("data", "sprue",
                                     arrays["candidate_face"], "u4"),
        "candidate_score": sprue_meta("scalar", "sprue",
                                      arrays["candidate_score"], "f4"),
        "candidate_subscores": sprue_meta(
            "data", "sprue", arrays["candidate_subscores"], "f4",
            metrics=list(gating.METRICS)),
        "proposal_index": sprue_meta("data", "sprue",
                                     arrays["proposal_index"], "u4"),
        "best_fill": sprue_meta("scalar", "clustered",
                                arrays["best_fill"], "f4"),
        "weld_edges_best": sprue_meta("mask", "clustered",
                                      arrays["weld_edges_best"], "u1"),
    }

    stats = {
        "schema": 2,
        "skeleton_hash": skeleton_hash,
        "graph": "cluster",
        "nodes": int(len(radii)),
        "edges": int(len(edges)),
        "mesh": mesh_spec,
        "orientation": orientation,
        "confidence": "full" if orientation["used"] else "no_orientation",
        "candidates": {
            "eligible_verts": int((vert_node != NODE_SENTINEL).sum()),
            "generated": generated,
            "scored": int(len(cands)),
            "rejected": rejected,
        },
        "thick": {"radius_threshold": thick_radius,
                  "percentile": float(thick_percentile),
                  "pack_radius": float(pack_factor * thick_radius),
                  "volume_fraction": thick_fraction},
        "weights": {name: float(weights[name]) for name in gating.METRICS},
        "metrics": list(gating.METRICS),
        "degenerate_metrics": degenerate,
        "proposals": proposals,
    }
    logger.info(
        f"sprue proposals: {generated} candidates, {len(cands)} scored, "
        f"top score {proposals[0]['score']:.3f}" if proposals
        else "sprue proposals: no viable candidates")
    return stats, arrays, field_meta


def ejection_sticking(workdir, *, skeleton, skeleton_hash, mesh_spec=None,
                      grip_deg=15.0, mu=0.5, p_shrink=0.5,
                      orientation_option=0, progress=None):
    """Draft-scaled wall sticking model for ejector-pin simulation.

    Per-face draft angle relative to the mold pull axis, grip mask and
    release traction (the force distribution ejector pins must overcome),
    plus the same loads aggregated per clustered skeleton node for the
    interactive stiffness solve. A mold_orientation result supplies the
    pull axis and restricts gripping to the B/core side when present;
    otherwise the pull falls back to +Z with a degraded-confidence note.
    Returns (stats, arrays, field_meta) — storing is the caller's job.
    """
    import ejection

    verts, faces = load_mesh_arrays(workdir)
    faces = faces.astype(np.int64, copy=False)
    radii = np.asarray(skeleton["cluster_radii"], dtype=np.float64)
    vert_node = np.asarray(skeleton["cluster_vert_node"])

    _report(progress, 0.1, "face geometry")
    normals, areas = ejection.face_geometry(verts, faces)

    # pull axis + B-side scope from the newest mold orientation, if any
    mold_hash, mold_payload, mold_arrays = _latest_mold_orientation(workdir)
    pull = np.array([0.0, 0.0, 1.0])
    scope = None
    orientation = {"used": False, "reason": "no mold_orientation result"}
    if mold_payload is not None:
        options = mold_payload.get("stats", {}).get("options", [])
        option = int(orientation_option)
        if not 0 <= option < len(options):
            option = 0
        if options:
            arrows = options[option].get("arrows", [])
            direction = next((arrow["direction"] for arrow in arrows
                              if arrow.get("kind") == "main_b"), None)
            if direction is not None:
                pull = np.asarray(direction, dtype=np.float64)
                orientation = {"used": True, "hash": mold_hash,
                               "option": option, "brep": False}
        if orientation["used"] and mold_arrays is not None \
                and f"membership_{option}" in mold_arrays:
            brep_path = os.path.join(workdir, BREP_FACES_FILE)
            brep_ids = (np.load(brep_path) if os.path.exists(brep_path)
                        else None)
            default_name = f"brep_default_{option}"
            if brep_ids is not None and default_name in mold_arrays:
                defaults = mold_arrays[default_name]
                scope = defaults[brep_ids.astype(np.int64)] == 1  # side B
                orientation["brep"] = True
            else:
                membership = mold_arrays[f"membership_{option}"]
                scope = (membership & 2) > 0  # reachable from side B

    _report(progress, 0.4, "sticking forces")
    draft = ejection.draft_angles(normals, pull)
    grip, face_force = ejection.sticking_forces(
        normals, areas, pull, grip_deg=grip_deg, mu=mu, p_shrink=p_shrink,
        scope=scope)
    vert_force = ejection.vertex_loads(faces, face_force, len(verts))
    node_load, lost = ejection.node_loads(vert_force, vert_node, len(radii))

    arrays = {
        "draft_deg": draft.astype("f4"),
        "grip_faces": grip.astype(np.uint8),
        "vert_force": vert_force.astype("f4"),
        "node_load": node_load.astype("f4"),
    }
    field_meta = {
        "draft_deg": {"kind": "ejection_draft", "association": "face",
                      "role": "scalar", "units": "deg", "dtype": "f4"},
        "grip_faces": {"kind": "ejection_grip", "association": "face",
                       "role": "mask", "dtype": "u1"},
        "vert_force": {"kind": "ejection_sticking", "association": "vertex",
                       "role": "scalar", "units": "N", "dtype": "f4"},
        "node_load": {"kind": "ejection_sticking", "association": "graph",
                      "graph": "clustered", "role": "scalar", "units": "N",
                      "dtype": "f4", "length": int(node_load.size),
                      "count": int(node_load.shape[0])},
    }
    total = float(face_force.sum())
    stats = {
        "schema": 2,
        "skeleton_hash": skeleton_hash,
        "mesh": mesh_spec,
        "pull": [float(c) for c in pull],
        "orientation": orientation,
        "confidence": "full" if orientation["used"] else "no_orientation",
        "grip_deg": float(grip_deg),
        "mu": float(mu),
        "p_shrink": float(p_shrink),
        "totals": {
            "sticking_force_n": total,
            "gripping_area_mm2": float(areas[grip].sum()),
            "gripping_faces": int(grip.sum()),
        },
        "lost_load_fraction": lost,
        "nodes": int(len(radii)),
        "edges": int(np.asarray(skeleton["cluster_edges"]).size // 2),
    }
    logger.info(
        f"ejection sticking: {stats['totals']['gripping_faces']} gripping "
        f"faces, total {total:.1f} N, confidence {stats['confidence']}")
    return stats, arrays, field_meta


def highlight_union(workdir, include=(), exclude=()):
    """Union of accessibility rows (or its inverse) as highlighted faces."""
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))

    if include:
        union = np.any(accessibility[list(include), :], axis=0)
    elif exclude:
        union = np.any(accessibility[list(exclude), :], axis=0)
        union = np.invert(union)
    else:
        union = np.zeros(accessibility.shape[1], dtype=bool)

    indices = np.where(union)[0].tolist()
    logger.debug(f"Highlighting {len(indices)} faces")
    write_highlights(workdir, indices)
    return indices


def precompute_fields(workdir, *, directions, pixel=None, tips=(), clearances=(),
                      engine="zmap", window=0.3, progress=None):
    """Cache height maps and per-tip/per-clearance fields for directions.

    ``pixel`` None = resolution/5 from mesh_meta (legacy fallback 0.1 mm).
    """
    from zmap import DirectionCache

    pixel = resolve_pixel(workdir, pixel)
    verts, faces = load_mesh_arrays(workdir)
    tips = [tips_entry if isinstance(tips_entry, tuple) else tuple(tips_entry)
            for tips_entry in tips]

    computed = []
    for step, direction_index in enumerate(directions):
        logger.info(f"Direction {direction_index}")
        _report(progress, step / max(len(directions), 1),
                f"direction {direction_index}")
        cache = DirectionCache(workdir, direction_index, verts=verts, faces=faces,
                               pixel=pixel, window=window, engine=engine)
        for diameter, corner_radius in tips:
            cache.tip_gap(diameter, corner_radius)
        for radius in clearances:
            cache.clearance(radius)
        if engine == "zmap":
            # tip-aware holder stickout fields per (tip, cylinder radius)
            for diameter, corner_radius in tips:
                for radius in clearances:
                    cache.tip_min_stickout(diameter, corner_radius, radius)
        computed.append(int(direction_index))

    return {
        "directions": computed,
        "tips": [list(tip) for tip in tips],
        "clearances": [float(r) for r in clearances],
        "engine": engine,
    }


def compose_tool(workdir, direction, *, pixel=None, tollerance=1e-1, diameter=2.0,
                 corner_radius=0.0, stickout=None, cylinders=None, sweep=(),
                 wall_tollerance=1.0, engine="zmap", window=0.3, progress=None):
    """Evaluate a full tool assembly from precomputed fields.

    Uses the canonical per-face rule (zmap.tool_face_verdict), including the
    side-milled treatment of near-vertical walls the viewer applies.
    ``pixel`` None = resolution/5 from mesh_meta (legacy fallback 0.1 mm).
    """
    import machining
    from zmap import DirectionCache, tool_face_verdict

    pixel = resolve_pixel(workdir, pixel)
    verts, faces = load_mesh_arrays(workdir)
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))

    _report(progress, 0.1, "composing tool verdict")
    cache = DirectionCache(workdir, direction, verts=verts, faces=faces,
                           pixel=pixel, window=window, engine=engine)
    angles = machining.face_angles_deg(machining.face_unit_normals(verts, faces),
                                       cache.direction)
    machinable, _, min_stick = tool_face_verdict(
        cache, faces, angles, diameter=diameter, corner_radius=corner_radius,
        stickout=stickout, cylinders=cylinders, tollerance=tollerance,
        wall_tollerance=wall_tollerance)

    # Keep only the faces that are accessible
    unreachable_faces = np.flatnonzero(~machinable & accessibility[direction])

    accessible_count = int(accessibility[direction].sum())
    logger.info(f"Tool D={diameter} rc={corner_radius} stickout={stickout} cannot reach {len(unreachable_faces)} of {accessible_count} accessible faces")

    # A stickout sweep is free: re-threshold the cached per-vertex fields
    sweep_results = []
    if sweep and min_stick is not None:
        for sweep_stickout in sweep:
            swept_ok, _, _ = tool_face_verdict(
                cache, faces, angles, diameter=diameter,
                corner_radius=corner_radius, stickout=sweep_stickout,
                cylinders=cylinders, tollerance=tollerance,
                wall_tollerance=wall_tollerance)
            swept = int((~swept_ok & accessibility[direction]).sum())
            logger.info(f"  stickout {sweep_stickout:8.2f}: {swept} unreachable faces")
            sweep_results.append({"stickout": float(sweep_stickout),
                                  "unreachable": swept})

    unreachable_faces = unreachable_faces.tolist()
    write_highlights(workdir, unreachable_faces)

    return {
        "unreachable": len(unreachable_faces),
        "accessible": accessible_count,
        "sweep": sweep_results,
        "faces": unreachable_faces,
    }
