"""STEP import front-end: assembly structure, face colors/names and PMI.

Port of instapart's explode.TreeBuilder + attributes.py onto OCP. Reads a
STEP file through XCAF (STEPCAFControl_Reader) to get what the plain
geometry reader drops: the assembly tree with per-instance placements,
face/part colors, face names and semantic PMI (dimensions, geometric
tolerances, datums), with instapart's documented retry-without-PMI fallback
for files that crash OCCT's GD&T transfer.

Artifacts written into part working directories:
- ``assembly.json`` (assembly workdir): nested instance tree — per instance
  a translation + rotation quaternion — plus the unique part list with
  their content-addressed part ids and quantities.
- ``face_attrs.json`` (part workdir): ``{"part_color": ..., "faces":
  {face_id: {color, name, pmi_refs}}}`` keyed by 0-based analyzer BREP face
  ids (the brep_faces.npy / aag.npz convention).
- ``pmi.json`` (part workdir): dimensions/tolerances/datums with 0-based
  face ids and canonical edge ids (TopExp.MapShapes order, matching aag).

XCAF attributes reference faces of the in-memory prototype shapes, while a
part workdir's ids come from re-reading its own source.stp — a STEP
write/read round-trip is not guaranteed to preserve face order. Ids are
therefore bridged geometrically: every face is signed by (area, centroid),
every edge by (length, centroid), and prototype ids remap onto the re-read
shape by nearest signature. Identical signatures on distinct faces are
ambiguous and dropped with a warning rather than guessed.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field

import numpy as np
from loguru import logger

import pmi_support

FACE_ATTRS_FILE = "face_attrs.json"
PMI_FILE = "pmi.json"
# pmi.json schema version lives in pmi_support (the framework-free single
# source shared with the exporter and the editor write-path). Re-exported here
# so existing step_import.PMI_SCHEMA references keep working.
PMI_SCHEMA = pmi_support.PMI_SCHEMA
ASSEMBLY_FILE = "assembly.json"


# ---------------------------------------------------------------------------
# document reading

@dataclass
class ImportedDoc:
    doc: object
    shape_tool: object
    color_tool: object
    pmi_degraded: bool = False
    source_path: str = None


def read_document(path, *, pmi=True):
    """Read a STEP file into an XCAF document (colors, names, layers, PMI).

    Retries without GD&T when the PMI transfer crashes OCCT (degenerate
    annotation directions) — geometry, colors and names still load, only
    the PMI labels are lost; the result carries ``pmi_degraded``.
    """
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-CAF"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetLayerMode(True)
    reader.SetNameMode(True)
    reader.SetMatMode(True)
    # face names ride on subshape labels; off by default in OCCT
    Interface_Static.SetIVal_s("read.stepcaf.subshapes.name", 1)
    if pmi:
        # view mode binds the semantic GD&T entities to the shapes they
        # annotate; the graphical presentation shapes are never read out
        reader.SetGDTMode(True)
        reader.SetViewMode(True)

    status = reader.ReadFile(str(path))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise ValueError(f"failed to read STEP file: {path}")
    try:
        reader.Transfer(doc)
    except Exception:
        if not pmi:
            raise
        logger.warning("GD&T transfer failed; retrying without PMI")
        degraded = read_document(path, pmi=False)
        degraded.pmi_degraded = True
        return degraded

    return ImportedDoc(doc=doc, shape_tool=shape_tool, color_tool=color_tool,
                       source_path=str(path))


def label_entry(label):
    """Tag entry string of a TDF label (e.g. '0:1:1:2'), usable as a key."""
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDF import TDF_Tool

    entry = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, entry)
    return entry.ToCString()


def _label_name(label):
    """User-visible name of a TDF label, or None."""
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDataStd import TDataStd_Name

    # IsAttribute guard first: OCP's FindAttribute hard-crashes (access
    # violation, not an exception) when the label lacks the attribute
    if not label.IsAttribute(TDataStd_Name.GetID_s()):
        return None
    attr = TDataStd_Name()
    if not label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        return None
    name = TCollection_AsciiString(attr.Get()).ToCString().strip()
    return name or None


def _sanitize(name):
    keep = "".join(c if (c.isalnum() or c in "-_. ") else "_"
                   for c in (name or ""))
    return keep.strip() or "part"


def _trsf_dict(location):
    """JSON-safe placement of a TopLoc_Location: translation + rotation
    quaternion (x, y, z, w)."""
    trsf = location.Transformation()
    translation = trsf.TranslationPart()
    q = trsf.GetRotation()
    return {"translation": [translation.X(), translation.Y(),
                            translation.Z()],
            "quaternion": [q.X(), q.Y(), q.Z(), q.W()]}


# ---------------------------------------------------------------------------
# assembly tree

@dataclass
class Prototype:
    """One unique referenced part (instances share it)."""
    label: object
    entry: str
    name: str
    shape: object            # untransformed TopoDS shape (its own frame)
    count: int = 0
    index: int = 0
    face_attrs: dict = field(default_factory=dict)   # map_id -> attrs
    part_color: tuple = None
    pmi: dict = None                                  # filled by extract_pmi
    face_map: object = None                           # alive during extract
    edge_map: object = None


def _has_faces(shape):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    return shape is not None and not shape.IsNull() and TopExp_Explorer(
        shape, TopAbs_FACE).More()


def build_tree(idoc):
    """Walk the XCAF assembly structure.

    Returns (roots, prototypes): nested instance nodes referencing unique
    prototypes by index, instapart's reference/dedup semantics — instances
    of the same prototype label share one Prototype with count > 1.
    """
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    shape_tool = idoc.shape_tool
    prototypes = []
    by_entry = {}

    def get_prototype(label):
        entry = label_entry(label)
        if entry not in by_entry:
            proto = Prototype(
                label=label, entry=entry,
                name=_sanitize(_label_name(label)),
                shape=XCAFDoc_ShapeTool.GetShape_s(label),
                index=len(prototypes))
            prototypes.append(proto)
            by_entry[entry] = proto
        return by_entry[entry]

    def walk(label, location, level):
        name = _label_name(label)
        if XCAFDoc_ShapeTool.IsAssembly_s(label):
            node = {"name": _sanitize(name), "part": None,
                    "transform": _trsf_dict(location), "components": []}
            components = TDF_LabelSequence()
            XCAFDoc_ShapeTool.GetComponents_s(label, components)
            for i in range(1, components.Length() + 1):
                component = components.Value(i)
                if not XCAFDoc_ShapeTool.IsReference_s(component):
                    continue
                referred = TDF_Label()
                XCAFDoc_ShapeTool.GetReferredShape_s(component, referred)
                child_location = location.Multiplied(
                    XCAFDoc_ShapeTool.GetLocation_s(component))
                child = walk(referred, child_location, level + 1)
                if child is not None:
                    child["name"] = _sanitize(
                        _label_name(component) or child["name"])
                    node["components"].append(child)
            return node

        if XCAFDoc_ShapeTool.IsSimpleShape_s(label):
            # GD&T view mode transfers graphical annotation shapes as extra
            # roots — wireframe compounds with no faces are not parts
            shape = XCAFDoc_ShapeTool.GetShape_s(label)
            if not _has_faces(shape):
                logger.debug(f"skipping face-less shape '{name}' "
                             "(annotation geometry)")
                return None
            proto = get_prototype(label)
            proto.count += 1
            return {"name": proto.name, "part": proto.index,
                    "transform": _trsf_dict(location), "components": []}

        return None

    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    nodes = []
    for i in range(1, roots.Length() + 1):
        node = walk(roots.Value(i), TopLoc_Location(), 0)
        if node is not None:
            nodes.append(node)
    return nodes, prototypes


# ---------------------------------------------------------------------------
# colors and names (per prototype, keyed by 1-based face-map ids)

def _shape_color(color_tool, shape):
    """Surface color of a shape, falling back to its generic color."""
    from OCP.Quantity import Quantity_Color
    from OCP.XCAFDoc import XCAFDoc_ColorType

    color = Quantity_Color()
    if color_tool.GetColor(shape, XCAFDoc_ColorType.XCAFDoc_ColorSurf, color):
        return (color.Red(), color.Green(), color.Blue())
    if color_tool.GetColor(shape, XCAFDoc_ColorType.XCAFDoc_ColorGen, color):
        return (color.Red(), color.Green(), color.Blue())
    return None


def extract_face_attributes(idoc, proto):
    """Fill proto.part_color and proto.face_attrs ({map_id: {...}}).

    Only explicit face-level data creates an entry; a single identical name
    stamped on every face is an exporter artifact (CATIA writes its body
    name on each face) and is dropped.
    """
    from OCP.TDF import TDF_LabelSequence
    from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    face_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(proto.shape, TopAbs_FACE, face_map)
    edge_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(proto.shape, TopAbs_EDGE, edge_map)
    proto.face_map = face_map
    proto.edge_map = edge_map

    attributes = {}

    def get(face_id):
        return attributes.setdefault(
            face_id, {"color": None, "name": None, "pmi_refs": []})

    proto.part_color = _shape_color(idoc.color_tool, proto.shape)

    for face_id in range(1, face_map.Extent() + 1):
        face_color = _shape_color(idoc.color_tool, face_map.FindKey(face_id))
        if face_color is not None and face_color != proto.part_color:
            get(face_id)["color"] = face_color

    sub_labels = TDF_LabelSequence()
    XCAFDoc_ShapeTool.GetSubShapes_s(proto.label, sub_labels)
    for i in range(1, sub_labels.Length() + 1):
        sub_label = sub_labels.Value(i)
        name = _label_name(sub_label)
        # most exporters fill the ADVANCED_FACE name with 'NONE'
        if not name or name == "NONE":
            continue
        sub_shape = XCAFDoc_ShapeTool.GetShape_s(sub_label)
        if sub_shape is None or sub_shape.IsNull():
            continue
        if sub_shape.ShapeType() != TopAbs_FACE:
            continue
        face_id = face_map.FindIndex(sub_shape)
        if face_id > 0:
            get(face_id)["name"] = name

    names = [a["name"] for a in attributes.values() if a["name"]]
    if names and len(names) == face_map.Extent() and len(set(names)) == 1:
        for face_id in list(attributes):
            attributes[face_id]["name"] = None
            entry = attributes[face_id]
            if entry["color"] is None and not entry["pmi_refs"]:
                del attributes[face_id]

    proto.face_attrs = attributes


# ---------------------------------------------------------------------------
# PMI (semantic GD&T)

def _enum_names(module, prefix):
    names = {}
    for name in dir(module):
        if name.startswith(prefix):
            try:
                names[int(getattr(module, name))] = name[len(prefix):]
            except (TypeError, ValueError):
                continue
    return names


def _seq_names(sequence, name_map):
    """Decode an OCP XCAFDimTolObjects_*ModifiersSequence (1-based, Length/Value)
    to a list of enum names, dropping the empty "None" sentinel."""
    out = []
    try:
        for i in range(1, sequence.Length() + 1):
            name = name_map.get(int(sequence.Value(i)))
            if name and name != "None":
                out.append(name)
    except Exception:
        pass
    return out


def _resolve_references(idoc, by_entry, ref_labels):
    """Resolve PMI reference labels to (prototype, face_ids, edge_ids).

    One PMI entity can reference shapes of several parts; ids are only
    meaningful relative to their own part, so the dominant owner (most
    resolved references) wins and the rest is dropped.
    """
    from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    owners = []
    ids_by_owner = {}

    for i in range(1, ref_labels.Length() + 1):
        ref_label = ref_labels.Value(i)
        owner = by_entry.get(label_entry(ref_label))
        if owner is None:
            owner = by_entry.get(label_entry(ref_label.Father()))
        if owner is None or owner.face_map is None:
            continue

        if owner not in owners:
            owners.append(owner)
            ids_by_owner[id(owner)] = ([], [])
        face_ids, edge_ids = ids_by_owner[id(owner)]

        shape = XCAFDoc_ShapeTool.GetShape_s(ref_label)
        if shape is None or shape.IsNull():
            continue
        if shape.ShapeType() == TopAbs_FACE:
            face_id = owner.face_map.FindIndex(shape)
            if face_id > 0:
                face_ids.append(face_id)
        elif shape.ShapeType() == TopAbs_EDGE:
            edge_id = owner.edge_map.FindIndex(shape)
            if edge_id > 0:
                edge_ids.append(edge_id)

    if not owners:
        return None, [], []
    owner = max(owners, key=lambda o: sum(map(len, ids_by_owner[id(o)])))
    if len(owners) > 1:
        logger.debug(f"PMI references span {len(owners)} parts; "
                     f"keeping {owner.name}")
    face_ids, edge_ids = ids_by_owner[id(owner)]
    return owner, face_ids, edge_ids


def _proto_pmi(proto):
    if proto.pmi is None:
        proto.pmi = {"dimensions": [], "tolerances": [], "datums": []}
    return proto.pmi


def _tag_faces(proto, face_ids, pmi_id):
    for face_id in face_ids:
        proto.face_attrs.setdefault(
            face_id, {"color": None, "name": None, "pmi_refs": []}
        )["pmi_refs"].append(pmi_id)


def _read_step_gdt_magnitudes(path):
    """Recover geometric-tolerance zone magnitudes straight from the STEP text,
    keyed by the tolerance's name (e.g. ``'Position.1' -> 0.75``).

    OpenCASCADE's XCAF reader returns ``0`` for the tolerance value on files
    that encode the magnitude as a complex ``MEASURE_WITH_UNIT(LENGTH_MEASURE(x))``
    (confirmed on the NIST AP242 set). The value is unambiguous in the raw STEP:
    ``*_TOLERANCE('<name>','<desc>',#<mag>,…)`` references a length measure. This
    resolves that reference by entity id. Best-effort — returns ``{}`` on any
    trouble; the name matches ``GeomToleranceObject.GetSemanticName()``.
    """
    import re
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        data = text.split("DATA;", 1)[1] if "DATA;" in text else text
        records = {int(m.group(1)): m.group(2) for m in re.finditer(
            r"#(\d+)\s*=\s*(.*?);\s*(?=#\d+\s*=|ENDSEC)", data, re.S)}

        def measure(entity_id):
            body = records.get(entity_id, "")
            m = re.search(r"LENGTH_MEASURE\(\s*([-\d.eE+]+)\s*\)", body)
            return float(m.group(1)) if m else None

        values = {}
        for body in records.values():
            # any *_TOLERANCE record with a non-empty name and a magnitude ref
            m = re.search(
                r"\b[A-Z_]*TOLERANCE\(\s*'([^']+)'\s*,\s*'[^']*'\s*,\s*#(\d+)", body)
            if m:
                v = measure(int(m.group(2)))
                if v is not None:
                    values[m.group(1)] = v
        return values
    except Exception:
        logger.warning("could not parse GD&T magnitudes from STEP text")
        return {}


def extract_pmi(idoc, prototypes):
    """Extract semantic PMI and attach it to the prototypes whose faces it
    annotates (map-id keyed; write_part_artifacts remaps to workdir ids).

    Never raises: attribute extraction must not break geometry processing.
    """
    try:
        _extract_pmi(idoc, prototypes)
    except Exception:
        logger.exception("PMI extraction failed")


def _extract_pmi(idoc, prototypes):
    from OCP import XCAFDimTolObjects
    from OCP.TDF import TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_Dimension, XCAFDoc_Datum, XCAFDoc_DocumentTool

    dimension_types = _enum_names(
        XCAFDimTolObjects, "XCAFDimTolObjects_DimensionType_")
    tolerance_types = _enum_names(
        XCAFDimTolObjects, "XCAFDimTolObjects_GeomToleranceType_")
    tolerance_value_types = _enum_names(
        XCAFDimTolObjects, "XCAFDimTolObjects_GeomToleranceTypeValue_")
    # semantic modifiers / qualifiers (schema 2) — see the control-frame panel
    geom_modifs = _enum_names(
        XCAFDimTolObjects, "XCAFDimTolObjects_GeomToleranceModif_")
    matreq_modifs = _enum_names(
        XCAFDimTolObjects, "XCAFDimTolObjects_GeomToleranceMatReqModif_")
    zone_modifs = _enum_names(
        XCAFDimTolObjects, "XCAFDimTolObjects_GeomToleranceZoneModif_")
    datum_modifs = _enum_names(
        XCAFDimTolObjects, "XCAFDimTolObjects_DatumSingleModif_")
    dim_qualifiers = _enum_names(
        XCAFDimTolObjects, "XCAFDimTolObjects_DimensionQualifier_")
    dim_modifs = _enum_names(
        XCAFDimTolObjects, "XCAFDimTolObjects_DimensionModif_")

    by_entry = {proto.entry: proto for proto in prototypes}
    dimtol_tool = XCAFDoc_DocumentTool.DimTolTool_s(idoc.doc.Main())
    pmi_id = 0
    # OCCT leaves GetValue()==0 for tolerances whose magnitude is a complex
    # MEASURE_WITH_UNIT; recover those from the STEP text, keyed by name.
    step_magnitudes = (_read_step_gdt_magnitudes(idoc.source_path)
                       if getattr(idoc, "source_path", None) else {})

    # -- dimensions --------------------------------------------------------
    dim_labels = TDF_LabelSequence()
    dimtol_tool.GetDimensionLabels(dim_labels)
    for i in range(1, dim_labels.Length() + 1):
        label = dim_labels.Value(i)
        try:
            dimension_object = XCAFDoc_Dimension.Set_s(label).GetObject()
        except Exception:
            logger.warning(f"could not read dimension object {i}")
            continue

        dimension_type = dimension_types.get(int(dimension_object.GetType()))
        if dimension_type == "DimensionPresentation":
            continue  # graphical placeholder, no semantics

        pmi_id += 1
        dimension = {"id": pmi_id, "kind": "dimension",
                     "type": dimension_type,
                     "value": float(dimension_object.GetValue()),
                     "upper_tolerance": None, "lower_tolerance": None,
                     "qualifier": None, "modifiers": [],
                     "angular": "Angular" in (dimension_type or "")}
        try:
            if dimension_object.IsDimWithPlusMinusTolerance():
                dimension["upper_tolerance"] = float(
                    dimension_object.GetUpperTolValue())
                dimension["lower_tolerance"] = float(
                    dimension_object.GetLowerTolValue())
            elif dimension_object.IsDimWithRange():
                dimension["upper_tolerance"] = float(
                    dimension_object.GetUpperBound()) - dimension["value"]
                dimension["lower_tolerance"] = float(
                    dimension_object.GetLowerBound()) - dimension["value"]
        except Exception:
            pass
        try:
            if dimension_object.HasQualifier():
                qual = dim_qualifiers.get(int(dimension_object.GetQualifier()))
                dimension["qualifier"] = qual if qual != "None" else None
            dimension["modifiers"] = _seq_names(
                dimension_object.GetModifiers(), dim_modifs)
        except Exception:
            pass

        first, second = TDF_LabelSequence(), TDF_LabelSequence()
        dimtol_tool.GetRefShapeLabel_s(label, first, second)
        proto, face_ids, edge_ids = _resolve_references(idoc, by_entry, first)
        second_proto, second_face_ids, _ = _resolve_references(
            idoc, by_entry, second)
        proto = proto or second_proto
        if second_proto is not None and second_proto is not proto:
            second_face_ids = []

        dimension["face_ids"] = face_ids
        dimension["secondary_face_ids"] = second_face_ids
        dimension["edge_ids"] = edge_ids
        if proto:
            _proto_pmi(proto)["dimensions"].append(dimension)
            _tag_faces(proto, face_ids, pmi_id)
            _tag_faces(proto, second_face_ids, pmi_id)

    # -- geometric tolerances ----------------------------------------------
    # OCP wraps XCAFDoc_GeomTolerance directly (pythonocc 7.9 did not, hence
    # instapart's XCAFDimTolObjects_Tool detour) — read per label
    from OCP.XCAFDoc import XCAFDoc_GeomTolerance

    tolerance_labels = TDF_LabelSequence()
    dimtol_tool.GetGeomToleranceLabels(tolerance_labels)

    for i in range(1, tolerance_labels.Length() + 1):
        label = tolerance_labels.Value(i)
        pmi_id += 1
        tolerance = {"id": pmi_id, "kind": "tolerance", "type": None,
                     "name": None, "value": None, "type_of_value": None,
                     "modifiers": [], "material_modifier": None,
                     "zone_modifier": None, "zone_value": None,
                     "max_value": None, "datum_refs": [], "datum_names": []}
        tolerance_object = None
        try:
            tolerance_object = XCAFDoc_GeomTolerance.Set_s(label).GetObject()
            tolerance["type"] = tolerance_types.get(
                int(tolerance_object.GetType()))
            tolerance["value"] = float(tolerance_object.GetValue())
            tolerance["type_of_value"] = tolerance_value_types.get(
                int(tolerance_object.GetTypeOfValue()))
            name = tolerance_object.GetSemanticName()
            tolerance["name"] = (name.ToCString() if name is not None
                                 and hasattr(name, "ToCString") else None)
        except Exception:
            logger.warning(f"could not read tolerance object {i}")

        # OCCT reports 0 for magnitudes it can't parse — backfill from the STEP
        if not tolerance["value"] and tolerance["name"] in step_magnitudes:
            tolerance["value"] = step_magnitudes[tolerance["name"]]

        if tolerance_object is not None:
            try:
                tolerance["modifiers"] = _seq_names(
                    tolerance_object.GetModifiers(), geom_modifs)
                mat = matreq_modifs.get(
                    int(tolerance_object.GetMaterialRequirementModifier()))
                tolerance["material_modifier"] = mat if mat != "None" else None
                zone = zone_modifs.get(int(tolerance_object.GetZoneModifier()))
                tolerance["zone_modifier"] = zone if zone != "None" else None
                if tolerance["zone_modifier"]:
                    tolerance["zone_value"] = float(
                        tolerance_object.GetValueOfZoneModifier())
                max_value = float(tolerance_object.GetMaxValueModifier())
                tolerance["max_value"] = max_value if max_value else None
            except Exception:
                logger.warning(f"could not read modifiers of tolerance {i}")

        # ordered datum reference frame (primary/secondary/tertiary + modifiers)
        datum_labels = TDF_LabelSequence()
        dimtol_tool.GetDatumWithObjectOfTolerLabels_s(label, datum_labels)
        for j in range(1, datum_labels.Length() + 1):
            try:
                datum_object = XCAFDoc_Datum.Set_s(
                    datum_labels.Value(j)).GetObject()
                name = datum_object.GetName()
                name = (str(name.ToCString()) if name is not None
                        and hasattr(name, "ToCString")
                        else str(name) if name is not None else None)
                tolerance["datum_refs"].append({
                    "name": name,
                    "position": int(datum_object.GetPosition()),
                    "modifiers": _seq_names(
                        datum_object.GetModifiers(), datum_modifs)})
            except Exception:
                logger.warning(f"could not read datum of tolerance {i}")
        # precedence order; unset positions (0) sort last, input order preserved
        tolerance["datum_refs"].sort(
            key=lambda r: r["position"] if r["position"] > 0 else 99)
        tolerance["datum_names"] = [r["name"] for r in tolerance["datum_refs"]
                                    if r["name"]]

        first, second = TDF_LabelSequence(), TDF_LabelSequence()
        dimtol_tool.GetRefShapeLabel_s(label, first, second)
        proto, face_ids, edge_ids = _resolve_references(idoc, by_entry, first)
        tolerance["face_ids"] = face_ids
        tolerance["edge_ids"] = edge_ids
        if proto:
            _proto_pmi(proto)["tolerances"].append(tolerance)
            _tag_faces(proto, face_ids, pmi_id)

    # -- datums (merged by name per part) ----------------------------------
    datum_labels = TDF_LabelSequence()
    dimtol_tool.GetDatumLabels(datum_labels)
    merged = {}
    for i in range(1, datum_labels.Length() + 1):
        label = datum_labels.Value(i)
        try:
            datum_object = XCAFDoc_Datum.Set_s(label).GetObject()
        except Exception:
            logger.warning(f"could not read datum object {i}")
            continue
        name = datum_object.GetName()
        name = (str(name.ToCString()) if name is not None
                and hasattr(name, "ToCString") else None)

        first, second = TDF_LabelSequence(), TDF_LabelSequence()
        dimtol_tool.GetRefShapeLabel_s(label, first, second)
        proto, face_ids, edge_ids = _resolve_references(idoc, by_entry, first)
        if proto is None:
            continue
        key = (proto.entry, name)
        if key not in merged:
            pmi_id += 1
            merged[key] = (proto, {"id": pmi_id, "kind": "datum",
                                   "name": name, "face_ids": [],
                                   "edge_ids": []})
        datum = merged[key][1]
        datum["face_ids"] = sorted(set(datum["face_ids"]) | set(face_ids))
        datum["edge_ids"] = sorted(set(datum["edge_ids"]) | set(edge_ids))

    for proto, datum in merged.values():
        _proto_pmi(proto)["datums"].append(datum)
        _tag_faces(proto, datum["face_ids"], datum["id"])


# ---------------------------------------------------------------------------
# id bridging: prototype map order -> workdir source.stp order

def _face_signatures(shape):
    """(F, 4) area + centroid per face, brep.iter_faces order."""
    import brep
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    rows = []
    for face in brep.iter_faces(shape):
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        center = props.CentreOfMass()
        rows.append([props.Mass(), center.X(), center.Y(), center.Z()])
    return np.array(rows) if rows else np.zeros((0, 4))


def _map_face_signatures(face_map):
    """(F, 4) signatures in 1-based map order (prototype side)."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    rows = []
    for i in range(1, face_map.Extent() + 1):
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face_map.FindKey(i), props)
        center = props.CentreOfMass()
        rows.append([props.Mass(), center.X(), center.Y(), center.Z()])
    return np.array(rows) if rows else np.zeros((0, 4))


