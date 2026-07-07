from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn
import numpy as np
import json
import time

from loguru import logger
import os
from itertools import combinations
from utils import log_execution_time


@log_execution_time
def load_mesh(path, heal=False, offset=None, tollerance=1e-1):
    start = time.time()
    mesh = mm.loadMesh(path)
    logger.debug( f"Time to load mesh: {time.time() - start}")
    
    if heal:
        start = time.time()
        mesh = heal_mesh(mesh, tollerance, True)
        logger.debug( f"Time to heal mesh: {time.time() - start}")
    
    if offset is not None:
        start = time.time()
        mesh = offset_mesh(mesh, offset=offset, tollerance=tollerance)
        logger.debug( f"Time to offset mesh: {time.time() - start}")
   
    return mesh


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
def single_offset( mesh : mm.Mesh, offset: float, voxelSize : float, decimate : bool = True) -> mm.Mesh:
    numHoles = mm.findRightBoundary(mesh.topology).size()
    oParams = mm.GeneralOffsetParameters()
    if (numHoles != 0):
        oParams.signDetectionMode = mm.SignDetectionMode.HoleWindingRule
  
    oParams.voxelSize = voxelSize
    resMesh = mm.generalOffsetMesh(mesh, offset, oParams)
    
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
def double_offset( mesh : mm.Mesh, offset_a: float, offset_b: float, voxelSize : float, decimate : bool = True) -> mm.Mesh:
    numHoles = mm.findRightBoundary(mesh.topology).size()
    oParams = mm.GeneralOffsetParameters()
    if (numHoles != 0):
        oParams.signDetectionMode = mm.SignDetectionMode.HoleWindingRule
  
    oParams.voxelSize = voxelSize
    resMesh = mm.doubleOffsetMesh(mesh, offset_a, offset_b, oParams)
    
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
def get_distance(mesh_a, mesh_b, upper_limit: float = 3.4028234663852886e+38, lower_limit: float = 0.0):
    res = mm.projectAllMeshVertices(mesh_b, mesh_a, upDistLimitSq=upper_limit, loDistLimitSq=lower_limit)
    return res.vec


@log_execution_time
def get_inside_mesh(mesh_a, mesh_b):
    bOperation = mm.BooleanOperation.InsideA
    # mm.BooleanOperation.InsideA
    bResMapper = mm.BooleanResultMapper()
    bResult = mm.boolean(mesh_a, mesh_b, bOperation, None, bResMapper)
    bResMesh = bResult.mesh
    return bResMesh


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
def map_result_faces(mesh_a, mesh_b, faces_a, min_range=None, max_range=None):
    distances = get_distance(mesh_a, mesh_b, upper_limit=10, lower_limit=0.0)
    
    # Compute all vertices where distance is abbove tollerance of 0.1
    if min_range is not None and max_range is not None:
        indices = np.where(np.abs(distances) >= min_range and np.abs(distances) <= max_range)[0]
        indices_set = set(indices)
    
    elif min_range is not None:
        indices = np.where(np.abs(distances) >= min_range)[0]
        indices_set = set(indices)
        
    elif max_range is not None:
        indices = np.where(np.abs(distances) <= max_range)[0]
        indices_set = set(indices)
    
    
    # Get all face indices where all three vertices are in the indices set
    inside_faces = [i for i, face in enumerate(faces_a) if set(face).issubset(indices_set)]
    return np.array(inside_faces)


def get_undercuts(mesh, x, y, z):
    # Compute analysis direction
    dir = mm.Vector3f()
    dir.x = x
    dir.y = y
    dir.z = z
    
    # Find undercuts
    undercuts = mm.FaceBitSet()
    mm.findUndercuts(mesh, dir, undercuts)

    # Extract face indices
    return mn.getNumpyBitSet(undercuts)



@log_execution_time
def fix_undercuts(input_mesh, x, y, z, tollerance=1e-1, bottom_offset=0.0):
    # Compute analysis direction
    dir = mm.Vector3f()
    dir.x = x
    dir.y = y
    dir.z = z
    
    # Copy mesh to avoid modifying the input
    mesh = mm.copyMesh(input_mesh)
    
    # Find undercuts
    undercuts = mm.FaceBitSet()
    mm.findUndercuts(mesh, dir, undercuts)
    
    # Remove undercuts
    mm.fixUndercuts(mesh, dir, tollerance, bottom_offset)

    # Return the updated mesh
    return mesh


@log_execution_time
def translate(mesh, x, y, z, distance=None):
    
    trans_vector = np.array([x, y, z], dtype=float)
    
    if distance is not None:
        unit_vector = trans_vector / np.linalg.norm(trans_vector)
        trans_vector = unit_vector * distance
        
    # Compute analysis direction
    vec = mm.Vector3f()
    vec.x = trans_vector[0]
    vec.y = trans_vector[1]
    vec.z = trans_vector[2]
    
    # Compute translation
    tanslation = mm.AffineXf3f.translation(vec)
    
    # Translate mesh
    mesh.transform(tanslation)

    # Return the updated mesh
    return mesh


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
def compute_accessibility(mesh, directions, face_count):
    # compute undercuts per direction and store them in one single numpy array
    
    dir_count = len(directions)
    accessibility = np.ones((dir_count, face_count), dtype=bool)
    for i in range(dir_count):
        x, y, z = directions[i]
        undercuts = get_undercuts(mesh, x, y, z)
        accessibility[i, :] = np.invert(undercuts)
        
    return accessibility


