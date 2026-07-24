"""Editor write-path for ``pmi.json``: validate an authored PMI payload, re-derive
round-trip warnings, and persist it.

OCP-free (no meshlib/OCCT) — the same class as the AP242 export route, so the API
serves a save synchronously without the jobs queue. Kept framework-free (only
``jsonschema`` + ``pmi_support``) so the CLI and tests can call ``save_pmi``
directly. Face/edge ids in an authored payload are 0-based ``brep.iter_faces``
ids into ``source.stp`` — the exact space the viewer picks on and
``step_export._LabelResolver`` inverts, so no import-time bridging is involved.

``pmi_support`` stays the single source of truth for what survives an AP242
round-trip; validation here rejects only *structural* nonsense and unknown
vocabulary, never a merely lossy construct (those are surfaced as warnings, the
same way the reader and exporter surface them).
"""

import json
import os

import jsonschema

import pmi_support

PMI_FILE = "pmi.json"
PMI_SCHEMA = pmi_support.PMI_SCHEMA

# --- authoring vocabulary --------------------------------------------------
# The 15 characteristic names step_export authors (OCP GeomToleranceType enum
# names), matching the frontend ControlFrame GDT_SYMBOL map. Coaxiality is
# accepted but warned as writer-unsupported (pmi_support), never blocked.
TOLERANCE_TYPES = frozenset({
    "Straightness", "Flatness", "CircularityOrRoundness", "Cylindricity",
    "ProfileOfLine", "ProfileOfSurface", "Angularity", "Perpendicularity",
    "Parallelism", "Position", "Concentricity", "Coaxiality", "Symmetry",
    "CircularRunout", "TotalRunout",
})
MATERIAL_MODIFIERS = frozenset({"M", "L", "S"})
# ISO 286 fundamental deviation letters (fit class, e.g. H7 / n6)
FIT_DEVIATIONS = frozenset({
    "A", "B", "C", "CD", "D", "E", "EF", "F", "FG", "G", "H", "J", "JS", "K",
    "M", "N", "P", "R", "S", "T", "U", "V", "X", "Y", "Z", "ZA", "ZB", "ZC",
})

# --- jsonschema for a pmi.json payload -------------------------------------
_STR = {"type": ["string", "null"]}
_NUM = {"type": ["number", "null"]}
_INT_LIST = {"type": "array", "items": {"type": "integer", "minimum": 0}}
_STR_LIST = {"type": "array", "items": {"type": "string"}}

_DATUM_REF = {
    "type": "object",
    "properties": {"name": _STR, "position": {"type": "integer"},
                   "modifiers": _STR_LIST},
    "required": ["name"],
}
_TOLERANCE = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"}, "kind": {"type": "string"},
        "name": _STR, "type": _STR, "value": _NUM, "type_of_value": _STR,
        "modifiers": _STR_LIST, "material_modifier": _STR, "zone_modifier": _STR,
        "zone_value": _NUM, "max_value": _NUM,
        "datum_refs": {"type": "array", "items": _DATUM_REF},
        "datum_names": _STR_LIST, "face_ids": _INT_LIST, "edge_ids": _INT_LIST,
    },
    "required": ["id", "face_ids"],
}
_DIMENSION = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"}, "kind": {"type": "string"},
        "name": _STR, "type": _STR, "value": _NUM,
        "upper_tolerance": _NUM, "lower_tolerance": _NUM, "qualifier": _STR,
        "modifiers": _STR_LIST, "angular": {"type": "boolean"},
        "face_ids": _INT_LIST, "secondary_face_ids": _INT_LIST,
        "edge_ids": _INT_LIST,
        "fit_class": {"type": ["object", "null"], "properties": {
            "deviation": {"type": "string"}, "grade": {"type": "integer"},
            "hole": {"type": "boolean"}}, "required": ["deviation", "grade"]},
        "thread": {"type": ["object", "null"], "properties": {
            "designation": {"type": "string"}, "class": _STR},
            "required": ["designation"]},
    },
    "required": ["id", "face_ids"],
}
_DATUM = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"}, "kind": {"type": "string"}, "name": _STR,
        "face_ids": _INT_LIST, "edge_ids": _INT_LIST,
    },
    "required": ["id", "face_ids"],
}
PMI_JSONSCHEMA = {
    "type": "object",
    "properties": {
        "schema": {"type": "integer"},
        "dimensions": {"type": "array", "items": _DIMENSION},
        "tolerances": {"type": "array", "items": _TOLERANCE},
        "datums": {"type": "array", "items": _DATUM},
        "warnings": _STR_LIST, "degraded": {"type": "boolean"},
    },
    "required": ["dimensions", "tolerances", "datums"],
}


def validate_pmi(payload):
    """Validate an authored PMI payload and return a normalized ``pmi`` dict
    (schema set, the three entity families present). Raises ``ValueError`` with
    a human-readable message on any structural or vocabulary error.
    """
    if not isinstance(payload, dict):
        raise ValueError("PMI payload must be a JSON object")
    pmi = {
        "schema": PMI_SCHEMA,
        "dimensions": payload.get("dimensions") or [],
        "tolerances": payload.get("tolerances") or [],
        "datums": payload.get("datums") or [],
    }
    try:
        jsonschema.validate(pmi, PMI_JSONSCHEMA)
    except jsonschema.ValidationError as error:
        where = "/".join(str(p) for p in error.absolute_path)
        raise ValueError(f"invalid PMI payload: {error.message}"
                         + (f" (at {where})" if where else ""))

    # ids identify a feature for highlighting/editing — unique across families
    ids = [e["id"] for kind in ("dimensions", "tolerances", "datums")
           for e in pmi[kind]]
    if len(ids) != len(set(ids)):
        raise ValueError("entity ids must be unique across the document")

    for tol in pmi["tolerances"]:
        t = tol.get("type")
        if t is not None and t not in TOLERANCE_TYPES:
            raise ValueError(
                f"unknown tolerance type {t!r} — must be one of "
                f"{', '.join(sorted(TOLERANCE_TYPES))}")
        mat = tol.get("material_modifier")
        if mat is not None and mat not in MATERIAL_MODIFIERS:
            raise ValueError(
                f"unknown material modifier {mat!r} — must be one of M, L, S")

    for dim in pmi["dimensions"]:
        fit = dim.get("fit_class")
        if fit is not None:
            dev = str(fit.get("deviation", "")).upper()
            if dev not in FIT_DEVIATIONS:
                raise ValueError(f"unknown fit deviation {fit.get('deviation')!r}")
            grade = fit.get("grade")
            if not isinstance(grade, int) or not (0 <= grade <= 18):
                raise ValueError("fit grade must be an integer IT0..IT18")
    return pmi


def save_pmi(workdir, payload):
    """Validate ``payload`` and write it to ``workdir/pmi.json``, re-deriving the
    round-trip warnings (the same field the reader annotates at import). Returns
    a summary dict ``{schema, counts, warnings}``. Raises ``ValueError`` on an
    invalid payload; the file is only replaced once validation passes.
    """
    pmi = validate_pmi(payload)
    pmi.pop("degraded", None)  # an authored payload is never a degraded stub
    pmi["warnings"] = pmi_support.roundtrip_warnings(pmi)

    path = os.path.join(workdir, PMI_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(pmi, handle)
    os.replace(tmp, path)  # atomic: never leave a half-written pmi.json

    return {
        "schema": PMI_SCHEMA,
        "counts": {"tolerances": len(pmi["tolerances"]),
                   "dimensions": len(pmi["dimensions"]),
                   "datums": len(pmi["datums"])},
        "warnings": pmi["warnings"],
    }
