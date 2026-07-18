"""
Synthetic kinematic graphs with hand-computable geometry.  No OCC required:
these build ``KinematicGraph`` objects directly, so the whole planning core
can be unit-tested anywhere.

All builders put the base panel in the flat frame with hinge lines parallel
to the flat X axis, thickness 2 mm unless stated, and 90 degree target
angles unless stated.  Dimensions in mm.
"""

import math

import numpy as np

from pressbrake.kinematics import finalize_graph
from pressbrake.model import Bend, KinematicGraph, Panel


def rectangle(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)


def _graph(panels, bends, thickness, name):
    graph = KinematicGraph(
        panels=panels, bends=bends, base_panel=0, thickness=thickness, source=name
    )
    return finalize_graph(graph)


def l_bracket(width=100.0, leg=50.0, flange=30.0, angle=math.pi / 2, thickness=2.0,
              inner_radius=2.0):
    """
    Base panel y in [0, leg], flange y in [leg, leg + flange], hinge at y=leg.
    """
    panels = [
        Panel(id=0, outline=rectangle(0, 0, width, leg)),
        Panel(id=1, outline=rectangle(0, leg, width, leg + flange)),
    ]
    bends = [
        Bend(id=0, axis_point=np.array([0.0, leg]), axis_dir=np.array([1.0, 0.0]),
             angle_target=angle, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=0, child_panel=1),
    ]
    return _graph(panels, bends, thickness, "l_bracket")


def u_channel(width=100.0, base=80.0, wall=40.0, angle=math.pi / 2, thickness=2.0,
              inner_radius=2.0):
    """
    Base panel y in [0, base]; walls fold up from y=0 and y=base.
    """
    panels = [
        Panel(id=0, outline=rectangle(0, 0, width, base)),
        Panel(id=1, outline=rectangle(0, -wall, width, 0)),
        Panel(id=2, outline=rectangle(0, base, width, base + wall)),
    ]
    bends = [
        Bend(id=0, axis_point=np.array([0.0, 0.0]), axis_dir=np.array([1.0, 0.0]),
             angle_target=angle, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=0, child_panel=1),
        Bend(id=1, axis_point=np.array([0.0, base]), axis_dir=np.array([1.0, 0.0]),
             angle_target=angle, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=0, child_panel=2),
    ]
    return _graph(panels, bends, thickness, "u_channel")


def z_profile(width=100.0, base=60.0, flange=30.0, thickness=2.0, inner_radius=2.0):
    """
    One flange up (+90 deg) at y=base, one flange down (-90 deg) at y=0.
    """
    panels = [
        Panel(id=0, outline=rectangle(0, 0, width, base)),
        Panel(id=1, outline=rectangle(0, -flange, width, 0)),
        Panel(id=2, outline=rectangle(0, base, width, base + flange)),
    ]
    bends = [
        Bend(id=0, axis_point=np.array([0.0, 0.0]), axis_dir=np.array([1.0, 0.0]),
             angle_target=-math.pi / 2, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=0, child_panel=1),
        Bend(id=1, axis_point=np.array([0.0, base]), axis_dir=np.array([1.0, 0.0]),
             angle_target=math.pi / 2, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=0, child_panel=2),
    ]
    return _graph(panels, bends, thickness, "z_profile")


