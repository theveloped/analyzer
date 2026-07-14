from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn
import numpy as np
import json
import time

from loguru import logger
import os
from utils import log_execution_time


@log_execution_time
def load_mesh(path):
    return mm.loadMesh(path)


@log_execution_time
def save_obj_mesh(verts, faces, path):
    with open(path, 'w') as file:
        # Write vertices to file
        for vert in verts:
            file.write(f"v {vert[0]} {vert[1]} {vert[2]}\n")

        # Write faces to file
        for face in faces:
            face_line = "f " + " ".join([f"{v_idx + 1}" for v_idx in face])
            file.write(face_line + "\n")


@log_execution_time
def get_mesh_data(mesh):
    verts = mn.getNumpyVerts(mesh)
    faces = mn.getNumpyFaces(mesh.topology)
    return verts, faces


@log_execution_time
def save_mesh(mesh, path):
    verts, faces = get_mesh_data(mesh)
    
    if path.endswith(".obj"):
        save_obj_mesh(verts, faces, path)
        
    else:
        raise ValueError("Invalid file format. Only .obj files are supported")
    

@log_execution_time    
def offset_mesh(mesh, offset=0, tollerance=1e-2):
    
    # Setup parameters
    params = mm.OffsetParameters()
    params.voxelSize = tollerance
    
    # If you have holes in mesh
    # if mm.findRightBoundary(mesh.topology).empty():
    params.signDetectionMode = mm.SignDetectionMode.HoleWindingRule
    
    # Make offset mesh
    return mm.offsetMesh(mesh, offset, params)
    

@log_execution_time    
def heal_mesh( mesh : mm.Mesh, voxelSize : float, decimate : bool = True) -> mm.Mesh:
    numHoles = mm.findRightBoundary(mesh.topology).size()
    oParams = mm.GeneralOffsetParameters()
    if (numHoles != 0):
        oParams.signDetectionMode = mm.SignDetectionMode.HoleWindingRule
  
    oParams.voxelSize = voxelSize
    resMesh = mm.generalOffsetMesh(mesh, 0.0, oParams)
    if (decimate):
        resMesh.packOptimally(False)
        dSettings = mm.DecimateSettings()
        dSettings.maxError = 0.25 * voxelSize
        dSettings.tinyEdgeLength = mesh.computeBoundingBox().diagonal() * 1e-4
        dSettings.stabilizer = 1e-5
        dSettings.packMesh = True
        dSettings.subdivideParts = 64
        mm.decimateMesh(resMesh,dSettings)
  
    return resMesh


@log_execution_time
def subdivide_mesh(mesh, max_edge_len):
    """
    Refine the mesh in place until no edge is longer than `max_edge_len`,
    WITHOUT changing the shape (maxDeviationAfterFlip = 0): planar facets
    stay planar and sharp edges stay sharp. Use this instead of healing for
    clean CAD tessellations (STEP): analysis results are reported per face,
    so face density sets how finely results localize on the part, while the
    geometry stays exact.
    """
    settings = mm.SubdivideSettings()
    settings.maxEdgeLen = max_edge_len
    settings.maxEdgeSplits = 100_000_000
    settings.maxDeviationAfterFlip = 0.0
    mm.subdivideMesh(mesh, settings)
    return mesh


@log_execution_time
def get_inside_indices(mesh_a, mesh_b):
    bOperation = mm.BooleanOperation.InsideA
    bResMapper = mm.BooleanResultMapper()
    bResult = mm.boolean(mesh_a, mesh_b, bOperation, None, bResMapper)

    inner_faces = mesh_a.topology.getValidFaces()
    for f in inner_faces:
        bs = mm.FaceBitSet()
        bs.resize( f.get()+1)
        bs.set(f)
        if (bResMapper.map(bs, mm.BooleanResMapObj.A).count() == 0):
            inner_faces.set(f,False)
    inner_verts = mesh_a.topology.getValidVerts()
    for v in inner_verts:
        bs = mm.VertBitSet()
        bs.resize( v.get()+1)
        bs.set(v)
        if (bResMapper.map(bs, mm.BooleanResMapObj.A).count() == 0):
            inner_verts.set(v,False)
    
    return mn.getNumpyBitSet(inner_faces), mn.getNumpyBitSet(inner_verts)


@log_execution_time
def sample_unity_vectors(n):
    # Using the Golden Spiral method to uniformly distribute points on a sphere
    indices = np.arange(0, n, 1)
    phi = np.pi * (3. - np.sqrt(5.))  # golden angle in radians
    y = 1 - (indices / (n - 1)) * 2  # y goes from 1 to -1
    radius = np.sqrt(1 - y * y)  # radius at y

    theta = phi * indices  # golden angle increment

    x = np.cos(theta) * radius
    z = np.sin(theta) * radius

    return np.vstack((x, y, z)).T


@log_execution_time
def sample_unity_vector_pairs(n):
    # Double the number of points to account for mirroring 
    n  *= 2

    # Using the Golden Spiral method to uniformly distribute n points on a sphere
    indices = np.arange(0, n, 1)
    phi = np.pi * (3. - np.sqrt(5.))  # Golden angle in radians
    y = 1 - (indices / ((n) - 1)) * 2  # y goes from 1 to -1, adjusted for n points
    
    # Sample only the top hemisphere
    indices = indices[y >= 0]
    y = y[y >= 0]
    
    # Compute the radius and theta
    radius = np.sqrt(1 - y * y)  # Radius at y
    theta = phi * indices  # Golden angle increment

    # Compute the x and z coordinates
    x = np.cos(theta) * radius
    z = np.sin(theta) * radius

    # Generate the original points
    points = np.vstack((x, y, z)).T

    # Mirror the points by negating the coordinates
    mirrored_points = points * -1

    # Initialize an array to hold both points and their mirrors
    full_points = np.zeros((n, 3))

    # Place each point and its mirror next to each other
    full_points[0::2] = points
    full_points[1::2] = mirrored_points

    return full_points


@log_execution_time
def compute_accessibility(mesh, directions, face_count, *, tolerance_deg=0.1,
                          pixel=None, normals=None):
    """Per-direction face accessibility via our own visibility test.

    A face is accessible iff it faces the direction within `tolerance_deg`
    (near-vertical walls are deterministically front-facing — no speckle)
    and no material shadows it per a rendered height map (zmap engine).
    `pixel` is the height-map resolution; None derives it from the part's
    bounding box diagonal. ``normals`` overrides the facet normals (pass
    exact BREP surface normals for STEP parts).
    """
    from zmap import face_visibility

    verts, faces = get_mesh_data(mesh)

    if pixel is None:
        diagonal = np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))
        pixel = float(np.clip(diagonal / 1000.0, 0.05, 1.0))
        logger.debug(f"Auto visibility map pixel: {pixel:.3f}")

    dir_count = len(directions)
    accessibility = np.ones((dir_count, face_count), dtype=bool)
    for i in range(dir_count):
        accessibility[i, :] = face_visibility(
            mesh, verts, faces, directions[i],
            tolerance_deg=tolerance_deg, pixel=pixel, normals=normals)

    return accessibility