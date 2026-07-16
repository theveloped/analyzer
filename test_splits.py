"""Analytic checks of user-driven BREP face splitting (splits.py).

Fixtures:
1. Box top-face split: two fresh ids, parent retired, exact disjoint
   partition, cut edges appear in subface_edge_pairs, original BREP edge
   pairs preserved (mapped through parents), meta fingerprint current.
2. Annulus (box minus a through-hole): a single rim-to-rim cut does not
   separate (stored, labeling unchanged); a second cut yields two pieces;
   re-splitting a piece retires its id and appends monotone fresh ids.
3. Undo/replay determinism: adding then undoing a cut reproduces
   subfaces.npy byte-identically; clear removes every sidecar.
4. Validation: same start/end, interior point, retired face id, unknown
   face id, stale mesh fingerprint; effective_face_ids fallback paths and
   sanitize_retired unit behavior.

Run from the repo root: python test_splits.py
"""
import json
import os
import tempfile

import numpy as np

import molding
import pipeline
import splits


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:40s} {detail}")
        if not condition:
            failures.append(name)
    return check


def write_step(tmp, shape):
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    path = os.path.join(tmp, "fixture.step")
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(path)
    return path


def make_box_step(tmp):
    """Plain OCC box: x,y in [-10, 10], z in [-5, 5]."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    return write_step(tmp, BRepPrimAPI_MakeBox(
        gp_Pnt(-10, -10, -5), 20, 20, 10).Shape())


def make_holed_step(tmp):
    """Box x,y in [-10, 10], z in [0, 10] minus a radius-4 through-hole
    along Z — the top face is an annulus (two boundary loops)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Pnt
    block = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, 0), 20, 20, 10).Shape()
    hole = BRepPrimAPI_MakeCylinder(4.0, 10.0).Shape()
    return write_step(tmp, BRepAlgoAPI_Cut(block, hole).Shape())


def mesh_fixture(tmp, step_path):
    workdir = os.path.join(tmp, "wd")
    os.makedirs(workdir)
    pipeline.mesh_part(step_path, workdir, subdivide=1.5)
    verts = np.load(os.path.join(workdir, pipeline.FINE_VERTS_FILE))
    faces = np.load(os.path.join(workdir, pipeline.FINE_FACES_FILE))
    ids = np.load(os.path.join(workdir, pipeline.BREP_FACES_FILE))
    return workdir, verts, faces, ids


def top_face_id(verts, faces, ids):
    """BREP id of the face containing the highest-centroid triangle."""
    cz = verts[faces].mean(axis=1)[:, 2]
    return int(ids[np.argmax(cz)])


def boundary_vertices(faces, eff, face_id):
    """Vertex ids on the boundary of an effective face's region."""
    tris = faces[eff == face_id].astype(np.int64)
    edges = np.stack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]],
                     axis=1).reshape(-1, 2)
    edges.sort(axis=1)
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    return np.unique(uniq[counts == 1])


def nearest_boundary_vertex(verts, faces, eff, face_id, target):
    candidates = boundary_vertices(faces, eff, face_id)
    d = np.linalg.norm(verts[candidates] - np.asarray(target, float), axis=1)
    return int(candidates[np.argmin(d)])


