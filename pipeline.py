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
from utils import has_valid_extension, ensure_directory

FINE_MESH_FILE = "fine_mesh.obj"
FINE_VERTS_FILE = "fine_verts.npy"
FINE_FACES_FILE = "fine_faces.npy"
DIRECTIONS_FILE = "directions.npy"
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


def mesh_part(input_path, workdir=None, *, heal=False, subdivide=None, offset=None,
              tollerance=1e-1, deflection=0.5, progress=None):
    """Canonicalize an input STL/STEP into a part working directory.

    Writes fine_mesh.obj + fine_verts.npy + fine_faces.npy (the stable face
    indexing every later stage refers to). STEP input tessellates through
    the BREP (brep.mesh_step) so every fine face carries its source BREP
    face id (brep_faces.npy) — heal/offset destroy the surfaces and fall
    back to the anonymous meshlib path, as does STL input.
    Returns the workdir and counts.
    """
    has_valid_extension(input_path, MESH_EXTENSIONS)

    is_step = os.path.splitext(input_path)[1].lower() in (".stp", ".step")
    brep_ids = None
    surface_types = None

    if is_step and not heal and offset is None:
        import brep

        _report(progress, 0.0, "tessellating BREP")
        verts, faces, brep_ids, surface_types = brep.mesh_step(
            input_path, deflection=deflection)

        if subdivide:
            _report(progress, 0.4, "subdividing mesh (tag preserving)")
            verts, faces, brep_ids = brep.subdivide_tagged(
                verts, faces, brep_ids, subdivide)

        verts = verts.astype(np.float32)
        mesh = mn.meshFromFacesVerts(faces, verts)
    else:
        _report(progress, 0.0, "loading mesh")
        mesh = load_mesh(input_path, heal=heal, offset=offset, tollerance=tollerance)

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

    counts = {"verts": int(len(verts)), "faces": int(len(faces))}
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
    visibility height-map resolution (None = auto from the bounding box).
    """
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
    values = np.array(result.vec, dtype=np.float32)
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
        "brep": brep_ids is not None,
        "options": options[:count],
    }
    return {"stats": stats, "arrays": arrays, "field_meta": field_meta}


def cnc_setups(workdir, *, indexed=True, tilt=90.0, max_setups=4,
               min_setup_faces=10, count=10, field_options=3, progress=None):
    """Search CNC setup combinations and derive per-face assignment fields.

    Machines searched: a plain 3-axis (one direction per setup) and, with
    ``indexed``, a 3+2 whose setups cover a ``tilt``-degree cone. Returns
    {"stats": <JSON-safe>, "arrays": {...}, "field_meta": {...}} with
    membership/region/brep fields for up to `field_options` options —
    picked as the best of each distinct (machine, setup count) signature
    within the reported top `count`, so a single-setup 3+2 plan is
    explorable next to the 3-axis flips instead of buried under them.
    stats["field_options"] maps field index k -> index into
    stats["options"]. The brep fields need brep_faces.npy (STEP-meshed
    parts); they are skipped otherwise.
    """
    import machining
    import molding

    verts, faces = load_mesh_arrays(workdir)
    directions = np.load(os.path.join(workdir, DIRECTIONS_FILE))
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))

    brep_path = os.path.join(workdir, BREP_FACES_FILE)
    brep_ids = np.load(brep_path) if os.path.exists(brep_path) else None

    machines = [("3-axis", 0.0)]
    if indexed:
        machines.append(("3+2", float(tilt)))

    _report(progress, 0.1, "searching setup combinations")
    options = machining.setup_search(
        directions, accessibility, machines=machines, max_setups=max_setups,
        min_setup_faces=min_setup_faces)

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

        setup_dirs = [s["direction"] for s in option["setups"]]
        membership = machining.setup_membership(setup_dirs, cover)
        region, region_counts = molding.internal_regions(membership, pairs,
                                                         len(faces))
        labels, colors = machining.setup_labels_colors(
            [s["vector"] for s in option["setups"]])

        common = {"kind": "setup_membership", "option": index,
                  "machine": option["machine"], "features": len(setup_dirs),
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
            valid = molding.brep_validity(membership, brep_ids,
                                          len(setup_dirs))
            arrays[f"brep_valid_{k}"] = valid
            field_meta[f"brep_valid_{k}"] = {
                **common, "variant": "brep_valid", "association": "none",
                "role": "data", "dtype": "u4", "count": int(len(valid))}
            defaults = machining.setup_defaults(membership, valid, brep_ids)
            arrays[f"brep_default_{k}"] = defaults
            field_meta[f"brep_default_{k}"] = {
                **common, "variant": "brep_default", "association": "none",
                "role": "data", "dtype": "u1", "count": int(len(defaults))}

    for option in options:
        option.pop("machine_rank", None)

    stats = {
        "schema": 1,
        "face_count": int(accessibility.shape[1]),
        "direction_count": int(directions.shape[0]),
        "brep": brep_ids is not None,
        "options": reported,
        "field_options": picked,
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
    vertex, so centers reconstruct vectorized as p - n*r. That is exact on
    smooth regions but the normal convention can differ from meshlib's at
    sharp features, so suspects (center measurably closer to the surface
    than its radius, or vertices spanning a sharp crease) are recomputed
    exactly with findInSphere.
    """
    from meshlib import mrmeshpy as mm
    from scipy.spatial import cKDTree

    normals = mn.toNumpyArray(mm.computePerVertNormals(mesh))
    centers = verts.astype(np.float64) - normals * radii[:, None]

    tri = verts[faces].astype(np.float64)
    face_normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    face_normals /= np.maximum(
        np.linalg.norm(face_normals, axis=1, keepdims=True), 1e-30)
    edge_lengths = np.linalg.norm(tri - np.roll(tri, -1, axis=1), axis=2)
    mean_edge = float(edge_lengths.mean())

    # sharp-crease flag: worst alignment between a vertex normal and the
    # normals of its incident faces
    alignment = np.ones(len(verts))
    spread = (face_normals[:, None, :] * normals[faces]).sum(axis=2)
    for corner in range(3):
        np.minimum.at(alignment, faces[:, corner], spread[:, corner])
    sharp = alignment < np.cos(np.radians(30.0))

    # penetration flag: a valid center keeps its radius from the surface
    # (vertex samples of it; mean_edge covers sampling slack)
    surface_distance, _ = cKDTree(verts).query(centers, workers=-1)
    tolerance = np.maximum(0.05 * radii, 0.5 * mean_edge)
    penetrating = surface_distance < radii - tolerance

    suspects = np.where((sharp | penetrating) & np.isfinite(radii))[0]
    if len(suspects) > 0.2 * len(verts):
        logger.warning(
            f"{len(suspects)}/{len(verts)} suspect sphere centers; "
            "normal convention mismatch? correcting all of them")
    for step, vert in enumerate(suspects):
        if step % 4096 == 0:
            _report(progress, 0.3 + 0.3 * step / len(suspects),
                    f"correcting sphere centers ({step}/{len(suspects)})")
        sphere = mm.findInSphere(mesh, mm.VertId(int(vert)), settings)
        centers[vert] = (sphere.center.x, sphere.center.y, sphere.center.z)
        radii[vert] = sphere.radius
    return centers, radii, len(suspects)