def _edge_signatures_of_map(edge_map):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    rows = []
    for i in range(1, edge_map.Extent() + 1):
        props = GProp_GProps()
        BRepGProp.LinearProperties_s(edge_map.FindKey(i), props)
        center = props.CentreOfMass()
        rows.append([props.Mass(), center.X(), center.Y(), center.Z()])
    return np.array(rows) if rows else np.zeros((0, 4))


def _edge_signatures(shape):
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    edge_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)
    return _edge_signatures_of_map(edge_map)


def _match_signatures(source, target, scale):
    """source row -> target row by nearest signature, or -1 when ambiguous.

    ``scale`` normalizes the tolerance to part size; matches worse than
    1e-6 * scale are rejected, as are targets claimed by two sources.
    """
    if not len(source) or not len(target):
        return np.full(len(source), -1, dtype=np.int64)
    from scipy.spatial import cKDTree

    tree = cKDTree(target)
    distance, index = tree.query(source)
    tolerance = max(1e-9, 1e-6 * scale)
    mapping = np.where(distance <= tolerance, index, -1)
    # reject duplicate claims (symmetric faces with identical signatures)
    values, counts = np.unique(mapping[mapping >= 0], return_counts=True)
    for value in values[counts > 1]:
        mapping[mapping == value] = -1
        logger.warning("ambiguous face/edge signature match dropped")
    return mapping


