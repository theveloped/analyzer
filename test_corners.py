"""Sharp-corner access checks (cnc/corner_access) on synthetic parts.

The model under test: a cutter is round, so which sharp BREP edges can come
out sharp from a given direction, and which are stuck with at least the
cutter radius. Fixtures are built with OCP primitives and written to STEP, so
nothing here depends on committed sample data.

- 20x20x10 block with a centred 8x8x5 pocket, flat D=6 from +Z: the 4
  vertical pocket corners are `radius` (R = D/2 = 3), the 4 floor/wall edges
  are `sharp` (a flat endmill's bottom edge IS a sharp circle), and the
  convex top rim is out of scope for the concave class
- the same part from -Z: every pocket edge is `blocked`
- a pocket with 15-degree drafted walls: the floor/wall edges stay `sharp`
  from +Z. A sloped wall does not stop the tool bottom lying flat in the
  corner, which is the whole point of the floor/wall rule
- the same pocket with R3 fillets on the 4 vertical corners: those edges are
  tangent (G1), leave the candidate set, and nothing is flagged — the
  analysis says the part is already fixed
- a T-slot: the undercut shoulders are `blocked` from +Z
- convex class on a plain box (the mold-cavity mirror): top rim `sharp`,
  verticals `radius`, bottom `blocked`
- array/stats contracts and the registry runner's cache round-trip

Run from the repo root: python test_corners.py
"""

import os
import sys
import tempfile

import numpy as np

import aag
import pipeline
from pipeline import EDGE_ACCESS_ROLES
from processes import get_analysis, resolver
from processes.base import apply_defaults, result_paths

PASSED = 0
FAILED = 0

UP = 4    # +Z — compute_directions(axes=True) prepends +-XYZ at indices 0..5
DOWN = 5  # -Z
DIAMETER = 6.0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [OK ] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


def write_step(shape, path):
    from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer

    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    writer.Write(path)
    return path


def _box(lo, hi):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(*lo), gp_Pnt(*hi)).Shape()


def _cut(a, b):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    return BRepAlgoAPI_Cut(a, b).Shape()


def make_pocket(tmp, name="pocket.stp"):
    """20x20x10 block, top at z=0, centred 8x8x5 pocket (floor at z=-5)."""
    return write_step(_cut(_box((-10, -10, -10), (10, 10, 0)),
                           _box((-4, -4, -5), (4, 4, 1))),
                      os.path.join(tmp, name))


def make_drafted_pocket(tmp, draft_deg=15.0, name="drafted.stp"):
    """The same pocket with walls drafted outward, so the floor/wall edges
    are floor-to-SLOPED-wall rather than floor-to-vertical-wall."""
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakePolygon,
                                    BRepBuilderAPI_MakeFace)
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.gp import gp_Pnt

    depth = 5.0
    bottom, top = 4.0, 4.0 + depth * np.tan(np.radians(draft_deg))

    def wire(half, z):
        polygon = BRepBuilderAPI_MakePolygon()
        for x, y in ((-half, -half), (half, -half), (half, half), (-half, half)):
            polygon.Add(gp_Pnt(x, y, z))
        polygon.Close()
        return polygon.Wire()

    loft = BRepOffsetAPI_ThruSections(True, True)
    loft.AddWire(wire(bottom, -depth))
    loft.AddWire(wire(top, 1.0))
    loft.Build()
    return write_step(_cut(_box((-10, -10, -10), (10, 10, 0)), loft.Shape()),
                      os.path.join(tmp, name))


def make_filleted_pocket(tmp, radius=3.0, name="filleted.stp"):
    """The plain pocket with the four vertical corners filleted — the fix a
    machinist would ask for."""
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.TopoDS import TopoDS
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_CurveType

    shape = _cut(_box((-10, -10, -10), (10, 10, 0)),
                 _box((-4, -4, -5), (4, 4, 1)))
    fillet = BRepFilletAPI_MakeFillet(shape)
    # MapShapes, not TopExp_Explorer: the explorer yields a shared edge once
    # per face it bounds, so every edge would be added to the fillet twice
    edge_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)
    added = 0
    for index in range(edge_map.Extent()):
        edge = TopoDS.Edge_s(edge_map.FindKey(index + 1))
        adaptor = BRepAdaptor_Curve(edge)
        if adaptor.GetType() != GeomAbs_CurveType.GeomAbs_Line:
            continue
        first, last = BRep_Tool.Range_s(edge)
        p0 = adaptor.Value(first)
        p1 = adaptor.Value(last)
        vertical = abs(p0.Z() - p1.Z()) > 1e-6
        inside = (max(abs(p0.X()), abs(p0.Y())) < 5.0
                  and max(abs(p1.X()), abs(p1.Y())) < 5.0)
        if vertical and inside:
            fillet.Add(radius, edge)
            added += 1
    if added != 4:
        raise RuntimeError(f"expected 4 vertical pocket corners, found {added}")
    return write_step(fillet.Shape(), os.path.join(tmp, name))


