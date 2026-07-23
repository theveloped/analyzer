"""Export a part's geometry + semantic GD&T as an AP242 STEP file.

The inverse of ``step_import``: re-read the exact BREP from the workdir's retained
``source.stp``, re-author the ``pmi.json`` dimensions/tolerances/datums onto it
through the OpenCASCADE XCAF API, and write AP242 with GD&T enabled. No raw STEP
text is authored — everything goes through XCAF, exactly as the reader does on the
way in.

Ground rules (all established empirically against OpenCASCADE 7.9):

* Schema: ``STEPControl_Controller.Init_s()`` MUST run before ``write.step.schema``
  is settable; AP242 is enum value 5. The writer enables GD&T with
  ``SetDimTolMode(True)`` (not ``SetGDTMode`` — that is reader-only).
* Face ids in ``pmi.json`` index ``brep.iter_faces`` (TopExp_Explorer) order;
  edge ids index ``TopExp.MapShapes`` order. We invert each with the matching
  iterator so the labels line up with what the reader wrote.
* Datum reference frames need ``SetPosition`` + ``SetDatum`` + ``SetDatumToGeomTol``
  or OCCT emits a bare datum with no reference frame.
* Units: OCCT writes tolerance magnitudes against the SI base unit (metre) unless
  a dimension anchors a millimetre context, in which case a reader scales them by
  1000. We self-calibrate (write, probe one tolerance, rescale + rewrite once if
  metre-mode is detected) so the file is correct for OCCT and for other CAD.

Export is best-effort and never blocks: constructs OCCT can't serialise (see
``pmi_support``) are dropped with a warning, and the rest still exports.
"""
import json
import math
import os
from dataclasses import dataclass, field

from loguru import logger

import pmi_support

PMI_FILE = "pmi.json"
_SCHEMA_ENUM = {"AP242": 5, "AP214": 2, "AP203": 3}


@dataclass
class ExportReport:
    out_path: str
    schema: str
    counts: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    unresolved_faces: list = field(default_factory=list)

    def to_dict(self):
        return {"out_path": self.out_path, "schema": self.schema,
                "counts": self.counts, "warnings": self.warnings,
                "unresolved_faces": self.unresolved_faces}


# --- enum resolution (name -> OCP enum member, from pmi.json string vocab) ---
def _enum_map(prefix):
    from OCP import XCAFDimTolObjects as XDT
    out = {}
    full = "XCAFDimTolObjects_" + prefix
    for name in dir(XDT):
        if name.startswith(full):
            out[name[len(full):]] = getattr(XDT, name)
    return out


# --- geometry / label helpers ----------------------------------------------
def _load_source_shape(workdir):
    import brep
    import pipeline
    return brep.load_step_shape_cached(pipeline.source_step_path(workdir))


class _LabelResolver:
    """Maps 0-based pmi.json face/edge ids to XCAF sub-shape labels on the
    re-read source shape, honouring the reader's iteration-order contract."""

    def __init__(self, shape, shape_tool, top):
        import brep
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedMapOfShape
        self._faces = list(brep.iter_faces(shape))          # Explorer order
        self._edges = TopTools_IndexedMapOfShape()          # IndexedMap order
        TopExp.MapShapes_s(shape, TopAbs_EDGE, self._edges)
        self._st = shape_tool
        self._top = top
        self.unresolved_faces = []
        self.unresolved_edges = []

    def face_seq(self, ids):
        from OCP.TDF import TDF_LabelSequence
        seq = TDF_LabelSequence()
        for i in ids or []:
            if 0 <= i < len(self._faces):
                seq.Append(self._st.AddSubShape(self._top, self._faces[i]))
            else:
                self.unresolved_faces.append(i)
        return seq

    def edge_seq(self, ids):
        from OCP.TDF import TDF_LabelSequence
        from OCP.TopoDS import TopoDS
        seq = TDF_LabelSequence()
        for i in ids or []:
            if 0 <= i < self._edges.Extent():
                seq.Append(self._st.AddSubShape(
                    self._top, TopoDS.Edge_s(self._edges.FindKey(i + 1))))
            else:
                self.unresolved_edges.append(i)
        return seq


def _has(s):
    from OCP.TCollection import TCollection_HAsciiString
    return TCollection_HAsciiString(s)


