"""DXF export of stored flat-pattern results.

Reads the entities a sheet_metal/flat_pattern (or tube_laser/profile
unroll) result stored — bulge polylines in the flat frame — and writes a
DXF with instapart's layer convention: OUTLINE (contour + holes), BENDS
(bend lines with an angle/radius/direction annotation) and ENGRAVING
(annotation text). ezdxf is imported lazily so processes/base.py stays
framework-free and the dependency is only needed when exporting.
"""

import glob
import json
import os

from loguru import logger

LAYERS = {
    "OUTLINE": 7,    # white
    "BENDS": 30,     # orange
    "ENGRAVING": 5,  # blue
}


def _lwpolyline_points(path):
    """ezdxf 'xyb' tuples from a stored bulge path; True when closed.

    Stored paths carry [x, y, bulge] per segment start and a plain [x, y]
    tail; a closed path repeats its first point, which the LWPOLYLINE close
    flag replaces.
    """
    if len(path) < 2:
        return [], False
    first = path[0]
    last = path[-1]
    closed = (abs(first[0] - last[0]) < 1e-9
              and abs(first[1] - last[1]) < 1e-9)
    entries = path[:-1] if closed else path
    points = [(float(entry[0]), float(entry[1]),
               float(entry[2]) if len(entry) > 2 else 0.0)
              for entry in entries]
    return points, closed


def latest_result_path(workdir, process, analysis):
    """Newest stored result JSON of one analysis, or None."""
    pattern = os.path.join(workdir, "results", process, analysis, "*.json")
    candidates = [path for path in glob.glob(pattern)
                  if not path.endswith("_overrides.json")]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def export_dxf(workdir, process="sheet_metal", analysis="flat_pattern",
               result_hash=None, out_path=None):
    """Write the flat pattern of a stored result to DXF; returns the path.

    ``result_hash`` selects a specific stored result; None takes the most
    recent one. ``out_path`` defaults to <hash>.dxf beside the result JSON
    (the API route serves that file).
    """
    import ezdxf

    if result_hash is not None:
        json_path = os.path.join(workdir, "results", process, analysis,
                                 f"{result_hash}.json")
        if not os.path.exists(json_path):
            raise ValueError(f"no stored result {result_hash}")
    else:
        json_path = latest_result_path(workdir, process, analysis)
        if json_path is None:
            raise ValueError(f"no stored {process}/{analysis} result — "
                             "run the analysis first")

    with open(json_path) as f:
        payload = json.load(f)
    entities = payload.get("stats", {}).get("entities")
    if not entities:
        raise ValueError("stored result carries no flat-pattern entities")
    thickness = payload["stats"].get("thickness")

    doc = ezdxf.new("R2010", setup=True)
    for name, color in LAYERS.items():
        doc.layers.add(name, color=color)
    msp = doc.modelspace()

    def add_path(path, layer):
        points, closed = _lwpolyline_points(path)
        if points:
            msp.add_lwpolyline(points, format="xyb", close=closed,
                               dxfattribs={"layer": layer})

    add_path(entities.get("contour", []), "OUTLINE")
    for hole in entities.get("holes", []):
        # holes may be plain paths or dicts with feature annotations
        add_path(hole["path"] if isinstance(hole, dict) else hole, "OUTLINE")
    for engraving in entities.get("engravings", []):
        add_path(engraving, "ENGRAVING")

    for bend in entities.get("bend_lines", []):
        path = bend.get("path", [])
        if len(path) < 2:
            continue
        start, end = path[0][:2], path[1][:2]
        msp.add_line(start, end, dxfattribs={"layer": "BENDS"})
        label = (f"{bend.get('angle_deg', 0):.0f}° "
                 f"{bend.get('direction', '').upper()} "
                 f"R{bend.get('inner_radius', 0):.1f}")
        mid = (0.5 * (start[0] + end[0]), 0.5 * (start[1] + end[1]))
        text = msp.add_text(label, dxfattribs={"layer": "ENGRAVING",
                                               "height": 2.0})
        text.set_placement(mid)

    if thickness is not None:
        note = msp.add_text(f"t = {thickness:.2f} mm",
                            dxfattribs={"layer": "ENGRAVING", "height": 2.5})
        note.set_placement((0.0, -6.0))

    if out_path is None:
        out_path = os.path.splitext(json_path)[0] + ".dxf"
    doc.saveas(out_path)
    logger.info(f"DXF written: {out_path}")
    return out_path