def fixture_box_split(check):
    with tempfile.TemporaryDirectory() as tmp:
        workdir, verts, faces, ids = mesh_fixture(tmp, make_box_step(tmp))
        n_brep = int(ids.max()) + 1
        top = top_face_id(verts, faces, ids)

        start = nearest_boundary_vertex(verts, faces, ids, top, (0, -10, 5))
        end = nearest_boundary_vertex(verts, faces, ids, top, (0, 10, 5))
        state = splits.add_cut(workdir, top, start, end)

        eff = np.load(os.path.join(workdir, pipeline.SUBFACES_FILE))
        created = state.cut_info[0]["created"]
        check("cut separates into two fresh ids",
              created == [n_brep, n_brep + 1]
              and state.cut_info[0]["separated"], f"created {created}")
        check("parent id retired from subfaces",
              top not in np.unique(eff), "")
        parent_tris = np.nonzero(ids == top)[0]
        piece_tris = np.nonzero((eff == n_brep) | (eff == n_brep + 1))[0]
        check("pieces partition the parent exactly",
              np.array_equal(parent_tris, piece_tris)
              and len(np.unique(eff[parent_tris])) == 2, "")
        check("pieces split by the cut line (x sign)",
              len(created) == 2 and abs(
                  float(verts[faces[eff == n_brep]][..., 0].mean())
                  + float(verts[faces[eff == n_brep + 1]][..., 0].mean()))
              < 6.0, "rough symmetry about the cut")

        pairs = np.load(os.path.join(workdir, pipeline.SUBFACE_EDGE_PAIRS_FILE))
        segs = np.load(os.path.join(workdir, pipeline.SUBFACE_EDGES_FILE))
        on_cut = (pairs[:, 0] == n_brep) & (pairs[:, 1] == n_brep + 1)
        check("cut edges appear between the pieces",
              bool(on_cut.any())
              and float(np.abs(segs[on_cut][..., 2] - 5.0).max()) < 1e-6,
              f"{int(on_cut.sum())} segments at z=5")

        old_pairs = np.load(os.path.join(workdir, pipeline.BREP_EDGE_PAIRS_FILE))
        remap = {top: {n_brep, n_brep + 1}}
        new_set = {tuple(p) for p in pairs.tolist()}
        preserved = all(
            any(tuple(sorted((a2, b2))) in new_set
                for a2 in remap.get(a, {a}) for b2 in remap.get(b, {b}))
            for a, b in {tuple(p) for p in old_pairs.tolist()})
        check("original BREP edge pairs preserved", preserved,
              f"{len(new_set)} effective pairs")

        with open(os.path.join(workdir, pipeline.SUBFACE_META_FILE)) as f:
            meta = json.load(f)
        check("meta fingerprint current",
              meta["mesh_fingerprint"] == pipeline.mesh_fingerprint(workdir)
              and meta["n_brep"] == n_brep and meta["n_effective"] == n_brep + 2
              and meta["parents"] == [top, top], "")
        check("splits fingerprint present",
              pipeline.splits_fingerprint(workdir) is not None, "")

        # straightness: a diagonal cut (worst case for grid zigzag) must
        # hug the chord between its endpoints, not wander mesh edges
        cz = verts[faces].mean(axis=1)[:, 2]
        bottom = int(ids[np.argmin(cz)])
        s2 = nearest_boundary_vertex(verts, faces, ids, bottom, (-10, -10, -5))
        e2 = nearest_boundary_vertex(verts, faces, ids, bottom, (10, 10, -5))
        state = splits.add_cut(workdir, bottom, s2, e2)
        with open(os.path.join(workdir, pipeline.FACE_SPLITS_FILE)) as f:
            path = np.asarray(json.load(f)["cuts"][-1]["path"])
        p0, p1 = verts[s2], verts[e2]
        chord = (p1 - p0) / np.linalg.norm(p1 - p0)
        rel = verts[path] - p0
        dev = np.linalg.norm(rel - np.outer(rel @ chord, chord), axis=1)
        tris = faces[ids == bottom]
        edge_len = float(np.linalg.norm(
            verts[tris[:, 0]] - verts[tris[:, 1]], axis=1).mean())
        check("diagonal cut hugs the chord",
              state.cut_info[-1]["separated"]
              and float(dev.max()) < 1.5 * edge_len,
              f"max deviation {dev.max():.2f} mm vs edge {edge_len:.2f} mm")


def fixture_annulus(check):
    with tempfile.TemporaryDirectory() as tmp:
        workdir, verts, faces, ids = mesh_fixture(tmp, make_holed_step(tmp))
        n_brep = int(ids.max()) + 1
        top = top_face_id(verts, faces, ids)

        # cut 1: outer rim to inner rim along +X — annulus stays connected
        s1 = nearest_boundary_vertex(verts, faces, ids, top, (10, 0, 10))
        e1 = nearest_boundary_vertex(verts, faces, ids, top, (4, 0, 10))
        state = splits.add_cut(workdir, top, s1, e1)
        eff = np.load(os.path.join(workdir, pipeline.SUBFACES_FILE))
        check("rim-to-rim cut does not separate",
              not state.cut_info[0]["separated"]
              and np.array_equal(eff, ids), "")
        api_state = splits.state(workdir)
        check("non-separating cut visible in state",
              len(api_state["cuts"]) == 1
              and not api_state["cuts"][0]["separated"]
              and len(api_state["cuts"][0]["polyline"]) >= 2, "")

        # cut 2: along -X — now two half-annulus pieces
        s2 = nearest_boundary_vertex(verts, faces, ids, top, (-10, 0, 10))
        e2 = nearest_boundary_vertex(verts, faces, ids, top, (-4, 0, 10))
        state = splits.add_cut(workdir, top, s2, e2)
        eff = np.load(os.path.join(workdir, pipeline.SUBFACES_FILE))
        created = state.cut_info[1]["created"]
        check("second cut separates into two pieces",
              created == [n_brep, n_brep + 1]
              and top not in np.unique(eff)
              and state.parents == [top, top], f"created {created}")

        # re-split the y<0 piece: its id retires, two more fresh ids
        lower = int(eff[np.argmin(verts[faces].mean(axis=1)[:, 1]
                                  + np.where(ids == top, 0, 1e9))])
        s3 = nearest_boundary_vertex(verts, faces, eff, lower, (0, -10, 10))
        e3 = nearest_boundary_vertex(verts, faces, eff, lower, (0, -4, 10))
        state = splits.add_cut(workdir, lower, s3, e3)
        eff2 = np.load(os.path.join(workdir, pipeline.SUBFACES_FILE))
        created = state.cut_info[2]["created"]
        check("re-split retires the piece id",
              created == [n_brep + 2, n_brep + 3]
              and lower not in np.unique(eff2)
              and state.parents == [top] * 4,
              f"created {created}, retired {lower}")
        check("untouched piece keeps its id",
              bool(np.any(eff2 == (n_brep if lower != n_brep
                                   else n_brep + 1))), "")


