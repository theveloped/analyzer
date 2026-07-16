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
    heal_mesh,
    save_mesh,
    get_mesh_data,
    subdivide_mesh,
    sample_unity_vector_pairs,
    compute_accessibility,
)
from utils import (ensure_directory, file_fingerprint, files_fingerprint,
                   has_valid_extension)

FINE_MESH_FILE = "fine_mesh.obj"
FINE_VERTS_FILE = "fine_verts.npy"
FINE_FACES_FILE = "fine_faces.npy"
NORMALS_FILE = "normals.npy"
MESH_META_FILE = "mesh_meta.json"
DIRECTIONS_FILE = "directions.npy"
DIRECTIONS_META_FILE = "directions_meta.json"
ACCESSIBILITY_FILE = "accessibility.npy"
BREP_FACES_FILE = "brep_faces.npy"
BREP_META_FILE = "brep_meta.json"
BREP_EDGES_FILE = "brep_edges.npy"
BREP_EDGE_PAIRS_FILE = "brep_edge_pairs.npy"
HIGHLIGHT_FILE = "highlights.json"

# user face-split sidecars (splits.py owns their semantics; the constants
# live here so pipeline/manifest can reference them without importing splits)
FACE_SPLITS_FILE = "face_splits.json"
SUBFACES_FILE = "subfaces.npy"
SUBFACE_EDGES_FILE = "subface_edges.npy"
SUBFACE_EDGE_PAIRS_FILE = "subface_edge_pairs.npy"
SUBFACE_META_FILE = "subface_meta.json"

# mold_orientation stats schema — must track MOLD_SCHEMA in
# processes/injection_molding.py (importing processes here would be circular)
MOLD_STATS_SCHEMA = 4  # 4: brep_valid/brep_default indexed by effective ids

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


def accessibility_fingerprint(workdir):
    """Short content hash of accessibility.npy (None before prep/directions).

    Re-running directions with different visibility parameters (tollerance,
    pixel, chord_error) changes the matrix while directions.npy stays
    byte-identical — results derived from the matrix salt this in too so
    they aren't served stale.
    """
    return file_fingerprint(os.path.join(workdir, ACCESSIBILITY_FILE))


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


def splits_fingerprint(workdir):
    """Short content hash of subfaces.npy (None when no face splits exist).

    Results that aggregate per BREP face salt this in so user face splits
    orphan them. Hashing the derived labeling (not face_splits.json) means
    a stored-but-not-yet-separating cut leaves results valid — the
    aggregation only changes when the labeling does — and an undone cut
    reverts the fingerprint byte-identically, re-validating older results.
    """
    return file_fingerprint(os.path.join(workdir, SUBFACES_FILE))


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


def _facet_normals(verts, faces):
    """Per-face unit normals from the triangle cross products."""
    tri = np.asarray(verts)[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    return (normals / np.maximum(lengths, 1e-30)).astype("<f4")


def load_face_normals(workdir):
    """Per-face unit normals for classification (facing/wall/draft tests).

    mesh_part stores exact BREP surface normals for STEP parts (quadrics
    analytically, freeform surfaces via UV evaluation on the live shape) as
    normals.npy — served here when fresh. Legacy/STL workdirs get facet
    cross-product normals, computed lazily and cached in the same file.
    """
    faces_path = os.path.join(workdir, FINE_FACES_FILE)
    normals_path = os.path.join(workdir, NORMALS_FILE)
    if (os.path.exists(normals_path)
            and os.path.getmtime(normals_path) >= os.path.getmtime(faces_path)):
        stored = np.load(normals_path)
        faces = np.load(faces_path, mmap_mode="r")
        face_count = int(faces.shape[0])
        del faces  # release the Windows file handle promptly
        if stored.shape == (face_count, 3):
            return stored

    verts, faces = load_mesh_arrays(workdir)
    normals = _facet_normals(verts, faces)
    np.save(normals_path, normals)
    return normals


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


def part_deflection(workdir):
    """The BREP tessellation deflection the part was meshed at — the chord
    error bound of the fine mesh. 0.0 for STL/heal meshes (their facets are
    the ground truth) and legacy workdirs without mesh_meta.json."""
    meta_path = os.path.join(workdir, MESH_META_FILE)
    if not os.path.exists(meta_path):
        return 0.0
    with open(meta_path) as f:
        deflection = json.load(f).get("deflection")
    return float(deflection) if deflection else 0.0


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
              subdivide=None, deflection=None, obj=False, progress=None):
    """Canonicalize an input STL/STEP into a part working directory.

    Writes fine_verts.npy + fine_faces.npy (the stable face indexing every
    later stage refers to). STEP input tessellates through the BREP
    (brep.mesh_shape) so every fine face carries its source BREP face id
    (brep_faces.npy) — healing destroys the surfaces and falls back to the
    anonymous meshlib path, as does STL input. ``obj=True`` additionally
    exports fine_mesh.obj for external tools — nothing in the pipeline or
    viewer reads it, and at fine resolutions it costs real time and disk.

    ``resolution`` is the single analysis-resolution knob: it defaults the
    subdivide edge target (= resolution), the BREP tessellation sag
    (= resolution / 8, so curved faces carry their true shape at analysis
    scale while planes stay coarse), the heal voxel size (= resolution / 5)
    and, via mesh_meta.json, the zmap pixel of every later stage
    (= resolution / 5). ``subdivide``/``deflection`` remain expert
    overrides. Returns the workdir and counts.
    """
    has_valid_extension(input_path, MESH_EXTENSIONS)

    is_step = os.path.splitext(input_path)[1].lower() in (".stp", ".step")
    brep_ids = None
    surface_types = None

    if is_step and not heal:
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
        (verts, faces, brep_ids, surface_types, surface_params,
         corner_uv) = brep.mesh_shape(shape, deflection=deflection)

        # parent-facet ids ride along with the BREP ids through subdivision:
        # children stay coplanar with their parent, so the parent's corner
        # UVs recover any child centroid's surface parameters exactly
        coarse_faces = faces
        parents = np.arange(len(faces), dtype=np.int32)
        if subdivide:
            _report(progress, 0.4, "subdividing mesh (tag preserving)")
            verts, faces, tags = brep.subdivide_tagged(
                verts, faces, np.stack([brep_ids, parents], axis=1),
                subdivide)
            brep_ids, parents = tags[:, 0], tags[:, 1]

        verts = verts.astype(np.float32)
        mesh = mn.meshFromFacesVerts(faces, verts)
    else:
        _report(progress, 0.0, "loading mesh")
        mesh = load_mesh(input_path)

        deflection = None  # no BREP tessellation on this path
        if resolution is None:
            box = mesh.computeBoundingBox()
            resolution = auto_subdivide((box.max - box.min).length())
        if subdivide is None:  # blank = resolution; 0 disables explicitly
            subdivide = resolution
        logger.info(f"resolution {resolution:.2f} mm -> subdivide "
                    f"{subdivide:.2f} mm")
        if heal:
            _report(progress, 0.2, "healing (voxel remesh)")
            mesh = heal_mesh(mesh, resolution / 5.0)
        if subdivide:
            _report(progress, 0.5, "subdividing mesh")
            mesh = subdivide_mesh(mesh, subdivide)
        verts, faces = get_mesh_data(mesh)

    if not workdir:
        input_name = os.path.basename(input_path)
        input_name = input_name.rsplit(".", 1)[0]
        workdir = os.path.join(os.path.abspath("."), input_name)

    ensure_directory(workdir)
    verts_path = os.path.join(workdir, FINE_VERTS_FILE)
    faces_path = os.path.join(workdir, FINE_FACES_FILE)

    _report(progress, 0.8, "storing mesh")
    logger.debug(f"Storing verts: {verts_path}")
    np.save(verts_path, verts)

    logger.debug(f"Storing faces: {faces_path}")
    np.save(faces_path, faces)

    if obj:
        obj_path = os.path.join(workdir, FINE_MESH_FILE)
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
                       "surface_types": surface_types,
                       "surface_params": surface_params}, f)
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

        # exact surface normals on every BREP face — quadrics from their
        # analytic params, freeform (bspline etc.) via UV evaluation on the
        # live shape. Classification uses these, geometry stays facet.
        # Written LAST so the normals cache is fresh by the mtime rule.
        _report(progress, 0.95, "computing exact surface normals")
        normals, exact = brep.analytic_face_normals(
            verts, faces, brep_ids, surface_params,
            _facet_normals(verts, faces))
        normals, freeform_exact = brep.freeform_face_normals(
            shape, verts, faces, brep_ids, surface_params,
            coarse_faces, corner_uv, parents, normals)
        exact |= freeform_exact
        np.save(os.path.join(workdir, NORMALS_FILE),
                normals.astype("<f4"))

        # surface the survivors: fine faces whose classification normal is
        # still the chord facet's (unhandled surface type, missing UVs,
        # degeneracies, ambiguous sign votes) — silent chord-frozen normals
        # are how near-tangent classification speckle sneaks back in
        no_params_face = np.array(
            [p is None for p in surface_params])[brep_ids]
        no_uv = no_params_face & ~np.isfinite(corner_uv).all(
            axis=(1, 2))[parents]
        kept = ~exact
        counts["normals_exact"] = int(exact.sum())
        counts["normals_facet"] = int(kept.sum())
        counts["normals_facet_no_uv"] = int((kept & no_uv).sum())
        if kept.any():
            logger.info(
                f"{int(kept.sum())} of {len(faces)} fine faces kept facet "
                f"normals ({int((kept & no_uv).sum())} on freeform faces "
                f"without UV nodes, {int((kept & ~no_uv).sum())} degenerate "
                f"or ambiguous)")

    return {"workdir": workdir, "counts": counts}