def make_tslot(tmp, name="tslot.stp"):
    """A slot whose body is wider than its mouth — the shoulders under the
    overhang cannot be seen from +Z at all."""
    block = _box((-10, -10, -10), (10, 10, 0))
    mouth = _box((-2, -11, -4), (2, 11, 1))     # narrow opening, z -4..0
    body = _box((-6, -11, -8), (6, 11, -4))     # wide body underneath
    return write_step(_cut(_cut(block, mouth), body),
                      os.path.join(tmp, name))


def build(tmp, step_path, sub):
    wd = os.path.join(tmp, sub)
    os.makedirs(wd, exist_ok=True)
    pipeline.mesh_part(step_path, wd, resolution=0.5)
    pipeline.compute_directions(wd, count=2, axes=True)
    # prep/aag is a declared prerequisite, so the resolver builds it in the
    # app; calling the pipeline function directly means building it here
    pipeline.compute_aag(wd)
    return wd


def roles_of(result, workdir, direction):
    """Per-edge role LABELS keyed by canonical edge id, for the edges the
    run treated as candidates."""
    codes = result["arrays"][f"edge_role_{direction}"]
    return {e: EDGE_ACCESS_ROLES[int(codes[e])]
            for e in range(len(codes)) if codes[e] != 0}


def edge_geometry(workdir):
    """Canonical edge id -> (midpoint, is_vertical) from the AAG polylines."""
    graph = aag.load_aag(workdir)
    out = {}
    for e in range(graph.edge_count):
        line = graph.polyline(e)
        if len(line) < 2:
            continue
        out[e] = (line.mean(axis=0),
                  bool(abs(line[-1][2] - line[0][2]) > 1e-6))
    return out, graph


# --------------------------------------------------------------------------


def case_plain_pocket(tmp):
    print("=== plain pocket, concave class, flat D=6 ===")
    wd = build(tmp, make_pocket(tmp), "pocket")
    result = pipeline.corner_access(wd, direction_indices=[UP, DOWN],
                                    diameter=DIAMETER)
    geom, graph = edge_geometry(wd)
    stats = result["stats"]

    # the pocket has 8 concave edges: 4 vertical corners + 4 floor/wall
    check("only the 8 concave pocket edges are candidates",
          stats["candidate_edges"] == 8,
          f"{stats['candidate_edges']} candidates of {stats['edge_count']} edges")

    up = roles_of(result, wd, UP)
    vertical = {e for e, (_, is_vertical) in geom.items() if is_vertical}
    corners = {e for e in up if e in vertical}
    floors = {e for e in up if e not in vertical}

    check("4 vertical pocket corners come out at the cutter radius",
          len(corners) == 4 and all(up[e] == "radius" for e in corners),
          f"{sorted(up[e] for e in corners)}")
    check("4 floor/wall edges are machinable sharp",
          len(floors) == 4 and all(up[e] == "sharp" for e in floors),
          f"{sorted(up[e] for e in floors)}")

    radius = result["arrays"][f"edge_radius_{UP}"]
    check("flagged corners report R = D/2",
          all(abs(float(radius[e]) - DIAMETER / 2) < 1e-6 for e in corners),
          f"R = {float(radius[min(corners)]):.3f} mm")
    check("sharp edges report the tool's corner radius (0 for a flat mill)",
          all(float(radius[e]) == 0.0 for e in floors))

    row = next(r for r in stats["per_direction"] if r["direction"] == UP)
    check("+Z: 4 flagged, nothing blocked",
          row["counts"] == {"sharp": 4, "radius": 4, "oblique": 0, "blocked": 0},
          str(row["counts"]))
    check("+Z: max required radius is D/2",
          abs(row["max_required_radius"] - DIAMETER / 2) < 1e-6,
          f"{row['max_required_radius']}")

    down = roles_of(result, wd, DOWN)
    check("-Z: every pocket edge is blocked",
          len(down) == 8 and set(down.values()) == {"blocked"},
          str(sorted(set(down.values()))))

    # the top rim is convex — the CNC question does not ask about it
    convex = int((graph.edge_convexity == aag.EDGE_CONVEX).sum())
    check("convex rim edges exist but are out of the concave class",
          convex >= 12 and stats["candidate_edges"] == 8,
          f"{convex} convex edges in the graph, none of them candidates")
    return wd, result


