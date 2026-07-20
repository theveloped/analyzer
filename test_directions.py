"""Checks for multi-source candidate directions + provenance.

Covers the direction-assembly helpers (antipodal pairing, dedup, averaged
face normals, PCA axes, hole axes) and one STEP end-to-end that exercises the
workdir-dependent sources plus the directions_sources.json sidecar.

Run from the repo root: python test_directions.py
"""
import json
import os
import sys
import tempfile

import numpy as np

import analysis
from analysis import (assemble_directions, average_face_normal, canonical_vector,
                      hole_axes_from_geometry, pca_axes, DIRECTION_SOURCES)
from processes.base import params_hash


def _antipodal(dirs):
    dirs = np.asarray(dirs)
    return dirs.shape[0] % 2 == 0 and np.allclose(dirs[0::2], -dirs[1::2])


def unit_checks(check):
    # canonical vector: unit + idempotent
    v = canonical_vector([0, 0, 2])
    check("canonical_vector normalizes", np.allclose(v, [0, 0, 1]), f"{v}")
    check("canonical_vector idempotent",
          np.array_equal(canonical_vector(v), v), "")

    # averaged face normal collapses a curved group to one axis
    normals = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    avg = average_face_normal(normals, [0, 1])
    check("average_face_normal of 90deg pair",
          np.allclose(avg, [0.70710678, 0.70710678, 0]), f"{avg}")
    flat = average_face_normal(np.tile([0, 0, 1.0], (5, 1)), [0, 1, 2, 3, 4])
    check("average_face_normal of a planar group == plane normal",
          np.allclose(flat, [0, 0, 1]), f"{flat}")

    # PCA axes: 3 orthonormal, major axis along the long dimension
    rng_pts = np.array([[x, 0, 0] for x in np.linspace(-10, 10, 21)]
                       + [[0, y, 0] for y in np.linspace(-3, 3, 7)]
                       + [[0, 0, z] for z in np.linspace(-1, 1, 3)], dtype=float)
    axesv = pca_axes(rng_pts)
    orth = max(abs(float(np.dot(axesv[i], axesv[j])))
               for i in range(3) for j in range(3) if i != j)
    norms = max(abs(float(np.linalg.norm(a)) - 1.0) for a in axesv)
    check("pca_axes orthonormal", orth < 1e-9 and norms < 1e-9,
          f"max abs(dot)={orth:.1e} max abs(norm-1)={norms:.1e}")
    check("pca_axes major axis is the long (X) axis",
          abs(abs(axesv[0][0]) - 1.0) < 1e-6, f"{axesv[0]}")

    # hole axes: quadric faces contribute, planes ignored, coaxial merged
    surface_params = [
        {"type": "plane", "normal": [0, 0, 1]},
        {"type": "cylinder", "axis": [0, 0, 1], "point": [0, 0, 0], "radius": 3.0},
        {"type": "cylinder", "axis": [0, 0, 1], "point": [0, 0, 5], "radius": 3.0},
        {"type": "cone", "axis": [1, 0, 0], "apex": [0, 0, 0]},
    ]
    holes = hole_axes_from_geometry(surface_params)
    check("hole_axes: plane ignored, coaxial merged", len(holes) == 2,
          f"{[d for _, d in holes]}")
    check("hole_axes: cylinder axis + radius carried",
          np.allclose(holes[0][0], [0, 0, 1]) and holes[0][1]["radius"] == 3.0,
          f"{holes[0]}")


def assemble_checks(check):
    # axes + uniform + manual, no workdir needed for these sources
    dirs, sources = assemble_directions(
        "", count=4, axes=True, bbox_axes=False, hole_axes=False,
        manual=[[1, 1, 0]], face_groups=[])
    check("assemble: antipodal over the whole array", _antipodal(dirs),
          f"{dirs.shape[0]} rows")
    check("assemble: sources index-aligned to rows", len(sources) == len(dirs),
          f"{len(sources)} sources / {len(dirs)} dirs")
    check("assemble: every source in the enum",
          all(s["source"] in DIRECTION_SOURCES for s in sources), "")
    check("assemble: manual [1,1,0] present",
          any(s["source"] == "manual" for s in sources),
          f"{sum(s['source'] == 'manual' for s in sources)} rows")

    # dedup: a manual axis coincident with a world axis is dropped
    dirs2, sources2 = assemble_directions(
        "", count=4, axes=True, manual=[[0, 0, 1]], face_groups=[])
    check("dedup: manual +Z folds into existing world +Z",
          not any(s["source"] == "manual" for s in sources2),
          f"{sum(s['source'] == 'manual' for s in sources2)} manual rows")


