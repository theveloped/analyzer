# -*- coding: utf-8 -*-
"""
Created on Wed Nov 20 16:49:50 2024

@author: DavidvanVenrooij
"""
import trimesh
import main
import subprocess
from scipy.spatial.transform import Rotation as R
import time

#import from analysis
from utils import log_execution_time

#import from main:
    
from loguru import logger
import os
import json
from argparse import Namespace

import numpy as np
from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn

from utils import has_valid_extension, ensure_directory, ensure_parent_directories
from server import serve
from analysis import map_inter_faces,load_mesh, heal_mesh, save_mesh, get_mesh_data, sample_unity_vector_pairs, sample_unity_vector_datums, compute_accessibility, find_combinations_matching_best, relax_accessibility, fix_undercuts, offset_mesh, double_offset, get_distance, get_inside_mesh, get_inside_indices, single_offset, translate, map_result_faces, generate_circle_translations

##SCRIPT
FINE_MESH_FILE = "fine_mesh.obj"
FINE_VERTS_FILE = "fine_verts.npy"
FINE_FACES_FILE = "fine_faces.npy"
TOOL_VERTS_FILE = "tool_verts.npy"
TOOL_FACES_FILE = "tool_faces.npy"
DIRECTIONS_FILE = "directions.npy"
ACCESSIBILITY_FILE = "accessibility.npy"
HIGHLIGHT_FILE = "highlights.json"
#user args
directory = '/output'

#creating arguments object

