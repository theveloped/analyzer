"""Checks of the exact surface normals stored by the mesh stage.

Meshes tests/AligatorDrafted.stp into a temp workdir and validates the
freeform (non-quadric) normal path introduced for bspline/extrusion faces:

A. Every stored normal is unit length.
B. Children of one coarse tessellation facet on a freeform face carry
   DIFFERENT normals (the facet-frozen fallback made them identical — this
   check fails by construction on the old code).
C. A sample of freeform-face normals matches central-difference normals
   evaluated on the true surface (up to the per-face sign vote).
D. Per freeform BREP face, the stored normals agree in sign with the facet
   normals (area-weighted).

Run from the repo root: python test_normals.py
"""
import json
import os
import sys
import tempfile

import numpy as np

import brep
import pipeline

STEP_PATH = "tests/AligatorDrafted.stp"
FREEFORM = {"bezier", "bspline", "revolution", "extrusion", "offset", "other"}


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:48s} {detail}")
        if not condition:
            failures.append(name)
    return check


def facet_normals(verts, faces):
    tri = verts[faces]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return normals / np.maximum(
        np.linalg.norm(normals, axis=1, keepdims=True), 1e-30)


def finite_difference_normals(shape, brep_ids, uvs, sample):
    """Central-difference surface normals at the sampled faces' UVs."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    faces_list = list(brep.iter_faces(shape))
    adaptors = {}
    out = np.zeros((len(sample), 3))
    for row, fine in enumerate(sample):
        fid = int(brep_ids[fine])
        if fid not in adaptors:
            adaptors[fid] = BRepAdaptor_Surface(faces_list[fid])
        adaptor = adaptors[fid]
        u, v = uvs[row]
        h_u = max(1e-5 * (adaptor.LastUParameter() - adaptor.FirstUParameter()),
                  1e-9)
        h_v = max(1e-5 * (adaptor.LastVParameter() - adaptor.FirstVParameter()),
                  1e-9)

        def value(uu, vv):
            p = adaptor.Value(uu, vv)
            return np.array([p.X(), p.Y(), p.Z()])

        du = value(u + h_u, v) - value(u - h_u, v)
        dv = value(u, v + h_v) - value(u, v - h_v)
        n = np.cross(du, dv)
        out[row] = n / max(np.linalg.norm(n), 1e-30)
    return out


def main():
    failures = []
    check = check_factory(failures)

    with tempfile.TemporaryDirectory() as tmp:
        workdir = os.path.join(tmp, "wd")
        os.makedirs(workdir)
        pipeline.mesh_part(STEP_PATH, workdir, subdivide=1.0)

        stored = np.load(os.path.join(workdir, "normals.npy"))
        brep_ids = np.load(os.path.join(workdir, "brep_faces.npy"))
        meta = json.load(open(os.path.join(workdir, "brep_meta.json")))
        verts, faces = pipeline.load_mesh_arrays(workdir)
        facet = facet_normals(verts.astype(np.float64), faces)

        types = meta["surface_types"]
        freeform_ids = {fid for fid, t in enumerate(types) if t in FREEFORM}
        target = np.isin(brep_ids, list(freeform_ids))
        idx = np.flatnonzero(target)
        counts = {t: types.count(t) for t in sorted(set(types))}
        check("part exercises freeform surfaces", idx.size > 0,
              f"{idx.size} fine faces on {counts}")

        lengths = np.linalg.norm(stored.astype(np.float64), axis=1)
        check("stored normals are unit length",
              bool(np.abs(lengths - 1).max() < 1e-5),
              f"max |len - 1| = {np.abs(lengths - 1).max():.2e}")

        # B: children of one coarse facet must not share a frozen normal.
        # Coplanar children share the facet normal, so group by it; a curved
        # surface must show angular spread inside groups of several children.
        keys = np.round(facet[idx] / 1e-6).astype(np.int64)
        keys = np.concatenate([brep_ids[idx, None], keys], axis=1)
        _, group, counts_per = np.unique(keys, axis=0, return_inverse=True,
                                         return_counts=True)
        spreads = []
        for g in np.flatnonzero(counts_per >= 4)[:200]:
            members = idx[group == g]
            group_normals = stored[members].astype(np.float64)
            cos = group_normals @ group_normals.T
            spreads.append(np.degrees(np.arccos(np.clip(cos.min(), -1, 1))))
        check("normals vary within one coarse facet",
              bool(spreads) and float(max(spreads)) > 0.01,
              f"max in-facet spread {max(spreads):.3f} deg over "
              f"{len(spreads)} facets" if spreads else "no multi-child facet")

        # C: accuracy against the true surface via finite differences —
        # recover each sampled face's UV the same way the pipeline does
        shape = brep.load_step_shape(STEP_PATH)
        mesh_meta = json.load(open(os.path.join(workdir, "mesh_meta.json")))
        (cverts, cfaces, cids, _, csurface_params,
         corner_uv) = brep.mesh_shape(shape,
                                      deflection=mesh_meta["deflection"])
        parents = np.arange(len(cfaces), dtype=np.int32)
        sverts, sfaces, tags = brep.subdivide_tagged(
            cverts, cfaces, np.stack([cids, parents], axis=1),
            mesh_meta["subdivide"])
        sids, sparents = tags[:, 0], tags[:, 1]
        check("re-tessellation reproduces the workdir mesh",
              sfaces.shape == faces.shape
              and bool(np.array_equal(sids, brep_ids)), f"{len(sfaces)} faces")

        rng = np.random.default_rng(42)
        sample = rng.choice(idx, size=min(200, idx.size), replace=False)
        tri = sverts[sfaces[sample]]
        centroids = tri.mean(axis=1)
        corners = sverts[cfaces[sparents[sample]]]
        e0, e1 = corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
        d = centroids - corners[:, 0]
        d00 = np.einsum("ij,ij->i", e0, e0)
        d01 = np.einsum("ij,ij->i", e0, e1)
        d11 = np.einsum("ij,ij->i", e1, e1)
        denom = d00 * d11 - d01 * d01
        wb = (d11 * np.einsum("ij,ij->i", d, e0)
              - d01 * np.einsum("ij,ij->i", d, e1)) / denom
        wc = (d00 * np.einsum("ij,ij->i", d, e1)
              - d01 * np.einsum("ij,ij->i", d, e0)) / denom
        puv = corner_uv[sparents[sample]]
        uvs = (puv[:, 0] + wb[:, None] * (puv[:, 1] - puv[:, 0])
               + wc[:, None] * (puv[:, 2] - puv[:, 0]))

        reference = finite_difference_normals(shape, brep_ids, uvs, sample)
        agreement = np.abs(np.einsum(
            "ij,ij->i", stored[sample].astype(np.float64), reference))
        worst = np.degrees(np.arccos(np.clip(agreement.min(), -1, 1)))
        check("normals match the true surface (up to sign)",
              worst < 0.5, f"worst angle {worst:.3f} deg over {len(sample)}")

        # D: sign sanity — outward per the facet vote
        sign_ok = True
        for fid in freeform_ids:
            members = np.flatnonzero(brep_ids == fid)
            if not members.size:
                continue
            tri = verts[faces[members]].astype(np.float64)
            areas = np.linalg.norm(
                np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
            vote = float(np.sum(areas * np.einsum(
                "ij,ij->i", stored[members].astype(np.float64),
                facet[members])))
            sign_ok &= vote > 0
        check("stored normals agree in sign with the facets", sign_ok, "")

    print("ALL CHECKS PASSED" if not failures
          else "FAILURES:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