def case_drafted_pocket(tmp):
    print("=== drafted pocket: a sloped wall still corners sharp ===")
    wd = build(tmp, make_drafted_pocket(tmp), "drafted")
    result = pipeline.corner_access(wd, direction_indices=[UP],
                                    diameter=DIAMETER)
    geom, _ = edge_geometry(wd)
    up = roles_of(result, wd, UP)
    floors = [e for e in up if not geom[e][1]]
    corners = [e for e in up if geom[e][1]]

    check("floor/wall edges under a 15 deg draft are still sharp",
          len(floors) == 4 and all(up[e] == "sharp" for e in floors),
          f"{sorted(up[e] for e in floors)}")
    check("the drafted corner edges still need a radius",
          len(corners) == 4
          and all(up[e] in ("radius", "oblique") for e in corners),
          f"{sorted(up[e] for e in corners)}")


def case_filleted_pocket(tmp):
    print("=== filleted pocket: the corners are already fixed ===")
    wd = build(tmp, make_filleted_pocket(tmp), "filleted")
    result = pipeline.corner_access(wd, direction_indices=[UP],
                                    diameter=DIAMETER)
    stats = result["stats"]
    geom, graph = edge_geometry(wd)
    up = roles_of(result, wd, UP)

    # the fillet replaces each sharp vertical corner with a cylindrical face
    # joined tangentially, and lands a NEW concave edge on the pocket floor —
    # so the candidate count goes 8 -> 8, but every one of them is now a
    # floor/wall junction rather than a wall/wall one
    check("no vertical corner edge survives as a candidate",
          not any(geom[e][1] for e in up),
          f"{sum(geom[e][1] for e in up)} vertical candidates")
    check("the fillets introduced tangent (G1) edges",
          int((graph.edge_continuity != 0).sum()) >= 8,
          f"{int((graph.edge_continuity != 0).sum())} tangent edges")
    check("nothing is flagged on a properly filleted pocket",
          set(up.values()) == {"sharp"}, str(sorted(set(up.values()))))
    row = stats["per_direction"][0]
    check("stats agree: zero flagged edges", row["flagged_edges"] == 0,
          f"flagged_edges = {row['flagged_edges']}")
    check("no edge in the table needs a fillet",
          all(e["required_radius"] == 0.0 for e in stats["edges"]),
          f"{sorted({e['required_radius'] for e in stats['edges']})}")


def case_tslot(tmp):
    print("=== T-slot: the undercut shoulders cannot be seen ===")
    wd = build(tmp, make_tslot(tmp), "tslot")
    result = pipeline.corner_access(wd, direction_indices=[UP],
                                    diameter=DIAMETER)
    geom, _ = edge_geometry(wd)
    up = roles_of(result, wd, UP)

    # the shoulder edges sit at the mouth/body junction, z = -4, out beyond
    # the 2 mm mouth half-width
    shoulders = [e for e in up
                 if abs(geom[e][0][2] + 4.0) < 1e-6 and abs(geom[e][0][0]) > 2.5]
    check("the T-slot has undercut shoulder edges", len(shoulders) >= 2,
          f"{len(shoulders)} shoulder edges at z = -4")
    check("undercut shoulders are blocked from +Z",
          bool(shoulders) and all(up[e] == "blocked" for e in shoulders),
          f"{sorted(set(up[e] for e in shoulders))}")


def case_convex_mold_mirror(tmp):
    print("=== convex class on a box: the mold-cavity mirror ===")
    wd = build(tmp, write_step(_box((-10, -10, -10), (10, 10, 0)),
                               os.path.join(tmp, "box.stp")), "box")
    result = pipeline.corner_access(wd, direction_indices=[UP],
                                    edge_class="convex", diameter=DIAMETER)
    geom, _ = edge_geometry(wd)
    up = roles_of(result, wd, UP)

    check("a box has 12 convex candidate edges",
          result["stats"]["candidate_edges"] == 12,
          f"{result['stats']['candidate_edges']} candidates")

    top = [e for e in up if abs(geom[e][0][2]) < 1e-6 and not geom[e][1]]
    vertical = [e for e in up if geom[e][1]]
    bottom = [e for e in up if abs(geom[e][0][2] + 10.0) < 1e-6
              and not geom[e][1]]
    check("top rim: the cavity floor/wall corner mills sharp",
          len(top) == 4 and all(up[e] == "sharp" for e in top),
          f"{sorted(up[e] for e in top)}")
    check("vertical box edges: the cavity corners need R = D/2",
          len(vertical) == 4 and all(up[e] == "radius" for e in vertical),
          f"{sorted(up[e] for e in vertical)}")
    check("bottom rim is blocked from +Z",
          len(bottom) == 4 and all(up[e] == "blocked" for e in bottom),
          f"{sorted(up[e] for e in bottom)}")