def compute_directions(workdir, *, count=64, axes=False, tollerance=0.1,
                       pixel=None, progress=None):
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
    chord_error = part_deflection(workdir)
    _report(progress, 0.1, f"accessibility for {directions.shape[0]} directions")
    accessibility = compute_accessibility(mesh, directions, face_count,
                                          tolerance_deg=tollerance, pixel=pixel,
                                          normals=load_face_normals(workdir),
                                          chord_error=chord_error)

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
                   "pixel": None if pixel is None else float(pixel),
                   "chord_error": float(chord_error)}, f)

    return {
        "directions": int(directions.shape[0]),
        "faces": int(face_count),
    }


def compute_thickness(workdir, *, max_radius=None, inverted=False,
                      max_iters=1000, sharp_deg=25.0, contact_angles=False,
                      progress=None):
    """Per-vertex maximal inscribed ("rolling") sphere diameter.

    inverted=False measures wall thickness inside the part. inverted=True
    runs the same search on an orientation-flipped copy of the mesh, so the
    exterior becomes the inside and the value is the local gap between
    opposing walls — on the same vertex indexing, no boolean inversion or
    cross-mesh mapping needed. Values cap at 2*max_radius (saturated = no
    opposing wall worth considering); max_radius=None derives meshlib's
    recommended 0.5 * min(bbox dims).

    The field itself is the raw probe: every reading is a valid empty ball,
    a trustworthy lower bound, so it is never modified. The tangent-at-
    vertex construction does read falsely LOW near sharp convex edges
    (concave ones for the gap) and on chord creases of C1-continuous BREP
    faces — those artifacts are captured in the returned masks instead:
    `band_lo`/`band_hi` bound the readings explainable by the nearest
    matching-sign sharp feature (_sharp_edge_vertices/_edge_band; `limit`
    = 2*d*tan(Omega/2) is the nominal explainable diameter, for display)
    and `suspect` flags penetrating reconstructed centers (crease wobble,
    _penetrating_mask). Combine them with edge_excluded() to keep thin-wall
    flags off edge artifacts at any threshold — cheap array logic, shared
    by the CLI and the viewer. sharp_deg=0/None skips the masks entirely.
    contact_angles=True additionally stores each ball's separation angle
    (_contact_angles: wall ~180 deg, N-degree corner ~N, edge ~0,
    saturated NaN) — an opt-in diagnostic of how wall-like each reading
    is.

    Returns (values float32[verts], max_radius, masks) with masks =
    {"limit"/"band_lo"/"band_hi" float32[verts] (limit -1 = no sharp
    features), "suspect" bool[verts], "angle" float32[verts] or None,
    "floor": float, "tol": EDGE_FIT_TOL}.
    """
    verts, faces = load_mesh_arrays(workdir)
    mesh = mn.meshFromFacesVerts(faces, verts)

    if max_radius is None:
        size = mesh.computeBoundingBox().size()
        max_radius = 0.5 * min(size.x, size.y, size.z)
        logger.debug(f"Auto inscribed sphere max radius: {max_radius:.3f}")

    if inverted:
        mesh.topology.flipOrientation()

    settings = _in_sphere_settings(max_radius, max_iters=max_iters)

    _report(progress, 0.2, "rolling inscribed spheres")
    result = mm.computeInSphereThicknessAtVertices(mesh, settings)
    raw = _per_vertex(np.array(result.vec, dtype=np.float32), len(verts))
    # meshlib returns an unbounded (inf) radius where no sphere is limited by
    # an opposing wall — degenerate/open spots common on welded STEP meshes.
    # Saturate those to the documented cap so the field, its stats and the
    # heatmap stay finite (cap = "no opposing wall worth considering").
    cap = np.float32(2.0 * max_radius)
    values = np.where(np.isfinite(raw), np.minimum(raw, cap), cap)

    if not (sharp_deg or contact_angles):
        masks = {"limit": np.full(len(verts), -1.0, dtype=np.float32),
                 "band_lo": np.zeros(len(verts), dtype=np.float32),
                 "band_hi": np.full(len(verts), -1.0, dtype=np.float32),
                 "suspect": np.zeros(len(verts), dtype=bool),
                 "angle": None, "floor": 0.0, "tol": EDGE_FIT_TOL}
        _report(progress, 1.0, "thickness field done")
        return values, float(max_radius), masks

    _report(progress, 0.7, "edge-limit masks")
    from scipy.spatial import cKDTree

    face_normals = load_face_normals(workdir).astype(np.float64)
    mean_edge = _mean_edge_length(verts, faces)
    floor = _slack_floor(mean_edge, part_deflection(workdir))

    # meshlib marks unbounded balls inconsistently (inf, but also
    # FLT_MAX-scale finite values on welded STEP meshes) — normalize beyond
    # the search radius to inf so the tree passes skip them
    radii = raw.astype(np.float64) / 2.0
    radii[~np.isfinite(radii) | (radii > max_radius * (1 + 1e-3))] = np.inf
    vertex_normals = _vertex_normals(verts, faces, face_normals)
    centers = _reconstruct_centers(verts, vertex_normals, radii,
                                   inverted=inverted)
    vert_tree = cKDTree(verts)

    if sharp_deg:
        suspect = _penetrating_mask(vert_tree, centers, radii, mean_edge)
        brep_path = os.path.join(workdir, BREP_FACES_FILE)
        brep_ids = np.load(brep_path) if os.path.exists(brep_path) else None
        sharp_idx, sharp_tan = _sharp_edge_vertices(
            verts, faces, face_normals, sharp_deg=sharp_deg,
            concave=inverted, brep_ids=brep_ids)
        limit, band_lo, band_hi = _edge_band(
            verts, sharp_idx, sharp_tan, mean_edge=mean_edge, floor=floor)
        logger.debug(f"edge masks: {len(sharp_idx)} sharp vertices, "
                     f"{int(suspect.sum())} penetrating of {len(verts)}")
    else:  # angles only — no exclusions
        suspect = np.zeros(len(verts), dtype=bool)
        limit = np.full(len(verts), -1.0)
        band_lo = np.zeros(len(verts))
        band_hi = np.full(len(verts), -1.0)

    angle = None
    if contact_angles:
        _report(progress, 0.85, "contact angles")
        angle = _contact_angles(vert_tree, verts, vertex_normals, centers,
                                radii, sign=1.0 if inverted else -1.0,
                                floor=floor, max_radius=float(max_radius))
        angle = angle.astype(np.float32)

    masks = {"limit": limit.astype(np.float32),
             "band_lo": band_lo.astype(np.float32),
             "band_hi": band_hi.astype(np.float32),
             "suspect": suspect, "angle": angle,
             "floor": float(floor), "tol": EDGE_FIT_TOL}
    _report(progress, 1.0, "thickness field done")
    return values, float(max_radius), masks