# ---------------------------------------------------------------------------
# artifact writing

def _write_degraded_pmi(workdir):
    """Stub pmi.json marking that OCCT's GD&T transfer crashed and PMI was
    skipped — so the UI can say so instead of showing a bare "no PMI"."""
    with open(os.path.join(workdir, PMI_FILE), "w") as f:
        json.dump({"schema": PMI_SCHEMA, "degraded": True,
                   "warnings": ["PMI import degraded — OpenCASCADE's GD&T "
                                "transfer failed; re-export will omit PMI"],
                   "dimensions": [], "tolerances": [], "datums": []}, f)


def write_part_artifacts(workdir, proto, source_shape=None, *, pmi_degraded=False):
    """Write face_attrs.json (+ pmi.json) into a part workdir, remapping the
    prototype's 1-based map ids onto the workdir's own 0-based BREP ids.

    ``source_shape`` is the shape re-read from the workdir's source.stp
    (loaded on demand when omitted). Faces whose identity cannot be bridged
    are dropped with a warning — never guessed. When ``pmi_degraded`` and the
    prototype carries no PMI, a stub pmi.json records the degradation.
    """
    import brep
    import pipeline

    if source_shape is None:
        source_shape = brep.load_step_shape_cached(
            pipeline.source_step_path(workdir))  # topology only — reuse parse

    has_attrs = bool(proto.face_attrs) or proto.part_color is not None
    has_pmi = proto.pmi is not None
    if pmi_degraded and not has_pmi:
        _write_degraded_pmi(workdir)
    if not (has_attrs or has_pmi):
        return {"faces": 0, "pmi": 0}

    target_faces = _face_signatures(source_shape)
    scale = float(np.abs(target_faces[:, 1:]).max()) if len(target_faces) else 1.0
    face_mapping = _match_signatures(
        _map_face_signatures(proto.face_map), target_faces, scale)

    faces = {}
    dropped = 0
    for map_id, attrs in sorted(proto.face_attrs.items()):
        target = face_mapping[map_id - 1] if map_id - 1 < len(face_mapping) else -1
        if target < 0:
            dropped += 1
            continue
        faces[str(int(target))] = attrs
    if dropped:
        logger.warning(f"{dropped} face attribute entries could not be "
                       f"bridged to {workdir}")

    payload = {
        "part_color": list(proto.part_color) if proto.part_color else None,
        "face_count": int(len(target_faces)),
        "faces": faces,
    }
    with open(os.path.join(workdir, FACE_ATTRS_FILE), "w") as f:
        json.dump(payload, f)

    pmi_count = 0
    if has_pmi:
        edge_mapping = _match_signatures(
            _edge_signatures_of_map(proto.edge_map),
            _edge_signatures(source_shape), scale)

        def remap_faces(ids):
            out = []
            for map_id in ids:
                target = (face_mapping[map_id - 1]
                          if map_id - 1 < len(face_mapping) else -1)
                if target >= 0:
                    out.append(int(target))
            return out

        def remap_edges(ids):
            out = []
            for map_id in ids:
                target = (edge_mapping[map_id - 1]
                          if map_id - 1 < len(edge_mapping) else -1)
                if target >= 0:
                    out.append(int(target))
            return out

        pmi = {"schema": PMI_SCHEMA,
               "dimensions": [], "tolerances": [], "datums": []}
        for kind in ("dimensions", "tolerances", "datums"):
            for entity in proto.pmi.get(kind, []):
                entity = dict(entity)
                entity["face_ids"] = remap_faces(entity.get("face_ids", []))
                if "secondary_face_ids" in entity:
                    entity["secondary_face_ids"] = remap_faces(
                        entity["secondary_face_ids"])
                entity["edge_ids"] = remap_edges(entity.get("edge_ids", []))
                pmi[kind].append(entity)
                pmi_count += 1
        # flag constructs that won't survive an AP242 round-trip (non-blocking)
        pmi["warnings"] = pmi_support.roundtrip_warnings(pmi)
        with open(os.path.join(workdir, PMI_FILE), "w") as f:
            json.dump(pmi, f)

    return {"faces": len(faces), "pmi": pmi_count}