def _cluster_nodes(nodes, radii, cluster_factor):
    """Merge nodes whose centers overlap within their local radius scale.

    Returns per-node cluster labels and the representative (max radius)
    member index per cluster; averaging positions instead would drift off
    the medial axis in thin features.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    count = len(nodes)
    if count == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    tree = cKDTree(nodes)
    neighborhoods = tree.query_ball_point(nodes, r=cluster_factor * radii,
                                          workers=-1)
    pair_a = np.fromiter(
        (i for i, hits in enumerate(neighborhoods) for _ in hits),
        dtype=np.int64, count=sum(len(hits) for hits in neighborhoods))
    pair_b = np.fromiter(
        (j for hits in neighborhoods for j in hits),
        dtype=np.int64, count=len(pair_a))
    # keep pairs within cluster_factor of the smaller sphere of the two
    span = np.linalg.norm(nodes[pair_a] - nodes[pair_b], axis=1)
    close = span <= cluster_factor * np.minimum(radii[pair_a], radii[pair_b])
    pair_a, pair_b = pair_a[close], pair_b[close]

    adjacency = coo_matrix((np.ones(len(pair_a), dtype=np.int8),
                            (pair_a, pair_b)), shape=(count, count))
    cluster_count, labels = connected_components(adjacency, directed=False)

    representative = np.zeros(cluster_count, dtype=np.int64)
    best_radius = np.full(cluster_count, -1.0)
    np.maximum.at(best_radius, labels, radii)
    is_best = radii >= best_radius[labels]
    representative[labels[is_best]] = np.where(is_best)[0]
    return labels.astype(np.int64), representative


def wall_skeleton(workdir, *, max_radius=5.0, min_radius=0.1,
                  cluster_factor=1.0, progress=None):
    """Wall thickness + medial skeleton graphs from inscribed spheres.

    Every vertex gets its maximal inscribed ("rolling") sphere; the sphere
    centers become skeleton nodes carrying the local wall radius, connected
    by the mesh edge adjacency (raw graph) and additionally merged into a
    reduced clustered graph. Returns (stats, arrays, field_meta) — storing
    the result is the caller's job.
    """
    from meshlib import mrmeshpy as mm

    verts, faces = load_mesh_arrays(workdir)
    faces = faces.astype(np.int64, copy=False)
    mesh = mn.meshFromFacesVerts(faces, verts)
    vert_count = len(verts)

    _report(progress, 0.05, "inscribed sphere thickness")
    settings = _in_sphere_settings(max_radius)
    thickness = np.array(
        mm.computeInSphereThicknessAtVertices(mesh, settings).vec)
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

    _report(progress, 0.7, "clustering skeleton nodes")
    labels, representative = _cluster_nodes(raw_nodes, raw_radii,
                                            cluster_factor)
    cluster_nodes = raw_nodes[representative]
    cluster_radii = raw_radii[representative]
    cluster_edges = labels[raw_edges.astype(np.int64)]
    cluster_edges = np.unique(np.sort(cluster_edges, axis=1), axis=0)
    cluster_edges = cluster_edges[
        cluster_edges[:, 0] != cluster_edges[:, 1]].astype(np.uint32)
    cluster_vert_node = np.full(vert_count, NODE_SENTINEL, dtype=np.uint32)
    cluster_vert_node[keep] = labels[vert_node[keep].astype(np.int64)]

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

    finite = thickness[np.isfinite(thickness)]
    stats = {
        "verts": vert_count,
        "raw_nodes": node_count,
        "raw_edges": int(len(raw_edges)),
        "cluster_nodes": int(len(cluster_nodes)),
        "cluster_edges": int(len(cluster_edges)),
        "mean_thickness": float(finite.mean()) if len(finite) else None,
        "min_thickness": float(finite.min()) if len(finite) else None,
        "corrected": int(corrected),
        "dropped": int(vert_count - node_count),
    }
    logger.info(
        f"wall skeleton: {node_count} raw / {len(cluster_nodes)} clustered "
        f"nodes, mean thickness {stats['mean_thickness']}")
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


def precompute_fields(workdir, *, directions, pixel=1e-1, tips=(), clearances=(),
                      engine="zmap", window=0.3, progress=None):
    """Cache height maps and per-tip/per-clearance fields for directions."""
    from zmap import DirectionCache

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


def compose_tool(workdir, direction, *, pixel=1e-1, tollerance=1e-1, diameter=2.0,
                 corner_radius=0.0, stickout=None, cylinders=None, sweep=(),
                 engine="zmap", window=0.3, progress=None):
    """Evaluate a full tool assembly from precomputed fields."""
    from zmap import DirectionCache, compose_unreachable

    verts, faces = load_mesh_arrays(workdir)
    accessibility = np.load(os.path.join(workdir, ACCESSIBILITY_FILE))

    _report(progress, 0.1, "composing tool verdict")
    cache = DirectionCache(workdir, direction, verts=verts, faces=faces,
                           pixel=pixel, window=window, engine=engine)
    unreachable_faces, gap, min_stick = compose_unreachable(
        cache, faces, diameter, corner_radius, tollerance,
        stickout=stickout, cylinders=cylinders,
    )

    # Keep only the faces that are accessible
    unreachable_faces = unreachable_faces[accessibility[direction, unreachable_faces]]

    accessible_count = int(accessibility[direction].sum())
    logger.info(f"Tool D={diameter} rc={corner_radius} stickout={stickout} cannot reach {len(unreachable_faces)} of {accessible_count} accessible faces")

    # A stickout sweep is free: threshold the cached per-vertex field
    sweep_results = []
    if sweep and min_stick is not None:
        for sweep_stickout in sweep:
            blocked = (gap > tollerance) | (min_stick > sweep_stickout + tollerance)
            swept = np.where(blocked[faces].all(axis=1))[0]
            swept = swept[accessibility[direction, swept]]
            logger.info(f"  stickout {sweep_stickout:8.2f}: {len(swept)} unreachable faces")
            sweep_results.append({"stickout": float(sweep_stickout),
                                  "unreachable": int(len(swept))})

    unreachable_faces = unreachable_faces.tolist()
    write_highlights(workdir, unreachable_faces)

    return {
        "unreachable": len(unreachable_faces),
        "accessible": accessible_count,
        "sweep": sweep_results,
        "faces": unreachable_faces,
    }