# --- authoring --------------------------------------------------------------
class _Author:
    def __init__(self, doc, resolver, tol_scale):
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        self.dt = XCAFDoc_DocumentTool.DimTolTool_s(doc.Main())
        self.r = resolver
        self.tol_scale = tol_scale     # length compensation for tolerances
        self.counts = {"dimensions": 0, "tolerances": 0, "datums": 0}
        self.GT_TYPE = _enum_map("GeomToleranceType_")
        self.GT_TOV = _enum_map("GeomToleranceTypeValue_")
        self.GT_MATREQ = _enum_map("GeomToleranceMatReqModif_")
        self.GT_MODIF = _enum_map("GeomToleranceModif_")
        self.GT_ZONE = _enum_map("GeomToleranceZoneModif_")
        self.DIM_TYPE = _enum_map("DimensionType_")
        self.DIM_QUAL = _enum_map("DimensionQualifier_")
        self.DIM_MODIF = _enum_map("DimensionModif_")
        self.DAT_MODIF = _enum_map("DatumSingleModif_")

    def dimension(self, dim):
        from OCP.XCAFDoc import XCAFDoc_Dimension
        from OCP.XCAFDimTolObjects import XCAFDimTolObjects_DimensionObject
        dtype = self.DIM_TYPE.get(dim.get("type"))
        if dtype is None:
            return
        d = self.dt.AddDimension()
        o = XCAFDimTolObjects_DimensionObject()
        o.SetType(dtype)
        angular = bool(dim.get("angular"))
        if dim.get("value") is not None:
            # dimensions serialise against the model (mm) unit, so no length
            # compensation; angular values are stored as radians, read as degrees
            o.SetValue(math.radians(dim["value"]) if angular else dim["value"])
        if dim.get("upper_tolerance") is not None:
            o.SetUpperTolValue(dim["upper_tolerance"])
        if dim.get("lower_tolerance") is not None:
            o.SetLowerTolValue(dim["lower_tolerance"])
        q = self.DIM_QUAL.get(dim.get("qualifier"))
        if q is not None:
            o.SetQualifier(q)
        for m in dim.get("modifiers", []):
            if m in self.DIM_MODIF:
                o.AddModifier(self.DIM_MODIF[m])
        if dim.get("name"):
            o.SetSemanticName(_has(dim["name"]))
        XCAFDoc_Dimension.Set_s(d).SetObject(o)
        self.dt.SetDimension(self.r.face_seq(dim.get("face_ids", [])),
                             self.r.face_seq(dim.get("secondary_face_ids", [])), d)
        self.counts["dimensions"] += 1

    def tolerance(self, tol, datum_features):
        from OCP.XCAFDoc import XCAFDoc_GeomTolerance
        from OCP.XCAFDimTolObjects import XCAFDimTolObjects_GeomToleranceObject
        ttype = self.GT_TYPE.get(tol.get("type"))
        if ttype is None:
            return
        g = self.dt.AddGeomTolerance()
        o = XCAFDimTolObjects_GeomToleranceObject()
        o.SetType(ttype)
        if tol.get("value") is not None:
            o.SetValue(tol["value"] * self.tol_scale)
        tov = self.GT_TOV.get(tol.get("type_of_value"))
        if tov is not None:
            o.SetTypeOfValue(tov)
        for m in tol.get("modifiers", []):
            if m in self.GT_MODIF:
                o.AddModifier(self.GT_MODIF[m])
        mat = self.GT_MATREQ.get(tol.get("material_modifier"))
        if mat is not None:
            o.SetMaterialRequirementModifier(mat)
        zone = self.GT_ZONE.get(tol.get("zone_modifier"))
        if zone is not None:
            o.SetZoneModifier(zone)
            if tol.get("zone_value") is not None:
                o.SetValueOfZoneModifier(tol["zone_value"] * self.tol_scale)
        if tol.get("max_value") is not None:
            o.SetMaxValueModifier(tol["max_value"] * self.tol_scale)
        if tol.get("name"):
            o.SetSemanticName(_has(tol["name"]))
        XCAFDoc_GeomTolerance.Set_s(g).SetObject(o)
        self.dt.SetGeomTolerance(self.r.face_seq(tol.get("face_ids", [])), g)
        self.counts["tolerances"] += 1

        for ref in tol.get("datum_refs", []):
            # attach the datum feature's geometry when it was bridged at import;
            # keep the reference-frame slot (name + precedence) either way so the
            # A|B|C frame survives even when B/C features were not bridged
            feature = datum_features.get(ref.get("name")) or {}
            self._datum(g, ref, feature.get("face_ids"))

    def _datum(self, geomtol_label, ref, feature_faces):
        from OCP.XCAFDoc import XCAFDoc_Datum
        from OCP.XCAFDimTolObjects import XCAFDimTolObjects_DatumObject
        dl = self.dt.AddDatum()
        do = XCAFDimTolObjects_DatumObject()
        if ref.get("name"):
            do.SetName(_has(ref["name"]))
        do.SetPosition(int(ref.get("position") or 1))
        for m in ref.get("modifiers", []):
            if m in self.DAT_MODIF:
                do.AddModifier(self.DAT_MODIF[m])
        XCAFDoc_Datum.Set_s(dl).SetObject(do)
        if feature_faces:
            self.dt.SetDatum(self.r.face_seq(feature_faces), dl)
        self.dt.SetDatumToGeomTol(dl, geomtol_label)
        self.counts["datums"] += 1


