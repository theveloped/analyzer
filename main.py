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
    
    # Create the parser for the "slender" command
    parser_slender = subparsers.add_parser("slender", help="pocket depth/width (thin mold steel) slenderness field along one pull direction")
    parser_slender.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_slender.add_argument("--direction", help="pull direction index (with --axes: 0..5 = +/-X +/-Y +/-Z)", type=int, default=4)
    parser_slender.add_argument("--max_diameter", help="max pocket width considered in mm (default: auto from bounding box)", type=float, default=None)
    parser_slender.add_argument("--ladder", help="geometric step between swept pocket widths - the ratio field is quantized to it (finer = smoother, cost ~ladder/(ladder-1))", type=float, default=None)
    parser_slender.add_argument("--min_ratio", help="flag faces with all vertices above this depth/width ratio", type=float, default=2.0)
    parser_slender.add_argument("--serve", help="serve results in browser", action="store_true")

    # Create the parser for the "span" command
    parser_span = subparsers.add_parser("span", help="thin-span / normal-stiffness proxy field: distance to supporting thick material over thickness scale, direction-free")
    parser_span.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_span.add_argument("--max_radius", help="inscribed sphere radius cap for the thickness sub-run (default: auto from bounding box)", type=float, default=None)
    parser_span.add_argument("--max_thickness", help="thickness above which material counts as bulk support in mm (default: auto p99 of the field)", type=float, default=None)
    parser_span.add_argument("--ladder", help="geometric step between swept thickness scales (finer = smoother, slower)", type=float, default=None)
    parser_span.add_argument("--contrast", help="support must be at least this factor thicker than the vertex itself", type=float, default=None)
    parser_span.add_argument("--max_span", help="distance saturation in mm (default: bounding box diagonal)", type=float, default=None)
    parser_span.add_argument("--min_ratio", help="flag faces with all vertices above this span/thickness ratio", type=float, default=5.0)
    parser_span.add_argument("--serve", help="serve results in browser", action="store_true")

    # Create the parser for the "directions" command
    parser_directions = subparsers.add_parser("directions", help="directions a file and derive the mesh")
    parser_directions.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_directions.add_argument("--count", help="number of directions determin", type=int, default=64)
    parser_directions.add_argument("--axes", help="prepend the six principal +/-X/Y/Z directions", action="store_true")
    parser_directions.add_argument("--bbox-axes", dest="bbox_axes", help="add oriented bounding-box (PCA) axes of the part", action="store_true")
    parser_directions.add_argument("--hole-axes", dest="hole_axes", help="add hole/cylinder/cone/torus axes from the analytic surfaces", action="store_true")
    parser_directions.add_argument("--manual", help="manual axes as x:y:z strings", nargs="+", default=[])
    parser_directions.add_argument("--face-group", dest="face_group", help="face indices to average into one direction (repeatable)", type=int, nargs="+", action="append", default=[])
    parser_directions.add_argument("--tollerance", help="angular relaxation of the visibility test in degrees (near-vertical walls within it count as facing)", type=float, default=0.1)
    parser_directions.add_argument("--pixel", help="visibility height map pixel size (default: resolution/5)", type=float, default=None)
    
    # Create the parser for the "explode" command
    parser_explode = subparsers.add_parser("explode", help="import a STEP file: split assemblies into per-part workdirs (content-addressed under <root>/parts/) and extract face colors/names + PMI artifacts")
    parser_explode.add_argument("input", help="path of the input .stp/.step file", type=PathType(type='file', dash_ok=True, exists=True))
    parser_explode.add_argument("-r", "--root", help="parts root directory (default: current directory, matching the API server)", type=PathType(type='dir', dash_ok=True, exists=True), default=".")

    # Create the parser for the "export" command
    parser_export = subparsers.add_parser("export", help="export a part's geometry + semantic GD&T back out as an AP242 STEP file (re-authors pmi.json onto the source BREP)")
    parser_export.add_argument("directory", help="part working directory (holds source.stp + pmi.json)", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_export.add_argument("-o", "--output", help="output STEP path (default: <directory>/export/part.ap242.stp)", default=None)
    parser_export.add_argument("--schema", help="STEP schema to write (default: AP242 — the only one that carries semantic PMI)", default="AP242")

    # Create the parser for the "aag" command
    parser_aag = subparsers.add_parser("aag", help="build the BREP face adjacency graph (convexity, tangency, dihedrals) shared by sheet metal / tube / feature recognition")
    parser_aag.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_aag.add_argument("--smooth_angle", help="geometric tangency tolerance in degrees (default: 0.57)", type=float, default=None)
    parser_aag.add_argument("--tollerance", help="geometric tolerance (default: 1e-6)", type=float, default=1e-6)
    parser_aag.add_argument("--deflection", help="edge polyline deflection in mm (default: resolution/5)", type=float, default=None)

    # Create the parser for the "sheet" command
    parser_sheet = subparsers.add_parser("sheet", help="sheet metal recognition: skins, thickness, bends and per-face roles (flat pattern once available)")
    parser_sheet.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_sheet.add_argument("--min_thickness", help="minimum sheet thickness in mm (default: 0.1)", type=float, default=0.1)
    parser_sheet.add_argument("--max_thickness", help="maximum sheet thickness in mm (default: none)", type=float, default=None)
    parser_sheet.add_argument("--detect_only", help="skip the unfold / flat pattern", action="store_true")
    parser_sheet.add_argument("--k_factor", help="K-factor for the bend allowance (default: 0.5)", type=float, default=0.5)
    parser_sheet.add_argument("--dxf", help="also export the flat pattern to this DXF file", type=str, default=None)
    parser_sheet.add_argument("--serve", help="serve results in browser", action="store_true")

    # Create the parser for the "bendplan" command
    parser_bendplan = subparsers.add_parser("bendplan", help="press-brake bend planning: per-bend tooling intervals, collision envelopes and a bend-sequence + segmented-tooling search")
    parser_bendplan.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_bendplan.add_argument("--punch", help="restrict to one catalogue punch id", type=str, default="")
    parser_bendplan.add_argument("--die", help="restrict to one catalogue die id", type=str, default="")
    parser_bendplan.add_argument("--machine", help="machine YAML path (default: bundled demo)", type=str, default="")
    parser_bendplan.add_argument("--punches", help="punch catalogue YAML path (default: bundled demo)", type=str, default="")
    parser_bendplan.add_argument("--dies", help="die catalogue YAML path (default: bundled demo)", type=str, default="")
    parser_bendplan.add_argument("--k_factor", help="K-factor, must match the unfold allowance (default: 0.5)", type=float, default=0.5)
    parser_bendplan.add_argument("--margin", help="collision clearance margin in mm (default: 2.0)", type=float, default=2.0)
    parser_bendplan.add_argument("--springback", help="springback overbend delta in degrees (default: 2.0)", type=float, default=2.0)
    parser_bendplan.add_argument("--no_search", help="skip the sequence search (fixed-order plan only)", action="store_true")
    parser_bendplan.add_argument("--mesh_check", help="verify the best plan with the meshlib collision check (posed fine mesh vs extruded tool sections)", action="store_true")
    parser_bendplan.add_argument("--solutions", help="ranked plans to keep (default: 4)", type=int, default=4)
    parser_bendplan.add_argument("--serve", help="serve results in browser", action="store_true")

    # Create the parser for the "tube" command
    parser_tube = subparsers.add_parser("tube", help="tube/profile recognition: round/rectangular/square section, wall thickness, length and the unrolled cut pattern")
    parser_tube.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_tube.add_argument("--no_unroll", help="skip the outer shell unroll", action="store_true")
    parser_tube.add_argument("--k_factor", help="K-factor for the unroll (default: 0.5)", type=float, default=0.5)
    parser_tube.add_argument("--serve", help="serve results in browser", action="store_true")

    # Create the parser for the "features" command
    parser_features = subparsers.add_parser("features", help="recognize machining features (through/blind holes, counterbores, countersinks, pockets) from the AAG")
    parser_features.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_features.add_argument("--axis_angle_tol", help="coaxiality angle tolerance in degrees (default: 1.0)", type=float, default=1.0)
    parser_features.add_argument("--axis_dist_tol", help="coaxiality axis distance tolerance in mm (default: 0.01)", type=float, default=1e-2)
    parser_features.add_argument("--no_pockets", help="skip best-effort pocket emission", action="store_true")
    parser_features.add_argument("--serve", help="serve results in browser", action="store_true")

    # Create the parser for the "options" command
    parser_options = subparsers.add_parser("options", help="rank mold orientations (plate pair + slides)")
    parser_options.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_options.add_argument("--max_slides", help="maximum number of slides per orientation", type=int, default=2)
    parser_options.add_argument("--slide_tollerance", help="slide perpendicularity tolerance in degrees", type=float, default=2.0)
    parser_options.add_argument("--count", help="ranked options to report", type=int, default=10)
    parser_options.add_argument("--min_slide_faces", help="minimum faces a slide must gain", type=int, default=50)
    parser_options.add_argument("--serve", help="serve results in browser", action="store_true")
    
    # Create the parser for the "flow" command
    parser_flow = subparsers.add_parser("flow", help="voxel/SDF flow analysis: interior voxelization and (with --gate) a Hele-Shaw fill with frozen-skin hesitation")
    parser_flow.add_argument("directory", help="working directory", type=PathType(type='dir', dash_ok=True, exists=True))
    parser_flow.add_argument("--voxel", help="voxel size in mm (default: auto from resolution)", type=float, default=None)
    parser_flow.add_argument("--gate", help="gate point x y z - runs the fill solve from it", nargs=3, type=float, default=None)
    parser_flow.add_argument("--delta0", help="initial frozen-skin thickness in mm", type=float, default=0.0)
    parser_flow.add_argument("--skin_coef", help="frozen-skin growth coefficient in mm/sqrt(s) (0 = off)", type=float, default=0.12)
    parser_flow.add_argument("--fill_time", help="nominal fill time in seconds", type=float, default=2.0)
    parser_flow.add_argument("--iterations", help="frozen-skin fixed-point passes", type=int, default=3)
    parser_flow.add_argument("--neighborhood", help="grid neighborhood: 26 (isotropic) or 6 (fast)", type=int, choices=[6, 26], default=26)
    parser_flow.add_argument("--serve", help="serve results in browser", action="store_true")

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

    elif args.command == "slender":
        logger.info("Computing pocket slenderness field")

        import processes
        from processes import resolver
        from processes.base import apply_defaults, load_result_arrays

        analysis = processes.get_analysis("injection_molding", "slenderness")
        params = {"direction": args.direction}
        if args.max_diameter is not None:
            params["max_diameter"] = args.max_diameter
        if args.ladder is not None:
            params["ladder"] = args.ladder
        merged = apply_defaults(analysis, params)
        result = analysis.run(args.directory, merged, None)
        logger.info(f"slenderness stats: {result.stats}")

        cache_key = resolver.cache_key(args.directory,
                                       "injection_molding/slenderness", merged)
        arrays = load_result_arrays(args.directory, "injection_molding",
                                    "slenderness", cache_key)
        ratio = arrays["slenderness"]

        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        flagged = np.all(ratio[faces] > args.min_ratio, axis=1)
        indices = np.flatnonzero(flagged).tolist()
        logger.info(f"Flagging {len(indices)} faces above depth/width "
                    f"ratio {args.min_ratio}")
        write_highlights(args.directory, indices)

        if args.serve:
            serve_workdir(args.directory)

    elif args.command == "span":
        logger.info("Computing thin-span stiffness proxy field")

        import processes
        from processes import resolver
        from processes.base import apply_defaults, load_result_arrays

        analysis = processes.get_analysis("injection_molding", "thin_span")
        params = {}
        for name in ("max_radius", "max_thickness", "ladder", "contrast",
                     "max_span"):
            value = getattr(args, name)
            if value is not None:
                params[name] = value
        merged = apply_defaults(analysis, params)
        result = analysis.run(args.directory, merged, None)
        logger.info(f"thin_span stats: {result.stats}")

        cache_key = resolver.cache_key(args.directory,
                                       "injection_molding/thin_span", merged)
        arrays = load_result_arrays(args.directory, "injection_molding",
                                    "thin_span", cache_key)
        ratio = arrays["span_ratio"]

        faces = np.load(os.path.join(args.directory, FINE_FACES_FILE))
        flagged = np.all(ratio[faces] > args.min_ratio, axis=1)
        indices = np.flatnonzero(flagged).tolist()
        logger.info(f"Flagging {len(indices)} faces above span/thickness "
                    f"ratio {args.min_ratio}")
        write_highlights(args.directory, indices)

        if args.serve:
            serve_workdir(args.directory)

    elif args.command == "directions":
        manual = [[float(c) for c in s.split(":")] for s in args.manual]
        compute_directions(args.directory, count=args.count, axes=args.axes,
                           bbox_axes=args.bbox_axes, hole_axes=args.hole_axes,
                           manual=manual, face_groups=args.face_group,
                           tollerance=args.tollerance, pixel=args.pixel)

    elif args.command == "explode":
        import step_import

        manifest = step_import.import_step(args.input, args.root)
        kind = "assembly" if manifest["assembly"] else "single part"
        logger.info(f"Imported {kind} '{manifest['name']}' "
                    f"(source part {manifest['source']})")
        for part in manifest["parts"]:
            logger.info(f"  {part['quantity']}x {part['name']} -> "
                        f"parts/{part['part']} ({part['face_attrs']} face "
                        f"attrs, {part['pmi']} PMI)")

    elif args.command == "export":
        import step_export

        report = step_export.export_step(args.directory, args.output,
                                         schema=args.schema)
        c = report.counts
        logger.info(f"Exported {report.schema} -> {report.out_path} "
                    f"({c['dimensions']} dims, {c['tolerances']} tols, "
                    f"{c['datums']} datums)")
        for warning in report.warnings:
            logger.warning(warning)

    elif args.command == "aag":
        import processes
        from processes.base import apply_defaults

        analysis = processes.get_analysis("prep", "aag")
        merged = apply_defaults(analysis, {
            "smooth_angle": args.smooth_angle,
            "tollerance": args.tollerance,
            "deflection": args.deflection,
        })
        result = analysis.run(args.directory, merged, None)
        logger.info(f"AAG stats: {result.stats}")

    elif args.command == "sheet":
        import processes
        from processes.base import apply_defaults

        analysis = processes.get_analysis("sheet_metal", "detect")
        merged = apply_defaults(analysis, {
            "min_thickness": args.min_thickness,
            "max_thickness": args.max_thickness,
        })
        result = analysis.run(args.directory, merged, None)
        stats = result.stats
        logger.info(f"Sheet verdict: {stats['verdict']} "
                    f"(thickness {stats['thickness']:.2f} mm, "
                    f"{stats['bend_count']} bends)")
        for reason in stats["reasons"]:
            logger.warning(f"  {reason}")

        if stats["verdict"] == "sheet" and not args.detect_only:
            analysis = processes.get_analysis("sheet_metal", "flat_pattern")
            merged = apply_defaults(analysis, {
                "k_factor": args.k_factor,
                "min_thickness": args.min_thickness,
            })
            pattern = analysis.run(args.directory, merged, None).stats
            logger.info(
                f"Flat pattern: {pattern['flat_size'][0]:.1f} x "
                f"{pattern['flat_size'][1]:.1f} mm, area "
                f"{pattern['flat_area']:.1f} mm2, {pattern['hole_count']} "
                f"holes, {len(pattern['bends'])} bend lines, volume error "
                f"{pattern['volume_error_pct']:.2f}% "
                f"({'ok' if pattern['volume_ok'] else 'CHECK'}), "
                f"{'developable' if pattern['developable'] else 'NOT developable'}")

            if args.dxf:
                import dxfexport
                dxfexport.export_dxf(args.directory, out_path=args.dxf)

        if args.serve:
            serve_workdir(args.directory)

    elif args.command == "bendplan":
        import processes
        from processes.base import apply_defaults

        analysis = processes.get_analysis("sheet_metal", "bend_plan")
        merged = apply_defaults(analysis, {
            "punch_id": args.punch,
            "die_id": args.die,
            "machine_path": args.machine,
            "punches_path": args.punches,
            "dies_path": args.dies,
            "k_factor": args.k_factor,
            "margin": args.margin,
            "springback_deg": args.springback,
            "search": not args.no_search,
            "solutions": args.solutions,
            "mesh_check": args.mesh_check,
        })
        stats = analysis.run(args.directory, merged, None).stats
        if stats.get("mesh_check"):
            verdict = stats["mesh_check"]
            logger.info(
                f"  mesh check: {'CLEAN' if verdict['clean'] else 'COLLIDES'}"
                f" (eps {verdict['eps']} mm, "
                f"{verdict['phi_step_deg']:.1f} deg steps)")
            for step in verdict["steps"]:
                if not step["clean"]:
                    tools = sorted({hit['tool'] for hit in step['hits']})
                    logger.warning(f"    step {step['step']}: hits on "
                                   f"{', '.join(tools)}")
        logger.info(
            f"Bend plan [{stats['mode']}]: "
            f"{'FEASIBLE' if stats['feasible'] else 'NOT FEASIBLE'} — "
            f"{stats['panel_count']} panels, {stats['bend_count']} bends "
            f"in {stats['sister_group_count']} groups, t "
            f"{stats['thickness']:.2f} mm on {stats['machine']}")
        for warning in stats["warnings"]:
            logger.warning(f"  {warning}")
        for action in stats["actions"]:
            label = (f"bends {action['bend_ids']} rot {action['rotation']}"
                     + (" flipped" if action["flip"] else ""))
            if action["feasible"]:
                best = action["best"]
                required = sum(b - a for a, b in best["required"])
                logger.info(f"  {label}: ok with {best['punch']} / "
                            f"{best['die']} (required {required:.0f} mm)")
            else:
                logger.info(f"  {label}: infeasible "
                            f"({action['collision_summary'] or 'collisions'})")
        for rank, plan_entry in enumerate(stats["plans"], start=1):
            order = " -> ".join(str(step["bend_ids"])
                                for step in plan_entry["steps"])
            objective = plan_entry["objective"]
            logger.info(
                f"  plan #{rank}: {order} | {int(objective[0])} setup "
                f"changes, {int(objective[2])} sections, "
                f"{objective[3]:.0f} mm installed")
            for setup in plan_entry["setups"]:
                logger.info(f"    setup {setup['punch_id']} / "
                            f"{setup['die_id']}: steps "
                            f"{setup['step_indices']}")

        if args.serve:
            serve_workdir(args.directory)

    elif args.command == "tube":
        import processes
        from processes.base import apply_defaults

        analysis = processes.get_analysis("tube_laser", "profile")
        merged = apply_defaults(analysis, {
            "unroll": not args.no_unroll,
            "k_factor": args.k_factor,
        })
        result = analysis.run(args.directory, merged, None)
        stats = result.stats
        if stats["verdict"] == "none":
            logger.info("Not a straight profile:")
            for reason in stats["reasons"]:
                logger.warning(f"  {reason}")
        else:
            logger.info(
                f"Profile: {stats['verdict']} {stats['width']:.1f} x "
                f"{stats['height']:.1f} x t{stats['thickness']:.2f}, "
                f"length {stats['length']:.1f} mm, corner radius "
                f"{stats['inner_radius']:.1f}/{stats['outer_radius']:.1f}")
            if "flat_size" in stats:
                logger.info(f"Cut pattern: {stats['flat_size'][0]:.1f} x "
                            f"{stats['flat_size'][1]:.1f} mm, "
                            f"{stats['hole_count']} holes")

        if args.serve:
            serve_workdir(args.directory)

    elif args.command == "features":
        import processes
        from processes.base import apply_defaults

        analysis = processes.get_analysis("cnc", "features")
        merged = apply_defaults(analysis, {
            "axis_angle_tol": args.axis_angle_tol,
            "axis_dist_tol": args.axis_dist_tol,
            "include_pockets": not args.no_pockets,
        })
        result = analysis.run(args.directory, merged, None)
        logger.info(f"Feature counts: {result.stats['counts']}")
        for feature in result.stats["features"]:
            size = (f"D{feature['diameter']:.2f}" if feature["diameter"]
                    else "freeform")
            logger.info(f"  #{feature['id']} {feature['type']} {size} "
                        f"depth {feature['depth']:.2f} "
                        f"({len(feature['faces'])} faces)")

        if args.serve:
            serve_workdir(args.directory)

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

    elif args.command == "flow":
        logger.info("Voxel/SDF flow analysis")

        import processes
        from processes.base import apply_defaults

        if args.gate is None:
            analysis = processes.get_analysis("injection_molding",
                                              "flow_voxels")
            merged = apply_defaults(analysis, {"voxel": args.voxel})
            result = analysis.run(args.directory, merged, None)
            spec = result.stats["resolution"]
            logger.info(
                f"grid {'x'.join(str(d) for d in result.stats['grid']['dims'])} "
                f"at {result.stats['grid']['voxel']:.3f} mm, "
                f"{result.stats['interior_voxels']} interior voxels, "
                f"{result.stats['cells']} cells, "
                f"resolution {spec['status']} "
                f"({spec['voxels_through_thickness']:.1f} voxels through "
                f"the median wall), sign check {result.stats['sign_check']}")
        else:
            analysis = processes.get_analysis("injection_molding",
                                              "flow_fill")
            merged = apply_defaults(analysis, {
                "voxel": args.voxel,
                "gate": list(args.gate),
                "delta0": args.delta0,
                "skin_coef": args.skin_coef,
                "fill_time": args.fill_time,
                "iterations": args.iterations,
                "neighborhood": str(args.neighborhood),
            })
            result = analysis.run(args.directory, merged, None)
            stats = result.stats
            logger.info(
                f"gate snapped {stats['gate']['snap_distance_mm']:.2f} mm, "
                f"{stats['reached_volume_fraction'] * 100:.1f}% of voxels "
                f"reached, freeze-off on "
                f"{stats['freeze_off']['surface_fraction'] * 100:.1f}% of "
                f"the judgeable surface, "
                f"p95 pressure proxy {stats['p95_cost']:.3g}")

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