def mold_orientation(workdir, *, max_slides=2, slide_tollerance=2.0, count=10,
                     min_slide_faces=50, field_options=3, progress=None):
    """Search mold orientations and derive per-face assignment fields.

    Returns {"stats": <JSON-safe>, "arrays": {...}, "field_meta": {...}}
    with band/resolved/brep category fields and parting-line segments for
    the top `field_options` options. The brep fields need brep_faces.npy
    (STEP-meshed parts); they are skipped otherwise.
    """
    import molding
    import splits

    verts, faces = load_mesh_arrays(workdir)
    directions = np.load(os.path.join(workdir, DIRECTIONS_FILE))
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))
    brep_ids, _, _ = splits.effective_face_ids(workdir)

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
            defaults = molding.brep_defaults(membership, valid, brep_ids)
            splits.sanitize_retired(valid, defaults, brep_ids)
            arrays[f"brep_valid_{k}"] = valid
            field_meta[f"brep_valid_{k}"] = {
                **common, "variant": "brep_valid", "association": "none",
                "role": "data", "dtype": "u4", "count": int(len(valid))}
            arrays[f"brep_default_{k}"] = defaults
            field_meta[f"brep_default_{k}"] = {
                **common, "variant": "brep_default", "association": "none",
                "role": "data", "dtype": "u1", "count": int(len(defaults))}

    stats = {
        "schema": MOLD_STATS_SCHEMA,
        "face_count": int(accessibility.shape[1]),
        "direction_count": int(directions.shape[0]),
        "directions_fingerprint": directions_fingerprint(workdir),
        "brep": brep_ids is not None,
        "options": options[:count],
    }
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}


SETUPS_STATS_SCHEMA = 3  # 3: brep_valid/brep_default indexed by effective ids


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
        import splits
        valid = molding.brep_validity(membership, brep_ids, setup_count)
        defaults = machining.setup_defaults(membership, valid, brep_ids)
        splits.sanitize_retired(valid, defaults, brep_ids)
        arrays[f"brep_valid_{k}"] = valid
        field_meta[f"brep_valid_{k}"] = {
            **common, "variant": "brep_valid", "association": "none",
            "role": "data", "dtype": "u4", "count": int(len(valid))}
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

    import splits
    brep_ids, _, _ = splits.effective_face_ids(workdir)

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
    normals = load_face_normals(workdir)

    import splits
    brep_ids, _, _ = splits.effective_face_ids(workdir)

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
                                   pixel=pixel, window=window)
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


def _in_sphere_settings(max_radius, max_iters=1000):
    from meshlib import mrmeshpy as mm

    # InSphereSearchSettings also exposes minAngleCos, which skips touch
    # points close to the start vertex *during* shrinking — it changes the
    # returned radius rather than classifying balls, so the support filter
    # below stays a post-pass. Possible future experiment, unused here.
    settings = mm.InSphereSearchSettings()
    settings.insideAndOutside = False
    settings.maxRadius = float(max_radius)
    settings.maxIters = int(max_iters)
    settings.minShrinkage = 1e-6
    return settings


# Wedges tighter than this interior opening are genuine knife/V-thin
# features: their small readings are true thin walls, never artifacts, so
# their edges are not exclusion sources.
SHARP_OMEGA_MIN_DEG = 60.0
# Relative width of the exclusion band around the edge-explainable limit
# 2*d*tan(Omega/2): readings inside [limit/tol - floor, limit*tol + floor]
# are what the wedge alone would produce; readings well below it are
# genuinely thinner than the edge explains and stay flaggable.
EDGE_FIT_TOL = 1.3


def _edge_lengths(verts, faces):
    """(faces, 3) edge lengths of every triangle."""
    tri = verts[faces].astype(np.float64)
    return np.linalg.norm(tri - np.roll(tri, -1, axis=1), axis=2)


def _mean_edge_length(verts, faces):
    return max(float(_edge_lengths(verts, faces).mean()), 1e-6)


def _slack_floor(mean_edge, deflection):
    """Absolute tangency/coverage slack floor: half a mesh edge (surfaces
    are only *sampled* by vertices) plus the chord deflection of curved
    faces."""
    return max(0.5 * mean_edge, float(deflection))


def _sharp_edge_vertices(verts, faces, face_normals, *, sharp_deg,
                         concave=False, brep_ids=None):
    """Vertices on matching-sign sharp feature edges, with wedge factors.

    An edge qualifies iff the exact-normal dihedral is at least sharp_deg,
    the interior wedge opening Omega = 180 - phi is at least
    SHARP_OMEGA_MIN_DEG, the sign matches (convex edges limit thickness
    balls; part-concave edges limit gap balls on the flipped mesh), and —
    when BREP provenance is available — the two faces belong to different
    BREP faces (chord steps interior to one curved BREP face are
    tessellation, not features; STL falls back to angle-only facet
    normals). Returns (vert_idx, tan_half): tan_half = tan(Omega/2), the
    exact wedge limit factor r = d*tan(Omega/2), maximized per vertex over
    its qualifying edges.
    """
    from molding import face_adjacency

    pairs, edge_verts = face_adjacency(faces)
    n1 = face_normals[pairs[:, 0].astype(np.int64)]
    n2 = face_normals[pairs[:, 1].astype(np.int64)]
    cos_phi = np.clip(np.einsum("ij,ij->i", n1, n2), -1.0, 1.0)

    sharp = (cos_phi <= np.cos(np.radians(sharp_deg))) \
        & (cos_phi >= np.cos(np.radians(180.0 - SHARP_OMEGA_MIN_DEG)))
    if brep_ids is not None:
        sharp &= (brep_ids[pairs[:, 0].astype(np.int64)]
                  != brep_ids[pairs[:, 1].astype(np.int64)])

    centroids = verts[faces].astype(np.float64).mean(axis=1)
    across = centroids[pairs[:, 1].astype(np.int64)] \
        - centroids[pairs[:, 0].astype(np.int64)]
    convex = np.einsum("ij,ij->i", n1, across) < 0.0
    sharp &= ~convex if concave else convex

    if not sharp.any():
        return np.zeros(0, dtype=np.int64), np.zeros(0)

    # tan(Omega/2) = cot(phi/2) = sin(phi) / (1 - cos(phi))
    sin_phi = np.sqrt(np.maximum(1.0 - cos_phi[sharp] ** 2, 0.0))
    tan_half = sin_phi / np.maximum(1.0 - cos_phi[sharp], 1e-12)

    edge_verts = edge_verts[sharp].astype(np.int64)
    per_vert = np.zeros(len(verts))
    np.maximum.at(per_vert, edge_verts[:, 0], tan_half)
    np.maximum.at(per_vert, edge_verts[:, 1], tan_half)
    idx = np.flatnonzero(per_vert > 0.0)
    return idx, per_vert[idx]


def _edge_band(verts, sharp_idx, sharp_tan, *, mean_edge, floor,
               tol=EDGE_FIT_TOL):
    """Per-vertex edge-explainable reading band (limit, band_lo, band_hi).

    limit = 2*d*tan(Omega/2) from the nearest matching-sign sharp vertex —
    the exact diameter the wedge alone produces at distance d. The band is
    asymmetric on purpose: the KD distance to sampled edge vertices never
    underestimates the true edge distance, so band_lo = limit/tol - floor
    needs no mesh term (widening it would swallow genuinely tight
    walls/gaps near corners); the discrete probe at on-edge vertices is
    limited by the first ring of sampled triangles (~0.7 edge lengths)
    instead of shrinking to the theoretical 0, so band_hi adds one
    mean-edge worth of wedge reach: tol*(limit + mean_edge*tan) + floor.
    When no sharp features exist limit = -1 and the band is empty.
    """
    from scipy.spatial import cKDTree

    if not len(sharp_idx):
        return (np.full(len(verts), -1.0), np.zeros(len(verts)),
                np.full(len(verts), -1.0))
    d, j = cKDTree(verts[sharp_idx]).query(verts, workers=-1)
    tan = sharp_tan[j]
    limit = 2.0 * d * tan
    band_lo = np.maximum(limit / tol - floor, 0.0)
    band_hi = tol * (limit + mean_edge * tan) + floor
    # a vertex ON the sharp feature can never yield trustworthy thin-wall
    # evidence — its tangent ball is edge-dominated by construction, and at
    # multi-face corners the discrete reading lands anywhere within a few
    # mesh edges — so on-edge readings are excluded outright
    band_hi[d <= 0.5 * mean_edge] = np.inf
    return limit, band_lo, band_hi


def _pair_budget_slices(counts, budget=5_000_000):
    """Consecutive index slices whose hit counts sum to <= budget each, so
    flattened (ball, vertex) pair arrays stay memory-bounded even when
    large balls hit thousands of vertices apiece."""
    cum = np.cumsum(counts, dtype=np.int64)
    start = 0
    while start < len(counts):
        base = int(cum[start - 1]) if start else 0
        end = int(np.searchsorted(cum, base + budget, side="right"))
        end = max(end, start + 1)
        yield slice(start, end)
        start = end


