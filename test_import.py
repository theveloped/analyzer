"""Checks of the STEP import front-end (step_import.py).

Fixture: a synthetic XCAF assembly authored in-memory — prototype 'plate'
(10x20x5 box, top face colored red) used twice (second instance translated
+30 X), prototype 'pin' (r4 h12 cylinder, green part color) used once —
written through STEPCAFControl_Writer and re-imported.

Asserts: tree shape and quantities, instance transforms, child workdirs
(content-addressed, source.stp retained), face_attrs.json with the red
face bridged to the geometrically correct BREP face id of the re-read
child STEP, part color on the pin, idempotent re-import, and the
single-part extraction path.

Run from the repo root: python test_import.py
"""
import json
import os
import sys
import tempfile

import numpy as np

import brep
import step_import


def check_factory(failures):
    def check(name, condition, detail=""):
        status = "OK " if condition else "FAIL"
        print(f"  [{status}] {name:40s} {detail}")
        if not condition:
            failures.append(name)
    return check


def _find_top_face(shape, z=5.0):
    """The face of ``shape`` whose centroid sits at height z."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    for face in brep.iter_faces(shape):
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        if abs(props.CentreOfMass().Z() - z) < 1e-9:
            return face
    raise AssertionError("top face not found")


def _color(r, g, b):
    from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor

    return Quantity_Color(r, g, b, Quantity_TypeOfColor.Quantity_TOC_RGB)


def _set_name(label, name):
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name

    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))


def make_assembly_step(tmp):
    """Author the fixture assembly through XCAF and write it to STEP."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_StepModelType
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool
    from OCP.gp import gp_Trsf, gp_Vec

    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-CAF"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    plate = BRepPrimAPI_MakeBox(10, 20, 5).Shape()
    plate_label = shape_tool.AddShape(plate, False)
    _set_name(plate_label, "plate")
    face_label = shape_tool.AddSubShape(plate_label, _find_top_face(plate))
    color_tool.SetColor(face_label, _color(1, 0, 0),
                        XCAFDoc_ColorType.XCAFDoc_ColorSurf)

    pin = BRepPrimAPI_MakeCylinder(4.0, 12.0).Shape()
    pin_label = shape_tool.AddShape(pin, False)
    _set_name(pin_label, "pin")
    color_tool.SetColor(pin_label, _color(0, 1, 0),
                        XCAFDoc_ColorType.XCAFDoc_ColorGen)

    assembly_label = shape_tool.NewShape()
    _set_name(assembly_label, "assy")

    def place(dx, dy, dz):
        trsf = gp_Trsf()
        trsf.SetTranslation(gp_Vec(dx, dy, dz))
        return TopLoc_Location(trsf)

    shape_tool.AddComponent(assembly_label, plate_label, place(0, 0, 0))
    shape_tool.AddComponent(assembly_label, plate_label, place(30, 0, 0))
    shape_tool.AddComponent(assembly_label, pin_label, place(0, 30, 0))
    shape_tool.UpdateAssemblies()

    path = os.path.join(tmp, "assy.step")
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    if not writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs):
        raise AssertionError("fixture transfer failed")
    if writer.Write(path) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise AssertionError("fixture write failed")
    return path