def fixture_undo_replay(check):
    with tempfile.TemporaryDirectory() as tmp:
        workdir, verts, faces, ids = mesh_fixture(tmp, make_box_step(tmp))
        top = top_face_id(verts, faces, ids)

        s1 = nearest_boundary_vertex(verts, faces, ids, top, (0, -10, 5))
        e1 = nearest_boundary_vertex(verts, faces, ids, top, (0, 10, 5))
        splits.add_cut(workdir, top, s1, e1)
        snapshot = open(os.path.join(workdir, pipeline.SUBFACES_FILE),
                        "rb").read()
        fp = pipeline.splits_fingerprint(workdir)

        eff = np.load(os.path.join(workdir, pipeline.SUBFACES_FILE))
        piece = int(eff[np.nonzero(ids == top)[0][0]])
        cx = float(verts[faces[eff == piece]][..., 0].mean())
        s2 = nearest_boundary_vertex(verts, faces, eff, piece, (cx, -10, 5))
        e2 = nearest_boundary_vertex(verts, faces, eff, piece, (cx, 10, 5))
        splits.add_cut(workdir, piece, s2, e2)
        check("second cut changes the labeling",
              pipeline.splits_fingerprint(workdir) != fp, "")

        splits.undo_last(workdir)
        replayed = open(os.path.join(workdir, pipeline.SUBFACES_FILE),
                        "rb").read()
        check("undo replays byte-identically",
              replayed == snapshot
              and pipeline.splits_fingerprint(workdir) == fp, "")

        splits.undo_last(workdir)
        gone = [pipeline.FACE_SPLITS_FILE, pipeline.SUBFACES_FILE,
                pipeline.SUBFACE_EDGES_FILE, pipeline.SUBFACE_EDGE_PAIRS_FILE,
                pipeline.SUBFACE_META_FILE]
        check("undoing the only cut clears all sidecars",
              not any(os.path.exists(os.path.join(workdir, f)) for f in gone)
              and pipeline.splits_fingerprint(workdir) is None, "")


