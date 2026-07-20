"""Checks of semantic PMI / GD&T extraction (step_import._extract_pmi).

Fixtures: the NIST MBE PMI validation set under tests/nist/. The AP242
variant carries semantic GD&T (dimensions, geometric tolerances, datums);
the AP203 variant is geometry-only and is the no-PMI negative control.

Asserts: pmi.json schema, non-empty tolerance/dimension/datum families, the
schema-2 enrichment (ordered datum reference frame with precedence, and at
least one geometric-tolerance modifier), dimension +/- tolerances, and that
the AP203 control produces no pmi.json. Face ids that cannot be bridged to
the re-read workdir geometry are dropped upstream (symmetric parts), so the
checks tolerate datum_refs whose names are absent from the datums list.

Run from the repo root: python test_pmi.py
"""
import glob
import json
import os
import shutil
import sys
import tempfile

import step_import

AP242 = "tests/nist/nist_ctc_01_asme1_ap242.stp"
AP203 = "tests/nist/nist_ctc_01_asme1_ap203.stp"


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:44s} {detail}")
        if not condition:
            failures.append(name)
    return check


def _load_pmi(root):
    """The merged pmi.json across all child workdirs of an import root."""
    merged = {"schema": None, "dimensions": [], "tolerances": [], "datums": []}
    for path in glob.glob(os.path.join(root, "parts", "*", "pmi.json")):
        data = json.load(open(path))
        merged["schema"] = data.get("schema")
        for kind in ("dimensions", "tolerances", "datums"):
            merged[kind].extend(data.get(kind, []))
    return merged


def fixture_ap242(check):
    root = tempfile.mkdtemp(prefix="pmitest_")
    try:
        manifest = step_import.import_step(AP242, root=root)
        check("AP242 import not PMI-degraded", not manifest["pmi_degraded"])
        pmi = _load_pmi(root)

        check("pmi.json schema is current",
              pmi["schema"] == step_import.PMI_SCHEMA,
              f"schema={pmi['schema']}")
        check("tolerances extracted", len(pmi["tolerances"]) > 0,
              f"{len(pmi['tolerances'])} tolerances")
        check("dimensions extracted", len(pmi["dimensions"]) > 0,
              f"{len(pmi['dimensions'])} dimensions")
        check("datums extracted", len(pmi["datums"]) > 0,
              f"{len(pmi['datums'])} datums")

        # every tolerance carries the schema-2 fields (even if empty)
        schema2_fields = all(
            "datum_refs" in t and "modifiers" in t
            and "material_modifier" in t and "zone_modifier" in t
            for t in pmi["tolerances"])
        check("tolerances carry schema-2 fields", schema2_fields)

        # ordered datum reference frame: some tolerance references >=2 datums
        # with monotonically non-decreasing precedence positions
        def ordered(t):
            refs = t.get("datum_refs", [])
            pos = [r["position"] for r in refs if r["position"] > 0]
            return len(refs) >= 2 and pos == sorted(pos) and len(pos) >= 2
        check("ordered datum reference frame present",
              any(ordered(t) for t in pmi["tolerances"]),
              next((str([r["name"] + str(r["position"])
                         for r in t["datum_refs"]])
                    for t in pmi["tolerances"] if ordered(t)), ""))

        # at least one geometric-tolerance modifier decoded (e.g. All_Around)
        check("geometric-tolerance modifier decoded",
              any(t.get("modifiers") for t in pmi["tolerances"]),
              next((str(t["modifiers"]) for t in pmi["tolerances"]
                    if t.get("modifiers")), ""))

        # tolerance magnitudes recovered from the STEP (OCCT returns 0 here)
        by_name = {t.get("name"): t for t in pmi["tolerances"]}
        check("every tolerance has a non-zero value",
              all(t["value"] for t in pmi["tolerances"]),
              str({t.get("name"): t["value"] for t in pmi["tolerances"]}))
        check("Position magnitude recovered (~0.75)",
              abs((by_name.get("Position.1") or {}).get("value", 0) - 0.75) < 1e-6)
        check("Flatness magnitude recovered (~0.2)",
              abs((by_name.get("Flatness.1") or {}).get("value", 0) - 0.2) < 1e-6)
        check("Perpendicularity magnitude recovered (~1.5)",
              abs((by_name.get("Perpendicularity.1") or {}).get("value", 0) - 1.5) < 1e-6)

        # dimensions carry the schema-2 fields and at least one +/- tolerance
        check("dimensions carry schema-2 fields",
              all("qualifier" in d and "modifiers" in d and "angular" in d
                  for d in pmi["dimensions"]))
        check("a dimension has a +/- tolerance",
              any(d["upper_tolerance"] is not None for d in pmi["dimensions"]))

        # face ids are ints (0-based BREP ids) where present
        check("tolerance face_ids are ints",
              all(all(isinstance(f, int) for f in t.get("face_ids", []))
                  for t in pmi["tolerances"]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def fixture_ap203_control(check):
    root = tempfile.mkdtemp(prefix="pmitest_")
    try:
        step_import.import_step(AP203, root=root)
        pmi_files = glob.glob(os.path.join(root, "parts", "*", "pmi.json"))
        check("AP203 control writes no pmi.json", not pmi_files,
              f"{len(pmi_files)} files")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    for path in (AP242, AP203):
        if not os.path.exists(path):
            print(f"MISSING FIXTURE: {path} — run from the repo root")
            sys.exit(1)

    failures = []
    check = check_factory(failures)

    print("=== fixture: NIST CTC-01 AP242 (semantic PMI) ===")
    fixture_ap242(check)
    print("=== fixture: NIST CTC-01 AP203 (no-PMI control) ===")
    fixture_ap203_control(check)

    if failures:
        print(f"{len(failures)} CHECKS FAILED: {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