def hash_checks(check):
    p = {"count": 64, "axes": True, "bbox_axes": False, "hole_axes": False,
         "manual": [[0.0, 0.0, 1.0]], "face_groups": [[1, 2, 3]],
         "tollerance": 0.1, "pixel": None}
    check("params_hash idempotent on identical dicts",
          params_hash(dict(p)) == params_hash(dict(p)), params_hash(p))
    q = dict(p, manual=[[0.0, 0.0, 1.0], [1.0, 0.0, 0.0]])
    check("params_hash changes when a manual axis is added",
          params_hash(p) != params_hash(q), "")


def _present(dirs, vec, deg=1.0):
    """Is `vec` (up to sign) within `deg` of any row of `dirs`?"""
    v = np.asarray(vec, float)
    v = v / (np.linalg.norm(v) or 1.0)
    return bool((np.abs(np.asarray(dirs) @ v) >= np.cos(np.radians(deg))).any())


def step_end_to_end(check):
    """Cylinder STEP -> real workdir -> compute_directions with every source.

    Each requested source contributes a vector; dedup may fold collinear
    provenances onto one row, so the robust contract is that each source's
    vector is *present* (up to sign) in the assembled set, not that a row
    carries a particular provenance label. The sidecar/antipodal/index and
    accessibility-shape invariants are asserted directly.
    """
    import pipeline
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from test_accessibility import write_step

    with tempfile.TemporaryDirectory() as tmp:
        path = write_step(os.path.join(tmp, "cyl.stp"),
                          BRepPrimAPI_MakeCylinder(6.0, 20.0).Shape())
        wd = os.path.join(tmp, "wd")
        os.makedirs(wd)
        pipeline.mesh_part(path, wd, resolution=0.8)

        group = [0, 1, 2]
        stats = pipeline.compute_directions(
            wd, count=4, axes=True, bbox_axes=True, hole_axes=True,
            manual=[[1, 1, 1]], face_groups=[group])

        dirs = np.load(os.path.join(wd, pipeline.DIRECTIONS_FILE))
        sources_path = os.path.join(wd, pipeline.DIRECTIONS_SOURCES_FILE)
        check("sidecar written", os.path.exists(sources_path), sources_path)
        with open(sources_path) as f:
            sources = json.load(f)

        check("end-to-end: antipodal over the whole array", _antipodal(dirs),
              f"{dirs.shape[0]} rows")
        check("end-to-end: sidecar index-aligned",
              len(sources) == len(dirs)
              and all(s["index"] == i for i, s in enumerate(sources)),
              f"{len(sources)} / {len(dirs)}")
        check("end-to-end: every source in the enum",
              all(s["source"] in DIRECTION_SOURCES for s in sources),
              f"{stats['sources']}")

        # each requested source's vector is present (up to sign) in the set
        normals = pipeline.load_face_normals(wd)
        group_axis = average_face_normal(normals, group)
        check("end-to-end: averaged face-group normal present",
              _present(dirs, group_axis), f"{np.round(group_axis, 3)}")
        check("end-to-end: manual [1,1,1] present",
              _present(dirs, [1, 1, 1]), "")
        with open(os.path.join(wd, pipeline.BREP_META_FILE)) as f:
            surf = json.load(f)["surface_params"]
        holes = hole_axes_from_geometry(surf)
        check("end-to-end: a hole axis was enumerated and present",
              bool(holes) and _present(dirs, holes[0][0]),
              f"{len(holes)} hole axes")

        # accessibility rows match the assembled direction count
        access = np.load(os.path.join(wd, pipeline.ACCESSIBILITY_FILE))
        check("end-to-end: accessibility rows == direction rows",
              access.shape[0] == dirs.shape[0],
              f"{access.shape[0]} / {dirs.shape[0]}")


def main():
    failures = []

    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:48s} {detail}")
        if not condition:
            failures.append(name)

    print("=== direction helpers ===")
    unit_checks(check)
    print("=== assemble_directions ===")
    assemble_checks(check)
    print("=== params_hash stability ===")
    hash_checks(check)
    print("=== STEP end-to-end ===")
    step_end_to_end(check)

    print("ALL CHECKS PASSED" if not failures
          else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