def fixture_validation(check):
    with tempfile.TemporaryDirectory() as tmp:
        workdir, verts, faces, ids = mesh_fixture(tmp, make_box_step(tmp))
        top = top_face_id(verts, faces, ids)
        n_brep = int(ids.max()) + 1
        boundary = boundary_vertices(faces, ids, top)
        region_verts = np.unique(faces[ids == top])
        interior = int(np.setdiff1d(region_verts, boundary)[0])
        b0, b1 = int(boundary[0]), int(boundary[-1])

        def raises(fn, exc=ValueError):
            try:
                fn()
                return False
            except exc:
                return True

        check("same start/end rejected",
              raises(lambda: splits.add_cut(workdir, top, b0, b0)), "")
        check("interior start rejected",
              raises(lambda: splits.add_cut(workdir, top, interior, b1)), "")
        check("unknown face rejected",
              raises(lambda: splits.add_cut(workdir, n_brep + 99, b0, b1)), "")

        s = nearest_boundary_vertex(verts, faces, ids, top, (0, -10, 5))
        e = nearest_boundary_vertex(verts, faces, ids, top, (0, 10, 5))
        splits.add_cut(workdir, top, s, e)
        check("retired face rejected",
              raises(lambda: splits.add_cut(workdir, top, b0, b1)), "")

        # stale mesh fingerprint refuses mutation, state reports stale
        path = os.path.join(workdir, pipeline.FACE_SPLITS_FILE)
        data = json.load(open(path))
        data["mesh_fingerprint"] = "0" * 12
        json.dump(data, open(path, "w"))
        check("stale fingerprint refuses add_cut",
              raises(lambda: splits.add_cut(workdir, n_brep, b0, b1),
                     splits.StaleSplitsError), "")
        check("state reports stale", splits.state(workdir)["stale"], "")

        # effective_face_ids: fallback on corrupt meta, identity without
        # splits, sub-face labeling when current
        meta_path = os.path.join(workdir, pipeline.SUBFACE_META_FILE)
        meta = json.load(open(meta_path))
        eff_ids, n_eff, parents = splits.effective_face_ids(workdir)
        check("effective ids use current splits",
              n_eff == n_brep + 2 and parents == [top, top]
              and int(eff_ids.max()) == n_brep + 1, "")
        meta["mesh_fingerprint"] = "0" * 12
        json.dump(meta, open(meta_path, "w"))
        eff_ids, n_eff, parents = splits.effective_face_ids(workdir)
        check("stale meta falls back to brep ids",
              n_eff == n_brep and parents == []
              and np.array_equal(eff_ids, ids), "")
        splits.clear(workdir)
        eff_ids, n_eff, _ = splits.effective_face_ids(workdir)
        check("no splits falls back to brep ids",
              n_eff == n_brep and np.array_equal(eff_ids, ids), "")

        # sanitize_retired: a retired id reads valid-for-everything out of
        # brep_validity — sanitize turns it into an inert conflict
        eff = np.array([1, 1, 2, 2], dtype=np.int32)
        membership = np.array([1, 1, 2, 2], dtype=np.uint32)
        valid = molding.brep_validity(membership, eff, 2)
        defaults = molding.brep_defaults(membership, valid, eff)
        check("unit: retired id garbage without sanitize",
              valid[0] == 3, f"valid[0] = {valid[0]}")
        splits.sanitize_retired(valid, defaults, eff)
        check("unit: sanitize_retired flags conflict",
              valid[0] == 0 and defaults[0] == molding.DEFAULT_CONFLICT
              and valid[1] == 1 and valid[2] == 2, "")


def latest_result(workdir, process, analysis):
    import glob as glob_module
    base = os.path.join(workdir, "results", process, analysis)
    paths = sorted(glob_module.glob(os.path.join(base, "*.json")),
                   key=os.path.getmtime)
    paths = [p for p in paths if not p.endswith("_overrides.json")]
    payload = json.load(open(paths[-1]))
    arrays = np.load(paths[-1].replace(".json", ".npz"))
    return payload, arrays


