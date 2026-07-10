from loguru import logger
import os
import json

import numpy as np
from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn

from analysis import save_mesh, fix_undercuts, double_offset, get_inside_mesh, single_offset, translate, map_result_faces, endmill_closing, endmill_flag_threshold
from pipeline import (
    FINE_MESH_FILE, FINE_VERTS_FILE, FINE_FACES_FILE,
    DIRECTIONS_FILE, ACCESSIBILITY_FILE, HIGHLIGHT_FILE,
    mesh_part, compute_directions, highlight_union,
    precompute_fields, compose_tool, parse_tips, parse_holder, write_highlights,
)


def serve_workdir(directory, timeout=600.0, port=8080, open_browser=True):
    """Open the interactive viewer preloaded on one working directory."""
    from api.app import serve_app
    workdir = os.path.abspath(directory)
    serve_app(root=os.path.dirname(workdir) or ".", preload=os.path.basename(workdir),
              port=port, open_browser=open_browser, timeout=timeout)

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
    parser_mesh.add_argument("--deflection", help="BREP tessellation deflection for STEP input (mm)", type=float, default=0.5)
    parser_mesh.add_argument("--heal", help="heal the mesh before storing (voxel remesh - for dirty STLs, NOT for clean STEP)", action="store_true")
    parser_mesh.add_argument("--subdivide", help="max edge length: refine without changing the shape (use for clean STEP input)", type=float, default=None)
    parser_mesh.add_argument("--offset", help="offset the mesh before storing", type=float, default=None)
    parser_mesh.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "directions" command
    parser_thickness = subparsers.add_parser("thickness", help="rolling sphere wall thickness (and optionally gaps) fields")
    parser_thickness.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_thickness.add_argument("--min", help="flag faces with all vertices thinner than this (mm)", type=float, default=1.0)
    parser_thickness.add_argument("--max_radius", help="inscribed sphere radius cap (default: auto from bounding box)", type=float, default=None)
    parser_thickness.add_argument("--both", help="also compute the gaps/clearance field on the inverted shape", action="store_true")
    parser_thickness.add_argument("--min_gap", help="with --both: also flag faces with wall-to-wall clearance below this (mm)", type=float, default=0.5)
    parser_thickness.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "directions" command
    parser_directions = subparsers.add_parser("directions", help="directions a file and derive the mesh")
    parser_directions.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_directions.add_argument("--count", help="number of directions determin", type=int, default=64)
    parser_directions.add_argument("--axes", help="prepend the six principal +/-X/Y/Z directions", action="store_true")
    parser_directions.add_argument("--tollerance", help="angular relaxation of the visibility test in degrees (near-vertical walls within it count as facing)", type=float, default=0.1)
    parser_directions.add_argument("--pixel", help="visibility height map pixel size (default: auto from bounding box)", type=float, default=None)
    parser_directions.add_argument("--relax", help="relax the winning directions", action="store_true")
    parser_directions.add_argument("--relax_tollerance", help="angle tollerance of slides in degrees", type=float, default=1.0)
    parser_directions.add_argument("--relax_samples", help="the number of additional sampels used in relaxation", type=int, default=4)
    
    # Create the parser for the "options" command
    parser_options = subparsers.add_parser("options", help="rank mold orientations (plate pair + slides)")
    parser_options.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_options.add_argument("--max_slides", help="maximum number of slides per orientation", type=int, default=2)
    parser_options.add_argument("--slide_tollerance", help="slide perpendicularity tolerance in degrees", type=float, default=2.0)
    parser_options.add_argument("--count", help="ranked options to report", type=int, default=10)
    parser_options.add_argument("--min_slide_faces", help="minimum faces a slide must gain", type=int, default=50)
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
    parser_view.add_argument("target", help="working directory or STEP/STL file to open")
    parser_view.add_argument("--timeout", help="seconds to keep the server alive (default: until Ctrl-C)", type=float, default=None)
    parser_view.add_argument("--port", help="port to serve on", type=int, default=8080)
    parser_view.add_argument("--no-browser", help="do not open a browser window", action="store_true")

    # Create the parser for the "endmill" command
    parser_endmill = subparsers.add_parser("endmill", help="generic endmill tip accessibility (ball, flat or radius end)")
    parser_endmill.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_endmill.add_argument("direction", help="index of the approach direction", type=int, default=0)
    parser_endmill.add_argument("--tollerance", help="voxel tollerance", type=float, default=1e-1)
    parser_endmill.add_argument("--diameter", help="endmill diameter", type=float, default=2.0)
    parser_endmill.add_argument("--corner_radius", help="tip corner radius: 0 = flat endmill, diameter/2 = ball nose, in between = radius endmill", type=float, default=0.0)
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

        result = mesh_part(args.input, args.output, heal=args.heal,
                           subdivide=args.subdivide, offset=args.offset,
                           tollerance=args.tollerance, deflection=args.deflection)

        if args.serve:
            serve_workdir(result["workdir"])
            
    elif args.command == "thickness":
        logger.info("Computing rolling sphere thickness field")

        import processes
        from processes.base import apply_defaults, load_result_arrays

        params = {}
        if args.max_radius is not None:
            params["max_radius"] = args.max_radius

        analysis = processes.get_analysis("injection_molding", "thickness")
        merged = apply_defaults(analysis, params)
        result = analysis.run(args.directory, merged, None)
        logger.info(f"thickness stats: {result.stats}")

        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        thickness = load_result_arrays(args.directory, "injection_molding",
                                       "thickness", merged)["thickness"]
        flagged = np.all(thickness[faces] < args.min, axis=1)

        if args.both:
            analysis = processes.get_analysis("injection_molding", "gaps")
            merged = apply_defaults(analysis, params)
            result = analysis.run(args.directory, merged, None)
            logger.info(f"gaps stats: {result.stats}")

            gap = load_result_arrays(args.directory, "injection_molding",
                                     "gaps", merged)["gap"]
            flagged |= np.all(gap[faces] < args.min_gap, axis=1)

        indices = np.flatnonzero(flagged).tolist()
        logger.info(f"Flagging {len(indices)} faces below the thresholds")
        write_highlights(args.directory, indices)

        if args.serve:
            serve_workdir(args.directory)

    elif args.command == "directions":
        compute_directions(args.directory, count=args.count, axes=args.axes,
                           tollerance=args.tollerance, pixel=args.pixel,
                           relax=args.relax, relax_tollerance=args.relax_tollerance,
                           relax_samples=args.relax_samples)

    elif args.command == "options":
        logger.info("Searching mold orientations")

        import processes
        from processes.base import apply_defaults

        analysis = processes.get_analysis("injection_molding", "mold_orientation")
        merged = apply_defaults(analysis, {
            "max_slides": args.max_slides,
            "slide_tollerance": args.slide_tollerance,
            "count": args.count,
            "min_slide_faces": args.min_slide_faces,
        })
        result = analysis.run(args.directory, merged, None)

        for rank, option in enumerate(result.stats["options"]):
            slides = ", ".join(f"d{s['direction']} (+{s['marginal']})"
                               for s in option["slides"]) or "none"
            logger.info(
                f"#{rank}  pair {tuple(option['pair'])}  "
                f"{'FEASIBLE' if option['feasible'] else 'infeasible'}  "
                f"coverage {option['coverage'] * 100:.1f}%  slides: {slides}  "
                f"internal {option['counts']['internal']}")

        if args.serve:
            serve_workdir(args.directory)

    elif args.command == "serve":
        logger.info("Serving results in browser")

        highlight_union(args.directory, include=args.include, exclude=args.exclude)

        serve_workdir(args.directory)
        
        
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
            serve_workdir(args.directory)
            
            
            
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
            serve_workdir(args.directory)
        
    elif args.command == "view":
        logger.info("Serving the interactive viewer")

        from api.app import serve_app
        target = os.path.abspath(args.target)

        if os.path.isdir(target):
            root = os.path.dirname(target) or "."
            preload = os.path.basename(target)
        elif os.path.isfile(target):
            # register the file as a part in the current directory's parts
            # root so the UI opens on it; processing then runs from the UI
            from api.parts import register_part_file
            root = os.path.abspath(".")
            part = register_part_file(root, target)
            preload = part["id"]
        else:
            logger.error(f"no such file or directory: {args.target}")
            sys.exit(1)

        serve_app(root=root, preload=preload, port=args.port,
                  open_browser=not args.no_browser, timeout=args.timeout)

    elif args.command == "precompute":
        logger.info("Precompute height maps and tool fields")
        precompute_fields(args.directory, directions=args.directions,
                          pixel=args.pixel, tips=parse_tips(args.tips),
                          clearances=args.clearances, engine=args.engine,
                          window=args.window)

    elif args.command == "compose":
        logger.info("Compose tool accessibility from precomputed fields")
        compose_tool(args.directory, args.direction, pixel=args.pixel,
                     tollerance=args.tollerance, diameter=args.diameter,
                     corner_radius=args.corner_radius, stickout=args.stickout,
                     cylinders=parse_holder(args.holder), sweep=args.sweep,
                     engine=args.engine, window=args.window)

        if args.serve:
            serve_workdir(args.directory)

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

        numpyData = {"faces": unreachable_faces}
        highlight_path = os.path.join(args.directory, HIGHLIGHT_FILE)
        with open(highlight_path, "w") as f:
            json.dump(numpyData, f)

        # Storage paths
        directory = os.path.abspath(args.directory)
        obj_path = os.path.join(directory, FINE_MESH_FILE)
        save_mesh(mesh, obj_path)
        if args.serve:
            serve_workdir(args.directory)
