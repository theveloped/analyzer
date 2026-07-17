"""
Press-brake bend simulation and tooling planning (instapart port, stage 1).

The package represents a sheet-metal part as a kinematic graph of rigid flat
panels connected by revolute bend hinges (see ``pressbrake.model``).  All
planning-loop geometry is pure numpy/shapely; OpenCASCADE (OCP) is only
touched in ``pressbrake.adapter``, which harvests the graph from the
analyzer's AAG + Unfolder stack (aag.py / unfold.py) — the rest of the
package runs without the conda environment.

Machine frame convention (used throughout):
    X: along the bend axis / machine width, x in [0, machine.x_length]
    Y: horizontal, positive toward the operator
    Z: up; the active bend line lies at Y=0, Z=0 (die top plane), the punch
       travels in -Z.

The flat frame is the unfolded pattern at z=0 (one SKIN of the sheet); the
mid-surface where hinges live sits at ``KinematicGraph.z_offset`` (+-t/2 for
extracted parts, 0 for synthetic builders).  Units are millimetres and
radians everywhere.

Open upstream roadmap items (kept for orientation): analytic arc-vs-edge
first-contact sweeps inside ``envelope.swept_region`` (P4), operation phases
— backgauge, handling, tonnage, die-penetration descent (P7), and exact
verification of winning plans (P8 — see docs/BACKLOG.md item 16, the
mesh-backed fold simulation).  ``tooling.check_section_seams`` is a stub.
"""

from pressbrake.model import (
    Panel,
    Bend,
    KinematicGraph,
    FoldState,
    BendAction,
)
from pressbrake.intervals import IntervalSet

__all__ = [
    "Panel",
    "Bend",
    "KinematicGraph",
    "FoldState",
    "BendAction",
    "IntervalSet",
]
