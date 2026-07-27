"""Which semantic GD&T survives an OpenCASCADE AP242 round-trip — one source of
truth, shared by the reader (``step_import``), the exporter (``step_export``) and
the CLI/API. Framework-free (no OCP, no fastapi) so anything can import it.

The support matrix is the empirical result of an OpenCASCADE AP242 GD&T
round-trip study (author each construct through XCAF, write AP242, re-import,
diff field-by-field). Keys are the *string* values as they appear in
``pmi.json`` — the same vocabulary the frontend ``PmiData`` types mirror — so no
OCCT enums are needed here.

Nothing in this module blocks: it only classifies and phrases warnings. Callers
decide when to surface them (the reader annotates ``pmi.json`` at import; the
exporter reports what it actually dropped).
"""

# --- unit handling ---------------------------------------------------------
# OCCT serializes GD&T length magnitudes against the SI *base* unit (metre) when
# a document carries no dimension to anchor a millimetre length-measure context;
# a plain reader then scales such tolerances by 1000 on the way back in. The
# exporter self-calibrates around this, but the constant lives here so the
# behaviour is documented in one place.
METRE_MM_FACTOR = 1000.0

# --- pmi.json schema version (single source; the frontend PmiData type + the
# reader/exporter/editor all key off this) ----------------------------------
# 4: added top-level "warnings" (round-trip losses) + degraded-PMI stub.
# 5: dimensions gained fit_class (ISO tolerance class, e.g. H7/n6) and thread
#    (e.g. M6x1-6H).
# Bump when pmi.json entry fields change — then also update the frontend
# PmiData mirror (frontend/src/api/types.ts). AGENTS.md hard rule 4.
PMI_SCHEMA = 5

# --- what OCCT's writer never emits ----------------------------------------
# The base tolerance still exports; only these decorations are lost, so an entity
# carrying one is exported best-effort (everything supported, minus the note).
WRITER_UNSUPPORTED_TOLERANCE_TYPES = frozenset({"Coaxiality"})
WRITER_UNSUPPORTED_ZONE_MODIFIERS = frozenset({"Projected"})
WRITER_UNSUPPORTED_MODIFIERS = frozenset({"All_Around", "All_Over"})
# max_value modifier (a non-null tolerance.max_value) is likewise dropped.

# --- what OCCT's writer emits but its own reader drops ----------------------
# Present and correct in the file (interoperable with other CAD), lost only on an
# OCCT re-import — worth flagging so a round-trip through *this* stack is honest.
READER_UNSUPPORTED = "dimension qualifier (min/max/avg)"

# --- never preserved on tolerances/dimensions (datum identifiers survive) ----
NAME_NOT_PRESERVED = "semantic names on dimensions/tolerances"


def _tol_label(tol):
    name = tol.get("name")
    return f"{tol.get('type') or 'tolerance'}{f' {name!r}' if name else ''}"


def tolerance_dropped_features(tol):
    """List of (field, reason) an AP242 export will drop for one tolerance.

    Empty when the tolerance round-trips whole. Best-effort: the tolerance and
    its datum frame still export; only the listed decorations are lost.
    """
    dropped = []
    if tol.get("type") in WRITER_UNSUPPORTED_TOLERANCE_TYPES:
        dropped.append(("type", f"tolerance type {tol.get('type')!r} is not "
                                 "written by OpenCASCADE (no STEP entity emitted)"))
    if tol.get("zone_modifier") in WRITER_UNSUPPORTED_ZONE_MODIFIERS:
        dropped.append(("zone_modifier",
                        f"{tol['zone_modifier'].lower()} tolerance-zone modifier"))
    bad_mods = [m for m in (tol.get("modifiers") or [])
                if m in WRITER_UNSUPPORTED_MODIFIERS]
    for m in bad_mods:
        dropped.append(("modifiers", f"{m.replace('_', ' ').lower()} modifier"))
    if tol.get("max_value") is not None:
        dropped.append(("max_value", "maximum-value modifier"))
    return dropped


def dimension_dropped_features(dim):
    """List of (field, reason) an AP242 round-trip will drop for one dimension."""
    dropped = []
    if dim.get("qualifier"):
        dropped.append(("qualifier",
                        "dimension qualifier is written to the file but dropped "
                        "by OpenCASCADE's own reader on re-import"))
    if dim.get("thread"):
        dropped.append(("thread", "thread specification has no AP242 semantic "
                        "entity — it is kept in the analyzer but not exported"))
    if dim.get("fit_class"):
        dropped.append(("fit_class", "ISO tolerance class (fit) is written to the "
                        "file but dropped by OpenCASCADE's own reader on re-import"))
    # a location is inherently between two features; OCCT will not serialise a
    # single-sided one (no secondary reference), so it is lost on export
    if ((dim.get("type") or "").startswith("Location")
            and not dim.get("secondary_face_ids") and not dim.get("edge_ids")):
        dropped.append(("references", "location dimension without a second "
                        "reference is not carried by AP242 export"))
    return dropped


def roundtrip_warnings(pmi):
    """Human-readable, entity-scoped warnings for a ``pmi.json`` payload.

    Used both by the reader (informational: what a future export would lose) and
    by the exporter (what this export actually dropped). Never raises; a missing
    or empty payload yields ``[]``.
    """
    warnings = []
    if not pmi:
        return warnings

    for tol in pmi.get("tolerances", []):
        for _field, reason in tolerance_dropped_features(tol):
            warnings.append(f"{_tol_label(tol)}: {reason} — not carried by AP242 "
                            "export")

    for dim in pmi.get("dimensions", []):
        for _field, reason in dimension_dropped_features(dim):
            warnings.append(f"{dim.get('type') or 'dimension'}: {reason}")

    named = sum(1 for kind in ("tolerances", "dimensions")
                for e in pmi.get(kind, []) if e.get("name"))
    if named:
        warnings.append(f"{named} {NAME_NOT_PRESERVED} are not preserved on export "
                        "(datum identifiers are); feature identity is anchored to "
                        "face ids instead")
    return warnings