# ---------------------------------------------------------------------------
# top-level flows

def _write_prototype_step(proto, path):
    """Write one prototype (its own frame) to STEP.

    Preferred path keeps colors/names in the child file (XCAF writer on
    the prototype label); on any binding/transfer trouble it falls back to
    plain geometry — part artifacts are bridged from the assembly document
    either way, the child file's own attributes are only a courtesy for
    external tools.
    """
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_StepModelType

    try:
        from OCP.STEPCAFControl import STEPCAFControl_Writer

        writer = STEPCAFControl_Writer()
        writer.SetColorMode(True)
        writer.SetNameMode(True)
        if writer.Transfer(proto.label,
                           STEPControl_StepModelType.STEPControl_AsIs):
            status = writer.Write(str(path))
            if status == IFSelect_ReturnStatus.IFSelect_RetDone:
                return
        logger.warning(f"XCAF export failed for {proto.name}; "
                       "writing geometry only")
    except Exception:
        logger.warning(f"XCAF export failed for {proto.name}; "
                       "writing geometry only")

    from OCP.STEPControl import STEPControl_Writer

    writer = STEPControl_Writer()
    writer.Transfer(proto.shape, STEPControl_StepModelType.STEPControl_AsIs)
    status = writer.Write(str(path))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise ValueError(f"could not write STEP for part {proto.name}")