def case_contracts(wd, result):
    print("=== array and stats contracts ===")
    graph = aag.load_aag(wd)
    arrays = result["arrays"]
    meta = result["field_meta"]
    stats = result["stats"]

    check("segment geometry is stored once, verdicts per direction",
          set(arrays) == {"segment_points", "segment_edge"}
          | {f"{stem}_{d}" for d in (UP, DOWN)
             for stem in ("segment_role", "edge_role", "edge_radius",
                          "face_flag")},
          str(sorted(arrays)))
    check("segment_points is (S,2,3) float32",
          arrays["segment_points"].dtype == np.dtype("<f4")
          and arrays["segment_points"].shape == (stats["segments"], 2, 3),
          str(arrays["segment_points"].shape))
    check("per-edge arrays span every canonical BREP edge",
          all(len(arrays[f"edge_role_{d}"]) == graph.edge_count
              and len(arrays[f"edge_radius_{d}"]) == graph.edge_count
              for d in (UP, DOWN)),
          f"edge_count {graph.edge_count}")
    check("face_flag is brep_face associated with an explicit length",
          all(meta[f"face_flag_{d}"]["association"] == "brep_face"
              and meta[f"face_flag_{d}"]["length"] == graph.face_count
              for d in (UP, DOWN)))
    check("role labels travel with the field",
          all(meta[f"segment_role_{d}"]["types"] == EDGE_ACCESS_ROLES
              for d in (UP, DOWN)))
    check("segment ids point at candidate edges only",
          bool(np.isin(arrays["segment_edge"],
                       np.flatnonzero(arrays[f"edge_role_{UP}"] != 0)).all()))

    for d in (UP, DOWN):
        row = next(r for r in stats["per_direction"] if r["direction"] == d)
        codes = arrays[f"edge_role_{d}"]
        histogram = {label: int((codes == code).sum())
                     for code, label in enumerate(EDGE_ACCESS_ROLES)
                     if code != 0}
        check(f"stats counts match the stored array (direction {d})",
              row["counts"] == histogram, f"{row['counts']} vs {histogram}")

    faces_flagged = arrays[f"face_flag_{UP}"].astype(bool)
    flagged = np.flatnonzero(arrays[f"edge_role_{UP}"] >= 2)
    expected = {int(f) for f in graph.edge_faces[flagged].ravel() if f >= 0}
    check("flagged faces are exactly the faces bounding a flagged edge",
          set(np.flatnonzero(faces_flagged).tolist()) == expected,
          f"{len(expected)} faces")

    first = stats["edges"][0]
    check("the edge table leads with the corners that need a fillet",
          first["role"][str(UP)] == "radius"
          and abs(first["required_radius"] - DIAMETER / 2) < 1e-6
          and abs(first["dihedral_deg"] - 90.0) < 1.0,
          f"first row {first['role'][str(UP)]}, R {first['required_radius']}, "
          f"{first['dihedral_deg']} deg")
    check("required_radius is the best over the selected directions",
          all(row["required_radius"] in (0.0, DIAMETER / 2)
              for row in stats["edges"]),
          "no row is scored by the -Z direction that sees nothing")

    check("an out-of-range direction raises",
          _raises(lambda: pipeline.corner_access(wd, direction_indices=[999])))
    check("an unknown edge class raises",
          _raises(lambda: pipeline.corner_access(wd, edge_class="sharpish")))


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def case_runner(wd):
    print("=== registry runner ===")
    analysis = get_analysis("cnc", "corner_access")
    params = apply_defaults(analysis, {"direction_indices": [UP],
                                       "diameter": DIAMETER})
    first = analysis.run(wd, params, None)
    key = resolver.cache_key(wd, "cnc/corner_access", params)
    json_path, npz_path = result_paths(wd, "cnc", "corner_access", key)
    check("runner stores under the resolver cache key",
          os.path.exists(json_path) and os.path.exists(npz_path))
    second = analysis.run(wd, params, None)
    check("second run returns the cached result",
          second.stats == first.stats and second.fields == first.fields)
    check("the cache key is salted by the directions fingerprint",
          key.get("directions") == pipeline.directions_fingerprint(wd)
          and key.get("aag") == pipeline.aag_fingerprint(wd),
          "directions + aag fingerprints present")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        wd, result = case_plain_pocket(tmp)
        case_drafted_pocket(tmp)
        case_filleted_pocket(tmp)
        case_tslot(tmp)
        case_convex_mold_mirror(tmp)
        case_contracts(wd, result)
        case_runner(wd)

    print()
    if FAILED:
        print(f"{FAILED} CHECK(S) FAILED ({PASSED} passed)")
        return 1
    print(f"ALL CHECKS PASSED ({PASSED} assertions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
