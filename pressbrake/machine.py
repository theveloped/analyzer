"""
Machine and tooling catalogue: YZ profile polygons extruded along machine X.

Vertical placement convention (v1): machine z=0 is the sheet MID-PLANE at
the bend line, so for sheet thickness t the die top plane sits at z=-t/2
and the punch tip at z=+t/2.  Tool profiles are stored in their own local
frames (punch origin at the tip, body toward +Z; die origin at the V-centre
on its top plane, body toward -Z) and shifted at query time via
``transformed_profile(thickness)``.  The machine ram profile is stored with
its bottom (punch clamp) edge at local z=0 and the table with its top (die
seat) edge at local z=0; they are positioned from the installed tool
heights.
"""

import math
import os

import jsonschema
import numpy as np
import yaml

from pressbrake.model import polygon_area

CATALOGUE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogue")

_PROFILE_SCHEMA = {
    "type": "array",
    "minItems": 3,
    "items": {
        "type": "array",
        "minItems": 2,
        "maxItems": 2,
        "items": {"type": "number"},
    },
}

_SECTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "lengths": {"type": "array", "items": {"type": "number", "exclusiveMinimum": 0}},
        "counts": {"type": "array", "items": {"type": "integer", "minimum": 0}},
        "horns": {"type": "array", "items": {"enum": ["left", "right"]}},
        "full": {"type": "number", "exclusiveMinimum": 0},
    },
    "required": ["lengths"],
}

MACHINE_SCHEMA = {
    "type": "object",
    "properties": {
        "machine": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "x_length": {"type": "number", "exclusiveMinimum": 0},
                "daylight": {"type": "number", "exclusiveMinimum": 0},
                "stroke": {"type": "number", "exclusiveMinimum": 0},
                "throat_depth": {"type": "number", "minimum": 0},
                "max_force_kn": {"type": "number", "exclusiveMinimum": 0},
                "ram_profile": _PROFILE_SCHEMA,
                "table_profile": _PROFILE_SCHEMA,
            },
            "required": ["name", "x_length", "ram_profile", "table_profile"],
        },
    },
    "required": ["machine"],
}

PUNCHES_SCHEMA = {
    "type": "object",
    "properties": {
        "punches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "tip_angle_deg": {"type": "number", "exclusiveMinimum": 0},
                    "tip_radius": {"type": "number", "minimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                    "max_load_kn_per_m": {"type": "number", "exclusiveMinimum": 0},
                    "mass_kg_per_m": {"type": "number", "exclusiveMinimum": 0},
                    "profile": _PROFILE_SCHEMA,
                    "sections": _SECTIONS_SCHEMA,
                },
                "required": ["id", "tip_angle_deg", "height", "profile"],
            },
        },
    },
    "required": ["punches"],
}

DIES_SCHEMA = {
    "type": "object",
    "properties": {
        "dies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "v_width": {"type": "number", "exclusiveMinimum": 0},
                    "v_angle_deg": {"type": "number", "exclusiveMinimum": 0},
                    "height": {"type": "number", "exclusiveMinimum": 0},
                    "min_thickness": {"type": "number", "minimum": 0},
                    "max_thickness": {"type": "number", "exclusiveMinimum": 0},
                    "max_load_kn_per_m": {"type": "number", "exclusiveMinimum": 0},
                    "mass_kg_per_m": {"type": "number", "exclusiveMinimum": 0},
                    "profile": _PROFILE_SCHEMA,
                    "sections": _SECTIONS_SCHEMA,
                },
                "required": ["id", "v_width", "v_angle_deg", "height", "profile"],
            },
        },
    },
    "required": ["dies"],
}


class CatalogueError(Exception):
    pass


class CatalogueSection:

    __slots__ = ("length", "count", "horn")

    def __init__(self, length, count=1, horn=None):
        self.length = float(length)
        self.count = int(count)
        self.horn = horn                 # None | "left" | "right"

    def __repr__(self):
        return "CatalogueSection({:.0f}mm x{}{})".format(
            self.length, self.count, ", horn " + self.horn if self.horn else "")


class ToolProfile:

    def __init__(self, id, kind, profile, height, tip_angle=None, tip_radius=0.0,
                 v_width=None, v_angle=None, min_thickness=None, max_thickness=None,
                 max_load_kn_per_m=None, mass_kg_per_m=None, sections=None, name=""):
        self.id = id
        self.kind = kind                 # "punch" | "die"
        self.profile = _normalize_profile(profile, id)
        self.height = float(height)
        self.tip_angle = tip_angle       # radians (punches)
        self.tip_radius = float(tip_radius)
        self.v_width = v_width           # mm (dies)
        self.v_angle = v_angle           # radians (dies)
        self.min_thickness = min_thickness
        self.max_thickness = max_thickness
        self.max_load_kn_per_m = max_load_kn_per_m
        self.mass_kg_per_m = mass_kg_per_m
        self.sections = sections or []
        self.name = name

    def transformed_profile(self, thickness=0.0, max_phi=0.0):
        """
        Profile in machine YZ coordinates for a given sheet thickness: the
        die top sits on the sheet bottom (-t/2); the punch tip sits at the
        sheet's inner corner at the final bend parameter,
        (t/2) / cos(max_phi/2) - with the pivot pinned at the mid-plane,
        both wing top surfaces intersect exactly there, so the punch is
        tangent to (not penetrating) the wings it forms.
        """
        if self.kind == "punch":
            shift = (thickness / 2.0) / max(math.cos(min(abs(max_phi), 2.6) / 2.0), 0.2)
        else:
            shift = -thickness / 2.0
        return self.profile + np.array([0.0, shift])

    def fits_thickness(self, thickness):
        if self.min_thickness is not None and thickness < self.min_thickness:
            return False
        if self.max_thickness is not None and thickness > self.max_thickness:
            return False
        return True

    def fits_angle(self, bend_angle):
        """
        Whether the tool's working angle physically allows forming
        ``bend_angle`` (rad): the remaining included angle of the part,
        pi - |bend|, must stay above the tool tip/V angle.
        """
        included = math.pi - abs(bend_angle)
        tool_angle = self.tip_angle if self.kind == "punch" else self.v_angle
        if tool_angle is None:
            return True
        return included >= tool_angle - 1e-9

    def __repr__(self):
        return "ToolProfile({}, {})".format(self.id, self.kind)


