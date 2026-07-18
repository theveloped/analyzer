"""
1D segmented tooling solver (phase 5 of the design).

Given the REQUIRED and FORBIDDEN machine-X interval sets of a bend (or the
union over several bends sharing one setup), choose physical catalogue
sections and their continuous X positions so that installed tool material
covers every required interval, avoids every forbidden interval, respects
section inventory, and is lexicographically minimal in
(section count, total length, total mass).

Structure: every maximal contiguous strip of installed sections is a
``ToolRun``.  A run may not cross a forbidden interval, so runs live inside
the "allowed regions" (complement of the forbidden set).  Within a region
the required intervals are partitioned into consecutive clusters, one run
per cluster - merging clusters may save sections (one long section instead
of two short ones across a small gap), splitting saves installed length.
Section multisets per run come from a bounded-knapsack min-coins DP.

All interval coordinates arrive from envelope.py and are already
margin-buffered there; this module treats them verbatim.

Roadmap note (concept section 12 / 20): after a placement is chosen, the
section END FACES (seams) should be checked against workpiece geometry near
each seam, sliding seam positions over the critical-position lattice
(region/required boundaries offset by partial sums of section lengths).
``check_section_seams`` is the v1 stub for that stage-2 check.
"""

import itertools
import math
from dataclasses import dataclass, field

from pressbrake.intervals import IntervalSet

GRID = 0.1              # mm; catalogue lengths are integral, so exact on grid
EPSILON = 1e-6
MAX_CLUSTER_PARTITION = 12   # 2^(k-1) partition enumeration guard


@dataclass
class PlacedSection:
    length: float
    x_start: float
    horn: str = None            # "left" | "right" | None

    @property
    def x_end(self):
        return self.x_start + self.length


@dataclass
class ToolRun:
    sections: list               # PlacedSection, ordered, back-to-back

    @property
    def x_start(self):
        return self.sections[0].x_start

    @property
    def x_end(self):
        return self.sections[-1].x_end

    @property
    def length(self):
        return sum(section.length for section in self.sections)


@dataclass
class ToolPlacement:
    tool_id: str
    kind: str                    # "punch" | "die"
    runs: list = field(default_factory=list)
    feasible: bool = False
    reason: str = ""
    mass_per_m: float = None

    @property
    def section_count(self):
        return sum(len(run.sections) for run in self.runs)

    @property
    def total_length(self):
        return sum(run.length for run in self.runs)

    @property
    def total_mass(self):
        """
        kg when the catalogue provides mass; installed length (mm) as a
        proxy otherwise, so the lexicographic comparison stays meaningful.
        """
        if self.mass_per_m is None:
            return self.total_length
        return self.mass_per_m * self.total_length / 1000.0

    @property
    def objective(self):
        return (self.section_count, self.total_length, self.total_mass)

    def installed(self):
        return IntervalSet([(run.x_start, run.x_end) for run in self.runs])


@dataclass
class SetupPlan:
    punch_id: str
    die_id: str
    step_indices: list
    punch_placement: ToolPlacement = None
    die_placement: ToolPlacement = None
    required: IntervalSet = None       # union of the bends' required cores
    feasible: bool = False
    reason: str = ""

    @property
    def objective(self):
        placements = [self.punch_placement, self.die_placement]
        return tuple(
            sum(p.objective[i] for p in placements if p is not None)
            for i in range(3)
        )

    @property
    def signature(self):
        """
        Identity of the physical setup: tool ids plus the installed run
        layout (used to count unique setups).
        """
        def runs_key(placement):
            if placement is None:
                return ()
            return tuple(
                (round(s.x_start, 3), s.length)
                for run in placement.runs for s in run.sections
            )
        return (self.punch_id, self.die_id,
                runs_key(self.punch_placement), runs_key(self.die_placement))


def solve_tool_placement(tool, required, forbidden, domain, inventory=None,
                         machine_x_length=None):
    """
    Choose sections and positions for one tool side.  ``domain`` is the
    (low, high) X range the solver may install within (typically the
    envelope x_range padded by the longest section).
    """
    placement = ToolPlacement(
        tool_id=tool.id, kind=tool.kind,
        mass_per_m=getattr(tool, "mass_kg_per_m", None))

    if required.is_empty():
        placement.feasible = True
        placement.reason = "nothing required"
        return placement

    if not required.intersect(forbidden).is_empty():
        placement.reason = "required span lies inside a forbidden zone"
        return placement

    stock = dict(inventory) if inventory is not None else section_inventory(tool)
    if not stock:
        placement.reason = "tool has no catalogue sections"
        return placement

    allowed = forbidden.complement(domain[0], domain[1])

    # group required intervals by their allowed region, largest spans first
    # so the shared inventory serves the hardest clusters first
    region_jobs = []
    for region in allowed.to_pairs():
        inside = required.intersect(IntervalSet([region]))
        if inside.is_empty():
            continue
        region_jobs.append((region, inside))
    covered = IntervalSet([job[0] for job in region_jobs]).intersect(required)
    if covered.measure() < required.measure() - EPSILON:
        placement.reason = "required region outside the allowed domain"
        return placement

    region_jobs.sort(key=lambda job: -job[1].measure())
    runs = []
    for region, inside in region_jobs:
        solved = _solve_region(region, inside.to_pairs(), stock)
        if solved is None:
            placement.reason = (
                "no section combination covers x in [{:.1f}, {:.1f}] "
                "(insufficient inventory or region too tight)".format(
                    region[0], region[1]))
            return placement
        runs.extend(solved)

    runs.sort(key=lambda run: run.x_start)
    placement.runs = runs

    if machine_x_length is not None:
        width = runs[-1].x_end - runs[0].x_start
        if width > machine_x_length + EPSILON:
            placement.reason = (
                "installed tooling spans {:.0f} mm, wider than the "
                "machine ({:.0f} mm)".format(width, machine_x_length))
            placement.runs = []
            return placement

    placement.feasible = True
    return placement


