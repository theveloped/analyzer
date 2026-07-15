from loguru import logger
import os

import numpy as np

from pipeline import (
    FINE_FACES_FILE,
    mesh_part, compute_directions, highlight_union, mesh_fingerprint,
    precompute_fields, compose_tool, parse_tips, parse_holder, write_highlights,
    edge_excluded,
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
    parser_mesh.add_argument("--resolution", help="analysis resolution in mm - drives deflection, subdivide, heal voxel and downstream pixel defaults (default: auto from part size)", type=float, default=None)
    parser_mesh.add_argument("--deflection", help="override: BREP tessellation deflection for STEP input in mm (default: resolution/8)", type=float, default=None)
    parser_mesh.add_argument("--heal", help="heal the mesh before storing (voxel remesh at resolution/5 - for dirty STLs, NOT for clean STEP)", action="store_true")
    parser_mesh.add_argument("--subdivide", help="override: max edge length, refines without changing the shape (default: resolution, 0 disables)", type=float, default=None)
    parser_mesh.add_argument("--obj", help="also export fine_mesh.obj for external tools (nothing in the pipeline reads it)", action="store_true")
    parser_mesh.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "directions" command
    parser_thickness = subparsers.add_parser("thickness", help="rolling sphere wall thickness (and optionally gaps) fields")
    parser_thickness.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_thickness.add_argument("--min", help="flag faces with all vertices thinner than this (mm)", type=float, default=1.0)
    parser_thickness.add_argument("--max_radius", help="inscribed sphere radius cap (default: auto from bounding box)", type=float, default=None)
    parser_thickness.add_argument("--sharp_deg", help="dihedral angle above which an edge is sharp; readings explainable by such edges are excluded from the thin flags (0 = no exclusions, default: 25)", type=float, default=None)
    parser_thickness.add_argument("--contact_angles", help="also store each sphere's contact separation angle (wall ~180 deg, N-degree corner ~N, edge ~0) as a plottable field", action="store_true")
    parser_thickness.add_argument("--both", help="also compute the gaps/clearance field on the inverted shape", action="store_true")
    parser_thickness.add_argument("--min_gap", help="with --both: also flag faces with wall-to-wall clearance below this (mm)", type=float, default=0.5)
    parser_thickness.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "directions" command
    parser_directions = subparsers.add_parser("directions", help="directions a file and derive the mesh")
    parser_directions.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_directions.add_argument("--count", help="number of directions determin", type=int, default=64)
    parser_directions.add_argument("--axes", help="prepend the six principal +/-X/Y/Z directions", action="store_true")
    parser_directions.add_argument("--tollerance", help="angular relaxation of the visibility test in degrees (near-vertical walls within it count as facing)", type=float, default=0.1)
    parser_directions.add_argument("--pixel", help="visibility height map pixel size (default: resolution/5)", type=float, default=None)
    
    # Create the parser for the "options" command
    parser_options = subparsers.add_parser("options", help="rank mold orientations (plate pair + slides)")
    parser_options.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_options.add_argument("--max_slides", help="maximum number of slides per orientation", type=int, default=2)
    parser_options.add_argument("--slide_tollerance", help="slide perpendicularity tolerance in degrees", type=float, default=2.0)
    parser_options.add_argument("--count", help="ranked options to report", type=int, default=10)
    parser_options.add_argument("--min_slide_faces", help="minimum faces a slide must gain", type=int, default=50)
    parser_options.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "setups" command
    parser_setups = subparsers.add_parser("setups", help="rank CNC setup combinations (3-axis / indexed 3+2)")
    parser_setups.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_setups.add_argument("--no_indexed", help="skip the indexed 5-axis (3+2) machine", action="store_true")
    parser_setups.add_argument("--tilt", help="3+2 head tilt cone half-angle in degrees", type=float, default=90.0)
    parser_setups.add_argument("--max_setups", help="maximum setups per option", type=int, default=4)
    parser_setups.add_argument("--min_setup_area", help="minimum area (mm^2) a setup must gain (default: 0.1%% of the part)", type=float, default=None)
    parser_setups.add_argument("--count", help="ranked options to report", type=int, default=10)
    parser_setups.add_argument("--field_options", help="plans that get per-face assignment fields", type=int, default=3)
    parser_setups.add_argument("--serve", help="serve results in browser", action="store_true")

    # Create the parser for the "verdict" command
    parser_verdict = subparsers.add_parser("verdict", help="re-verdict one ranked setup plan with a real tool library")
    parser_verdict.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_verdict.add_argument("--option", help="ranked plan index to verdict", type=int, default=0)
    parser_verdict.add_argument("--tools", help="tools as D[:rc[:stickout[:holder_radius]]] (default: builtin flat+ball library)", nargs="*", type=str, default=None)
    parser_verdict.add_argument("--tollerance", help="gap threshold to flag a vertex", type=float, default=1e-1)
    parser_verdict.add_argument("--wall_tollerance", help="wall angle tolerance in degrees (side-milled)", type=float, default=1.0)
    parser_verdict.add_argument("--pixel", help="height map pixel size (default: resolution/5)", type=float, default=None)
    parser_verdict.add_argument("--no_indexed", help="skip the indexed 5-axis (3+2) machine", action="store_true")
    parser_verdict.add_argument("--tilt", help="3+2 head tilt cone half-angle in degrees", type=float, default=90.0)
    parser_verdict.add_argument("--max_setups", help="maximum setups per option", type=int, default=4)
    parser_verdict.add_argument("--min_setup_area", help="minimum area (mm^2) a setup must gain (default: 0.1%% of the part)", type=float, default=None)
    parser_verdict.add_argument("--serve", help="serve results in browser", action="store_true")

    # Create the parser for the "options" command
    parser_serve = subparsers.add_parser("serve", help="find injection molding options")
    parser_serve.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_serve.add_argument("--include", help="direction indices to highlight", nargs="+", type=int, default=[])
    parser_serve.add_argument("--exclude", help="direction indices to exclude from highlight", nargs="+", type=int, default=[])
    parser_serve.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "precompute" command
    parser_precompute = subparsers.add_parser("precompute", help="cache height maps and per-radius tool fields for fast composition")
    parser_precompute.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_precompute.add_argument("--directions", help="indices of approach directions to precompute", nargs="+", type=int, required=True)
    parser_precompute.add_argument("--pixel", help="height map pixel size (default: resolution/5)", type=float, default=None)
    parser_precompute.add_argument("--tips", help="tool tips as diameter:corner_radius (0 = flat, D/2 = ball)", nargs="*", type=str, default=[])
    parser_precompute.add_argument("--clearances", help="cylinder radii for holder/shank clearance fields", nargs="*", type=float, default=[])
    parser_precompute.add_argument("--window", help="gap accuracy window: gaps up to this are Euclidean-exact (zmap engine)", type=float, default=0.3)

    # Create the parser for the "compose" command
    parser_compose = subparsers.add_parser("compose", help="evaluate a full tool assembly from precomputed fields")
    parser_compose.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_compose.add_argument("direction", help="index of the approach direction", type=int)
    parser_compose.add_argument("--pixel", help="height map pixel size (default: resolution/5)", type=float, default=None)
    parser_compose.add_argument("--tollerance", help="gap threshold to flag a vertex", type=float, default=1e-1)
    parser_compose.add_argument("--diameter", help="tool diameter", type=float, default=2.0)
    parser_compose.add_argument("--corner_radius", help="tip corner radius: 0 = flat endmill, diameter/2 = ball nose", type=float, default=0.0)
    parser_compose.add_argument("--stickout", help="tool length out of the holder", type=float, default=None)
    parser_compose.add_argument("--holder", help="holder as stacked cylinders radius:start,radius:start,... (start measured from the tool tip at stickout 0)", type=str, default=None)
    parser_compose.add_argument("--sweep", help="additional stickout values to report coverage for", nargs="*", type=float, default=[])
    parser_compose.add_argument("--window", help="gap accuracy window: gaps up to this are Euclidean-exact (zmap engine)", type=float, default=0.3)
    parser_compose.add_argument("--serve", help="serve results in browser", action="store_true")

    # Create the parser for the "view" command
    parser_view = subparsers.add_parser("view", help="interactive viewer over all cached analysis fields")
    parser_view.add_argument("target", help="working directory or STEP/STL file to open")
    parser_view.add_argument("--timeout", help="seconds to keep the server alive (default: until Ctrl-C)", type=float, default=None)
    parser_view.add_argument("--port", help="port to serve on", type=int, default=8080)
    parser_view.add_argument("--no-browser", help="do not open a browser window", action="store_true")


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
                           resolution=args.resolution, subdivide=args.subdivide,
                           deflection=args.deflection, obj=args.obj)

        if args.serve:
            serve_workdir(result["workdir"])
            
    elif args.command == "thickness":
        logger.info("Computing rolling sphere thickness field")

        import processes
        from processes.base import apply_defaults, load_result_arrays

        params = {}
        if args.max_radius is not None:
            params["max_radius"] = args.max_radius
        if args.sharp_deg is not None:
            params["sharp_deg"] = args.sharp_deg
        if args.contact_angles:
            params["contact_angles"] = True

        def thin_vertices(analysis_id, member, threshold):
            """Below-threshold vertices, minus edge-explainable readings."""
            analysis = processes.get_analysis("injection_molding",
                                              analysis_id)
            merged = apply_defaults(analysis, params)
            result = analysis.run(args.directory, merged, None)
            logger.info(f"{analysis_id} stats: {result.stats}")

            cache_key = {**merged, "mesh": mesh_fingerprint(args.directory)}
            arrays = load_result_arrays(args.directory, "injection_molding",
                                        analysis_id, cache_key)
            values = arrays[member]
            excluded = edge_excluded(values, arrays["band_lo"],
                                     arrays["band_hi"],
                                     arrays["suspect"].astype(bool))
            return (values < threshold) & ~excluded

        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        thin = thin_vertices("thickness", "thickness", args.min)
        flagged = np.all(thin[faces], axis=1)

        if args.both:
            thin = thin_vertices("gaps", "gap", args.min_gap)
            flagged |= np.all(thin[faces], axis=1)

        indices = np.flatnonzero(flagged).tolist()
        logger.info(f"Flagging {len(indices)} faces below the thresholds")
        write_highlights(args.directory, indices)

        if args.serve:
            serve_workdir(args.directory)

    elif args.command == "directions":
        compute_directions(args.directory, count=args.count, axes=args.axes,
                           tollerance=args.tollerance, pixel=args.pixel)

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

    elif args.command == "setups":
        logger.info("Searching CNC setup combinations")

        import processes
        from processes.base import apply_defaults

        analysis = processes.get_analysis("cnc", "setups")
        params = {
            "indexed": not args.no_indexed,
            "tilt": args.tilt,
            "max_setups": args.max_setups,
            "count": args.count,
            "field_options": args.field_options,
        }
        if args.min_setup_area is not None:
            params["min_setup_area"] = args.min_setup_area
        merged = apply_defaults(analysis, params)
        result = analysis.run(args.directory, merged, None)

        for rank, option in enumerate(result.stats["options"]):
            setups = ", ".join(f"d{s['direction']} (+{s['marginal']:.0f}mm2)"
                               for s in option["setups"])
            logger.info(
                f"#{rank}  {option['machine']:6s} {len(option['setups'])} setup(s)"
                f"{' FLIP' if option['flip'] else '     '}  "
                f"{'FEASIBLE' if option['feasible'] else 'infeasible'}  "
                f"coverage {option['coverage'] * 100:.1f}%  [{setups}]  "
                f"unmachinable {option['counts']['internal']:.0f}mm2")

        if args.serve:
            serve_workdir(args.directory)

    elif args.command == "verdict":
        logger.info("Tool-library verdict of one setup plan")

        import processes
        from processes.base import apply_defaults

        analysis = processes.get_analysis("cnc", "setup_verdict")
        params = {
            "option": args.option,
            "tollerance": args.tollerance,
            "wall_tollerance": args.wall_tollerance,
            "pixel": args.pixel,
            "indexed": not args.no_indexed,
            "tilt": args.tilt,
            "max_setups": args.max_setups,
        }
        if args.tools is not None:
            params["tools"] = args.tools
        if args.min_setup_area is not None:
            params["min_setup_area"] = args.min_setup_area
        merged = apply_defaults(analysis, params)
        result = analysis.run(args.directory, merged, None)

        option = result.stats["options"][0]
        verdict = option["verdict"]
        setups = ", ".join(f"d{s['direction']} (+{s['marginal']:.0f}mm2)"
                           for s in option["setups"])
        logger.info(
            f"{option['machine']} {len(option['setups'])} setup(s)  "
            f"{'FEASIBLE' if option['feasible'] else 'infeasible'} with tools  "
            f"coverage {option['coverage'] * 100:.1f}% "
            f"(visibility {verdict['base_coverage'] * 100:.1f}%)  [{setups}]  "
            f"lost to tooling {verdict['lost']:.0f}mm2")

        if args.serve:
            serve_workdir(args.directory)

    elif args.command == "serve":
        logger.info("Serving results in browser")

        highlight_union(args.directory, include=args.include, exclude=args.exclude)

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
                          clearances=args.clearances, window=args.window)

    elif args.command == "compose":
        logger.info("Compose tool accessibility from precomputed fields")
        compose_tool(args.directory, args.direction, pixel=args.pixel,
                     tollerance=args.tollerance, diameter=args.diameter,
                     corner_radius=args.corner_radius, stickout=args.stickout,
                     cylinders=parse_holder(args.holder), sweep=args.sweep,
                     window=args.window)

        if args.serve:
            serve_workdir(args.directory)
