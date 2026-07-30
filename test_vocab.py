"""Cross-side vocabulary tests: every frozenset in Python against the TS union
that mirrors it, and the shipped route templates against both.

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
import typing

import api.schemas as schemas
import plans
import processes
from processes import base

PASSED = 0
FAILED = 0

ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(ROOT, "frontend", "src")
ROUTES = os.path.join(ROOT, "catalogue", "routes")


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


def test_plan_vocabularies():
    compare("OPERATION_KINDS == OperationKind",
            plans.OPERATION_KINDS, TYPES, "OperationKind")
    compare("DECISION_STATES == DecisionState",
            plans.DECISION_STATES, TYPES, "DecisionState")
    compare("DISPOSITION_STATES == DispositionState",
            plans.DISPOSITION_STATES, TYPES, "DispositionState")
    # a decision kind exists exactly when the backend can project its `value`
    compare("DECISION_PROJECTIONS == DecisionKind",
            plans.DECISION_PROJECTIONS, TYPES, "DecisionKind")
    compare("STATS_RULES == StatsRule",
            plans.STATS_RULES, "v2/checks/evaluators.ts", "StatsRule")


def test_request_literal():
    """The Pydantic Literal is a third copy of the disposition states."""
    field = schemas.DispositionRequest.model_fields["state"]
    literal = set(typing.get_args(field.annotation))
    check("DispositionRequest.state Literal == DISPOSITION_STATES",
          literal == set(plans.DISPOSITION_STATES),
          f"{sorted(literal)} vs {sorted(plans.DISPOSITION_STATES)}")


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


def _route_files():
    if not os.path.isdir(ROUTES):
        return []
    return [os.path.join(ROUTES, n) for n in sorted(os.listdir(ROUTES))
            if n.endswith((".yaml", ".yml"))]


def test_route_templates():
    """The shipped templates, read as text — no YAML dependency for a grep.

    `validate_plan` rejects a bad kind or rule the moment a route is
    instantiated, but that only fires for a route somebody runs. These
    assertions cover the templates as authored.
    """
    files = _route_files()
    check("route templates found", bool(files), f"looked in {ROUTES}")
    known = {f"{p.id}/{a.id}" for p in processes.REGISTRY.values()
             for a in p.analyses}
    for path in files:
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        analyses = set(re.findall(r"^\s*analysis:\s*(\S+)", text, re.M))
        unknown = sorted(analyses - known)
        check(f"{name}: analyses exist in the registry", not unknown,
              f"unknown: {unknown}")
        kinds = set(re.findall(r"^\s*kind:\s*(\w+)", text, re.M))
        # `kind: stats` inside a policy is the policy's kind, not an operation's
        kinds -= {"stats"}
        bad = sorted(kinds - set(plans.OPERATION_KINDS))
        check(f"{name}: operation kinds are legal", not bad, f"unknown: {bad}")
        rules = set(re.findall(r"rule:\s*(\w+)", text))
        bad = sorted(rules - set(plans.STATS_RULES))
        check(f"{name}: stats rules are legal", not bad, f"unknown: {bad}")


def main():
    test_field_vocabularies()
    test_plan_vocabularies()
    test_request_literal()
    test_salts_have_implementations()
    test_route_templates()

    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED == 0:
        print("ALL CHECKS PASSED")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