def solve_setup(envelopes, punch, die, machine=None, step_indices=None):
    """
    One shared physical setup for several bend envelopes (all evaluated for
    the same punch/die pair, coordinates already in machine X with each
    action's x_offset applied - v1 keeps all offsets at 0, i.e. bend starts
    aligned).
    """
    setup = SetupPlan(
        punch_id=punch.id, die_id=die.id,
        step_indices=list(step_indices) if step_indices is not None
        else list(range(len(envelopes))))

    required = IntervalSet()
    forbidden_punch = IntervalSet()
    forbidden_die = IntervalSet()
    for env in envelopes:
        if not env.forbidden_machine.is_empty():
            setup.reason = "machine interference in one of the bends"
            return setup
        # required_core: the spans that MUST be pressed (a few unsupported
        # mm at bend ends is acceptable practice, e.g. box corners)
        required = required.union(getattr(env, "required_core", env.required))
        forbidden_punch = forbidden_punch.union(env.forbidden_punch)
        forbidden_die = forbidden_die.union(env.forbidden_die)
    setup.required = required

    # fast compatibility test (concept section 21) before any placement work
    if not required.intersect(forbidden_punch).is_empty():
        setup.reason = "punch: union required meets union forbidden"
        return setup
    if not required.intersect(forbidden_die).is_empty():
        setup.reason = "die: union required meets union forbidden"
        return setup

    domain = _setup_domain(envelopes, punch, die)
    machine_x = machine.x_length if machine is not None else None

    setup.punch_placement = solve_tool_placement(
        punch, required, forbidden_punch, domain, machine_x_length=machine_x)
    if not setup.punch_placement.feasible:
        setup.reason = "punch: " + setup.punch_placement.reason
        return setup

    setup.die_placement = solve_tool_placement(
        die, required, forbidden_die, domain, machine_x_length=machine_x)
    if not setup.die_placement.feasible:
        setup.reason = "die: " + setup.die_placement.reason
        return setup

    if not check_section_seams(setup, envelopes):
        setup.reason = "section seam collision"
        return setup

    setup.feasible = True
    return setup


def check_section_seams(setup, envelopes):
    """
    Stage-2 seam check STUB (concept section 12): validate the X-normal end
    faces of adjacent sections against workpiece geometry near each seam,
    sliding seams over the critical-position lattice when a seam lands on a
    sensitive spot.  v1 accepts every placement; the placement geometry
    needed to implement this (run/section boundaries) is already available
    on the SetupPlan.
    """
    return True


def section_inventory(tool):
    """
    Aggregate catalogue sections into {length: available count} (horn
    sections included; their end-geometry meaning only matters at the
    stage-2 seam check).
    """
    stock = {}
    for section in tool.sections:
        stock[section.length] = stock.get(section.length, 0) + section.count
    return stock


# --- region solving ----------------------------------------------------------


def _solve_region(region, required_pairs, stock):
    """
    Cover the required intervals of one allowed region with runs; mutates
    ``stock`` on success.  Returns a list of ToolRun or None.
    """
    count = len(required_pairs)
    if count > MAX_CLUSTER_PARTITION:
        partitions = [_singleton_partition(count)]
    else:
        partitions = _consecutive_partitions(count)

    best = None
    best_objective = None
    for partition in partitions:
        candidate = _try_partition(region, required_pairs, partition, stock)
        if candidate is None:
            continue
        runs, used = candidate
        objective = (
            sum(len(run.sections) for run in runs),
            sum(run.length for run in runs),
        )
        if best_objective is None or objective < best_objective:
            best, best_objective = (runs, used), objective

    if best is None:
        return None
    runs, used = best
    for length, quantity in used.items():
        stock[length] -= quantity
    return runs