def make_single_step(tmp):
    """A single colored part written through XCAF."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_StepModelType
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-CAF"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    block = BRepPrimAPI_MakeBox(10, 20, 5).Shape()
    label = shape_tool.AddShape(block, False)
    _set_name(label, "single")
    face_label = shape_tool.AddSubShape(label, _find_top_face(block))
    color_tool.SetColor(face_label, _color(0, 0, 1),
                        XCAFDoc_ColorType.XCAFDoc_ColorSurf)

    path = os.path.join(tmp, "single.step")
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    if writer.Write(path) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise AssertionError("fixture write failed")
    return path


def _colored_face_checks(check, workdir, expected_color, prefix):
    attrs = json.load(open(os.path.join(workdir, "face_attrs.json")))
    colored = {fid: a for fid, a in attrs["faces"].items() if a["color"]}
    check(f"{prefix}: exactly one colored face",
          len(colored) == 1, f"{len(colored)} colored")
    if len(colored) != 1:
        return
    face_id, entry = next(iter(colored.items()))
    check(f"{prefix}: color survives",
          bool(np.allclose(entry["color"], expected_color, atol=1e-3)),
          f"{entry['color']}")

    # bridge correctness: that face id, in the re-read child STEP, must be
    # the geometric top face of the 10x20x5 plate (centroid (5, 10, 5))
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    import pipeline
    shape = brep.load_step_shape(pipeline.source_step_path(workdir))
    faces = list(brep.iter_faces(shape))
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(faces[int(face_id)], props)
    center = props.CentreOfMass()
    check(f"{prefix}: id bridges to the geometric top face",
          bool(np.allclose([center.X(), center.Y(), center.Z()],
                           [5.0, 10.0, 5.0], atol=1e-6)),
          f"centroid ({center.X():.1f}, {center.Y():.1f}, {center.Z():.1f})")


def fixture_assembly(check):
    with tempfile.TemporaryDirectory() as tmp:
        path = make_assembly_step(tmp)
        root = os.path.join(tmp, "root")
        os.makedirs(root)

        manifest = step_import.import_step(path, root)

        check("assembly detected",
              manifest["assembly"] and not manifest["pmi_degraded"], "")
        check("two unique parts", len(manifest["parts"]) == 2,
              f"{len(manifest['parts'])}")

        by_name = {part["name"]: part for part in manifest["parts"]}
        check("plate quantity 2 / pin quantity 1",
              by_name.get("plate", {}).get("quantity") == 2
              and by_name.get("pin", {}).get("quantity") == 1, "")

        instances = manifest["tree"][0]["components"]
        check("tree has 3 instances", len(instances) == 3,
              f"{len(instances)}")
        translations = [node["transform"]["translation"]
                        for node in instances]
        check("instance transforms recorded",
              any(np.allclose(t, [30, 0, 0]) for t in translations)
              and any(np.allclose(t, [0, 30, 0]) for t in translations),
              f"{translations}")

        from api import parts as parts_api
        plate_dir = parts_api.workdir_for(root, by_name["plate"]["part"])
        pin_dir = parts_api.workdir_for(root, by_name["pin"]["part"])
        check("child workdirs registered with source.stp",
              os.path.exists(os.path.join(plate_dir, "source.stp"))
              and os.path.exists(os.path.join(pin_dir, "source.stp")), "")

        _colored_face_checks(check, plate_dir, [1, 0, 0], "plate")

        pin_attrs = json.load(open(os.path.join(pin_dir, "face_attrs.json")))
        check("pin: part color green",
              bool(np.allclose(pin_attrs["part_color"], [0, 1, 0],
                               atol=1e-3)), f"{pin_attrs['part_color']}")

        # child parts flow through the normal pipeline
        import pipeline
        result = pipeline.mesh_part(
            os.path.join(plate_dir, "source.stp"), plate_dir, subdivide=2.0)
        check("plate child meshes with BREP ids",
              result["counts"].get("brep_faces") == 6,
              f"{result['counts'].get('brep_faces')} faces")

        # idempotence: re-import resolves to the same content-addressed ids
        manifest2 = step_import.import_step(path, root)
        check("re-import dedupes to the same part ids",
              [p["part"] for p in manifest2["parts"]]
              == [p["part"] for p in manifest["parts"]], "")


def fixture_single(check):
    with tempfile.TemporaryDirectory() as tmp:
        path = make_single_step(tmp)
        root = os.path.join(tmp, "root")
        os.makedirs(root)

        manifest = step_import.import_step(path, root)
        check("single part: not an assembly",
              not manifest["assembly"] and len(manifest["parts"]) == 1, "")

        from api import parts as parts_api
        workdir = parts_api.workdir_for(root, manifest["parts"][0]["part"])
        check("single part: artifacts in the source workdir",
              os.path.exists(os.path.join(workdir, "face_attrs.json")), "")
        _colored_face_checks(check, workdir, [0, 0, 1], "single")

        # standalone re-extraction on an existing workdir
        os.remove(os.path.join(workdir, "face_attrs.json"))
        counts = step_import.extract_part_attributes(workdir)
        check("extract_part_attributes rebuilds artifacts",
              counts["faces"] == 1
              and os.path.exists(os.path.join(workdir, "face_attrs.json")),
              f"{counts}")


def main():
    failures = []
    check = check_factory(failures)

    print("=== fixture A: synthetic XCAF assembly ===")
    fixture_assembly(check)
    print("=== fixture B: single colored part ===")
    fixture_single(check)

    if failures:
        print(f"{len(failures)} CHECKS FAILED: {failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