def _contact_angles(vert_tree, verts, normals, centers, radii, *, sign,
                    floor, max_radius=None, chunk=200_000):
    """Separation angle (degrees) per ball: the largest angle any surface
    contact subtends at the center against the ball's own tangency
    direction (p - c). A wall reads ~180, a ball bound by an N-degree
    corner reads ~N, an edge-collapsed ball ~0 — how "wall-like" each
    thickness/gap reading is.

    Contacts are the vertices within r + floor of the center, but the
    angle is measured against each contact's implied tangency direction
    `-sign * n_q` (a ball tangent to a wall with outward normal n touches
    it at `c - sign*r*n` regardless of where on the wall the sample sits)
    — exact on planar walls, so the shell can be generous without the
    position-based overshoot of samples past the tangency. Contacts whose
    implied tangency lands back on the ball's own vertex (same-wall
    samples: |t_q - p| <= r/2) are its p-tangency, not a separate
    contact, and are skipped. NaN where the angle is meaningless:
    sub-resolution balls (center effectively on the surface) and
    saturated ones. Saturated = at the search cap, matching the field's
    own saturated_fraction semantics (meshlib reports unbounded balls
    either as inf markers or as a finite stop at the cap) — skipping them
    up front also avoids the most expensive large-radius tree queries.
    """
    count = len(radii)
    angles = np.full(count, np.nan)
    with np.errstate(invalid="ignore"):
        active = np.isfinite(radii) & (radii > floor)
        if max_radius is not None:
            active &= radii < max_radius * (1 - 1e-4)
    active = np.flatnonzero(active)
    if not len(active):
        return angles

    v64 = verts.astype(np.float64)
    min_cos = np.ones(count)
    for start in range(0, len(active), chunk):
        idx = active[start:start + chunk]
        # query once; the pair budget only bounds the flattened math below
        all_hits = vert_tree.query_ball_point(
            centers[idx], r=radii[idx] + floor, workers=-1,
            return_sorted=False)
        counts = np.fromiter(map(len, all_hits), np.int64, len(all_hits))
        for piece in _pair_budget_slices(counts):
            sub = idx[piece]
            pair_ball = np.repeat(sub, counts[piece])
            pair_vert = np.concatenate(
                all_hits[piece], dtype=np.int64, casting="unsafe")
            u_p = v64[pair_ball] - centers[pair_ball]
            u_p /= np.maximum(
                np.linalg.norm(u_p, axis=1, keepdims=True), 1e-30)
            n_q = normals[pair_vert]
            r_pair = radii[pair_ball][:, None]
            tangency = centers[pair_ball] - sign * r_pair * n_q
            distinct = np.linalg.norm(
                tangency - v64[pair_ball], axis=1) > 0.5 * radii[pair_ball]
            cos = np.einsum("ij,ij->i", u_p, -sign * n_q)
            np.minimum.at(min_cos, pair_ball[distinct], cos[distinct])
    angles[active] = np.degrees(
        np.arccos(np.clip(min_cos[active], -1.0, 1.0)))
    return angles


def edge_excluded(values, band_lo, band_hi, suspect):
    """Readings that must not count as thin walls: inside the explainable
    band around the nearest sharp edge (the wedge alone would produce a
    ball that size — the false-low corner/edge artifact), or with a
    penetrating reconstructed center (C1 crease wobble). Readings below
    the band are genuinely thinner than the edge explains and stay
    flaggable. Two comparisons + an OR — shared by the CLI highlights and
    mirrored by the viewer's interactive thresholds."""
    values = np.asarray(values, dtype=np.float64)
    band = (np.asarray(band_lo, dtype=np.float64) <= values) \
        & (values <= np.asarray(band_hi, dtype=np.float64))
    return band | np.asarray(suspect, dtype=bool)


def _vertex_normals(verts, faces, face_normals):
    """Area-weighted average of the incident faces' unit normals, per vertex.

    With exact BREP surface normals (normals.npy) these vary smoothly within
    a BREP face, so reconstructed sphere centers stop wobbling with the
    tessellation's chord facets."""
    tri = verts[faces].astype(np.float64)
    face_weights = np.linalg.norm(
        np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    accum = np.zeros((len(verts), 3))
    np.add.at(accum, faces.reshape(-1),
              np.repeat(face_normals * face_weights[:, None], 3, axis=0))
    return accum / np.maximum(
        np.linalg.norm(accum, axis=1, keepdims=True), 1e-30)


def _reconstruct_centers(verts, normals, radii, inverted=False):
    """Inscribed-sphere centers: meshlib constrains each center to the
    inward normal at the vertex, so centers reconstruct as p - n*r. On an
    orientation-flipped mesh (the gaps field) meshlib's inward direction is
    the original outward normal, so the sign flips to p + n*r — normals stay
    in the on-disk original orientation. Non-finite (saturated/dropped)
    balls keep their center at the vertex so tree queries stay finite."""
    sign = 1.0 if inverted else -1.0
    reach = np.where(np.isfinite(radii), radii, 0.0)[:, None]
    return verts.astype(np.float64) + sign * normals * reach


def _penetrating_mask(vert_tree, centers, radii, mean_edge):
    """Balls whose reconstructed center sits closer to the surface than
    their own radius (vertex samples of it; mean_edge covers sampling
    slack) — the reading is unreliable: facet-normal wobble on
    C1-continuous faces or degenerate spots. Callers decide the
    consequence (field: exclude from thin flags; skeleton: drop the
    node)."""
    surface_distance, _ = vert_tree.query(centers, workers=-1)
    tolerance = np.maximum(0.05 * radii, 0.5 * mean_edge)
    with np.errstate(invalid="ignore"):  # inf radii yield nan -> False
        penetrating = ((surface_distance < radii - tolerance)
                       & np.isfinite(radii))
    dropped = int(penetrating.sum())
    if dropped > 0.1 * len(radii):
        logger.warning(
            f"{dropped}/{len(radii)} penetrating sphere centers flagged; "
            "mesh may be under-resolved or degenerate")
    return penetrating


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
    into their walls (_absorb_clusters). Centers reconstruct on exact
    normals (_reconstruct_centers); penetrating reconstructions are dropped
    (_penetrating_mask) — every surviving reading is a valid measured ball
    and is kept: a raw meshlib reading is a trustworthy lower bound, and
    the thickest balls (rib junctions) are exactly the ones sprue packing
    needs. Stats include a mesh-resolution spec (p95 edge length vs the
    median measured wall thickness) — the workdir's single canonical mesh
    is shared by every analysis and visualization, and this gate validates
    that its resolution is adequate for thickness/skeleton work. Returns
    (stats, arrays, field_meta) — storing the result is the caller's job.
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
    from scipy.spatial import cKDTree

    # normalize meshlib's inconsistent unbounded markers (inf, but also
    # FLT_MAX-scale finite values on welded STEP meshes) so the finite
    # gates and stats hold
    radii[~np.isfinite(radii) | (radii > max_radius * (1 + 1e-3))] = np.inf
    centers = _reconstruct_centers(
        verts, _vertex_normals(
            verts, faces, load_face_normals(workdir).astype(np.float64)),
        radii)
    mean_edge = _mean_edge_length(verts, faces)
    penetrating = _penetrating_mask(cKDTree(verts), centers, radii,
                                    mean_edge)
    radii = radii.copy()
    radii[penetrating] = np.nan
    corrected = int(penetrating.sum())
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
    edge_lengths = _edge_lengths(verts, faces)
    mesh_edge = mean_edge
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


# --------------------------------------------------------------------------
# voxel/SDF flow analysis
#
# The wall skeleton's graph connectivity is a tessellation artifact, so it
# cannot carry trustworthy flow numbers. The voxel model replaces it for
# fill physics: one signed-distance volume of the part, Hele-Shaw front
# propagation over the implicit grid (speed ~ wall-distance^2 self-selects
# mid-channel paths — no medial-axis extraction needed), plus a frozen-skin
# fixed point that closes thin late-filling walls (short-shot risk).

FLOW_MAX_CELLS = 32_000_000

# half offsets: each undirected neighbor pair once (dijkstra directed=False)
_FLOW_OFFSETS_6 = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.int64)
_FLOW_OFFSETS_26 = np.array(
    [[dx, dy, dz]
     for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
     if (dx, dy, dz) > (0, 0, 0)], dtype=np.int64)


def _flow_voxel_size(workdir, voxel, diagonal):
    """Explicit voxel size, else the part's single analysis resolution,
    else a bbox-derived legacy fallback."""
    if voxel is not None:
        return float(voxel)
    resolution = part_resolution(workdir)
    if resolution:
        logger.info(f"flow voxel {resolution:.3f} mm from part resolution")
        return resolution
    return float(np.clip(diagonal / 192.0, 0.15, 1.5))