"#creating a mesh"
class Mesh:
    def __init__(self,args):
        logger.debug(f"initiated class: {args.input, args.output}")
        if args.output:
            self.dir_path = args.output
        else:
            input_name = os.path.basename(args.input)
            input_name = input_name.rsplit(".", 1)[0]
        self.input = args.input
        self.obj_path = os.path.join(self.dir_path, FINE_MESH_FILE)
        self.verts_path = os.path.join(self.dir_path, FINE_VERTS_FILE)
        self.faces_path = os.path.join(self.dir_path, FINE_FACES_FILE)  
        self.tool_verts_path = os.path.join(self.dir_path, TOOL_VERTS_FILE)
        self.tool_faces_path = os.path.join(self.dir_path, TOOL_FACES_FILE)
        
    @log_execution_time
    def BuildMesh(self,args):
        
        #logging stuff
        logger.debug(f"Building Mesh of file: {self.input}")
        
        # Check if the file has a valid extension
        has_valid_extension(self.input, [".stl",".glb"])
        
        if self.input[-4:] == '.glb':
            #load the Mesh from glb
            mesh = trimesh.load(self.input, force = 'mesh', process = True)
            verts = mesh.vertices
            faces = mesh.faces
            self.normals = mesh.face_normals
            self.metadata = mesh.metadata
            mesh  = mn.meshFromFacesVerts(faces, verts, duplicateNonManifoldVertices= False)
            #mesh  = heal_mesh(mesh, voxelSize = args.tollerance)
        else:
            # load the Mesh from stl
            mesh = load_mesh(self.input, heal=args.heal, offset=args.offset, tollerance=args.tollerance)
            verts, faces = get_mesh_data(mesh)
        
        # Define other files
        ensure_directory(self.dir_path)
        
        #storing vertices 
        print(f"Saving to path: {self.verts_path}")
        np.save(self.verts_path, verts)
        #storing faces
        print(f"Saving to path: {self.faces_path}")
        np.save(self.faces_path, faces)
        #storing object file
        save_mesh(mesh, self.obj_path)
        #debugging purpous
        self.og_faces = faces
        self.og_verts = verts
        
    def BuildToolMesh(self):
        #logging stuff
        logger.debug(f"Building tool mesh of: {self.input}")
    
        #load from cash
        verts = np.load(os.path.join(self.dir_path, FINE_VERTS_FILE))
        faces = np.load(os.path.join(self.dir_path, FINE_FACES_FILE))
        
        #storing vertices  under different name
        logger.debug(f"Storing verts: {self.tool_verts_path}")
        np.save(self.tool_verts_path, verts)
        #storing faces
        logger.debug(f"Storing faces: {self.tool_faces_path}")
        np.save(self.tool_faces_path, faces)
        
    @log_execution_time
    def Plot(self, mesh = None):
        index_path = os.path.abspath("./index.html")
        
        if mesh == None:
            #show online    
            directory = os.path.dirname(self.obj_path)
            serve(index_path, directory, timeout=10.0)
        else:
            #temp save
            save_mesh(mesh, './temp/fine_mesh.obj')
            #serve
            serve(index_path, './temp', timeout=10.0)
        
    @log_execution_time
    def RadialTool(self,args):
        logger.info("Perform a tool analyis on the mesh")
        
        #load from cash
        verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        mesh = mn.meshFromFacesVerts(faces, verts)
        logger.debug(f"Mesh loaded with {len(faces)} faces")
        directions = np.load(os.path.join(args.directory, DIRECTIONS_FILE))
        accessibility = np.load(os.path.join(args.directory, ACCESSIBILITY_FILE))
        
        #Fill undercuts
        undercut_mesh = fix_undercuts(mesh, directions[args.direction][0], directions[args.direction][1], directions[args.direction][2])
        
        directory = os.path.abspath(args.directory)
        obj_path = os.path.join(self.obj_path, FINE_MESH_FILE)
        # save_mesh(undercut_mesh, obj_path)
        
        # radius_mesh = double_offset(undercut_mesh, args.radius, -args.radius, args.tollerance, decimate=False)
        # distances = get_distance(mesh, radius_mesh, upper_limit=10, lower_limit=0.0)
        
        # # Compute all vertices where distance is abbove tollerance of 0.1
        # indices = np.where(np.abs(distances) > 0.1)[0]
        # indices_set = set(indices)
        
        # # Get all face indices where all three vertices are in the indices set
        # radius_faces = [i for i, face in enumerate(faces) if set(face).issubset(indices_set)]
        # radius_faces = np.array(radius_faces)
               
        # Map the results
        radius_mesh = double_offset(undercut_mesh, args.radius, -args.radius, args.tollerance, decimate=False)
        radius_faces = map_result_faces(mesh, radius_mesh, faces, min_range=args.intersect_tollerance, n_intersect=1)
        
        # Keep only the faces that are accessible
        # radius_faces = radius_faces[accessibility[args.direction, radius_faces]]
        
        radius_faces = radius_faces.tolist()
        
        # # Keep only the faces that are accessible
        # radius_faces = radius_faces[accessibility[args.direction, radius_faces]]
        # radius_faces = radius_faces.tolist()
        
        #save the fases which are in contact with the radius as highlight
        numpyData = {"faces": radius_faces}
        highlight_path = os.path.join(self.dir_path, HIGHLIGHT_FILE)
        
        with open(highlight_path, "w") as f:
            json.dump(numpyData, f)
    
    def get_z_radius(self, mesh, radius, tollerance, scale):
        #scale in z
        n_verts, n_faces = get_mesh_data(mesh)
        n_verts[:,2] = n_verts[:,2] * scale
        new_mesh     = mn.meshFromFacesVerts(n_faces, n_verts)
            
        # Offset outer diameter+R & -R
        new_mesh  = double_offset(new_mesh , radius, -radius, tollerance, decimate=False)
        
        # Scale z back
        n_verts, n_faces = get_mesh_data(new_mesh)
        n_verts[:,2] = n_verts[:,2] / scale
        new_mesh = mn.meshFromFacesVerts(n_faces, n_verts)
        
        return new_mesh
    
    def get_xy_offset(self, mesh, radius, tollerance, scale):
        #scale in z
        n_verts, n_faces = get_mesh_data(mesh)
        n_verts[:,2] = n_verts[:,2] * scale
        new_mesh     = mn.meshFromFacesVerts(n_faces, n_verts)
            
        # Offset outer diameter+R & -R
        new_mesh  = single_offset(new_mesh , radius, tollerance, decimate=False)
        
        # Scale z back
        n_verts, n_faces = get_mesh_data(new_mesh)
        n_verts[:,2] = n_verts[:,2] / scale
        new_mesh = mn.meshFromFacesVerts(n_faces, n_verts)
        
        return new_mesh
    
    @log_execution_time
    def MSTool(self,args):
        
        r_outter = args.outer_radius
        
        if not "inner_radius" in dir(args):
            logger.warning("no inner radius given, will analyse as if it is end mill")
            r_inner = 0.0
        else:
            r_inner = args.inner_radius
        
        if not "length" in dir(args):
            logger.warning("no length was given, not analysing depth")
            length = 0
        else:
            length = args.length
        
        if not "tool_radius" in dir(args):
            logger.warning("no tool radius was given, not analysing depth")
            r_tool = 0
        else:
            r_tool = args.tool_radius
        
        logger.debug(f"perform tool analysis, outer radius {r_outter}, inner radius {r_inner}, length = {length}, tool radius = {r_tool}")
        
        "for now always centerline z = mill direction, rotate beforehand!"
                
        # Load from cash
        verts = np.load(os.path.join(self.dir_path, FINE_VERTS_FILE))
        faces = np.load(os.path.join(self.dir_path, FINE_FACES_FILE))
        
        # Load tool from cash (less mesh points, mesh is only used for calcs)
        tool_verts = np.load(os.path.join(self.dir_path, TOOL_VERTS_FILE))
        tool_faces = np.load(os.path.join(self.dir_path, TOOL_FACES_FILE))
        
        "rotate the tool and mesh body"
        rotation = args.rotation/180 * np.pi
        vector   = args.vector
        r = R.from_rotvec(rotation * np.array(vector))
        
        #apply the rotation
        tool_verts = r.apply(tool_verts)
        verts      = r.apply(verts)
        
        #save the meshes
        tool_mesh  = mn.meshFromFacesVerts(tool_faces, tool_verts)
        mesh       = mn.meshFromFacesVerts(faces, verts)
        ma.tool_mesh = tool_mesh
        ma.mesh      = mesh
        
        "Check acces"
        #Fill undercuts (z+) Staring with tool
        undercut_mesh = fix_undercuts(tool_mesh, 0 , 0 , 1 , bottom_offset = args.intersect_tollerance)
        
        "The outer radius (expensive)"
        #get scale
        scale = np.min([300 , np.max([args.outer_radius,r_tool]) / (args.intersect_tollerance / 10)])
        
        #scale in z
        me_mesh = self.get_z_radius(undercut_mesh, args.outer_radius, args.tollerance, scale)
                
        "Check acces by tool diameter (expensive)"
        if length > 0:
            #translate
            acces_mesh = translate(tool_mesh, 0, 0, -length)
            ma.acces_mesh = acces_mesh
            if r_tool > 0:
                #z radius analysis
                acces_mesh_1 = self.get_xy_offset( acces_mesh, r_tool - r_outter, args.tollerance, scale)
                ma.acces_mesh_1 = acces_mesh_1
                #small offset to ensure intersection
                acces_mesh_2 = single_offset( acces_mesh_1 , args.tollerance , args.tollerance)
                ma.acces_mesh_2 = acces_mesh_2
            else:
                acces_mesh_2 = single_offset( acces_mesh , args.tollerance , args.tollerance)
                ma.acces_mesh_2 = acces_mesh_2
            
            #get intersecting
            intersect_mesh = get_inside_mesh( mesh, acces_mesh_2)
            intersect_mesh = heal_mesh(intersect_mesh, args.tollerance, decimate = False)
            ma.intersect_mesh = intersect_mesh
            
            #map intersecting faces
            inside_faces_in = map_result_faces( mesh, intersect_mesh, faces, max_range=args.intersect_tollerance, n_intersect = 1)
            self.inside_faces_in = inside_faces_in
        else:
            inside_faces_in  = []
            
        "rotate the tool back (not needed, since we only need face id's"
        # r = R.from_rotvec(-rotation * np.array(vector))
        # #apply the rotation
        # n_verts = r.apply(n_verts)
        # new_mesh = mn.meshFromFacesVerts(n_faces, n_verts)

        "The tip radius(cheap)"
        if args.inner_radius > args.outer_radius * 0.1:
            #get the double offset to emulate the ballmill
            ms_mesh = double_offset(me_mesh , args.inner_radius, -args.inner_radius, args.tollerance, decimate=False)
        else:
            ms_mesh = me_mesh
        self.ms_mesh = ms_mesh
        #get faces outside end mill mesh
        outside_faces_in = map_result_faces( mesh, ms_mesh, faces, min_range=args.intersect_tollerance, n_intersect=1)
        ma.outside_faces_in = outside_faces_in
        
        "make list of bad faces"
        highlight_faces = []
        for face in range(0,len(faces)):
            if (face in outside_faces_in) or (face in inside_faces_in):# or ( (face in Errorfaces[idxmin+1]) and (not face in Errorfaces[idxmin])):
                highlight_faces.append(face)
                
        ma.highlight_faces= highlight_faces
                       
        #save the fases which are in contact with the radius as highlight
        numpyData = {"faces": highlight_faces}
        highlight_path = os.path.join(self.dir_path, HIGHLIGHT_FILE)
        with open(highlight_path, "w") as f:
            json.dump(numpyData, f)
            
        return self.get_result(faces,highlight_faces)
    
    def get_result(self,total_faces,fault_faces):
        conn = np.array(self.connect)
        og_ids = np.unique(conn[:,1])
        Total_facecount = np.zeros(len(og_ids))
        Fail_facecount  = np.zeros(len(og_ids))
        result          = np.zeros([len(og_ids),2])
        #store succes rate based on OG face-id's
        for face_id in total_faces:
            #add 1 to original face id (indicating a face belongs to a certain og_face)
            og_id = conn[ face_id , 1 ]
            Total_facecount[ og_id ] = Total_facecount[ og_id ] + 1
        
        for fail_id in fault_faces:
            og_id = conn[ fail_id , 1 ]
            Fail_facecount[ og_id ] = Fail_facecount[ og_id ] + 1
        
        #temp, but probably good enough
        result[:,0] = 1 - Fail_facecount / Total_facecount
        
        for i in range(1,len(og_ids)):
            result[i,1] = i
    
        result = result.tolist()
        result.sort()
        
        # print('succes rate worst 20 faces')
        # for i in range(0,np.min([len(result),20])):
        #     print( np.round(result[i][0],2) , result[i][1] )
        # print('succes rate best 20 faces')
        # for i in range(1,np.min([len(result),21])):
        #     print( np.round(result[-i][0],2) , result[-i][1] )
        
        return result
    
    @log_execution_time
    def Radii_analysis(self,args):
        "collect input"
        r_min      = args.rmin
        r_max      = args.rmax
        count      = args.count
        radius     = []
        Errorfaces = []
        
        " load from memory "
        verts = np.load(os.path.join(self.dir_path, FINE_VERTS_FILE))
        faces = np.load(os.path.join(self.dir_path, FINE_FACES_FILE))
        mesh  = mn.meshFromFacesVerts(faces, verts)
                
        "The tip radius(cheap)"
        for r in np.linspace(r_min , r_max , count):
            logger.info(f"analysing radii {np.round(r,1)}")
            new_mesh  = double_offset(mesh , r, -r, args.tollerance, decimate=False)
            outside_faces = map_result_faces(mesh, new_mesh, faces, min_range=args.intersect_tollerance ,n_intersect=1)
            Errorfaces.append(outside_faces)
            radius.append(r)
        
        self.radius     = radius
        self.Errorfaces = Errorfaces
        
        return radius, Errorfaces
    
    @log_execution_time
    def subdivide_mesh_simple(self, vertices, faces, n_subdivisions=1):
        """
        Subdivide a triangular mesh using linear subdivision.
    
        Parameters:
            vertices (np.ndarray): Array of vertex coordinates (n, 3).
            faces (np.ndarray): Array of triangle indices (m, 3).
            n_subdivisions (int): Number of times to subdivide the mesh.
    
        Returns:
            tuple: (new_vertices, new_faces)
                - new_vertices (np.ndarray): Subdivided vertex coordinates.
                - new_faces (np.ndarray): Subdivided triangle indices.
        """
        def midpoint(v1, v2):
            """Compute the midpoint between two vertices."""
            return (v1 + v2) / 2
    
        for _ in range(n_subdivisions):
            edge_to_midpoint = {}
            new_vertices = vertices.tolist()
            new_faces = []
    
            for face in faces:
                # Get the vertices of the current face
                v0, v1, v2 = face
                p0, p1, p2 = vertices[v0], vertices[v1], vertices[v2]
    
                # Calculate midpoints for each edge
                edges = [(v0, v1), (v1, v2), (v2, v0)]
                midpoints = []
                for edge in edges:
                    edge = tuple(sorted(edge))  # Sort to ensure uniqueness
                    if edge not in edge_to_midpoint:
                        midpoint_coords = midpoint(vertices[edge[0]], vertices[edge[1]])
                        edge_to_midpoint[edge] = len(new_vertices)
                        new_vertices.append(midpoint_coords)
                    midpoints.append(edge_to_midpoint[edge])
    
                # Add four new faces
                m0, m1, m2 = midpoints
                new_faces.extend([
                    [v0, m0, m2],
                    [v1, m1, m0],
                    [v2, m2, m1],
                    [m0, m1, m2],
                ])
    
            vertices = np.array(new_vertices)
            faces = np.array(new_faces)
    
        return vertices , faces

    @log_execution_time
    def directions(self,args):
        logger.debug(f"Computing {args.count} directions")
        #directions = sample_unity_vector_pairs(args.count)
        directions = sample_unity_vector_datums(0)
        logger.debug("Cheking accessibility per direction")
        verts = np.load(os.path.join(self.dir_path, FINE_VERTS_FILE))
        faces = np.load(os.path.join(self.dir_path, FINE_FACES_FILE))
        mesh = mn.meshFromFacesVerts(faces, verts)
        
        face_count = len(faces)
        accessibility = compute_accessibility(mesh, directions, face_count)
        
        if args.relax:
            for direction_index in range(directions.shape[0]):
                relaxed_accessibility = relax_accessibility(mesh, accessibility[direction_index,:], directions[direction_index], tolerance_degrees=args.relax_tollerance, n=args.relax_samples)
                accessibility[direction_index,:] = relaxed_accessibility
        
        directions_path = os.path.join(self.dir_path, DIRECTIONS_FILE)
        accessibility_path = os.path.join(self.dir_path, ACCESSIBILITY_FILE)
        
        logger.debug(f"Storing directions at: {directions_path}")
        np.save(directions_path, directions)
        
        logger.debug(f"Storing accessibility at: {accessibility_path}")
        np.save(accessibility_path, accessibility)
        
    @log_execution_time
    def directions_datumonly(self,args):
        logger.debug(f"Computing {args.count} directions")
        directions = sample_unity_vector_datums(args.count)
        
        logger.debug("Cheking accessibility per direction")
        verts = np.load(os.path.join(self.dir_path, FINE_VERTS_FILE))
        faces = np.load(os.path.join(self.dir_path, FINE_FACES_FILE))
        mesh = mn.meshFromFacesVerts(faces, verts)
        
        face_count = len(faces)
        accessibility = compute_accessibility(mesh, directions, face_count)
        
        if args.relax:
            for direction_index in range(directions.shape[0]):
                relaxed_accessibility = relax_accessibility(mesh, accessibility[direction_index,:], directions[direction_index], tolerance_degrees=args.relax_tollerance, n=args.relax_samples)
                accessibility[direction_index,:] = relaxed_accessibility
        
        directions_path = os.path.join(self.dir_path, DIRECTIONS_FILE)
        accessibility_path = os.path.join(self.dir_path, ACCESSIBILITY_FILE)
        
        logger.debug(f"Storing directions at: {directions_path}")
        np.save(directions_path, directions)
        
        logger.debug(f"Storing accessibility at: {accessibility_path}")
        np.save(accessibility_path, accessibility)

    def subdivide_mesh_planar(self,vertices, faces, mesh_size):
        """
        Subdivide a triangular mesh (STL-style) efficiently until all edges are shorter than `mesh_size`.
        The subdivision preserves the planes of the original triangles.
    
        Parameters:
            vertices (np.ndarray): Array of vertex coordinates (n, 3).
            faces (np.ndarray): Array of triangle indices (m, 3).
            mesh_size (float): Maximum allowed edge length.
    
        Returns:
            tuple: (new_vertices, new_faces)
                - new_vertices (np.ndarray): Subdivided vertex coordinates.
                - new_faces (np.ndarray): Subdivided triangle indices.
        """
        def midpoint(v1, v2):
            """Compute the midpoint between two vertices."""
            return (np.array(v1) + np.array(v2)) / 2
    
        def edge_length(v1, v2):
            return np.linalg.norm(np.array(v1) - np.array(v2))

    
        def is_edge_too_long(v0, v1):
            """Check if the edge is longer than the mesh size."""
            return edge_length(new_vertices[v0], new_vertices[v1]) > mesh_size

    
        def subdivide_face(vertices,v0, v1, v2):
            
            """Subdivide a single triangle face if necessary."""
            p0, p1, p2 = vertices[v0], vertices[v1], vertices[v2]
            edges = [(v0, v1), (v1, v2), (v2, v0)]
            too_long_edges = [is_edge_too_long(e[0], e[1]) for e in edges]
    
            if not any(too_long_edges):
                # No subdivision needed
                return [[v0, v1, v2]]
    
            # Compute midpoints for edges that are too long
            edge_to_midpoint = {}
            for edge, too_long in zip(edges, too_long_edges):
                if too_long:
                    edge = tuple(sorted(edge))  # Sort to ensure uniqueness
                    if edge not in edge_to_midpoint:
                        midpoint_coords = midpoint(vertices[edge[0]], vertices[edge[1]])
                        edge_to_midpoint[edge] = len(new_vertices)
                        new_vertices.append(midpoint_coords.tolist())
                              
            # Build new faces while maintaining coplanarity
            midpoints = [edge_to_midpoint.get(tuple(sorted(edge)), None) for edge in edges]
            m0, m1, m2 = midpoints
    
            if all(midpoints):
                # Subdivide into four smaller triangles
                return [
                    [v0, m0, m2],
                    [v1, m1, m0],
                    [v2, m2, m1],
                    [m0, m1, m2],
                ]
            elif m0 and m1:
                # Subdivide into three triangles (split one edge and its opposing vertex)
                return [
                    [v0, m0, v2],
                    [m0, m1, v2],
                    [v1, m1, m0],
                ]
            elif m1 and m2:
                # Subdivide into three triangles (split one edge and its opposing vertex)
                return [
                    [v1, m1, v0],
                    [m1, m2, v0],
                    [v2, m2, m1],
                ]
            elif m2 and m0:
                # Subdivide into three triangles (split one edge and its opposing vertex)
                return [
                    [v2, m2, v1],
                    [m2, m0, v1],
                    [v0, m0, m2],
                ]
            else:
                raise ValueError("Unexpected edge subdivision configuration")
    
        vertices = vertices.astype(float)  # Ensure vertices are floating-point
        new_vertices = vertices.tolist()
        new_faces = []
    
        for face in faces:
            v0, v1, v2 = face
            stack = [[v0, v1, v2]]  # Initialize stack with the original face
            while stack:
                current_face = stack.pop()
                subdivided_faces = subdivide_face(new_vertices, *current_face)
                for f in subdivided_faces:
                    if len(f) == 3:
                        stack.append(f)  # Push new faces onto the stack
                    else:
                        new_faces.append(f)
    
        return np.array(new_vertices), np.array(new_faces)
    
    def compute_edges(self,vertices, faces):
        """
        Compute the edges array from the given vertices and faces.
        
        Parameters:
            vertices (np.ndarray): Array of vertex coordinates and IDs (n, 4) - [ID, x, y, z].
            faces (np.ndarray): Array of face data (m, 4) - [ID, vert1, vert2, vert3].
        
        Returns:
            np.ndarray: Edges array with columns:
                        [Edge ID, Vertice ID 1, Vertice ID 2, Face ID Left, Face ID Right, Length].
        """
        # Dictionary to track unique edges and associated data
        edge_dict = {}
        edge_id = 0
    
        # Helper function to calculate edge length
        def edge_length(v1, v2):
            """
            Calculate the Euclidean distance between two vertices.
            v1 and v2 are the row indices of the vertices in the vertices array.
            """
            coord1 = vertices[int(v1), 0:3]  # Directly use row index to access coordinates
            coord2 = vertices[int(v2), 0:3]  # ...
            return np.linalg.norm(coord2 - coord1)
                
        # Process each face
        for face_id, face in enumerate(faces):
            v1, v2, v3 = face  # Unpack vertex IDs directly
            
            # Define the three edges of the triangle
            tri_edges = [
                (int(v1), int(v2)),
                (int(v2), int(v3)),
                (int(v3), int(v1)),
            ]
            
            #maak een array met per edge allemaal informatie enzo .......
                        
            for vert1, vert2 in tri_edges:
                # Sort vertex IDs to maintain consistency
                edge_key = tuple(sorted((int(vert1), int(vert2))))
    
                if edge_key not in edge_dict:
                    # New edge: Add it to the dictionary
                    edge_dict[edge_key] = [edge_id, vert1, vert2, face_id, -1, edge_length(*edge_key)]
                    edge_id += 1
                else:
                    # Existing edge: Update the "right" face ID
                    edge_dict[edge_key][4] = face_id
                    
            #test if it is correct
            self.test = edge_dict[edge_key]
            if (not v1 in faces[int(edge_dict[edge_key][3])]) and  (not v1 in faces[int(edge_dict[edge_key][4])]):
                print('error')
            
                
        # Convert edge dictionary to a NumPy array
        edges = np.array(
            [value for value in edge_dict.values()],
            dtype=float
        )
        
        edges = edges[edges[:,5].argsort()][::-1]
        
        #missing_faces = [key for key, val in edge_dict.items() if val[4] == -1]
        #print(f"Edges with missing faces: {missing_faces}")

        
        return edges
    
    def subdivide_mesh_length(self, vertices, faces, edges, edge_length_threshold):
        """
        Subdivide a mesh based on edge length.
    
        Parameters:
            vertices (np.ndarray): Array of vertex coordinates (n, 3).
            faces (np.ndarray): Array of face data (m, 3).
            edges (np.ndarray): Array of edges (k, 6) - 
                                [Edge ID, Vertice 1 Index, Vertice 2 Index, Face Left, Face Right, Length].
            edge_length_threshold (float): Threshold for maximum allowable edge length.
    
        Returns:
            tuple: (new_vertices, new_faces)
                - new_vertices (np.ndarray): Subdivided vertex coordinates.
                - new_faces (np.ndarray): Subdivided faces.
        """
        # Convert vertices to list for easier appending
        new_vertices = vertices.tolist()
        new_faces = []
    
        # Dictionary to store midpoint vertex indices for edges
        edge_midpoints = {}
    
        def add_midpoint(v1, v2):
            """Add a midpoint for a given edge and return its index."""
            edge_key = tuple(sorted((v1, v2)))
            if edge_key not in edge_midpoints:
                # Compute midpoint
                coord1 = vertices[v1]
                coord2 = vertices[v2]
                midpoint = (coord1 + coord2) / 2
                # Add midpoint to vertices and record its index
                new_vertices.append(midpoint.tolist())
                edge_midpoints[edge_key] = len(new_vertices) - 1
            return edge_midpoints[edge_key]
    
        for face in faces:
            v1, v2, v3 = face
            # Get edge lengths and midpoints
            edges_to_subdivide = [
                (v1, v2, edges[(edges[:, 1] == v1) & (edges[:, 2] == v2) | (edges[:, 1] == v2) & (edges[:, 2] == v1), 5][0]),
                (v2, v3, edges[(edges[:, 1] == v2) & (edges[:, 2] == v3) | (edges[:, 1] == v3) & (edges[:, 2] == v2), 5][0]),
                (v3, v1, edges[(edges[:, 1] == v3) & (edges[:, 2] == v1) | (edges[:, 1] == v1) & (edges[:, 2] == v3), 5][0]),
            ]
    
            # Determine which edges need subdivision
            subdivide = [e[2] > edge_length_threshold for e in edges_to_subdivide]
    
            if not any(subdivide):
                # No subdivision needed, keep the original face
                new_faces.append([v1, v2, v3])
            else:
                # Get or create midpoints for edges to subdivide
                midpoints = [
                    add_midpoint(e[0], e[1]) if subdivide[i] else None
                    for i, e in enumerate(edges_to_subdivide)
                ]
                m1, m2, m3 = midpoints
    
                # Create new faces depending on which edges are subdivided
                if all(subdivide):
                    # All edges subdivided: 4 new faces
                    new_faces.extend([
                        [v1, m1, m3],
                        [v2, m2, m1],
                        [v3, m3, m2],
                        [m1, m2, m3],
                    ])
                elif subdivide[0] and subdivide[1]:
                    # First two edges subdivided: 3 new faces
                    new_faces.extend([
                        [v1, m1, v3],
                        [m1, m2, v3],
                        [v2, m2, m1],
                    ])
                elif subdivide[1] and subdivide[2]:
                    # Last two edges subdivided: 3 new faces
                    new_faces.extend([
                        [v2, m2, v1],
                        [m2, m3, v1],
                        [v3, m3, m2],
                    ])
                elif subdivide[2] and subdivide[0]:
                    # First and last edges subdivided: 3 new faces
                    new_faces.extend([
                        [v3, m3, v2],
                        [m3, m1, v2],
                        [v1, m1, m3],
                    ])
                else:
                    # Only one edge subdivided: 2 new faces
                    if subdivide[0]:
                        new_faces.extend([
                            [v1, m1, v3],
                            [m1, v2, v3],
                        ])
                    elif subdivide[1]:
                        new_faces.extend([
                            [v2, m2, v1],
                            [m2, v3, v1],
                        ])
                    elif subdivide[2]:
                        new_faces.extend([
                            [v3, m3, v2],
                            [m3, v1, v2],
                        ])
    
        # Convert back to NumPy arrays
        return np.array(new_vertices), np.array(new_faces)

    def subdivide_mesh_near_edges(self, vertices, faces, edges, connect_old, edge_length_threshold):
        """
        Subdivide a mesh near long edges based on edge length.
    
        Parameters:
            vertices (np.ndarray): Array of vertex coordinates (n, 3).
            faces (np.ndarray): Array of face data (m, 3).
            edges (np.ndarray): Array of edges (k, 6) - 
                                [Edge ID, Vertice 1 Index, Vertice 2 Index, Face Left, Face Right, Length].
            edge_length_threshold (float): Threshold for maximum allowable edge length.
    
        Returns:
            tuple: (new_vertices, new_faces, new_edges)
                - new_vertices (np.ndarray): Subdivided vertex coordinates.
                - new_faces (np.ndarray): Subdivided faces.
                - new_edges (np.ndarray): Updated edges after subdivision.
        """
        
        #convert to list for easy appending
        #vertices = vertices.tolist()
        #faces    = faces.tolist()
        
        # Dictionary to store midpoint vertex indices for edges
        edge_midpoints = {}
        # Remember current faces
        new_faces = faces
    
        # Dictionary to store midpoint vertex indices for edges
        edge_midpoints = {}
    
        def add_midpoint(v1, v2, vertices):
            """Add a midpoint for a given edge and return its index."""
            edge_key = tuple(sorted((v1, v2)))  # Sort to avoid duplicating midpoints for undirected edges
            if edge_key not in edge_midpoints:
                # Compute midpoint
                coord1 = vertices[int(v1)]
                coord2 = vertices[int(v2)]
                midpoint = (coord1 + coord2) / 2
                vertices = np.append(vertices,[midpoint.tolist()],axis = 0)  # Append new midpoint to the list
                edge_midpoints[edge_key] = len(vertices) - 1  # Store index of the midpoint
            return vertices, int(len(vertices) - 1)
    
        # Loop through edges and subdivide if necessary
        connect    = []
        exceptface = []
        
        for edge in edges:
            v1, v2, face_left, face_right, length = edge[1::]
            face_left  = int(face_left)
            face_right = int(face_right)
            
            # If the edge length exceeds the threshold, subdivide it
            if length > edge_length_threshold and (face_left not in exceptface) and (face_right not in exceptface):
                v1 = int(v1)
                v2 = int(v2)    
                #make a new vertice on midpoint
                vertices, midpoint_i = add_midpoint(v1, v2, vertices)
                #add original faces to list in order to ignore them for rest of subdevide
                if face_left != -1:
                    exceptface = np.append(exceptface,[face_left])
                if face_right != -1:
                    exceptface = np.append(exceptface,[face_right])
                
                #Store the old faces and modify them to new faces (other verts coordinates)
                Face_left      = faces[face_left]
                Face_right     = faces[face_right]
                Face_left_new  = faces[face_left].copy()
                Face_right_new = faces[face_right].copy()

                for i in range(0,3):
                    if Face_left[i]      == v1: 
                        Face_left[i]      = midpoint_i
                    if Face_right[i]     == v1: 
                        Face_right[i]     = midpoint_i
                    if Face_left_new [i] == v2: 
                        Face_left_new[i]  = midpoint_i
                    if Face_right_new[i] == v2: 
                        Face_right_new[i] = midpoint_i
                    
                # Add the new faces to the faces array
                face_left_new  = int(len(new_faces)-1)
                new_faces      = np.append(new_faces,[Face_left_new],axis=0)
                face_right_new = int(len(new_faces)-1)
                new_faces      = np.append(new_faces,[Face_right_new],axis=0)
                
                #change the old faces
                new_faces[int(face_left)]  = Face_left
                new_faces[int(face_right)] = Face_right
                
                #create a connection matrix for the new faces created
                #left = new, right = old
                
                connect.append([ int(face_left)    ,  connect_old[ int(face_left)][1] ])
                connect.append([ int(face_right)   ,  connect_old[int(face_right)][1] ])
                connect.append([ int(face_left_new),  connect_old[ int(face_left)][1] ])
                connect.append([ int(face_right_new), connect_old[int(face_right)][1] ])
                
                #Create new edges with the midpoint
                new_edges = [
                    [len(edges)  , v1        , midpoint_i , face_left_new , face_right_new , np.linalg.norm(vertices[v1] - vertices[midpoint_i])],
                    [len(edges)+1, midpoint_i, v2         , face_left     , face_right     , np.linalg.norm(vertices[midpoint_i] - vertices[v2])]
                ]
                #append new edges to df.
                edges = np.append(edges,new_edges, axis = 0)
            
        #after analysis was done add all the non-subdevided faces
        for i in range(0,len(faces)):
            if i not in exceptface:
                connect.append([ int(i)  ,  connect_old[ int(i)][1]  ])
                
                
        return vertices, new_faces, sorted(connect)
    
    @log_execution_time
    def stitch_mesh(self, vertices, faces, tolerance=1e-6):
        logger.debug(f"stitching mesh with tolerance: {tolerance}")
        
        """
        Stitches a mesh by combining vertices that are within a specified tolerance
        and updates the face definitions to use the new vertex IDs.
        
        Args:
            vertices (list or np.ndarray): List of vertex coordinates (e.g., [[x1, y1, z1], [x2, y2, z2], ...]).
            faces (list of lists): List of faces, each defined by indices into the `vertices` list (e.g., [[0, 1, 2], ...]).
            tolerance (float): Maximum distance between vertices to be considered equal.
            
        Returns:
            tuple: (new_vertices, new_faces)
                - new_vertices: List of unique vertices after stitching.
                - new_faces: Updated face definitions using new vertex indices.
        """
        # Convert vertices to numpy array for efficient processing
        vertices = np.array(vertices)
        
        # Initialize a mapping from old vertex indices to new ones
        vertex_map = np.arange(len(vertices))
        
        # Find and merge duplicate vertices
        for i in range(len(vertices)):
            for j in range(i + 1, len(vertices)):
                if np.linalg.norm(vertices[i] - vertices[j]) < tolerance:
                    # Merge vertex j into vertex i
                    vertex_map[j] = vertex_map[i]
        
        # Create unique vertices
        unique_indices, inverse_map = np.unique(vertex_map, return_inverse=True)
        new_vertices = vertices[unique_indices]
        
        # Update face definitions
        new_faces = [[inverse_map[vid] for vid in face] for face in faces]
        
        return np.array(new_vertices), new_faces
    
    @log_execution_time
    def subdivision(self,args):
        
        logger.debug("performing subdivision")
        
        t0 = time.process_time()
        #load from cash
        verts = np.load(os.path.join(self.dir_path, FINE_VERTS_FILE))
        faces = np.load(os.path.join(self.dir_path, FINE_FACES_FILE))
        verts, faces = self.stitch_mesh(verts, faces, tolerance = 1e-2)
        self.faces = faces
        self.verts = verts
        
        #initiate conn matrix
        connect = []
        for i in range(0,len(faces)):
            connect.append([i,i])
        
        #start the subdivide loop
        l_max = 100
        n = 0
        #create edges array
        tp=10
        while l_max > args.tollerance and tp <= args.count:
            tp = time.process_time() - t0
            edges = self.compute_edges( verts, faces)
            #store for debugging purposes
            self.edges = edges
            self.faces = faces
            self.verts = verts
            
            
            l_max = max(edges[:,5])
            n = n + 1
            
            if args.method == 'simple':# or (n/10) - np.round(n/10,0) == 0:
                #subdivide old way. Every edge is split in two
                verts, faces = self.subdivide_mesh_simple( verts, faces)
            else:
                #Subdivide the new way, splitting only edges that are too long
                verts, faces, connect = self.subdivide_mesh_near_edges( verts, faces, edges, connect, np.max([args.tollerance,l_max*0.5]))
                
            #store connect for debugging porpous
            self.connect = connect
            
            #write message in console            
            logger.debug(f"subdivision done l = {np.round(l_max,1)}, nf = {n}, nv = {len(verts)}")
        
        #build mesh from verts and faces
        mesh = mn.meshFromFacesVerts(faces, verts)
        
        #save it for the plotting
        #storing vertices 
        np.save(self.verts_path, verts)
        
        #storing faces
        np.save(self.faces_path, faces)
        
        #storing object file
        save_mesh(mesh, self.obj_path)
    
    def Rotation(self,args):
        
        #check input:    
        if 'rotation' in dir(args):
            logger.warning('no rotation value in args')
            rotation = args.rotation/360 * np.pi
        else:
            rotation = 0
        
        if 'vector' in dir(args):
            vector   = args.vector
            rotation = 0
        else:
            logger.warning('no rotation vector in args')
            vector   = [0,0,1]
        
        #rotary shizzle
        r = R.from_rotvec(rotation * np.array(vector))
        
        #load from cash
        verts = np.load(os.path.join(self.dir_path, FINE_VERTS_FILE))
        faces = np.load(os.path.join(self.dir_path, FINE_FACES_FILE))
        
        #do the rotation
        verts = r.apply(verts)
        
        #build the mesh
        mesh  = mn.meshFromFacesVerts(faces, verts)
        
        #restore vertices
        np.save(self.verts_path, verts)
        
        #restore faces
        np.save(self.faces_path, faces)
        
        #restore object file
        save_mesh(mesh, self.obj_path)
        
