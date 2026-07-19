"""Dependency resolver: auto-run prerequisites, reuse, cascade invalidation.

Plain script (``python test_resolver.py``, not pytest). Drives
``processes.resolver.ensure`` on the small STEP fixture and asserts that the
prep prerequisites are built on demand, reused while current, and rebuilt when
an upstream (the mesh) changes — the content-addressed cascade.

Prints ``[OK ]`` / ``[FAIL]`` lines (count those, not the exit code).
"""

import json
import os
import shutil
import tempfile
import time

import processes.resolver as resolver
from processes.prep import aag_current, directions_current, mesh_current

FIXTURE = os.path.join("tests", "testpart_42.stp")

_results = []


def check(name, ok, detail=""):
    _results.append(bool(ok))
    tag = "[OK ]" if ok else "[FAIL]"
    print(f"  {tag} {name}{(': ' + detail) if detail else ''}")


def _mtime(path):
    return os.path.getmtime(path) if os.path.exists(path) else None


def main():
    quiet = lambda fraction, message: None
    root = tempfile.mkdtemp(prefix="resolver_test_")
    workdir = os.path.join(root, "part")
    os.makedirs(workdir)
    shutil.copyfile(FIXTURE, os.path.join(workdir, "source.stp"))
    with open(os.path.join(workdir, "part.json"), "w") as handle:
        json.dump({"name": "testpart_42", "source": "source.stp"}, handle)

    fine = os.path.join(workdir, "fine_faces.npy")
    acc = os.path.join(workdir, "accessibility.npy")
    aag = os.path.join(workdir, "aag.npz")

    try:
        print("=== prerequisites auto-run on demand ===")
        check("start: no mesh", not os.path.exists(fine))
        # requesting a directions-dependent analysis with only the source
        # present must build prep/mesh then prep/directions, in order
        resolver.ensure(workdir, "injection_molding/mold_orientation", {}, quiet)
        check("mesh built as prerequisite", os.path.exists(fine))
        check("directions built as prerequisite", os.path.exists(acc))
        check("prep gates report current",
              mesh_current(workdir, {}) and directions_current(workdir, {}))

        print("=== prerequisites reused while current ===")
        fine_m, acc_m = _mtime(fine), _mtime(acc)
        time.sleep(0.05)
        resolver.ensure(workdir, "injection_molding/slenderness", {}, quiet)
        check("mesh not rebuilt", _mtime(fine) == fine_m)
        check("directions not rebuilt (reused)", _mtime(acc) == acc_m)

        print("=== aag built, then reused as a prerequisite ===")
        resolver.ensure(workdir, "prep/aag", {}, quiet)  # explicit target -> runs
        check("aag current after run", aag_current(workdir, {}))
        aag_m = _mtime(aag)
        time.sleep(0.05)
        resolver.ensure(workdir, "cnc/features", {}, quiet)  # requires prep/aag
        check("aag reused as prerequisite (not rebuilt)", _mtime(aag) == aag_m)

        print("=== cascade invalidation on re-mesh ===")
        # re-mesh at a different resolution -> new mesh fingerprint -> every
        # downstream prep gate must flip stale
        resolver.ensure(workdir, "prep/mesh", {"subdivide": 0.8}, quiet)
        check("directions stale after re-mesh",
              not directions_current(workdir, {}))
        check("aag stale after re-mesh", not aag_current(workdir, {}))
        acc_m2 = _mtime(acc)
        time.sleep(0.05)
        # a downstream request now rebuilds the stale intermediate automatically
        resolver.ensure(workdir, "injection_molding/mold_orientation", {}, quiet)
        check("directions rebuilt after re-mesh", _mtime(acc) != acc_m2)
        check("directions current again", directions_current(workdir, {}))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print()
    passed = sum(_results)
    total = len(_results)
    if all(_results):
        print(f"ALL CHECKS PASSED ({passed}/{total})")
    else:
        print(f"{total - passed} FAILURE(S) ({passed}/{total})")
    raise SystemExit(0 if all(_results) else 1)


if __name__ == "__main__":
    main()