def _flow_grid(vmin, vmax, voxel, pad_voxels, max_cells):
    """(origin, h, dims, adjusted) with the max_cells cap applied."""
    extent = np.asarray(vmax, dtype=np.float64) - np.asarray(vmin,
                                                             dtype=np.float64)
    h = float(voxel)
    adjusted = False
    for _ in range(8):
        dims = np.maximum(
            np.ceil((extent + 2 * pad_voxels * h) / h).astype(np.int64), 1)
        if int(dims.prod()) <= max_cells:
            break
        h *= float((dims.prod() / max_cells) ** (1.0 / 3.0)) * 1.01
        adjusted = True
    origin = np.asarray(vmin, dtype=np.float64) - pad_voxels * h
    return origin, h, dims, adjusted


def flow_voxels(workdir, *, voxel=None, pad_voxels=2, max_cells=FLOW_MAX_CELLS,
                probe_depth=8, progress=None):
    """SDF voxelization of the part interior for flow/cooling analysis.

    One meshlib signed-distance volume (negative inside, voxel centers at
    origin + (i + 0.5) * h, C-order linear indexing) is the only geometry
    pass; everything downstream is numpy over the interior voxel set. Each
    interior voxel carries its distance to the mold wall — the local
    half-thickness both the Hele-Shaw fill solve and the cooling estimate
    key on. Every surface vertex maps to a near-ridge interior voxel by
    probing the SDF along the inward vertex normal (deepest interior
    sample within probe_depth * h wins), so per-voxel results paint onto
    the mesh without near-wall arrival artifacts; the probed depth doubles
    as the per-vertex half-thickness (saturating at probe_depth * h on
    very thick sections). Returns (stats, arrays, field_meta) — storing
    is the caller's job.
    """
    verts, faces = load_mesh_arrays(workdir)
    faces = faces.astype(np.int64, copy=False)
    vert_count = len(verts)
    vmin = verts.min(axis=0).astype(np.float64)
    vmax = verts.max(axis=0).astype(np.float64)
    diagonal = float(np.linalg.norm(vmax - vmin))
    requested = _flow_voxel_size(workdir, voxel, diagonal)
    origin, h, dims, adjusted = _flow_grid(vmin, vmax, requested,
                                           pad_voxels, max_cells)
    nx, ny, nz = (int(d) for d in dims)
    if adjusted:
        logger.warning(f"flow grid capped at {max_cells} cells: "
                       f"voxel {requested:g} -> {h:g} mm")

    _report(progress, 0.05, "signed distance volume")
    mesh = mn.meshFromFacesVerts(faces, verts)
    mesh_volume = float(mesh.volume())
    params = mm.MeshToDistanceVolumeParams()
    params.vol.origin = mm.Vector3f(*origin)
    params.vol.voxelSize = mm.Vector3f(h, h, h)
    params.vol.dimensions = mm.Vector3i(nx, ny, nz)
    params.dist.signMode = mm.SignDetectionMode.HoleWindingRule
    params.dist.maxDistSq = max(diagonal ** 2, 1.0)
    if progress is not None:
        params.vol.cb = lambda f: (
            progress(0.05 + 0.55 * f, "signed distance volume"), True)[1]
    volume = mm.meshToDistanceVolume(mesh, params)
    sdf = np.ascontiguousarray(mn.getNumpy3Darray(volume),
                               dtype=np.float32).ravel()
    del volume, mesh

    _report(progress, 0.65, "extracting interior voxels")
    inside = sdf < 0.0
    voxel_index = np.flatnonzero(inside).astype(np.uint32)
    voxel_dist = -sdf[voxel_index.astype(np.int64)]
    interior_volume = float(len(voxel_index)) * h ** 3
    ratio = interior_volume / max(mesh_volume, 1e-30)
    sign_check = "ok" if 0.5 <= ratio <= 1.5 else "suspect"

    _report(progress, 0.7, "mapping vertices to ridge voxels")
    normals = _vertex_normals(verts, faces,
                              load_face_normals(workdir).astype(np.float64))
    depths = np.arange(1, 2 * probe_depth + 1) * (0.5 * h)
    dims64 = np.array([nx, ny, nz], dtype=np.int64)
    vert_voxel = np.full(vert_count, NODE_SENTINEL, dtype=np.uint32)
    vert_half = np.full(vert_count, np.nan, dtype=np.float32)
    chunk = 100_000
    for start in range(0, vert_count, chunk):
        stop = min(start + chunk, vert_count)
        pos = (verts[start:stop, None, :].astype(np.float64)
               - normals[start:stop, None, :] * depths[None, :, None])
        ijk = np.floor((pos - origin) / h).astype(np.int64)
        ok = ((ijk >= 0) & (ijk < dims64)).all(axis=2)
        lin = (ijk[..., 0] * ny + ijk[..., 1]) * nz + ijk[..., 2]
        lin[~ok] = 0
        depth = np.where(ok & inside[lin], -sdf[lin], -np.inf)
        best = np.argmax(depth, axis=1)
        rows = np.arange(stop - start)
        best_depth = depth[rows, best]
        mapped = best_depth > 0.0
        compact = np.searchsorted(voxel_index,
                                  lin[rows, best][mapped].astype(np.uint32))
        vert_voxel[start:stop][mapped] = compact.astype(np.uint32)
        vert_half[start:stop][mapped] = best_depth[mapped]
    unmapped = float((vert_voxel == NODE_SENTINEL).sum() / max(vert_count, 1))

    finite_half = vert_half[np.isfinite(vert_half)]
    median_half = float(np.median(finite_half)) if len(finite_half) else 0.0
    through = 2.0 * median_half / h
    spec = {
        "h_mm": h,
        "median_half_thickness_mm": median_half,
        "voxels_through_thickness": through,
        "status": "ok" if through >= 3.0 else
                  "marginal" if through >= 2.0 else "coarse",
    }
    grid = {"origin": [float(c) for c in origin], "voxel": h,
            "dims": [nx, ny, nz]}

    def voxel_meta(array, dtype, **extra):
        return {"kind": "flow_voxels", "association": "none", "role": "data",
                "dtype": dtype, "length": int(array.size),
                "count": int(array.shape[0]), "grid": grid, **extra}

    arrays = {
        "voxel_index": voxel_index,
        "voxel_dist": voxel_dist.astype("f4"),
        "vert_voxel": vert_voxel,
        "vert_half_thickness": vert_half.astype("f4"),
    }
    field_meta = {
        "voxel_index": voxel_meta(voxel_index, "u4"),
        "voxel_dist": voxel_meta(voxel_dist, "f4", units="mm"),
        "vert_voxel": {"kind": "flow_voxels", "association": "vertex",
                       "role": "data", "dtype": "u4"},
        "vert_half_thickness": {"kind": "flow_voxels",
                                "association": "vertex", "role": "scalar",
                                "dtype": "f4", "units": "mm"},
    }
    stats = {
        "grid": grid,
        "cells": int(nx * ny * nz),
        "interior_voxels": int(len(voxel_index)),
        "interior_volume_mm3": interior_volume,
        "mesh_volume_mm3": mesh_volume,
        "voxel_adjusted": bool(adjusted),
        "resolution": spec,
        "sign_check": sign_check,
        "unmapped_vertex_fraction": unmapped,
        "median_half_thickness": median_half,
        "max_half_thickness": (float(voxel_dist.max())
                               if len(voxel_dist) else None),
    }
    logger.info(
        f"flow voxels: {nx}x{ny}x{nz} grid at {h:.3f} mm, "
        f"{len(voxel_index)} interior voxels, resolution {spec['status']}")
    return stats, arrays, field_meta