def find_valid_directions(principal, directions, tolerance_cosine=np.cos(np.radians(89))):
    """
    Validate directions based on the cosine similarity with the principal direction. Values
    that are within the tolerance are considered valid. Used to determine if a direction is
    perpendicular to the principal direction within a certain tolerance.
    
    :param principal: The principal direction to compare against.
    :param directions: An array of directions to validate.
    :param tolerance_cosine: The cosine similarity tolerance to use.
    :return: An array of indices of valid directions.
    """
    dot_products = np.dot(directions, principal.T)
    valid = np.abs(dot_products) <= tolerance_cosine
    return np.where(valid)[0]
  

@log_execution_time
def find_combinations_matching_best(directions, accessibility, max_slides=1, max_results=10, tolerance_degrees=1.0):
    """
    Find all combinations of rows in the undercuts array that match the best value.
    
    Parameters:
    - undercuts: A 2D numpy array where each row represents undercuts for a direction.
    - best_value: The best achievable result (sum of True values in the best case scenario).
    
    Returns:
    - A list of tuples, where each tuple contains indices of rows in `undercuts` that,
      when combined, match the best_value.
    """
    combinations_sum = []
    n = directions.shape[0]
    face_count = accessibility.shape[1]
    
    tolerance_cosine = np.cos( np.radians(90 - tolerance_degrees) )
    for i in range(0, n, 2):
        pair_union = np.any(accessibility[i:i+2], axis=0) # Union of paired directions
        total_performance = np.sum(pair_union) / face_count
        combinations_sum.append(([i, i+1], total_performance))
        slide_options = find_valid_directions(directions[i], directions, tolerance_cosine=tolerance_cosine)

        max_combinations = min(len(slide_options), max_slides)
        for r in range(1, max_combinations + 1):
            for combination in combinations(range(len(slide_options)), r):         
                slides_indexes = slide_options[list(combination)]
                # slides_directions = directions[slides_indexes]
                slides_union = np.any(accessibility[slides_indexes, :], axis=0)
                total_union = np.any([pair_union, slides_union], axis=0)
                total_performance = np.sum(total_union) / face_count

                # print(slides_directions)
                total_directions = [i, i+1] + slides_indexes.tolist()
                combinations_sum.append((total_directions, total_performance))
    
    # # Sort combinations based on the sum of True values in descending order
    sorted_combinations = sorted(combinations_sum, key=lambda x: x[1], reverse=True)

    # # Return the top N combinations
    max_results = min(max_results, len(sorted_combinations))
    
    sorted_combinations = sorted(combinations_sum, key=lambda x: x[1], reverse=True)
    max_results = min(max_results, len(sorted_combinations))
    for option in sorted_combinations[:max_results]:
        logger.debug(f"Option is {option[0]} with a performance {option[1]:.2f}")
        
    return sorted_combinations


def find_perpendicular_vector(v):
    """Find a non-zero vector perpendicular to v."""
    if v[0] == 0 and v[1] == 0:
        if v[2] == 0:
            # v is a zero vector
            return None
        # v is along the z-axis
        return np.array([1, 0, 0], dtype=float)
    return np.array([-v[1], v[0], 0], dtype=float)


def rotate_vector_around_axis(v, axis, theta_deg):
    """Rotate vector v around axis by theta degrees."""
    theta = np.radians(theta_deg)
    axis = axis / np.linalg.norm(axis)
    v_rot = (v * np.cos(theta) +
             np.cross(axis, v) * np.sin(theta) +
             axis * np.dot(axis, v) * (1 - np.cos(theta)))
    return v_rot


def generate_cone_vectors(x, y, z, angle_deg, N):
    original_vector = np.array([x, y, z], dtype=float)
    axis_vector = original_vector / np.linalg.norm(original_vector)
    
    perp_vector = find_perpendicular_vector(axis_vector)
    cone_vector = rotate_vector_around_axis(axis_vector, perp_vector, angle_deg)
    
    vectors = []
    for i in range(N):
        theta = 360.0 * i / N  # Step through 360 degrees in N increments
        rotated_vector = rotate_vector_around_axis(cone_vector, axis_vector, theta)
        vectors.append(rotated_vector)
    
    return np.array(vectors)


def generate_circle_translations(x, y, z, radius, N):
    original_vector = np.array([x, y, z], dtype=float)
    axis_vector = original_vector / np.linalg.norm(original_vector)
    
    perp_vector = find_perpendicular_vector(axis_vector)
    radius_vector = perp_vector * radius
    # cone_vector = rotate_vector_around_axis(axis_vector, perp_vector, angle_deg)
    
    vectors = []
    for i in range(N):
        theta = 360.0 * i / N  # Step through 360 degrees in N increments
        rotated_vector = rotate_vector_around_axis(radius_vector, axis_vector, theta)
        
        print(f"{rotated_vector[0]:.2f}, {rotated_vector[1]:.2f}, {rotated_vector[2]:.2f}")
        
        # Compute relative translation compared to previous vector
        if i > 0:
            vectors.append(rotated_vector - previous_vector)
            previous_vector = rotated_vector

        else:
            previous_vector = rotated_vector
            vectors.append(rotated_vector)
    
    return np.array(vectors)


@log_execution_time
def relax_accessibility(mesh, initial_accessibility, direction, tolerance_degrees=1.0, n=4):
    # Compute the additional directions
    cone_vectors = generate_cone_vectors(direction[0], direction[1], direction[2], tolerance_degrees, n)
    
    face_count = initial_accessibility.shape[0]
    cone_accessibility = np.ones((n, face_count), dtype=bool)
    
    for i in range(n):
        x, y, z = cone_vectors[i]
        undercuts = get_undercuts(mesh, x, y, z)
        cone_accessibility[i, :] = np.invert(undercuts)
        
    relaxed_accessibility = np.any(cone_accessibility, axis=0)
    
    return relaxed_accessibility