DMU = [[6/2 , 0.2, 30],
       [16/2, 0.2, 30],
       [4/2 , 0.0, 30],
       [3/2 , 0.0, 30],
       [6/2 , 0.1, 30]]
        
if input('dostuff')=='y':
    #input_dir= './tests/part_asm.glb' #smart thing
    input_dir= './tests/part_asm_3.glb'
    #input_dir = './tests/TestModel.stl'
    initargs  = Namespace(input = input_dir, output = directory)
    mtol      = 0.5
    mmesh     = 1.0
    ma        = Mesh(initargs)
    
    # #create the mesh
    buildargs = Namespace(heal=False, offset=None, tollerance=mtol, tool_tollerance=mtol)
    ma.BuildMesh(buildargs)
        
    # # Build tool mesh
    ma.BuildToolMesh()
    
    # # subdevide
    subdevargs = Namespace(count=60, tollerance=mmesh, method = 'difficult')
    ma.subdivision(subdevargs)
    
    # #Calculate Tool
    Tool = DMU[1]
    toolargsMS = Namespace(rotation = 90, vector = [0,1,0], outer_radius = 5 ,tool_radius = 8, length = 30, inner_radius = 0, tollerance = mtol, intersect_tollerance = mtol*0.6)
    result = ma.MSTool(toolargsMS)
    
    # # plot the phone
    ma.Plot()