def _flow_adjacency(voxel_index, dims, h, neighborhood=26):
    """Half-edge arrays (a, b, step_mm) between interior grid voxels.

    a/b are compact indices into voxel_index; each undirected pair appears
    once (the solve runs dijkstra directed=False). Peak memory is the dense
    grid->compact id map (4 bytes/cell) plus ~13 int32 pairs per voxel at
    the 26-neighborhood default.
    """
    nx, ny, nz = (int(d) for d in dims)
    offsets = _FLOW_OFFSETS_26 if int(neighborhood) == 26 else _FLOW_OFFSETS_6
    id_map = np.full(nx * ny * nz, -1, dtype=np.int32)
    lin = voxel_index.astype(np.int64)
    id_map[lin] = np.arange(len(lin), dtype=np.int32)
    ix = lin // (ny * nz)
    iy = (lin // nz) % ny
    iz = lin % nz
    edges_a, edges_b, steps = [], [], []
    for dx, dy, dz in offsets:
        valid = np.ones(len(lin), dtype=bool)
        if dx:
            valid &= (ix + dx >= 0) & (ix + dx < nx)
        if dy:
            valid &= (iy + dy >= 0) & (iy + dy < ny)
        if dz:
            valid &= (iz + dz >= 0) & (iz + dz < nz)
        src = np.flatnonzero(valid).astype(np.int32)
        neighbor = id_map[lin[src] + (dx * ny + dy) * nz + dz]
        hit = neighbor >= 0
        edges_a.append(src[hit])
        edges_b.append(neighbor[hit])
        steps.append(np.full(int(hit.sum()),
                             h * float(np.sqrt(dx * dx + dy * dy + dz * dz)),
                             dtype=np.float32))
    return (np.concatenate(edges_a), np.concatenate(edges_b),
            np.concatenate(steps))


def flow_fill_solve(voxel_index, voxel_dist, dims, h, *, sources, delta=0.0,
                    eps_factor=0.1, neighborhood=26, adjacency=None):
    """Arrival cost per interior voxel: multi-source Dijkstra over the grid.

    Edge cost is step / v with the Hele-Shaw front speed
    v = max(mean(d_a, d_b) - mean(delta_a, delta_b), eps_factor * h)^2 —
    the accumulated cost reads as the relative injection pressure needed
    to push the front there, and the d^2 speed makes the front self-select
    mid-channel paths. Cells inside the frozen skin (d <= delta + eps) are
    closed. ``delta`` is scalar or per-voxel. Returns f8 (N,) arrival with
    inf = unreached; pass a precomputed ``adjacency`` to reuse it across
    skin iterations.
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra

    count = len(voxel_index)
    dist = np.asarray(voxel_dist, dtype=np.float64)
    delta = np.broadcast_to(np.asarray(delta, dtype=np.float64), (count,))
    eps = eps_factor * float(h)
    if adjacency is None:
        adjacency = _flow_adjacency(voxel_index, dims, h, neighborhood)
    a, b, step = adjacency
    open_cell = dist > delta + eps
    keep = open_cell[a] & open_cell[b]
    ea = a[keep].astype(np.int64)
    eb = b[keep].astype(np.int64)
    speed = np.maximum(0.5 * (dist[ea] + dist[eb])
                       - 0.5 * (delta[ea] + delta[eb]), eps) ** 2
    matrix = csr_matrix((step[keep].astype(np.float64) / speed, (ea, eb)),
                        shape=(count, count))
    sources = np.atleast_1d(np.asarray(sources, dtype=np.int64))
    arrival = dijkstra(matrix, directed=False, indices=sources, min_only=True)
    arrival[~open_cell] = np.inf
    return arrival


def flow_frozen_skin(voxel_index, voxel_dist, dims, h, *, sources, delta0=0.0,
                     skin_coef=0.12, fill_time=2.0, iterations=3,
                     eps_factor=0.1, neighborhood=26, progress=None):
    """Fill arrival with the frozen-skin (hesitation) fixed point.

    Pass 1 solves with a uniform starting skin delta0 (default 0 — skin
    at first melt contact) and pins the time axis: its last-filled voxel
    defines fill_time seconds. The skin then regrows per voxel as
    delta0 + skin_coef * sqrt(min(t_arrival, fill_time)) — melt reaching
    a wall late has been cooling against mold walls the whole way, so
    thin sections far from the gate hesitate and freeze while the gate
    region stays fluid — and the solve repeats on the same adjacency.
    Clamping the growth time at the fill window keeps slowed-but-filling
    channels from feeding back into runaway skin; the skin is also kept
    monotone non-decreasing so the reached set only shrinks and the
    iteration converges. skin_coef defaults to ~a third of the polymer
    thermal penetration coefficient sqrt(alpha) (~0.1 mm/sqrt(s)). Note
    the per-voxel reading: near-wall voxels turning solid is ordinary
    skin formation, not a defect — freeze-off is only meaningful where
    mid-channel (ridge) voxels close. Returns (arrival, frozen, scale):
    raw final-pass arrival cost (inf = unreached), frozen u1 per voxel
    (255 = filled, 0 = never reached, k = lost to skin growth at pass k),
    and the pass-1 cost-to-seconds scale.
    """
    count = len(voxel_index)
    adjacency = _flow_adjacency(voxel_index, dims, h, neighborhood)
    delta = np.full(count, float(delta0))
    frozen = np.zeros(count, dtype=np.uint8)
    arrival = np.full(count, np.inf)
    scale = 0.0
    rounds = max(int(iterations), 1)
    for iteration in range(1, rounds + 1):
        _report(progress, (iteration - 1) / rounds,
                f"fill pass {iteration}/{rounds}")
        arrival = flow_fill_solve(voxel_index, voxel_dist, dims, h,
                                  sources=sources, delta=delta,
                                  eps_factor=eps_factor,
                                  neighborhood=neighborhood,
                                  adjacency=adjacency)
        reached = np.isfinite(arrival)
        if iteration == 1:
            frozen[reached] = 255
            scale = (float(fill_time)
                     / max(float(arrival[reached].max()), 1e-30)
                     if reached.any() else 0.0)
        else:
            frozen[(frozen == 255) & ~reached] = iteration
        if iteration >= rounds or skin_coef <= 0 or not reached.any():
            break
        growth = np.zeros(count)
        growth[reached] = skin_coef * np.sqrt(
            np.minimum(arrival[reached] * scale, float(fill_time)))
        delta = np.maximum(delta, float(delta0) + growth)
    return arrival, frozen, scale


def flow_fill(workdir, *, voxels, grid, voxels_hash, gate, delta0=0.0,
              skin_coef=0.12, fill_time=2.0, iterations=3, neighborhood=26,
              eps_factor=0.1, resolution_spec=None, progress=None):
    """Gate-seeded voxel fill with freeze-off detection.

    ``voxels`` maps the flow_voxels result arrays, ``grid`` its grid meta
    and ``voxels_hash`` its cache hash so the viewer binds the identical
    voxel set. The gate point snaps to the nearest interior voxel (the
    snap distance is reported — a large one means the click missed the
    material). Arrival is stored in seconds on the pass-1 time axis
    (last optimistic fill = fill_time); raw Dijkstra costs (the pressure
    proxy) are summarized in stats. Freeze-off is judged where it is
    physical: on the ridge voxels behind the surface (vert_frozen), while
    per-voxel skin solidification is reported separately. Returns
    (stats, arrays, field_meta) — storing is the caller's job.
    """
    voxel_index = np.asarray(voxels["voxel_index"], dtype=np.uint32)
    voxel_dist = np.asarray(voxels["voxel_dist"], dtype=np.float64)
    vert_voxel = np.asarray(voxels["vert_voxel"], dtype=np.uint32)
    vert_half = np.asarray(voxels["vert_half_thickness"], dtype=np.float64)
    if not len(voxel_index):
        raise ValueError("flow_voxels found no interior voxels — "
                         "decrease the voxel size")
    origin = np.asarray(grid["origin"], dtype=np.float64)
    h = float(grid["voxel"])
    dims = [int(d) for d in grid["dims"]]
    nx, ny, nz = dims
    gate = np.asarray(gate, dtype=np.float64).reshape(3)

    _report(progress, 0.02, "snapping gate to the interior")
    lin = voxel_index.astype(np.int64)
    centers = origin + (np.stack(
        [lin // (ny * nz), (lin // nz) % ny, lin % nz],
        axis=1).astype(np.float64) + 0.5) * h
    source = int(np.argmin(((centers - gate) ** 2).sum(axis=1)))
    gate_position = centers[source].copy()
    snap = float(np.linalg.norm(gate_position - gate))
    del centers
    if snap > 4.0 * h:
        logger.warning(f"gate snapped {snap:.2f} mm to the nearest interior "
                       "voxel — the click may have missed the material")

    arrival, frozen, scale = flow_frozen_skin(
        voxel_index, voxel_dist, dims, h, sources=[source], delta0=delta0,
        skin_coef=skin_coef, fill_time=fill_time, iterations=iterations,
        eps_factor=eps_factor, neighborhood=neighborhood,
        progress=lambda f, m: _report(progress, 0.05 + 0.85 * f, m))

    _report(progress, 0.92, "mapping fill onto the surface")
    reached = np.isfinite(arrival)
    max_cost = float(arrival[reached].max()) if reached.any() else 0.0
    p95_cost = (float(np.percentile(arrival[reached], 95))
                if reached.any() else 0.0)
    # seconds on the pass-1 time axis; late passes can exceed fill_time
    scaled = np.full(len(arrival), np.inf)
    scaled[reached] = arrival[reached] * scale
    scaled = scaled.astype("f4")

    # surface fields read through the ridge voxels (vert_voxel), so skin
    # solidifying against the walls never shows as unreached surface —
    # only a closed mid-channel (real freeze-off) does
    mapped = vert_voxel != NODE_SENTINEL
    idx = vert_voxel[mapped].astype(np.int64)
    vert_arrival = np.full(len(vert_voxel), np.nan, dtype=np.float32)
    vert_arrival[mapped] = np.where(np.isfinite(scaled[idx]), scaled[idx],
                                    np.nan)
    # unmapped vertices start at 254 = unjudged, not 0 = never reached
    vert_frozen = np.full(len(vert_voxel), 254, dtype=np.uint8)
    vert_frozen[mapped] = frozen[idx]

    lost = (frozen > 0) & (frozen < 255)
    lost_per_pass = {int(code): int((frozen == code).sum())
                     for code in np.unique(frozen[lost])}
    # freeze-off risk is only judgeable where the local channel spans at
    # least two voxels — rim wedges taper below grid resolution and their
    # shallow ridge voxels would flag every healthy plate edge
    with np.errstate(invalid="ignore"):
        resolvable = mapped & (vert_half >= 2.0 * h)
    risk = resolvable & (vert_frozen != 255)
    risk_verts = int(risk.sum())
    resolvable_count = int(resolvable.sum())
    # 254 = unjudged (channel below grid resolution) so the viewer can
    # paint risk as any code below 254 without re-deriving the gate
    vert_frozen[mapped & ~resolvable & (vert_frozen != 255)] = 254

    def voxel_meta(array, dtype, **extra):
        return {"kind": "flow_fill", "association": "none", "role": "data",
                "dtype": dtype, "length": int(array.size),
                "count": int(array.shape[0]), "grid": grid, **extra}

    arrays = {
        "arrival": scaled,
        "frozen": frozen,
        "vert_arrival": vert_arrival,
        "vert_frozen": vert_frozen,
    }
    field_meta = {
        "arrival": voxel_meta(scaled, "f4", units="s"),
        "frozen": voxel_meta(frozen, "u1"),
        "vert_arrival": {"kind": "flow_fill", "association": "vertex",
                         "role": "scalar", "dtype": "f4", "units": "s"},
        "vert_frozen": {"kind": "flow_fill", "association": "vertex",
                        "role": "mask", "dtype": "u1"},
    }
    stats = {
        "voxels_hash": voxels_hash,
        "grid": grid,
        "gate": {"point": [float(c) for c in gate],
                 "voxel": source,
                 "position": [float(c) for c in gate_position],
                 "snap_distance_mm": snap},
        "reached_volume_fraction": float(reached.sum() / len(arrival)),
        "unreached_volume_mm3": float((~reached).sum()) * h ** 3,
        "skin": {"solidified_volume_mm3": float(lost.sum()) * h ** 3,
                 "lost_per_pass": lost_per_pass},
        "freeze_off": {"surface_fraction": (risk_verts / resolvable_count
                                            if resolvable_count else 0.0),
                       "risk_vertices": risk_verts,
                       "resolvable_vertices": resolvable_count},
        "p95_cost": p95_cost,
        "max_cost": max_cost,
        "fill": {"delta0": float(delta0), "skin_coef": float(skin_coef),
                 "fill_time": float(fill_time),
                 "iterations": int(iterations),
                 "neighborhood": int(neighborhood)},
        "resolution": resolution_spec,
    }
    logger.info(
        f"flow fill: {100 * stats['reached_volume_fraction']:.1f}% of voxels "
        f"reached, freeze-off on "
        f"{100 * stats['freeze_off']['surface_fraction']:.1f}% of the "
        f"surface, gate snap {snap:.2f} mm")
    return stats, arrays, field_meta


def _latest_mold_orientation(workdir):
    """Newest current-schema mold_orientation result: (hash, payload, npz) or Nones.

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
        if payload.get("stats", {}).get("schema") != MOLD_STATS_SCHEMA:
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


def _result_effective_ids(workdir, payload):
    """Per-triangle labeling matching a stored result's aggregation arrays.

    Results carry a splits salt: their brep_valid/brep_default arrays are
    indexed by the effective sub-face ids of that splits state. The
    labeling is only recoverable while the workdir's current splits match
    the salt — on mismatch return None so callers degrade to their
    membership fallback (same as a missing mold result).
    """
    import splits

    if payload.get("params", {}).get("splits") != splits_fingerprint(workdir):
        return None
    ids, _, _ = splits.effective_face_ids(workdir)
    return ids


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
    # user splits relabel the aggregation — while their boundary arrays
    # match the defaults' indexing, they replace the mesh-time edges (and
    # carry the cut segments the parting line must follow)
    meta_path = os.path.join(workdir, SUBFACE_META_FILE)
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        if (meta.get("mesh_fingerprint") == mesh_fingerprint(workdir)
                and meta.get("n_effective") == len(brep_default)):
            edges_path = os.path.join(workdir, SUBFACE_EDGES_FILE)
            pairs_path = os.path.join(workdir, SUBFACE_EDGE_PAIRS_FILE)
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
        brep_ids = _result_effective_ids(workdir, mold_payload)
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
    import machining

    verts, faces = load_mesh_arrays(workdir)
    faces = faces.astype(np.int64, copy=False)
    radii = np.asarray(skeleton["cluster_radii"], dtype=np.float64)
    vert_node = np.asarray(skeleton["cluster_vert_node"])

    _report(progress, 0.1, "face geometry")
    areas = machining.face_areas(verts, faces)
    normals = load_face_normals(workdir)

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
            brep_ids = _result_effective_ids(workdir, mold_payload)
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
                      window=0.3, progress=None):
    """Cache height maps and per-tip/per-clearance fields for directions.

    ``pixel`` None = resolution/5 from mesh_meta (legacy fallback 0.1 mm).
    """
    from zmap import DirectionCache

    pixel = resolve_pixel(workdir, pixel)
    verts, faces = load_mesh_arrays(workdir)
    tips = [tips_entry if isinstance(tips_entry, tuple) else tuple(tips_entry)
            for tips_entry in tips]

    # per-field progress: each field is minutes on a big part, so a
    # per-direction report would freeze the UI bar for the whole run
    per_direction = 1 + len(tips) + len(clearances) + len(tips) * len(clearances)
    total = max(len(directions) * per_direction, 1)
    done = 0

    def _step(message):
        nonlocal done
        _report(progress, done / total, message)
        done += 1

    computed = []
    for direction_index in directions:
        logger.info(f"Direction {direction_index}")
        _step(f"direction {direction_index}: height map")
        cache = DirectionCache(workdir, direction_index, verts=verts, faces=faces,
                               pixel=pixel, window=window)
        for diameter, corner_radius in tips:
            _step(f"direction {direction_index}: tip {diameter:g}:{corner_radius:g}")
            cache.tip_gap(diameter, corner_radius)
        for radius in clearances:
            _step(f"direction {direction_index}: clearance r={radius:g}")
            cache.clearance(radius)
        # tip-aware holder stickout fields per (tip, cylinder radius)
        for diameter, corner_radius in tips:
            for radius in clearances:
                _step(f"direction {direction_index}: stickout "
                      f"{diameter:g}:{corner_radius:g} r={radius:g}")
                cache.tip_min_stickout(diameter, corner_radius, radius)
        computed.append(int(direction_index))

    return {
        "directions": computed,
        "tips": [list(tip) for tip in tips],
        "clearances": [float(r) for r in clearances],
    }


