"""Cross-side vocabulary tests: every frozenset in Python against the TS union
that mirrors it.

The rule this enforces is in AGENTS.md and docs/CONCEPTS.md §3: a fixed set of
legal strings is checked where the value ENTERS, and a TS union mirroring one is
kept honest by a test rather than by a comment. The failure mode being designed
out is drift that nothing notices — `FieldRole` was missing `fold` for a whole
schema version, which cost the frontend nothing loudly and the reader everything.

Parsing, not importing: there is no TS runtime here. Each mirror is declared as a
NAMED exported union (`export type FieldRole = 'a' | 'b';`) precisely so this file
can find it with one regex — an inline union inside an interface member is not
mirrorable, so promote it before adding a pair below.

    python test_vocab.py
"""

import os
import re

import processes
import route as route_mod
from processes import base

PASSED = 0
FAILED = 0

ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(ROOT, "frontend", "src")


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"[OK ] {name}")
    else:
        FAILED += 1
        print(f"[FAIL] {name}")
        if detail:
            print(f"      {detail}")


def ts_union(rel_path, name):
    """The string literals of `export type <name> = 'a' | 'b' | …;`.

    Returns None when the declaration is absent — a renamed or deleted union is
    a failure to report, not a KeyError to crash on.
    """
    path = os.path.join(FRONTEND, rel_path)
    with open(path, encoding="utf-8") as f:
        source = f.read()
    match = re.search(rf"export type {name}\s*=\s*(.*?);", source, re.S)
    if match is None:
        return None
    body = match.group(1)
    # only the alternation itself: a union of literals has nothing else in it,
    # and a union of non-literals is not a vocabulary
    if re.sub(r"'[^']*'|\s|\|", "", body):
        return None
    return set(re.findall(r"'([^']*)'", body))


def compare(name, py_values, rel_path, ts_name):
    """One mirror: the Python vocabulary against its TS union."""
    want = set(py_values)
    got = ts_union(rel_path, ts_name)
    if got is None:
        check(name, False, f"no literal union `{ts_name}` in {rel_path}")
        return
    missing = sorted(want - got)
    extra = sorted(got - want)
    detail = ""
    if missing:
        detail += f"missing from TS: {missing} "
    if extra:
        detail += f"not in Python: {extra}"
    check(name, not missing and not extra, detail.strip())


TYPES = "api/types.ts"


def test_field_vocabularies():
    compare("PARAM_TYPES == ParamType",
            base.PARAM_TYPES, TYPES, "ParamType")
    compare("FIELD_ASSOCIATIONS == FieldAssociation",
            base.FIELD_ASSOCIATIONS, TYPES, "FieldAssociation")
    compare("FIELD_ROLES == FieldRole",
            base.FIELD_ROLES, TYPES, "FieldRole")
    compare("FIELD_DTYPES == FieldDtype",
            base.FIELD_DTYPES, TYPES, "FieldDtype")


def test_route_vocabularies():
    compare("OPERATION_KINDS == OperationKind",
            route_mod.OPERATION_KINDS, TYPES, "OperationKind")
    compare("STATS_RULES == StatsRule",
            route_mod.STATS_RULES, "v2/checks/evaluators.ts", "StatsRule")


def test_salts_have_implementations():
    """AnalysisDef validates the name; the resolver must own the computation."""
    from processes import resolver
    check("KNOWN_SALTS == resolver._OPT_IN_SALTS",
          set(base.KNOWN_SALTS) == set(resolver._OPT_IN_SALTS),
          f"{sorted(base.KNOWN_SALTS)} vs {sorted(resolver._OPT_IN_SALTS)}")
    declared = {salt for process in processes.REGISTRY.values()
                for analysis in process.analyses for salt in analysis.salts}
    check("every declared salt is a known salt",
          declared <= set(base.KNOWN_SALTS),
          f"unknown: {sorted(declared - set(base.KNOWN_SALTS))}")


def main():
    test_field_vocabularies()
    test_route_vocabularies()
    test_salts_have_implementations()

    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED == 0:
        print("ALL CHECKS PASSED")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
