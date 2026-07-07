from loguru import logger
import os
import json

import numpy as np
from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn

from utils import has_valid_extension, ensure_directory, ensure_parent_directories
from server import serve
from analysis import load_mesh, save_mesh, get_mesh_data, sample_unity_vector_pairs, compute_accessibility, find_combinations_matching_best, relax_accessibility, fix_undercuts, offset_mesh, double_offset, get_distance, get_inside_mesh, get_inside_indices, single_offset, translate, map_result_faces, generate_circle_translations


FINE_MESH_FILE = "fine_mesh.obj"
FINE_VERTS_FILE = "fine_verts.npy"
FINE_FACES_FILE = "fine_faces.npy"
DIRECTIONS_FILE = "directions.npy"
ACCESSIBILITY_FILE = "accessibility.npy"
HIGHLIGHT_FILE = "highlights.json"

if __name__ == "__main__":
    import argparse
    import sys
    
    from pathtypes import PathType

    # Constants
    NETWORK_CHANNEL = "can0"
    NETWORK_BUS = "socketcan"
    DRIVE_EDS = "./ASDA_A2_1042sub980_C.eds"

    # Setup parser
    parser = argparse.ArgumentParser(prog="CLI for testing meshlib analysis functions")
    subparsers = parser.add_subparsers(
        help="desired command to initiate", dest="command"
    )
    
    # Create the parser for the "mesh" command
    parser_mesh = subparsers.add_parser("mesh", help="mesh a file and derive the mesh")
    parser_mesh.add_argument("input", help="path of the input .stl file", type=PathType(type='file', dash_ok=True, exists=True))
    parser_mesh.add_argument("-o", "--output", help="path of the output dir", type=PathType(type='dir', dash_ok=True))
    parser_mesh.add_argument("--tollerance", help="voxel tollerance", type=float, default=1e-1)
    parser_mesh.add_argument("--heal", help="heal the mesh before storing", action="store_true")
    parser_mesh.add_argument("--offset", help="offset the mesh before storing", type=float, default=None)
    parser_mesh.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "directions" command
    parser_thickness = subparsers.add_parser("thickness", help="directions a file and derive the mesh")
    parser_thickness.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_thickness.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "directions" command
    parser_directions = subparsers.add_parser("directions", help="directions a file and derive the mesh")
    parser_directions.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_directions.add_argument("--count", help="number of directions determin", type=int, default=64)
    parser_directions.add_argument("--relax", help="relax the winning directions", action="store_true")
    parser_directions.add_argument("--relax_tollerance", help="angle tollerance of slides in degrees", type=float, default=1.0)
    parser_directions.add_argument("--relax_samples", help="the number of additional sampels used in relaxation", type=int, default=4)
    
    # Create the parser for the "options" command
    parser_options = subparsers.add_parser("options", help="find injection molding options")
    parser_options.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_options.add_argument("--slides", help="number of slides to consider", type=int, default=0)
    parser_options.add_argument("--slide_tollerance", help="angle tollerance of slides in degrees", type=float, default=2e-1)
    parser_options.add_argument("--count", help="number of results to continue with", type=int, default=10)
    parser_options.add_argument("--relax", help="relax the winning directions", action="store_true")
    parser_options.add_argument("--relax_tollerance", help="angle tollerance of slides in degrees", type=float, default=1.0)
    parser_options.add_argument("--relax_samples", help="the number of additional sampels used in relaxation", type=int, default=4)
    parser_options.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "options" command
    parser_serve = subparsers.add_parser("serve", help="find injection molding options")
    parser_serve.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_serve.add_argument("--include", help="direction indices to highlight", nargs="+", type=int, default=[])
    parser_serve.add_argument("--exclude", help="direction indices to exclude from highlight", nargs="+", type=int, default=[])
    parser_serve.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "tool" command
    parser_tool = subparsers.add_parser("tool", help="ballmill radius accessibility")
    parser_tool.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_tool.add_argument("direction", help="working directory", type=int, default=0)
    parser_tool.add_argument("--tollerance", help="voxel tollerance", type=float, default=1e-1)
    parser_tool.add_argument("--offset", help="offset the mesh before storing", type=float, default=None)
    parser_tool.add_argument("--radius", help="tool radius of ballmill or nose", type=float, default=1.0)
    parser_tool.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "length" command
    parser_length = subparsers.add_parser("length", help="ballmill length accessibility")
    parser_length.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_length.add_argument("direction", help="working directory", type=int, default=0)
    parser_length.add_argument("--tollerance", help="voxel tollerance", type=float, default=1e-1)
    parser_length.add_argument("--offset", help="offset the mesh before storing", type=float, default=None)
    parser_length.add_argument("--diameter", help="tool diameter of ballmill or nose", type=float, default=2.0)
    parser_length.add_argument("--length", help="tool length of ballmill or nose", type=float, default=3.0)
    parser_length.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # # Create the parser for the "endmill" command
    # parser_endmill = subparsers.add_parser("endmill", help="endmill radius accissibility")
    # parser_endmill.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    # parser_endmill.add_argument("direction", help="working directory", type=int, default=0)
    # parser_endmill.add_argument("--tollerance", help="voxel tollerance", type=float, default=1e-1)
    # parser_endmill.add_argument("--offset", help="offset the mesh before storing", type=float, default=None)
    # parser_endmill.add_argument("--diameter", help="endmill diameter of ballmill or nose", type=float, default=2.0)
    # parser_endmill.add_argument("--length", help="endmill length of ballmill or nose", type=float, default=3.0)
    # parser_endmill.add_argument("--samples", help="samples to use for circle approximation", type=int, default=18)
    # parser_endmill.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # parser_directions.add_argument("--serve", help="serve results in browser", action="store_true")

    # Parse the arguments
    args = parser.parse_args()

    # Prin the help function if needed
    if not args.command:
        parser.print_help()
        sys.exit()

    logger.debug(f"CLI command: {args.command}")

    if args.command == "mesh":
        logger.debug(f"Meshing file: {args.input}")
        
        # Check if the file has a valid extension
        has_valid_extension(args.input, [".stl"])
        
        # load the mesh
        mesh = load_mesh(args.input, heal=args.heal, offset=args.offset, tollerance=args.tollerance)
        verts, faces = get_mesh_data(mesh)
        
        dir_path = args.output
        
        # Save mesh to file
        if args.output:
            dir_path = args.output

        else:
            input_name = os.path.basename(args.input)
            input_name = input_name.rsplit(".", 1)[0]
            dir_path = os.path.join(os.path.abspath("."), input_name)
        
        # Define other files
        ensure_directory(dir_path)
        obj_path = os.path.join(dir_path, FINE_MESH_FILE)
        verts_path = os.path.join(dir_path, FINE_VERTS_FILE)
        faces_path = os.path.join(dir_path, FINE_FACES_FILE)
         
        logger.debug(f"Storing verts: {verts_path}")
        np.save(verts_path, verts)
        
        logger.debug(f"Storing faces: {faces_path}")
        np.save(faces_path, faces)
        
        logger.debug(f"Storing obj file: {obj_path}")
        save_mesh(mesh, obj_path)
        
        if args.serve:
            logger.info(f"Mesh served at: {obj_path}")
            index_path = os.path.abspath("./index.html")
            directory = os.path.dirname(obj_path)
            serve(index_path, dir_path, timeout=10.0)
            
    elif args.command == "thickness":
        logger.debug("Computing thickness")
        
        verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        mesh = mn.meshFromFacesVerts(faces, verts)
        
        settings = mm.InSphereSearchSettings()
        settings.insideAndOutside = False
        settings.maxRadius = 5.0
        settings.maxIters = 1000
        settings.minShrinkage = 1e-6
        distances = mm.computeInSphereThicknessAtVertices(mesh, settings)  
        
        mean_distance = np.mean(distances.vec)
        logger.warning(f"mean thickness inscribed sphere: {mean_distance}")
        
        # Save mesh to file
        thin_vertices = set()
        for i in range(distances.vec.size()):
            distance = distances.vec[i]
            
            if distance < 0.7 * mean_distance:
                thin_vertices.add(i)
                
        thin_faces = []    
        for i in range(len(faces)):
            face = faces[i]
            
            if face[0] in thin_vertices and face[1] in thin_vertices and face[2] in thin_vertices:
                thin_faces.append(i)
                

        # Save mesh to file
        thick_vertices = set()
        for i in range(distances.vec.size()):
            distance = distances.vec[i]
            
            if distance > 1.3 * mean_distance:
                thick_vertices.add(i)
                
        thick_faces = []    
        for i in range(len(faces)):
            face = faces[i]
            
            if face[0] in thick_vertices and face[1] in thick_vertices and face[2] in thick_vertices:
                thick_faces.append(i)
            
                
                
        numpyData = {"faces": thick_faces}
        highlight_path = os.path.join(args.directory, HIGHLIGHT_FILE)
        with open(highlight_path, "w") as f:
            json.dump(numpyData, f)
        
        # Storage paths
        directory = os.path.abspath(args.directory)
        obj_path = os.path.join(directory, FINE_MESH_FILE)
        save_mesh(mesh, obj_path)
        if args.serve:
            logger.info(f"Mesh served at: {obj_path}")
            index_path = os.path.abspath("./index.html")
            directory = os.path.dirname(obj_path)
            serve(index_path, directory, timeout=15.0)
        
    elif args.command == "directions":
        logger.debug(f"Computing {args.count} directions")
        directions = sample_unity_vector_pairs(args.count)
        
        logger.debug("Cheking accessibility per direction")
        verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        mesh = mn.meshFromFacesVerts(faces, verts)
        
        face_count = len(faces)
        accessibility = compute_accessibility(mesh, directions, face_count)
        
        if args.relax:
            for direction_index in range(directions.shape[0]):
                relaxed_accessibility = relax_accessibility(mesh, accessibility[direction_index,:], directions[direction_index], tolerance_degrees=args.relax_tollerance, n=args.relax_samples)
                accessibility[direction_index,:] = relaxed_accessibility
        
        directions_path = os.path.join(args.directory, DIRECTIONS_FILE)
        accessibility_path = os.path.join(args.directory, ACCESSIBILITY_FILE)
        
        logger.debug(f"Storing directions at: {directions_path}")
        np.save(directions_path, directions)
        
        logger.debug(f"Storing accessibility at: {accessibility_path}")
        np.save(accessibility_path, accessibility)
        
        
    elif args.command == "options":
        logger.debug(f"Computing preferred options with {args.slides} slides")

        verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        mesh = mn.meshFromFacesVerts(faces, verts)
        
        directions = np.load(os.path.join(args.directory, DIRECTIONS_FILE))
        accessibility = np.load(os.path.join(args.directory, ACCESSIBILITY_FILE))
        
        matching_combinations = find_combinations_matching_best(directions, accessibility, max_slides=args.slides, max_results=args.count, tolerance_degrees=args.slide_tollerance)
        
        if args.relax:
           
            # Compute the unique open closed directions
            unique_directions = set()
            for option, performance in matching_combinations[:args.count]:
                unique_directions.update(option)

            for direction_index in unique_directions:
                relaxed_accessibility = relax_accessibility(mesh, accessibility[direction_index,:], directions[direction_index], tolerance_degrees=args.relax_tollerance, n=args.relax_samples)
                accessibility[direction_index,:] = relaxed_accessibility
                
            np.save(os.path.join(args.directory, ACCESSIBILITY_FILE), accessibility)
            
    elif args.command == "serve":
        logger.info("Serving results in browser")
        
        directions = np.load(os.path.join(args.directory, DIRECTIONS_FILE))
        accessibility = np.load(os.path.join(args.directory, ACCESSIBILITY_FILE))

        
        if args.include:
            union = np.any(accessibility[args.include, :], axis=0)
            
        elif args.exclude:
            union = np.any(accessibility[args.exclude, :], axis=0)  
            union = np.invert(union)
            # union = np.logical_not(union)

        # Export inidces as an example file to disk
        indices = np.where(union)[0]
        indices = indices.tolist()
        
        logger.debug(f"Highlighting {len(indices)} faces")

        numpyData = {"faces": indices}
        highlight_path = os.path.join(args.directory, HIGHLIGHT_FILE)
        with open(highlight_path, "w") as f:
            json.dump(numpyData, f)
            
        index_path = os.path.abspath("./index.html")
        directory = os.path.abspath(args.directory)
        serve(index_path, directory, timeout=15.0)
        
        
    elif args.command == "tool":
        logger.info("Perform a tool analyis on the mesh")
        
        verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        mesh = mn.meshFromFacesVerts(faces, verts)
        
        logger.debug(f"Mesh loaded with {len(faces)} faces")
        
        directions = np.load(os.path.join(args.directory, DIRECTIONS_FILE))
        accessibility = np.load(os.path.join(args.directory, ACCESSIBILITY_FILE))
        
        undercut_mesh = fix_undercuts(mesh, directions[args.direction][0], directions[args.direction][1], directions[args.direction][2])
        
        directory = os.path.abspath(args.directory)
        obj_path = os.path.join(directory, FINE_MESH_FILE)
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
        radius_faces = map_result_faces(mesh, radius_mesh, faces, min_range=args.tollerance)
        
        # Keep only the faces that are accessible
        radius_faces = radius_faces[accessibility[args.direction, radius_faces]]
        radius_faces = radius_faces.tolist()
        
        
        
        
        # # Keep only the faces that are accessible
        # radius_faces = radius_faces[accessibility[args.direction, radius_faces]]
        # radius_faces = radius_faces.tolist()

        numpyData = {"faces": radius_faces}
        highlight_path = os.path.join(args.directory, HIGHLIGHT_FILE)
        with open(highlight_path, "w") as f:
            json.dump(numpyData, f)
            
    
        save_mesh(mesh, obj_path)
        if args.serve:
            logger.info(f"Mesh served at: {obj_path}")
            index_path = os.path.abspath("./index.html")
            directory = os.path.dirname(obj_path)
            serve(index_path, directory, timeout=15.0)
            
            
            
    elif args.command == "length":
        logger.info("Perform a tool length analyis on the mesh")
        
        # Load mesh
        verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        mesh = mn.meshFromFacesVerts(faces, verts)
        
        # Load cached accessibility
        directions = np.load(os.path.join(args.directory, DIRECTIONS_FILE))
        accessibility = np.load(os.path.join(args.directory, ACCESSIBILITY_FILE))
        
        # Perform a undercut free mesh
        undercut_mesh = fix_undercuts(mesh, directions[args.direction][0], directions[args.direction][1], directions[args.direction][2])
        
        # Offset using half of the tool diameter
        radius_mesh = single_offset(undercut_mesh, args.diameter / 2.0, args.tollerance, decimate=False)
        
        # Translate mesh
        distance = args.diameter / -2.0 - args.length
        translated_mesh = translate(radius_mesh, directions[args.direction][0], directions[args.direction][1], directions[args.direction][2], distance=distance)
        
        # Map the results
        inside_mesh = get_inside_mesh(mesh, translated_mesh)
        inside_faces = map_result_faces(mesh, inside_mesh, faces, max_range=args.tollerance)
        
        # Keep only the faces that are accessible
        inside_faces = inside_faces[accessibility[args.direction, inside_faces]]
        inside_faces = inside_faces.tolist()
        
        
        numpyData = {"faces": inside_faces}
        highlight_path = os.path.join(args.directory, HIGHLIGHT_FILE)
        with open(highlight_path, "w") as f:
            json.dump(numpyData, f)
        
        # Storage paths
        directory = os.path.abspath(args.directory)
        obj_path = os.path.join(directory, FINE_MESH_FILE)
        save_mesh(mesh, obj_path)
        if args.serve:
            logger.info(f"Mesh served at: {obj_path}")
            index_path = os.path.abspath("./index.html")
            directory = os.path.dirname(obj_path)
            serve(index_path, directory, timeout=15.0)
        
    # elif args.command == "endmill":
    #     logger.info("Perform a tool length analyis for a endmill on the mesh")
        
    #     # Load mesh
    #     verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
    #     faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
    #     mesh = mn.meshFromFacesVerts(faces, verts)
        
    #     # Load cached accessibility
    #     directions = np.load(os.path.join(args.directory, DIRECTIONS_FILE))
    #     accessibility = np.load(os.path.join(args.directory, ACCESSIBILITY_FILE))
        
    #     # Perform a undercut free mesh
    #     undercut_mesh = fix_undercuts(mesh, directions[args.direction][0], directions[args.direction][1], directions[args.direction][2])
        
    #     # Offset using half of the tool diameter
    #     # radius_mesh = single_offset(undercut_mesh, args.diameter / 2.0, args.tollerance, decimate=False)
        
    #     # Translate mesh
    #     # distance = args.diameter / -2.0 - args.length
    #     # translated_mesh = translate(radius_mesh, directions[args.direction][0], directions[args.direction][1], directions[args.direction][2], distance=distance)
        
    #     distance = -args.length
    #     translated_mesh = translate(undercut_mesh, directions[args.direction][0], directions[args.direction][1], directions[args.direction][2], distance=distance)
        
    #     # Compute vectors
    #     union_faces = set()
    #     translations = generate_circle_translations(directions[args.direction][0], directions[args.direction][1], directions[args.direction][2], args.diameter / 2.0, args.samples)
    #     for i in range(args.samples):
    #         logger.info(f"Translation: {i}/{args.samples}")
    #         translated_mesh = translate(translated_mesh, translations[i][0], translations[i][1], translations[i][2])
        
    #         # Map the results
    #         inside_mesh = get_inside_mesh(mesh, translated_mesh)
    #         inside_faces = map_result_faces(mesh, inside_mesh, faces, max_range=args.tollerance)
        
    #         # Keep only the faces that are accessible
    #         inside_faces = inside_faces[accessibility[args.direction, inside_faces]]
    #         inside_faces = inside_faces.tolist()
            
    #         union_faces.update(inside_faces)
            
    #     union_faces = list(union_faces)
        
    #     numpyData = {"faces": union_faces}
    #     highlight_path = os.path.join(args.directory, HIGHLIGHT_FILE)
    #     with open(highlight_path, "w") as f:
    #         json.dump(numpyData, f)
        
    #     # Storage paths
    #     directory = os.path.abspath(args.directory)
    #     obj_path = os.path.join(directory, FINE_MESH_FILE)
    #     save_mesh(mesh, obj_path)
    #     if args.serve:
    #         logger.info(f"Mesh served at: {obj_path}")
    #         index_path = os.path.abspath("./index.html")
    #         directory = os.path.dirname(obj_path)
    #         serve(index_path, directory, timeout=15.0)
    
    
    elif args.command == "endmill":
        logger.info("Perform a tool length analyis for a endmill on the mesh")
        
        # Load mesh
        verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        mesh = mn.meshFromFacesVerts(faces, verts)
        
        # Load cached accessibility
        directions = np.load(os.path.join(args.directory, DIRECTIONS_FILE))
        accessibility = np.load(os.path.join(args.directory, ACCESSIBILITY_FILE))
        
        # Perform a undercut free mesh
        undercut_mesh = fix_undercuts(mesh, directions[args.direction][0], directions[args.direction][1], directions[args.direction][2])
        
        # Map the results
        radius_mesh = double_offset(undercut_mesh, args.diameter / 2.0, -args.diameter / 2.0, args.tollerance, decimate=False)
        # radius_mesh = single_offset(radius_mesh, args.diameter / 2.0, args.tollerance, decimate=False)
        radius_faces = map_result_faces(mesh, radius_mesh, faces, min_range=args.tollerance)
        
        # Keep only the faces that are accessible
        radius_faces = radius_faces[accessibility[args.direction, radius_faces]]
        radius_faces = radius_faces.tolist()
        
        # Translate mesh
        # distance = args.diameter / -2.0
        # translated_mesh = translate(radius_mesh, directions[args.direction][0], directions[args.direction][1], directions[args.direction][2], distance=distance)
        
        # Map the results
        # inside_mesh = get_inside_mesh(mesh, translated_mesh)
        # inside_faces = map_result_faces(mesh, inside_mesh, faces, max_range=args.tollerance)
        
        # Keep only the faces that are accessible
        # inside_faces = inside_faces[accessibility[args.direction, inside_faces]]
        # inside_faces = inside_faces.tolist()
        
        # distance = -args.length
        # translated_mesh = translate(undercut_mesh, directions[args.direction][0], directions[args.direction][1], directions[args.direction][2], distance=distance)
        
        # Compute vectors
        # union_faces = set()
        # translations = generate_circle_translations(directions[args.direction][0], directions[args.direction][1], directions[args.direction][2], args.diameter / 2.0, args.samples)
        # for i in range(args.samples):
        #     logger.info(f"Translation: {i}/{args.samples}")
        #     translated_mesh = translate(translated_mesh, translations[i][0], translations[i][1], translations[i][2])
        
        #     # Map the results
        #     inside_mesh = get_inside_mesh(mesh, translated_mesh)
        #     inside_faces = map_result_faces(mesh, inside_mesh, faces, max_range=args.tollerance)
        
        #     # Keep only the faces that are accessible
        #     inside_faces = inside_faces[accessibility[args.direction, inside_faces]]
        #     inside_faces = inside_faces.tolist()
            
        #     union_faces.update(inside_faces)
            
        # union_faces = list(union_faces)

        numpyData = {"faces": radius_faces}
        highlight_path = os.path.join(args.directory, HIGHLIGHT_FILE)
        with open(highlight_path, "w") as f:
            json.dump(numpyData, f)
        
        # Storage paths
        directory = os.path.abspath(args.directory)
        obj_path = os.path.join(directory, FINE_MESH_FILE)
        save_mesh(mesh, obj_path)
        if args.serve:
            logger.info(f"Mesh served at: {obj_path}")
            index_path = os.path.abspath("./index.html")
            directory = os.path.dirname(obj_path)
            serve(index_path, directory, timeout=15.0)