def compose_tool(workdir, direction, *, pixel=None, tollerance=1e-1, diameter=2.0,
                 corner_radius=0.0, stickout=None, cylinders=None, sweep=(),
                 wall_tollerance=1.0, window=0.3, progress=None):
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
                           pixel=pixel, window=window)
    angles = machining.face_angles_deg(load_face_normals(workdir),
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


def pocket_slenderness(workdir, *, direction, max_diameter=None, ladder=1.5,
                       pixel=None, window=0.3, progress=None):
    """Per-vertex pocket depth/width ratio along one pull direction: the
    slenderness of the steel core the mold half needs to fill each pocket
    (thin-steel risk when it exceeds ~2-3). One closing ladder covers all
    pocket widths up to ``max_diameter`` — see zmap.slenderness_ladder.

    ``ladder`` is the geometric step between swept pocket widths: the ratio
    field is quantized to it (finer = smoother, cost ~ladder/(ladder-1));
    ``pixel`` None = resolution/5 from mesh_meta (legacy fallback 0.1 mm);
    ``max_diameter`` None = half the smallest bounding box extent (the same
    auto convention as the thickness fields' sphere radius cap).
    """
    from zmap import DirectionCache, slenderness_ladder

    pixel = resolve_pixel(workdir, pixel)
    verts, faces = load_mesh_arrays(workdir)
    if max_diameter is None:
        extents = verts.max(axis=0) - verts.min(axis=0)
        max_diameter = 0.5 * float(extents.min())
        logger.info(f"max pocket width {max_diameter:.2f} mm from bounding box")

    _report(progress, 0.1, "rendering height map")
    cache = DirectionCache(workdir, direction, verts=verts, faces=faces,
                           pixel=pixel, window=window)

    _report(progress, 0.4, "closing ladder")
    fx, fy, vheight = cache.vertex_projection()
    window_px = max(2, int(np.ceil(cache.window / pixel)))
    ratio, width, diameters = slenderness_ladder(cache.heights, fx, fy,
                                                 vheight, pixel, max_diameter,
                                                 ladder=ladder,
                                                 window_px=window_px)

    # vertices the pull direction cannot see belong to the other mold half
    # (or a slide): their depth below the closing is the part itself, not a
    # pocket this half's steel fills. A vertex counts as this half's only if
    # ALL its faces are accessible — any-face membership would keep e.g. the
    # bottom rim ring of a vertical wall (shared with the visible wall) and
    # read the whole part height as pocket depth at the smallest scale
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))
    visible = np.ones(len(verts), dtype=bool)
    visible[faces[~accessibility[direction]].ravel()] = False
    ratio[~visible] = 0.0
    width[~visible] = 0.0

    stats = {
        "direction": int(direction),
        "direction_vector": [float(c) for c in cache.direction],
        "pixel": float(pixel),
        "max_diameter": float(max_diameter),
        "ladder": float(ladder),
        "scales": len(diameters),
        "verts": int(ratio.size),
        "p50": float(np.percentile(ratio, 50)),
        "p95": float(np.percentile(ratio, 95)),
        "max": float(ratio.max()),
        "above_2": float(np.mean(ratio > 2.0)),
        "visible_fraction": float(visible.mean()),
    }
    return ratio, width, stats