class MachineProfile:

    def __init__(self, name, x_length, ram_profile, table_profile, daylight=None,
                 stroke=None, throat_depth=0.0, max_force_kn=None):
        self.name = name
        self.x_length = float(x_length)
        self.ram_profile = _normalize_profile(ram_profile, name + ".ram")
        self.table_profile = _normalize_profile(table_profile, name + ".table")
        self.daylight = daylight
        self.stroke = stroke
        self.throat_depth = throat_depth
        self.max_force_kn = max_force_kn

    def ram_transformed(self, punch, thickness, max_phi=0.0):
        """
        Ram profile positioned above the installed punch (its local z=0 is
        the punch clamp line) at the fully-closed position.
        """
        punch_tip = float(np.min(
            punch.transformed_profile(thickness, max_phi)[:, 1]))
        return self.ram_profile + np.array([0.0, punch_tip + punch.height])

    def table_transformed(self, die, thickness):
        """
        Table profile positioned below the installed die (its local z=0 is
        the die seat line).
        """
        return self.table_profile + np.array([0.0, -thickness / 2.0 - die.height])

    def __repr__(self):
        return "MachineProfile({})".format(self.name)


def load_machine(path=None):
    path = path or os.path.join(CATALOGUE_DIR, "demo_machine.yaml")
    data = _load_yaml(path, MACHINE_SCHEMA)
    machine = data["machine"]
    return MachineProfile(
        name=machine["name"],
        x_length=machine["x_length"],
        ram_profile=machine["ram_profile"],
        table_profile=machine["table_profile"],
        daylight=machine.get("daylight"),
        stroke=machine.get("stroke"),
        throat_depth=machine.get("throat_depth", 0.0),
        max_force_kn=machine.get("max_force_kn"),
    )


def load_punches(path=None):
    path = path or os.path.join(CATALOGUE_DIR, "demo_punches.yaml")
    data = _load_yaml(path, PUNCHES_SCHEMA)
    punches = {}
    for item in data["punches"]:
        punches[item["id"]] = ToolProfile(
            id=item["id"],
            kind="punch",
            profile=item["profile"],
            height=item["height"],
            tip_angle=math.radians(item["tip_angle_deg"]),
            tip_radius=item.get("tip_radius", 0.0),
            max_load_kn_per_m=item.get("max_load_kn_per_m"),
            mass_kg_per_m=item.get("mass_kg_per_m"),
            sections=_parse_sections(item.get("sections")),
            name=item.get("name", ""),
        )
    return punches


def load_dies(path=None):
    path = path or os.path.join(CATALOGUE_DIR, "demo_dies.yaml")
    data = _load_yaml(path, DIES_SCHEMA)
    dies = {}
    for item in data["dies"]:
        dies[item["id"]] = ToolProfile(
            id=item["id"],
            kind="die",
            profile=item["profile"],
            height=item["height"],
            v_width=item["v_width"],
            v_angle=math.radians(item["v_angle_deg"]),
            min_thickness=item.get("min_thickness"),
            max_thickness=item.get("max_thickness"),
            max_load_kn_per_m=item.get("max_load_kn_per_m"),
            mass_kg_per_m=item.get("mass_kg_per_m"),
            sections=_parse_sections(item.get("sections")),
            name=item.get("name", ""),
        )
    return dies


def _load_yaml(path, schema):
    with open(path) as handle:
        data = yaml.safe_load(handle)
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as error:
        raise CatalogueError("{}: {}".format(path, error.message))
    return data


def _parse_sections(data):
    if not data:
        return []
    sections = []
    counts = data.get("counts") or [1] * len(data["lengths"])
    if len(counts) != len(data["lengths"]):
        raise CatalogueError("sections lengths/counts size mismatch")
    for length, count in zip(data["lengths"], counts):
        sections.append(CatalogueSection(length, count))
    for horn in data.get("horns", []):
        sections.append(CatalogueSection(data["lengths"][0], 1, horn=horn))
    if "full" in data:
        sections.append(CatalogueSection(data["full"], 1))
    return sections


def _normalize_profile(points, label):
    """
    Validate and normalize a profile polygon: closed, simple, CCW, as a
    (N,2) float array without a duplicated closing point.
    """
    arr = np.asarray(points, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2 or len(arr) < 3:
        raise CatalogueError("profile {} is not a polygon".format(label))
    if np.allclose(arr[0], arr[-1]):
        arr = arr[:-1]
    if len(arr) < 3:
        raise CatalogueError("profile {} is degenerate".format(label))
    if polygon_area(arr) < 0:
        arr = arr[::-1]

    from shapely.geometry import Polygon
    if not Polygon(arr).is_valid:
        raise CatalogueError("profile {} is self-intersecting".format(label))
    return arr