def hat_profile(width=100.0, top=80.0, wall=35.0, foot=15.0, thickness=2.0,
                inner_radius=2.0):
    """
    Top panel is the base; walls fold down (-90), feet fold back out (+90
    relative to their wall) so the folded hat sits with the top at z=0 and
    horizontal feet at z=-wall.
    """
    panels = [
        Panel(id=0, outline=rectangle(0, 0, width, top)),                 # top (base)
        Panel(id=1, outline=rectangle(0, -wall, width, 0)),               # wall A
        Panel(id=2, outline=rectangle(0, top, width, top + wall)),        # wall B
        Panel(id=3, outline=rectangle(0, -wall - foot, width, -wall)),    # foot A
        Panel(id=4, outline=rectangle(0, top + wall, width, top + wall + foot)),  # foot B
    ]
    quarter = math.pi / 2
    bends = [
        Bend(id=0, axis_point=np.array([0.0, 0.0]), axis_dir=np.array([1.0, 0.0]),
             angle_target=-quarter, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=0, child_panel=1),
        Bend(id=1, axis_point=np.array([0.0, top]), axis_dir=np.array([1.0, 0.0]),
             angle_target=-quarter, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=0, child_panel=2),
        Bend(id=2, axis_point=np.array([0.0, -wall]), axis_dir=np.array([1.0, 0.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=1, child_panel=3),
        Bend(id=3, axis_point=np.array([0.0, top + wall]), axis_dir=np.array([1.0, 0.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=2, child_panel=4),
    ]
    return _graph(panels, bends, thickness, "hat_profile")


def box(side=80.0, wall=30.0, corner_gap=5.0, thickness=2.0, inner_radius=2.0):
    """
    Square base with four walls folding up, one on each edge.  The west and
    east walls are inset by ``corner_gap`` at both ends: with a zero gap the
    folded wall corners touch and self-collide within any margin.
    """
    gap = corner_gap
    panels = [
        Panel(id=0, outline=rectangle(0, 0, side, side)),
        Panel(id=1, outline=rectangle(0, -wall, side, 0)),                    # south
        Panel(id=2, outline=rectangle(0, side, side, side + wall)),           # north
        Panel(id=3, outline=rectangle(-wall, gap, 0, side - gap)),            # west
        Panel(id=4, outline=rectangle(side, gap, side + wall, side - gap)),   # east
    ]
    quarter = math.pi / 2
    bends = [
        Bend(id=0, axis_point=np.array([0.0, 0.0]), axis_dir=np.array([1.0, 0.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=side, parent_panel=0, child_panel=1),
        Bend(id=1, axis_point=np.array([0.0, side]), axis_dir=np.array([1.0, 0.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=side, parent_panel=0, child_panel=2),
        Bend(id=2, axis_point=np.array([0.0, gap]), axis_dir=np.array([0.0, 1.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=side - 2 * gap, parent_panel=0, child_panel=3),
        Bend(id=3, axis_point=np.array([side, gap]), axis_dir=np.array([0.0, 1.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=side - 2 * gap, parent_panel=0, child_panel=4),
    ]
    return _graph(panels, bends, thickness, "box")


def offset_lip(width=100.0, base=60.0, wall=30.0, lip=20.0, thickness=2.0,
               inner_radius=2.0):
    """
    Base - wall - return lip, both bends +90: once the lip is formed, bending
    the wall makes the lip curl over the punch blade.  The classic gooseneck
    demonstration part.
    """
    panels = [
        Panel(id=0, outline=rectangle(0, 0, width, base)),
        Panel(id=1, outline=rectangle(0, base, width, base + wall)),
        Panel(id=2, outline=rectangle(0, base + wall, width, base + wall + lip)),
    ]
    quarter = math.pi / 2
    bends = [
        Bend(id=0, axis_point=np.array([0.0, base]), axis_dir=np.array([1.0, 0.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=0, child_panel=1),
        Bend(id=1, axis_point=np.array([0.0, base + wall]), axis_dir=np.array([1.0, 0.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=1, child_panel=2),
    ]
    return _graph(panels, bends, thickness, "offset_lip")


def tabbed_flange(width=100.0, base=50.0, tab=30.0, gap=(40.0, 60.0), thickness=2.0,
                  inner_radius=2.0):
    """
    Two collinear tabs folding up from the same line y=base with a gap between
    x=gap[0] and x=gap[1]: a sister-bend pair.
    """
    panels = [
        Panel(id=0, outline=rectangle(0, 0, width, base)),
        Panel(id=1, outline=rectangle(0, base, gap[0], base + tab)),
        Panel(id=2, outline=rectangle(gap[1], base, width, base + tab)),
    ]
    quarter = math.pi / 2
    bends = [
        Bend(id=0, axis_point=np.array([0.0, base]), axis_dir=np.array([1.0, 0.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=gap[0], parent_panel=0, child_panel=1),
        Bend(id=1, axis_point=np.array([gap[1], base]), axis_dir=np.array([1.0, 0.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=width - gap[1], parent_panel=0, child_panel=2),
    ]
    return _graph(panels, bends, thickness, "tabbed_flange")


def partial_lip_notched(width=100.0, base=60.0, wall=30.0, lip=20.0,
                        lip_end=30.0, notch_depth=5.0, thickness=2.0,
                        inner_radius=2.0):
    """
    Base - wall - return lip where the lip covers only x in [0, lip_end],
    and the wall bend line is notched over that span so the wall-bend
    REQUIRED interval starts past the lip while the formed lip forbids
    straight-punch material over [0, lip_end].

    Because envelope coordinates are bend-relative (each hinge START at
    x=0), the lip bend's required [0, lip_end] lands exactly inside the
    wall bend's forbidden zone under the aligned-starts convention: a
    straight punch therefore needs TWO setups (a segmented run starting
    past the lip for the wall, a separate run for the lip), while a
    relief punch handles both bends in one setup.
    """
    n1 = lip_end + notch_depth
    base_outline = np.array([
        [0, 0], [width, 0], [width, base],
        [n1, base], [n1, base - notch_depth],
        [0, base - notch_depth],
    ], dtype=float)
    # material crosses the wall bend line only for x > n1
    wall_outline = np.array([
        [n1, base], [width, base], [width, base + wall],
        [0, base + wall], [0, base + notch_depth], [n1, base + notch_depth],
    ], dtype=float)
    lip_outline = rectangle(0, base + wall, lip_end, base + wall + lip)
    panels = [
        Panel(id=0, outline=base_outline),
        Panel(id=1, outline=wall_outline),
        Panel(id=2, outline=lip_outline),
    ]
    quarter = math.pi / 2
    bends = [
        Bend(id=0, axis_point=np.array([0.0, base]), axis_dir=np.array([1.0, 0.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=0, child_panel=1),
        Bend(id=1, axis_point=np.array([0.0, base + wall]),
             axis_dir=np.array([1.0, 0.0]),
             angle_target=quarter, inner_radius=inner_radius, k_factor=0.5,
             length=lip_end, parent_panel=1, child_panel=2),
    ]
    return _graph(panels, bends, thickness, "partial_lip_notched")


def notched_bend(width=100.0, base=50.0, flange=30.0, notch=(40.0, 60.0),
                 notch_depth=5.0, thickness=2.0, inner_radius=2.0):
    """
    L-bracket with a rectangular notch cut across the bend line between
    x=notch[0] and x=notch[1]: material crosses the hinge only outside the
    notch, so the REQUIRED tooling interval must exclude it.
    """
    n0, n1 = notch
    base_outline = np.array([
        [0, 0], [width, 0], [width, base],
        [n1, base], [n1, base - notch_depth], [n0, base - notch_depth], [n0, base],
        [0, base],
    ], dtype=float)
    flange_outline = np.array([
        [0, base], [n0, base], [n0, base + notch_depth], [n1, base + notch_depth],
        [n1, base], [width, base], [width, base + flange], [0, base + flange],
    ], dtype=float)
    panels = [
        Panel(id=0, outline=base_outline),
        Panel(id=1, outline=flange_outline),
    ]
    bends = [
        Bend(id=0, axis_point=np.array([0.0, base]), axis_dir=np.array([1.0, 0.0]),
             angle_target=math.pi / 2, inner_radius=inner_radius, k_factor=0.5,
             length=width, parent_panel=0, child_panel=1),
    ]
    return _graph(panels, bends, thickness, "notched_bend")
