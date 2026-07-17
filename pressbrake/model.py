"""
Core data structures of the press-brake planner.

The part is a tree of rigid flat panels joined by revolute bend hinges, all
defined in the *flat frame*: the unfolded pattern lying in the z=0 plane (as
produced by ``AdjacencyGraph.unfold_graph(align=True)``), +Z being the "up"
side of the sheet.  Folding is pure rigid-transform composition; no BREP is
touched in the planning loop.

Sign convention: a positive bend angle rotates the child subtree toward +Z.
To make that hold, ``Bend.axis_dir`` is normalized so that the in-plane
normal ``n = z_hat x axis_dir`` points from the axis toward the child panel
(see ``kinematics.normalize_axis``).
"""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Panel:
    id: int                          # dense index; equals its bit position in masks
    outline: np.ndarray              # (N, 2) CCW polygon, flat frame, mm
    holes: list = field(default_factory=list)   # list of (M, 2) polygons
    face_hashes: tuple = ()          # source AAG planar face hashes (traceability)

    def centroid(self):
        return polygon_centroid(self.outline)


@dataclass
class Bend:
    id: int                          # dense index; equals its bit position in masks
    axis_point: np.ndarray           # (2,) point on the hinge line, flat frame
    axis_dir: np.ndarray             # (2,) unit direction, normalized toward child
    angle_target: float              # signed rad; + = child rotates toward +Z
    inner_radius: float
    k_factor: float
    length: float                    # bend-line material span, mm
    parent_panel: int
    child_panel: int
    moving_mask: int = 0             # bitmask over Panel.id: the child subtree
    sister_group: int = -1           # collinear simultaneous-forming group id
    angle_overbend: float = None     # target + springback compensation
    angle_relaxed: float = None      # angle after springback
    zone_width: float = 0.0          # flat bend-allowance length BA consumed by
                                     # this bend; 0 = sharp hinge (synthetic
                                     # parts).  Drives the bend-deduction
                                     # translation in kinematics.fold_transforms.
    face_hashes: tuple = ()          # source cylindrical face hashes

    def __post_init__(self):
        self.axis_point = np.asarray(self.axis_point, dtype=float)
        self.axis_dir = np.asarray(self.axis_dir, dtype=float)
        norm = np.linalg.norm(self.axis_dir)
        if norm > 0:
            self.axis_dir = self.axis_dir / norm
        if self.angle_overbend is None:
            self.angle_overbend = self.angle_target
        if self.angle_relaxed is None:
            self.angle_relaxed = self.angle_target


@dataclass
class KinematicGraph:
    panels: list
    bends: list
    base_panel: int
    thickness: float
    z_offset: float = 0.0            # mid-surface height above the pattern
                                     # plane: the unfolder flattens one SKIN
                                     # of the sheet, so extracted parts get
                                     # +/- t/2 (side probed against the
                                     # solid); synthetic builder parts are
                                     # defined ON the mid-surface (0)
    source: str = ""                 # provenance (file path or builder name)

    def __post_init__(self):
        self._validate()

    def _validate(self):
        for index, panel in enumerate(self.panels):
            if panel.id != index:
                raise ValueError("panel ids must be dense indices")
        for index, bend in enumerate(self.bends):
            if bend.id != index:
                raise ValueError("bend ids must be dense indices")

    @property
    def panel_count(self):
        return len(self.panels)

    @property
    def bend_count(self):
        return len(self.bends)

    def sister_groups(self):
        """
        Mapping of sister_group id -> list of bend ids, singletons included.
        """
        groups = {}
        for bend in self.bends:
            key = bend.sister_group if bend.sister_group >= 0 else -1 - bend.id
            groups.setdefault(key, []).append(bend.id)
        return groups

    def flat_state(self):
        return FoldState(theta=np.zeros(self.bend_count), done_mask=0)

    def folded_state(self):
        theta = np.array([bend.angle_relaxed for bend in self.bends])
        return FoldState(theta=theta, done_mask=(1 << self.bend_count) - 1)

    # Batched kinematics (implemented in pressbrake.kinematics; thin wrappers
    # here so callers can stay on the graph object).

    def fold_transforms(self, theta):
        from pressbrake import kinematics
        return kinematics.fold_transforms(self, theta)

    def panel_vertices(self, theta):
        from pressbrake import kinematics
        return kinematics.panel_vertices(self, theta)


@dataclass
class FoldState:
    theta: np.ndarray                # (B,) current angle per bend, rad
    done_mask: int = 0               # bitmask over Bend.id (memoization key)

    def with_bend_done(self, graph, bend_id):
        theta = np.array(self.theta, dtype=float)
        theta[bend_id] = graph.bends[bend_id].angle_relaxed
        return FoldState(theta=theta, done_mask=self.done_mask | (1 << bend_id))


@dataclass
class BendAction:
    """
    One press stroke: form all bends of a sister group simultaneously.

    ``flip`` records whether the part lies upside down on the die.  It is not
    a free choice: the bend must always form upward in the machine frame
    (the die is below), so flip is forced by the sign of the bend angle.
    ``rotation`` selects which end of the hinge points toward machine +X and
    is the genuinely free orientation choice (2 options per group).
    """
    bend_ids: tuple                  # bends of one sister group
    flip: bool
    rotation: int                    # 0 or 1
    x_offset: float = 0.0            # translation along machine X (position invariance)

    @property
    def primary(self):
        return self.bend_ids[0]


def polygon_area(points):
    """
    Signed area of a 2D polygon (positive = CCW).
    """
    points = np.asarray(points, dtype=float)
    x, y = points[:, 0], points[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def polygon_centroid(points):
    points = np.asarray(points, dtype=float)
    x, y = points[:, 0], points[:, 1]
    cross = x * np.roll(y, -1) - np.roll(x, -1) * y
    area = 0.5 * np.sum(cross)
    if abs(area) < 1e-12:
        return points.mean(axis=0)
    cx = np.sum((x + np.roll(x, -1)) * cross) / (6.0 * area)
    cy = np.sum((y + np.roll(y, -1)) * cross) / (6.0 * area)
    return np.array([cx, cy])
