from loguru import logger
import os
import json

import numpy as np
from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn

from utils import has_valid_extension, ensure_directory, ensure_parent_directories
from server import serve
from analysis import load_mesh, save_mesh, get_mesh_data, sample_unity_vector_pairs, compute_accessibility, find_combinations_matching_best, relax_accessibility, fix_undercuts, offset_mesh, double_offset, get_distance, get_inside_mesh, get_inside_indices, single_offset, translate, map_result_faces, generate_circle_translations, endmill_closing, endmill_flag_threshold, endmill_depth_obstacle


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
    parser_mesh.add_argument("input", help="path of the input .stl/.step file", type=PathType(type='file', dash_ok=True, exists=True))
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
    parser_directions.add_argument("--axes", help="prepend the six principal +/-X/Y/Z directions", action="store_true")
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
    
    # Create the parser for the "precompute" command
    parser_precompute = subparsers.add_parser("precompute", help="cache height maps and per-radius tool fields for fast composition")
    parser_precompute.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_precompute.add_argument("--directions", help="indices of approach directions to precompute", nargs="+", type=int, required=True)
    parser_precompute.add_argument("--pixel", help="height map pixel size", type=float, default=1e-1)
    parser_precompute.add_argument("--tips", help="tool tips as diameter:corner_radius (0 = flat, D/2 = ball)", nargs="*", type=str, default=[])
    parser_precompute.add_argument("--clearances", help="cylinder radii for holder/shank clearance fields", nargs="*", type=float, default=[])
    parser_precompute.add_argument("--engine", help="field computation engine", choices=["zmap", "voxel"], default="zmap")
    parser_precompute.add_argument("--window", help="gap accuracy window: gaps up to this are Euclidean-exact (zmap engine)", type=float, default=0.3)

    # Create the parser for the "compose" command
    parser_compose = subparsers.add_parser("compose", help="evaluate a full tool assembly from precomputed fields")
    parser_compose.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_compose.add_argument("direction", help="index of the approach direction", type=int)
    parser_compose.add_argument("--pixel", help="height map pixel size", type=float, default=1e-1)
    parser_compose.add_argument("--tollerance", help="gap threshold to flag a vertex", type=float, default=1e-1)
    parser_compose.add_argument("--diameter", help="tool diameter", type=float, default=2.0)
    parser_compose.add_argument("--corner_radius", help="tip corner radius: 0 = flat endmill, diameter/2 = ball nose", type=float, default=0.0)
    parser_compose.add_argument("--stickout", help="tool length out of the holder", type=float, default=None)
    parser_compose.add_argument("--holder", help="holder as stacked cylinders radius:start,radius:start,... (start measured from the tool tip at stickout 0)", type=str, default=None)
    parser_compose.add_argument("--sweep", help="additional stickout values to report coverage for", nargs="*", type=float, default=[])
    parser_compose.add_argument("--engine", help="field computation engine", choices=["zmap", "voxel"], default="zmap")
    parser_compose.add_argument("--window", help="gap accuracy window: gaps up to this are Euclidean-exact (zmap engine)", type=float, default=0.3)
    parser_compose.add_argument("--serve", help="serve results in browser", action="store_true")

    # Create the parser for the "view" command
    parser_view = subparsers.add_parser("view", help="interactive viewer over all cached analysis fields")
    parser_view.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_view.add_argument("--timeout", help="seconds to keep the server alive", type=float, default=600.0)
    parser_view.add_argument("--port", help="port to serve on", type=int, default=8080)

    # Create the parser for the "endmill" command
    parser_endmill = subparsers.add_parser("endmill", help="generic endmill tip accessibility (ball, flat or radius end)")
    parser_endmill.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_endmill.add_argument("direction", help="index of the approach direction", type=int, default=0)
    parser_endmill.add_argument("--tollerance", help="voxel tollerance", type=float, default=1e-1)
    parser_endmill.add_argument("--diameter", help="endmill diameter", type=float, default=2.0)
    parser_endmill.add_argument("--corner_radius", help="tip corner radius: 0 = flat endmill, diameter/2 = ball nose, in between = radius endmill", type=float, default=0.0)
    parser_endmill.add_argument("--length", help="usable tool length from tip to holder, omit to skip the depth check", type=float, default=None)
    parser_endmill.add_argument("--holder_diameter", help="holder diameter above the usable length, defaults to the tool diameter", type=float, default=None)
    parser_endmill.add_argument("--scale", help="anisotropy stretch factor used to emulate the in-plane offset", type=float, default=10.0)
    parser_endmill.add_argument("--serve", help="serve results in browser", action="store_true")

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
        has_valid_extension(args.input, [".stl", ".stp", ".step"])
        
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

        if args.axes:
            # principal axes as antipodal pairs, matching the pair layout
            axes = np.array([
                [1.0, 0.0, 0.0], [-1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0], [0.0, -1.0, 0.0],
                [0.0, 0.0, 1.0], [0.0, 0.0, -1.0],
            ])
            directions = np.vstack([axes, directions])
        
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
        
    elif args.command == "view":
        logger.info("Exporting cached fields and serving the interactive viewer")

        from viewer import export_viewer_bundle
        export_viewer_bundle(args.directory)

        index_path = os.path.abspath("./viewer.html")
        directory = os.path.abspath(args.directory)
        serve(index_path, directory, port=args.port, timeout=args.timeout)

    elif args.command == "precompute":
        logger.info("Precompute height maps and tool fields")
        from zmap import DirectionCache

        verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))

        tips = []
        for spec in args.tips:
            diameter, _, corner = spec.partition(":")
            tips.append((float(diameter), float(corner or 0.0)))

        for direction_index in args.directions:
            logger.info(f"Direction {direction_index}")
            cache = DirectionCache(args.directory, direction_index, verts=verts, faces=faces, pixel=args.pixel, window=args.window, engine=args.engine)
            for diameter, corner_radius in tips:
                cache.tip_gap(diameter, corner_radius)
            for radius in args.clearances:
                cache.clearance(radius)

    elif args.command == "compose":
        logger.info("Compose tool accessibility from precomputed fields")
        from zmap import DirectionCache, compose_unreachable

        verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        accessibility = np.load(os.path.join(args.directory, ACCESSIBILITY_FILE))

        cylinders = None
        if args.holder:
            cylinders = []
            for spec in args.holder.split(","):
                radius, _, start = spec.partition(":")
                cylinders.append((float(radius), float(start or 0.0)))

        cache = DirectionCache(args.directory, args.direction, verts=verts, faces=faces, pixel=args.pixel, window=args.window, engine=args.engine)
        unreachable_faces, gap, min_stick = compose_unreachable(
            cache, faces, args.diameter, args.corner_radius, args.tollerance,
            stickout=args.stickout, cylinders=cylinders,
        )

        # Keep only the faces that are accessible
        unreachable_faces = unreachable_faces[accessibility[args.direction, unreachable_faces]]

        accessible_count = int(accessibility[args.direction].sum())
        logger.info(f"Tool D={args.diameter} rc={args.corner_radius} stickout={args.stickout} cannot reach {len(unreachable_faces)} of {accessible_count} accessible faces")

        # A stickout sweep is free: threshold the cached per-vertex field
        if args.sweep and min_stick is not None:
            for stickout in args.sweep:
                blocked = (gap > args.tollerance) | (min_stick > stickout + args.tollerance)
                swept = np.where(blocked[faces].all(axis=1))[0]
                swept = swept[accessibility[args.direction, swept]]
                logger.info(f"  stickout {stickout:8.2f}: {len(swept)} unreachable faces")

        numpyData = {"faces": unreachable_faces.tolist()}
        highlight_path = os.path.join(args.directory, HIGHLIGHT_FILE)
        with open(highlight_path, "w") as f:
            json.dump(numpyData, f)

        directory = os.path.abspath(args.directory)
        obj_path = os.path.join(directory, FINE_MESH_FILE)
        if args.serve:
            logger.info(f"Mesh served at: {obj_path}")
            index_path = os.path.abspath("./index.html")
            serve(index_path, directory, timeout=15.0)

    elif args.command == "endmill":
        logger.info("Perform an endmill tip analysis on the mesh")

        # Load mesh
        verts = np.load(os.path.join(args.directory, FINE_VERTS_FILE))
        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        mesh = mn.meshFromFacesVerts(faces, verts)

        # Load cached accessibility
        directions = np.load(os.path.join(args.directory, DIRECTIONS_FILE))
        accessibility = np.load(os.path.join(args.directory, ACCESSIBILITY_FILE))

        # Perform a undercut free mesh
        direction = directions[args.direction]
        undercut_mesh = fix_undercuts(mesh, direction[0], direction[1], direction[2])

        # Close the mesh with the tool bottom shape: a disk of radius
        # (D/2 - rc) perpendicular to the approach direction, Minkowski
        # summed with a sphere of radius rc
        closed_mesh = endmill_closing(undercut_mesh, direction, args.diameter, args.corner_radius, args.tollerance, scale=args.scale)

        # Deviations below the disk emulation residual cannot be trusted
        threshold = endmill_flag_threshold(args.diameter, args.corner_radius, args.tollerance, args.scale)
        if threshold > args.tollerance:
            logger.warning(f"Flag threshold raised to {threshold:.3f} by the in-plane offset residual, increase --scale for finer sensitivity")

        # Map the results
        unreachable_faces = map_result_faces(mesh, closed_mesh, faces, min_range=threshold)

        # Keep only the faces that are accessible
        unreachable_faces = unreachable_faces[accessibility[args.direction, unreachable_faces]]
        unreachable_faces = unreachable_faces.tolist()

        logger.info(f"Endmill D={args.diameter} rc={args.corner_radius} cannot reach {len(unreachable_faces)} faces from direction {args.direction}")

        # Optional depth check: the holder bottom disk at --length above the
        # tip contact point must clear the part
        depth_faces = []
        if args.length is not None:
            holder_diameter = args.holder_diameter if args.holder_diameter is not None else args.diameter
            obstacle = endmill_depth_obstacle(undercut_mesh, direction, holder_diameter, args.length, args.tollerance, scale=args.scale)

            # Map the results
            inside_mesh = get_inside_mesh(mesh, obstacle)
            depth_faces = map_result_faces(mesh, inside_mesh, faces, max_range=args.tollerance)

            # Keep only the faces that are accessible
            depth_faces = depth_faces[accessibility[args.direction, depth_faces]]
            depth_faces = depth_faces.tolist()

            logger.info(f"Holder D={holder_diameter} at length {args.length} blocks {len(depth_faces)} faces from direction {args.direction}")

        # Tip violations drawn last so red wins where both apply
        depth_only_faces = sorted(set(depth_faces) - set(unreachable_faces))
        numpyData = {
            "faces": sorted(set(unreachable_faces) | set(depth_faces)),
            "groups": [
                {"color": "#ff9900", "faces": depth_only_faces},
                {"color": "#ff2222", "faces": unreachable_faces},
            ],
        }
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