def _canonicalize_step_bytes(data):
    """Zero the FILE_NAME timestamp in a generated STEP header.

    Child part files are re-generated on every import; the header timestamp
    is the only run-dependent content, and it would defeat the
    content-addressed dedup of part workdirs. Applies only to files this
    module writes — user uploads are never modified.
    """
    import re

    return re.sub(
        rb"(FILE_NAME\s*\(\s*'[^']*'\s*,\s*)'[^']*'",
        rb"\1''", data, count=1)


def import_step(path, root=".", *, progress=None):
    """Import a STEP file: explode assemblies into per-part workdirs and
    persist colors/names/PMI artifacts.

    Every unique part becomes a content-addressed workdir under
    <root>/parts/ (re-imports dedupe); the input file itself is registered
    too and, when it is an assembly, carries assembly.json linking the
    instance tree to the child part ids. Returns the manifest dict.
    """
    from api import parts as parts_api
    import brep

    def report(fraction, message):
        if progress is not None:
            progress(fraction, message)

    report(0.0, "reading STEP (XCAF)")
    idoc = read_document(path)
    nodes, prototypes = build_tree(idoc)
    logger.info(f"STEP tree: {len(prototypes)} unique parts")

    report(0.2, "extracting colors and names")
    for proto in prototypes:
        extract_face_attributes(idoc, proto)
    if not idoc.pmi_degraded:
        extract_pmi(idoc, prototypes)

    # register the source file itself (assembly record or the single part)
    with open(path, "rb") as f:
        source_bytes = f.read()
    source_info = parts_api.create_part(root, os.path.basename(path),
                                        source_bytes)
    source_dir = parts_api.workdir_for(root, source_info["id"])

    single = len(prototypes) == 1
    children = []
    for index, proto in enumerate(prototypes):
        report(0.3 + 0.6 * index / max(len(prototypes), 1),
               f"exporting {proto.name}")
        if single:
            # one part: the registered source IS the part workdir; extract
            # attributes straight against its own bytes
            workdir = source_dir
            part_info = source_info
            source_shape = brep.load_step_shape(
                os.path.join(workdir, source_info["source"]))
        else:
            with tempfile.TemporaryDirectory() as tmp:
                step_path = os.path.join(tmp, f"{proto.name}.stp")
                _write_prototype_step(proto, step_path)
                with open(step_path, "rb") as f:
                    data = _canonicalize_step_bytes(f.read())
            part_info = parts_api.create_part(root, f"{proto.name}.stp", data)
            workdir = parts_api.workdir_for(root, part_info["id"])
            source_shape = brep.load_step_shape(
                os.path.join(workdir, "source.stp"))

        counts = write_part_artifacts(workdir, proto,
                                      source_shape=source_shape,
                                      pmi_degraded=idoc.pmi_degraded)
        children.append({
            "name": proto.name,
            "part": part_info["id"],
            "quantity": proto.count,
            "face_attrs": counts["faces"],
            "pmi": counts["pmi"],
        })

    manifest = {
        "name": os.path.splitext(os.path.basename(path))[0],
        "source": source_info["id"],
        "assembly": not single,
        "pmi_degraded": idoc.pmi_degraded,
        "parts": children,
        "tree": [_link_tree(node, children) for node in nodes],
    }
    if not single:
        with open(os.path.join(source_dir, ASSEMBLY_FILE), "w") as f:
            json.dump(manifest, f, indent=1)
    report(1.0, "import complete")
    return manifest


def _link_tree(node, children):
    """Replace prototype indices with registered part ids in a tree node."""
    linked = dict(node)
    if node["part"] is not None:
        linked["part"] = children[node["part"]]["part"]
    linked["components"] = [_link_tree(child, children)
                            for child in node["components"]]
    return linked


def extract_part_attributes(workdir):
    """Extract colors/names/PMI for an existing single-part workdir from its
    own retained source STEP (upload path parity: same artifacts import_step
    writes, no assembly handling)."""
    import brep
    import pipeline

    source = pipeline.source_step_path(workdir)
    idoc = read_document(source)
    _, prototypes = build_tree(idoc)
    if not prototypes:
        raise ValueError("no shapes in source STEP")
    if len(prototypes) > 1:
        raise ValueError("source STEP is an assembly — use import/explode")
    proto = prototypes[0]
    extract_face_attributes(idoc, proto)
    if not idoc.pmi_degraded:
        extract_pmi(idoc, prototypes)
    return write_part_artifacts(
        workdir, proto, source_shape=brep.load_step_shape_cached(source))
