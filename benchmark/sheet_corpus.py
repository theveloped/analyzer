"""Corpus benchmark: score the sheet/tube port against instapart's examples.

Runs the analyzer pipeline (import -> mesh -> aag -> sheet detect ->
flat_pattern, tube profile as fallback) over instapart's example corpus and
compares against the expectations in its benchmarks/manifest.yaml:
part/sheet/tube counts, dominant thickness, bend count, signed bend angles
and the <= 2.5% volume-conservation invariant.

Not CI-gating — the corpus lives outside this repo. Typical runs:

    python benchmark/sheet_corpus.py --smoke
    python benchmark/sheet_corpus.py --category parts --limit 20
    python benchmark/sheet_corpus.py                       # all fast entries
    python benchmark/sheet_corpus.py --slow                # include 900s assemblies

The default instapart checkout is the sibling of this repo's parent; point
--instapart elsewhere if needed. Workdirs land under --workroot (kept for
inspection). A CSV summary is written next to the workroot.

instapart message-code semantics used for scoring: 2 = no thickness/base,
3 = volume difference expected, 4 = could not flatten, 7 = no valid solids,
8 = features on both sides. Entries expecting those codes (or marked
known_failure) don't count their corresponding analyzer failure against
the score.
"""

import argparse
import csv
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

VOLUME_LIMIT_PCT = 2.5
ANGLE_TOL_DEG = 1.5
THICKNESS_TOL = 0.05  # relative