def _consecutive_partitions(count):
    """
    All partitions of items 0..count-1 into consecutive clusters, encoded
    as tuples of cluster sizes.
    """
    partitions = []
    for cuts in itertools.product((False, True), repeat=count - 1):
        sizes = []
        size = 1
        for cut in cuts:
            if cut:
                sizes.append(size)
                size = 1
            else:
                size += 1
        sizes.append(size)
        partitions.append(tuple(sizes))
    return partitions


def _singleton_partition(count):
    return tuple(1 for _ in range(count))


def _try_partition(region, required_pairs, partition, stock):
    """
    Solve one cluster partition of a region.  Windows: inter-cluster gaps
    split at their midpoints; if the midpoint cap fails, retry with
    left-greedy packing.  Returns (runs, used_inventory) or None.
    """
    clusters = []
    index = 0
    for size in partition:
        group = required_pairs[index:index + size]
        clusters.append((group[0][0], group[-1][1]))
        index += size

    windows = _midpoint_windows(region, clusters)
    result = _pack_clusters(clusters, windows, stock, greedy=False)
    if result is not None:
        return result
    return _pack_clusters(clusters, None, stock, greedy=True, region=region)


def _midpoint_windows(region, clusters):
    bounds = [region[0]]
    for left, right in zip(clusters[:-1], clusters[1:]):
        bounds.append((left[1] + right[0]) / 2.0)
    bounds.append(region[1])
    return list(zip(bounds[:-1], bounds[1:]))


def _pack_clusters(clusters, windows, stock, greedy=False, region=None):
    """
    Choose a section multiset per cluster and place the runs.  With
    ``greedy`` the runs are packed leftmost against the previous run inside
    the region instead of per-window.
    """
    working = dict(stock)
    used = {}
    runs = []
    previous_end = region[0] if greedy else None

    for order, cluster in enumerate(clusters):
        span = cluster[1] - cluster[0]
        if greedy:
            window = (previous_end, region[1])
        else:
            window = windows[order]
        room = window[1] - window[0]
        if room < span - EPSILON:
            return None

        multiset = _min_sections_for_span(span, room, working)
        if multiset is None:
            return None
        total = sum(length * quantity for length, quantity in multiset.items())

        # leftmost placement containing the cluster hull
        x_start = max(window[0], cluster[1] - total)
        if x_start > cluster[0] + EPSILON or x_start + total > window[1] + EPSILON:
            return None

        sections = []
        cursor = x_start
        for length in sorted(multiset, reverse=True):
            for _ in range(multiset[length]):
                sections.append(PlacedSection(length=length, x_start=cursor))
                cursor += length
        runs.append(ToolRun(sections=sections))

        for length, quantity in multiset.items():
            working[length] -= quantity
            used[length] = used.get(length, 0) + quantity
        if greedy:
            previous_end = cursor

    return runs, used


def _min_sections_for_span(span, room, stock):
    """
    Bounded-knapsack min-coins: the cheapest multiset of section lengths
    with total in [span, room], minimising (count, total length).  Exact on
    a 0.1 mm grid.  Returns {length: quantity} or None.
    """
    # totals beyond span + longest section are never optimal (some section
    # could always be dropped), so the DP grid stays small
    longest = max(stock) if stock else 0.0
    room = min(room, span + longest)
    target = int(math.ceil(span / GRID - EPSILON))
    cap = int(math.floor(room / GRID + EPSILON))
    if cap < target:
        return None
    target = max(target, 1)

    INF = float("inf")
    dp = [INF] * (cap + 1)
    choice = [None] * (cap + 1)
    dp[0] = 0

    # binary-split bounded counts into power-of-two bundles
    bundles = []
    for length, available in sorted(stock.items()):
        grid_length = int(round(length / GRID))
        if grid_length <= 0:
            continue
        remaining = available
        power = 1
        while remaining > 0:
            take = min(power, remaining)
            bundles.append((length, grid_length * take, take))
            remaining -= take
            power *= 2

    for length, grid_size, pieces in bundles:
        for total in range(cap, grid_size - 1, -1):
            candidate = dp[total - grid_size] + pieces
            if candidate < dp[total]:
                dp[total] = candidate
                choice[total] = (length, pieces, total - grid_size)

    best_total = None
    for total in range(target, cap + 1):
        if dp[total] == INF:
            continue
        if best_total is None or (dp[total], total) < (dp[best_total], best_total):
            best_total = total
    if best_total is None:
        return None

    multiset = {}
    total = best_total
    while total > 0:
        length, pieces, previous = choice[total]
        multiset[length] = multiset.get(length, 0) + pieces
        total = previous
    return multiset


def _setup_domain(envelopes, punch, die):
    """
    Installation domain: the union of the envelopes' x ranges padded by the
    longest catalogue section (tool may overhang the part).
    """
    low = min(env.x_range[0] for env in envelopes)
    high = max(env.x_range[1] for env in envelopes)
    lengths = [s.length for s in punch.sections] + [s.length for s in die.sections]
    pad = max(lengths) if lengths else 100.0
    return (low - pad, high + pad)