def fixture_split_aware(check):
    """A conflict wall becomes two individually-valid pieces after a cut,
    for both the mold and the CNC aggregation; results stale/unstale and
    cache-hit across cut/undo."""
    import processes
    from api import manifest as manifest_api
    from api import parts as parts_api
    from processes.base import apply_defaults
    from processes.cnc import SETUPS_SCHEMA
    from processes.injection_molding import MOLD_SCHEMA

    with tempfile.TemporaryDirectory() as tmp:
        workdir = os.path.join(tmp, "wd")
        os.makedirs(workdir)
        pipeline.mesh_part(make_box_step(tmp), workdir, subdivide=1.5)
        verts = np.load(os.path.join(workdir, pipeline.FINE_VERTS_FILE))
        faces = np.load(os.path.join(workdir, pipeline.FINE_FACES_FILE))
        ids = np.load(os.path.join(workdir, pipeline.BREP_FACES_FILE))
        n_brep = int(ids.max()) + 1

        # ±Z pull with an overlap band: top/bottom fully covered, every
        # side wall only partially by each -> conflict until split at z~0
        cz = verts[faces].mean(axis=1)[:, 2]
        np.save(os.path.join(workdir, pipeline.DIRECTIONS_FILE),
                np.array([[0, 0, 1.0], [0, 0, -1.0]]))
        np.save(os.path.join(workdir, pipeline.ACCESSIBILITY_FILE),
                np.stack([cz > -1.5, cz < 1.5]))

        mold = processes.get_analysis("injection_molding", "mold_orientation")
        mold_params = apply_defaults(mold, {})
        mold.run(workdir, mold_params, None)
        payload, arrays = latest_result(workdir, "injection_molding",
                                        "mold_orientation")
        wall = int(ids[np.argmin(verts[faces].mean(axis=1)[:, 1])])
        check("wall is conflict before split",
              arrays["brep_default_0"][wall] == molding.DEFAULT_CONFLICT
              and len(arrays["brep_default_0"]) == n_brep, "")
        check("result carries the splits salt",
              "splits" in payload["params"]
              and payload["params"]["splits"] is None, "")

        s = nearest_boundary_vertex(verts, faces, ids, wall, (-10, -10, 0))
        e = nearest_boundary_vertex(verts, faces, ids, wall, (10, -10, 0))
        splits.add_cut(workdir, wall, s, e)
        mold.run(workdir, mold_params, None)
        payload2, arrays2 = latest_result(workdir, "injection_molding",
                                          "mold_orientation")
        defaults = arrays2["brep_default_0"]
        valid = arrays2["brep_valid_0"]
        eff = np.load(os.path.join(workdir, pipeline.SUBFACES_FILE))
        piece_a, piece_b = n_brep, n_brep + 1
        up_a = float(verts[faces[eff == piece_a]][..., 2].mean())
        up_b = float(verts[faces[eff == piece_b]][..., 2].mean())
        upper, lower = (piece_a, piece_b) if up_a > up_b else (piece_b, piece_a)
        check("aggregation sized to effective ids",
              len(defaults) == n_brep + 2 and len(valid) == n_brep + 2, "")
        check("upper piece assigns to side A / lower to B",
              defaults[upper] == molding.FEAT_A
              and defaults[lower] == molding.FEAT_B,
              f"upper {defaults[upper]} lower {defaults[lower]}")
        check("retired wall sanitized to conflict",
              valid[wall] == 0
              and defaults[wall] == molding.DEFAULT_CONFLICT, "")
        check("schema and fingerprint current",
              payload2["stats"]["schema"] == MOLD_SCHEMA
              and payload2["params"]["splits"]
              == pipeline.splits_fingerprint(workdir), "")

        # CNC mirror over the same synthetic accessibility
        setups = processes.get_analysis("cnc", "setups")
        setups_params = apply_defaults(setups, {})
        setups.run(workdir, setups_params, None)
        payload3, arrays3 = latest_result(workdir, "cnc", "setups")
        sdef = arrays3["brep_default_0"]
        check("cnc aggregation split-aware",
              payload3["stats"]["schema"] == SETUPS_SCHEMA
              and len(sdef) == n_brep + 2
              and sdef[upper] < molding.DEFAULT_CONFLICT
              and sdef[lower] < molding.DEFAULT_CONFLICT
              and sdef[wall] == molding.DEFAULT_CONFLICT,
              f"piece defaults {sdef[upper]}/{sdef[lower]}")

        # manifest staleness across cut / undo, cache hit after undo
        def stale_by_hash():
            part = parts_api.part_info(tmp, "wd")
            manifest = manifest_api.build_manifest(tmp, part)
            return {r["hash"]: r["stale"] for r in manifest["results"]
                    if r["analysis"] == "mold_orientation"}
        flags = stale_by_hash()
        check("manifest: pre-split stale, fresh current",
              len(flags) == 2 and sum(flags.values()) == 1, f"{flags}")

        wall2 = int(ids[np.argmax(verts[faces].mean(axis=1)[:, 1])])
        s2 = nearest_boundary_vertex(verts, faces, ids, wall2, (-10, 10, 0))
        e2 = nearest_boundary_vertex(verts, faces, ids, wall2, (10, 10, 0))
        splits.add_cut(workdir, wall2, s2, e2)
        check("second cut stales every result",
              all(stale_by_hash().values()), "")

        splits.undo_last(workdir)
        flags = stale_by_hash()
        check("undo un-stales the matching result",
              sum(flags.values()) == 1, f"{flags}")
        calls = []
        mold.run(workdir, mold_params,
                 lambda fraction, message: calls.append(message))
        check("re-run after undo is a cache hit", not calls,
              f"progress calls: {len(calls)}")


def main():
    failures = []
    check = check_factory(failures)
    print("=== fixture 1: box top-face split ===")
    fixture_box_split(check)
    print("=== fixture 2: annulus / iterative cuts ===")
    fixture_annulus(check)
    print("=== fixture 3: undo / replay determinism ===")
    fixture_undo_replay(check)
    print("=== fixture 4: validation & helpers ===")
    fixture_validation(check)
    print("=== fixture 5/6: split-aware analyses & staleness ===")
    fixture_split_aware(check)
    if failures:
        print(f"\n{len(failures)} check(s) FAILED: {failures}")
        raise SystemExit(1)
    print("\nall split checks passed")


if __name__ == "__main__":
    main()