def span_ladder(verts, faces, thickness, *, ladder=1.5, contrast=1.5,
                max_thickness, max_span):
    """Per-vertex thin-span ratio: the geodesic distance (over the mesh
    edge graph) to the nearest ADEQUATE support — material at least
    ``contrast`` times the vertex's own thickness — divided by the vertex's
    own thickness. The direction-free sibling of zmap.slenderness_ladder:
    support thickness plays the pocket-width role, distance-to-support the
    depth role.

    Sweep rule: ascending geometric ladder of thickness scales (step
    ``ladder``); each vertex is assigned at the FIRST scale that meets its
    support requirement (contrast x own thickness) and has any seed
    vertices, via one multi-source Dijkstra per used scale. The nearest
    adequate support wins — a rib supported by a nearby modest wall reads
    the short distance to that wall, not the long distance to bulk (the
    wall's own floppiness is the wall's own reading; series compliance
    stays per-member). Vertices whose requirement exceeds every scale with
    material — near-bulk vertices, and uniform parts with no thickness
    contrast — stay 0: no support contrast, no span reading. The ladder
    step only rounds the support requirement UP (by < ladder), which can
    substitute a farther, thicker support: conservative, and finer ladders
    tighten it.

    Pure numpy/scipy over the given arrays (no meshlib, no workdir) so
    synthetic fixtures can exercise it directly. Distances saturate at
    ``max_span``. Returns (ratio float32[V], critical float32[V], scales):
    critical is the support-thickness scale each vertex was measured
    against (0 where unassigned).
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra

    finite = np.isfinite(thickness) & (thickness > 0)
    count = len(verts)
    ratio = np.zeros(count, dtype=np.float32)
    critical = np.zeros(count, dtype=np.float32)
    if not finite.any():
        return ratio, critical, []

    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]],
                            faces[:, [2, 0]]])
    edges.sort(axis=1)
    edges = np.unique(edges, axis=0)
    lengths = np.linalg.norm(verts[edges[:, 0]] - verts[edges[:, 1]], axis=1)
    graph = csr_matrix(
        (np.concatenate([lengths, lengths]),
         (np.concatenate([edges[:, 0], edges[:, 1]]),
          np.concatenate([edges[:, 1], edges[:, 0]]))),
        shape=(count, count))

    # ladder from the smallest support requirement present (p05 keeps
    # outlier-thin corner readings from adding useless scales) up to the
    # thickest support available
    t_lo = max(contrast * float(np.percentile(thickness[finite], 5)), 1e-6)
    scales = []
    t = min(t_lo, max_thickness)
    while t < max_thickness:
        scales.append(t)
        t *= ladder
    scales.append(float(max_thickness))

    requirement = contrast * thickness
    assigned = ~finite  # never assign non-finite readings
    used = []
    for t in scales:
        select = ~assigned & (requirement <= t)
        if not select.any():
            continue
        seeds = np.flatnonzero(finite & (thickness >= t))
        if not len(seeds):
            continue  # requirement waits for a scale that has material
        dist = dijkstra(graph, directed=False, indices=seeds, min_only=True)
        np.minimum(dist, max_span, out=dist)
        ratio[select] = (dist[select] / thickness[select]).astype(np.float32)
        critical[select] = t
        assigned |= select
        used.append(float(t))
    return ratio, critical, used


def thin_span(workdir, *, thickness, band_lo=None, band_hi=None,
              suspect=None, max_thickness=None, ladder=1.5, contrast=1.5,
              max_span=None, progress=None):
    """Per-vertex thin-span (normal-direction stiffness proxy) field: how
    far each vertex sits from material at least ``contrast`` times its own
    thickness, in units of its own thickness — see span_ladder. Bending
    compliance of a strip/plate grows ~(span/thickness)^3, so the linear
    ratio stored here is the right dimensionless screen for "thin areas
    that spread far": stubby ribs and short bridges read low, long thin
    bridges and large unsupported panels read high, and near-bulk material
    (nothing meaningfully thicker exists) reads 0. Proxy limits: curvature
    stiffening is ignored (a curved shell reads like a flat panel),
    fixturing / load direction are unknown, and a perfectly uniform part
    has no thickness contrast to measure against — it screens internal
    support only, not a FEA.

    ``thickness`` is the per-vertex inscribed-sphere diameter field (the
    cached `thickness` analysis result); pass its ``band_lo/band_hi/
    suspect`` masks to lift edge-explainable false-low readings to their
    band ceiling, so chamfer rings do not divide by an artifact thickness.
    ``max_thickness`` None = the p99 of the field; ``max_span`` None = the
    bounding box diagonal.
    """
    verts, faces = load_mesh_arrays(workdir)

    # edge-explainable readings are false-LOW: substituting the band
    # ceiling keeps both the support requirement and the denominator sane
    if band_lo is not None and band_hi is not None and suspect is not None:
        excluded = edge_excluded(thickness, band_lo, band_hi,
                                 suspect.astype(bool))
        thickness = np.where(excluded, np.maximum(thickness, band_hi),
                             thickness)

    # readings below the mesh's analysis resolution are sliver/knife-edge
    # artifacts the mesh cannot measure — dividing by them mints absurd
    # ratios, so they read 0 (unmeasurable, not "infinitely floppy")
    floor = part_resolution(workdir)
    if floor:
        thickness = np.where(thickness < floor, np.nan, thickness)

    finite = np.isfinite(thickness) & (thickness > 0)
    if max_thickness is None:
        max_thickness = (float(np.percentile(thickness[finite], 99))
                         if finite.any() else 0.0)
        logger.info(f"max support thickness {max_thickness:.2f} mm from p99")
    if max_span is None:
        extents = verts.max(axis=0) - verts.min(axis=0)
        max_span = float(np.linalg.norm(extents))

    _report(progress, 0.2, "span ladder")
    ratio, critical, scales = span_ladder(
        verts, faces, thickness, ladder=ladder, contrast=contrast,
        max_thickness=max_thickness, max_span=max_span)

    stats = {
        "max_thickness": float(max_thickness),
        "max_span": float(max_span),
        "ladder": float(ladder),
        "contrast": float(contrast),
        "scales": len(scales),
        "verts": int(ratio.size),
        "p50": float(np.percentile(ratio, 50)),
        "p95": float(np.percentile(ratio, 95)),
        "max": float(ratio.max()),
        "above_5": float(np.mean(ratio > 5.0)),
    }
    return ratio, critical, stats