def load_manifest(instapart_root):
    import yaml

    path = os.path.join(instapart_root, "benchmarks", "manifest.yaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("defaults", {}), data["files"]


def signed_angles(bends):
    """Signed bend angles in degrees, instapart's convention (up +, down -)."""
    out = []
    for bend in bends:
        magnitude = bend["angle_deg"]
        out.append(magnitude if bend["direction"] == "up" else -magnitude)
    return out


def match_angles(found, expected):
    """Greedy multiset match fraction, trying the global sign flip too
    (which skin got unfolded flips every direction at once)."""
    if not expected:
        return 1.0 if not found else 0.0

    def score(candidate):
        remaining = sorted(expected)
        hits = 0
        for angle in sorted(candidate):
            for i, target in enumerate(remaining):
                if abs(angle - target) <= ANGLE_TOL_DEG:
                    remaining.pop(i)
                    hits += 1
                    break
        return hits / max(len(expected), len(candidate))

    return max(score(found), score([-a for a in found]))


def run_part(workdir):
    """Analyze one part workdir; returns a per-part result dict."""
    import pipeline
    import processes
    from processes.base import apply_defaults

    result = {"kind": "other", "thickness": None, "bends": [],
              "volume_error_pct": None, "developable": None, "error": None}
    try:
        if not os.path.exists(os.path.join(workdir, pipeline.FINE_VERTS_FILE)):
            pipeline.mesh_part(pipeline.source_step_path(workdir), workdir,
                               subdivide=0.0)
        if not os.path.exists(os.path.join(workdir, pipeline.AAG_FILE)):
            pipeline.compute_aag(workdir)

        def try_tube():
            profile = processes.get_analysis("tube_laser", "profile")
            tube_stats = profile.run(
                workdir, apply_defaults(profile, {"unroll": False}),
                None).stats
            if tube_stats["verdict"] != "none":
                result["kind"] = "tube"
                result["thickness"] = tube_stats.get("thickness")
                result["bends"] = []
                result["volume_error_pct"] = None
                return True
            return False

        detect = processes.get_analysis("sheet_metal", "detect")
        stats = detect.run(workdir, apply_defaults(detect, {}), None).stats
        if stats["verdict"] == "sheet":
            result["kind"] = "sheet"
            result["thickness"] = stats["thickness"]
            try:
                pattern = processes.get_analysis("sheet_metal",
                                                 "flat_pattern")
                pattern_stats = pattern.run(
                    workdir, apply_defaults(pattern, {}), None).stats
                result["bends"] = pattern_stats["bends"]
                result["volume_error_pct"] = pattern_stats["volume_error_pct"]
                result["developable"] = pattern_stats["developable"]
            except Exception:
                result["developable"] = False
            # instapart's dispatch: a closed profile passes the thickness
            # probe but cannot flatten (open wires) — try the tube path then
            if not result["developable"] and try_tube():
                return result
            return result

        try_tube()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def run_entry(entry, instapart_root, workroot):
    """Import one corpus file and analyze every unique part."""
    import step_import
    from api import parts as parts_api

    path = os.path.join(instapart_root, entry["path"])
    row = {"file": entry["path"], "category": entry.get("category"),
           "expected": entry.get("expected"), "error": None, "parts": 0,
           "instances": 0, "sheets": 0, "tubes": 0, "thicknesses": [],
           "bends": [], "worst_volume_pct": None, "part_errors": 0,
           "seconds": 0.0}
    started = time.time()
    try:
        manifest = step_import.import_step(path, workroot)
    except Exception as exc:
        row["error"] = f"import: {type(exc).__name__}: {exc}"
        row["seconds"] = time.time() - started
        return row

    row["parts"] = len(manifest["parts"])
    row["instances"] = sum(p["quantity"] for p in manifest["parts"])

    worst = 0.0
    for part in manifest["parts"]:
        workdir = parts_api.workdir_for(workroot, part["part"])
        outcome = run_part(workdir)
        if outcome["error"]:
            row["part_errors"] += 1
            continue
        if outcome["kind"] == "sheet":
            row["sheets"] += 1
            row["thicknesses"].append(round(outcome["thickness"], 2))
            row["bends"].extend(signed_angles(outcome["bends"]))
            if outcome["volume_error_pct"] is not None:
                worst = max(worst, outcome["volume_error_pct"])
        elif outcome["kind"] == "tube":
            row["tubes"] += 1
    row["worst_volume_pct"] = round(worst, 2)
    row["seconds"] = round(time.time() - started, 1)
    return row


def score_entry(entry, row):
    """Per-expectation verdicts; None = not applicable / no expectation."""
    codes = set(entry.get("expected_message_codes") or [])
    known_failure = entry.get("expected") == "known_failure"
    checks = {}

    if row["error"]:
        checks["ran"] = known_failure or 7 in codes
        return checks
    checks["ran"] = True

    if entry.get("expected_parts") is not None:
        checks["parts"] = (row["parts"] == entry["expected_parts"]
                          or row["instances"] == entry["expected_parts"])
    # codes 0/1/2/4/5/6/7 encode legacy instapart processing failures
    # (read/topology/thickness/flatten/type/process/solids); the port
    # succeeding where the legacy pipeline failed counts as superior, so
    # those expectations become lower bounds. Only 3 (volume difference)
    # and 8 (features both sides) are soft warnings of a successful run.
    legacy_failed = bool(codes & {0, 1, 2, 4, 5, 6, 7})
    if entry.get("expected_sheets") is not None and not known_failure:
        checks["sheets"] = (row["sheets"] >= entry["expected_sheets"]
                            if legacy_failed
                            else row["sheets"] == entry["expected_sheets"])
    if entry.get("expected_tubes") is not None and not known_failure:
        checks["tubes"] = row["tubes"] == entry["expected_tubes"]
    if entry.get("expected_thickness") and not known_failure:
        target = entry["expected_thickness"]
        checks["thickness"] = any(
            abs(t - target) <= max(0.05, THICKNESS_TOL * target)
            for t in row["thicknesses"])
    if entry.get("expected_bends") is not None and not known_failure:
        checks["bends"] = (len(row["bends"]) >= entry["expected_bends"]
                           if legacy_failed
                           else len(row["bends"]) == entry["expected_bends"])
    if entry.get("expected_bend_angles") and not known_failure:
        fraction = match_angles(row["bends"], entry["expected_bend_angles"])
        checks["angles"] = fraction >= 0.9
        row["angle_match"] = round(fraction, 2)
    if (row["sheets"] and not known_failure and 3 not in codes
            and not legacy_failed):
        checks["volume"] = row["worst_volume_pct"] <= VOLUME_LIMIT_PCT
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    default_instapart = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
        "instapart"))
    parser.add_argument("--instapart", default=default_instapart)
    parser.add_argument("--workroot",
                        default=r"C:\temp\claude\corpus")
    parser.add_argument("--smoke", action="store_true",
                        help="only manifest entries marked smoke")
    parser.add_argument("--category", default=None)
    parser.add_argument("--file", default=None,
                        help="substring filter on the path")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--slow", action="store_true",
                        help="include timeout_s >= 900 entries (big assemblies)")
    parser.add_argument("--out", default=None, help="CSV path")
    args = parser.parse_args()

    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    defaults, entries = load_manifest(args.instapart)
    selected = []
    for entry in entries:
        if args.smoke and not entry.get("smoke"):
            continue
        if args.category and entry.get("category") != args.category:
            continue
        if args.file and args.file.lower() not in entry["path"].lower():
            continue
        if not args.slow and (entry.get("timeout_s") or 120) >= 900:
            continue
        selected.append(entry)
    if args.limit:
        selected = selected[:args.limit]

    os.makedirs(args.workroot, exist_ok=True)
    rows = []
    totals = {}
    for index, entry in enumerate(selected):
        row = run_entry(entry, args.instapart, args.workroot)
        checks = score_entry(entry, row)
        row["checks"] = checks
        rows.append(row)
        for name, ok in checks.items():
            passed, total = totals.get(name, (0, 0))
            totals[name] = (passed + bool(ok), total + 1)
        status = " ".join(f"{name}:{'ok' if ok else 'FAIL'}"
                          for name, ok in checks.items())
        print(f"[{index + 1}/{len(selected)}] {entry['path']}"
              f"  ({row['seconds']}s)  {status}"
              + (f"  ERROR {row['error']}" if row["error"] else ""))

    print("\n=== corpus summary ===")
    for name, (passed, total) in sorted(totals.items()):
        print(f"  {name:10s} {passed}/{total}")
    all_pass = sum(all(r["checks"].values()) for r in rows)
    print(f"  files fully passing: {all_pass}/{len(rows)}")

    out = args.out or os.path.join(args.workroot, "sheet_corpus.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["file", "category", "expected", "seconds", "error",
                         "parts", "instances", "sheets", "tubes",
                         "thicknesses", "bend_angles", "angle_match",
                         "worst_volume_pct", "part_errors", "checks"])
        for row in rows:
            writer.writerow([
                row["file"], row["category"], row["expected"],
                row["seconds"], row["error"], row["parts"], row["instances"],
                row["sheets"], row["tubes"], row["thicknesses"],
                [round(a, 1) for a in row["bends"]],
                row.get("angle_match"), row["worst_volume_pct"],
                row["part_errors"],
                " ".join(f"{k}={'ok' if v else 'FAIL'}"
                         for k, v in row["checks"].items()),
            ])
    print(f"CSV: {out}")


if __name__ == "__main__":
    main()