# --- document build + write -------------------------------------------------
def _build_doc(shape, pmi, tol_scale):
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    top = shape_tool.AddShape(shape, False, False)

    resolver = _LabelResolver(shape, shape_tool, top)
    author = _Author(doc, resolver, tol_scale)
    datum_features = {d.get("name"): d for d in pmi.get("datums", [])}
    for dim in pmi.get("dimensions", []):
        author.dimension(dim)
    for tol in pmi.get("tolerances", []):
        author.tolerance(tol, datum_features)
    return doc, author, resolver


def _write(doc, out_path, schema):
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_Controller
    from OCP.STEPCAFControl import STEPCAFControl_Writer

    STEPControl_Controller.Init_s()                       # register static params
    Interface_Static.SetIVal_s("write.step.schema", _SCHEMA_ENUM.get(schema, 5))
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    writer.SetDimTolMode(True)                            # GD&T on (writer spelling)
    if not writer.Transfer(doc):
        raise RuntimeError("STEPCAFControl_Writer.Transfer(doc) failed")
    writer.Write(out_path)


def _max_tolerance_readback(out_path):
    """Largest geometric-tolerance value read back (for unit calibration).

    The whole document shares one length-unit mode, so the maximum authored
    magnitude maps to the maximum read-back magnitude regardless of ordering or
    of any tolerance that could not be authored — a robust ratio probe.
    """
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_GeomTolerance
    from OCP.TDF import TDF_LabelSequence
    from OCP.STEPCAFControl import STEPCAFControl_Reader

    reader = STEPCAFControl_Reader()
    reader.SetGDTMode(True)
    reader.ReadFile(out_path)
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    app.InitDocument(doc)
    reader.Transfer(doc)
    dt = XCAFDoc_DocumentTool.DimTolTool_s(doc.Main())
    labels = TDF_LabelSequence()
    dt.GetGeomToleranceLabels(labels)
    values = [abs(XCAFDoc_GeomTolerance.Set_s(labels.Value(i)).GetObject().GetValue())
              for i in range(1, labels.Length() + 1)]
    return max(values) if values else None


def _max_nominal_tolerance(pmi):
    values = [abs(t["value"]) for t in pmi.get("tolerances", []) if t.get("value")]
    return max(values) if values else None


def export_step(workdir, out_path=None, *, schema="AP242", write_report=True):
    """Export ``workdir``'s geometry + ``pmi.json`` GD&T as an AP242 STEP file.

    Returns an :class:`ExportReport`. Never raises for unsupported PMI — those are
    dropped best-effort and listed in ``report.warnings``. Raises only if the
    source geometry is missing or OCCT's transfer fails outright.
    """
    if schema not in _SCHEMA_ENUM:
        raise ValueError(f"unsupported schema {schema!r}; expected one of "
                         f"{sorted(_SCHEMA_ENUM)}")
    if out_path is None:
        out_path = os.path.join(workdir, "export", "part.ap242.stp")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    pmi_path = os.path.join(workdir, PMI_FILE)
    pmi = {}
    if os.path.exists(pmi_path):
        with open(pmi_path) as f:
            pmi = json.load(f)

    shape = _load_source_shape(workdir)

    # first pass at true scale, then self-calibrate the tolerance unit if OCCT
    # fell back to metre (x1000) for a tolerance-only document (see module docs)
    doc, author, resolver = _build_doc(shape, pmi, tol_scale=1.0)
    _write(doc, out_path, schema)

    nominal = _max_nominal_tolerance(pmi)
    if nominal:
        readback = _max_tolerance_readback(out_path)
        # metre-mode fallback reads back ~1000x the authored magnitude; mm-mode
        # reads back ~1x. Anything above the midpoint means a rescale is needed.
        if readback and readback / nominal > pmi_support.METRE_MM_FACTOR / 2:
            logger.info("GD&T exported in metre unit; rescaling tolerances "
                        "x1/{:.0f} for correct magnitudes",
                        pmi_support.METRE_MM_FACTOR)
            doc, author, resolver = _build_doc(
                shape, pmi, tol_scale=1.0 / pmi_support.METRE_MM_FACTOR)
            _write(doc, out_path, schema)

    warnings = pmi_support.roundtrip_warnings(pmi)
    unresolved = sorted(set(resolver.unresolved_faces))
    if unresolved:
        warnings.append(f"{len(unresolved)} PMI face id(s) could not be resolved "
                        f"to the source geometry and were skipped: {unresolved}")

    report = ExportReport(out_path=out_path, schema=schema, counts=author.counts,
                          warnings=warnings, unresolved_faces=unresolved)
    logger.info("exported {} ({} dims, {} tols, {} datums, {} warnings)",
                out_path, author.counts["dimensions"], author.counts["tolerances"],
                author.counts["datums"], len(warnings))

    if write_report:
        report_path = os.path.join(os.path.dirname(os.path.abspath(out_path)),
                                   "report.json")
        with open(report_path, "w") as f:
            json.dump(report.to_dict(), f, indent=1)
    return report
