"""
Bend-sequence search with setup-minimising assignment (phase 6-lite).

The search runs over sister-group orderings.  A state is the bitmask of
completed groups: the fold angles are fully determined by it (completed
bends at their relaxed angle, the rest flat), which makes states shareable
across all orderings that reach the same subset - the n!-to-2^n collapse
of the design concept.

Costs are gated cheap-to-expensive per move: precomputed tool candidates,
then self-collision (tool independent, cached per (mask, group, rotation)),
then collision envelopes through a global cache keyed
(mask, group, rotation, punch, die).  During the DFS only the FIRST
feasible (punch, die) pair is computed per move; the setup-assignment DP
afterwards pulls further pairs through the same cache, so nothing is ever
evaluated twice.

Ranking of complete sequences is lexicographic:
(setup changes, unique setups, section count, installed length, installed
mass, flip transitions).
"""

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from pressbrake import collision, envelope, kinematics, tooling

logger = logging.getLogger("pressbrake.sequence")

HEM_LIMIT = math.radians(150.0)


@dataclass
class SearchConfig:
    max_solutions: int = 8
    max_groups: int = 10          # guard; beyond this beam_width is required
    beam_width: int = None
    margin: float = 2.0


@dataclass
class ProcessStep:
    bend_ids: tuple
    sister_group: int
    action: object                # BendAction (rotation/flip fixed)


@dataclass
class ProcessPlan:
    steps: list
    setups: list                  # SetupPlan; step_indices partition the steps
    objective: tuple

    @property
    def feasible(self):
        return bool(self.setups) and all(s.feasible for s in self.setups)


@dataclass
class SearchResult:
    plans: list = field(default_factory=list)
    exhaustive: bool = False
    stats: dict = field(default_factory=dict)

    @property
    def feasible(self):
        return bool(self.plans)


class _Evaluator:
    """
    All caching: self-collision verdicts, envelopes, and per-move first
    feasible pair.  done_mask fully determines the fold state.
    """

    def __init__(self, graph, machine, punches, dies, groups, margin):
        self.graph = graph
        self.machine = machine
        self.margin = margin
        self.groups = groups                       # ordered list of (key, bend_ids)
        self.group_bends = dict(groups)
        self.actions = {
            (key, rotation): action
            for key, bend_ids in groups
            for rotation, action in enumerate(kinematics.enumerate_actions(graph, bend_ids))
        }
        self.candidates = {
            key: _tool_candidates(graph, graph.bends[bend_ids[0]], punches, dies)
            for key, bend_ids in groups
        }
        self._theta_cache = {}
        self._self_cache = {}
        self._envelope_cache = {}
        self.stats = {"envelopes": 0, "self_checks": 0,
                      "envelope_hits": 0, "states": 0, "dead_states": 0}

    def theta(self, done_mask):
        if done_mask not in self._theta_cache:
            theta = np.zeros(self.graph.bend_count)
            for index, (key, bend_ids) in enumerate(self.groups):
                if done_mask >> index & 1:
                    for bend_id in bend_ids:
                        theta[bend_id] = self.graph.bends[bend_id].angle_relaxed
            self._theta_cache[done_mask] = theta
        return self._theta_cache[done_mask]

    def self_collides(self, done_mask, group_index, rotation):
        cache_key = (done_mask, group_index, rotation)
        if cache_key not in self._self_cache:
            key, bend_ids = self.groups[group_index]
            action = self.actions[(key, rotation)]
            self.stats["self_checks"] += 1
            hits = collision.check_self_collision(
                self.graph, self.theta(done_mask), action,
                margin=self.margin, stop_on_first=True)
            self._self_cache[cache_key] = bool(hits)
        return self._self_cache[cache_key]

    def envelope_for(self, done_mask, group_index, rotation, punch, die):
        cache_key = (done_mask, group_index, rotation, punch.id, die.id)
        if cache_key in self._envelope_cache:
            self.stats["envelope_hits"] += 1
            return self._envelope_cache[cache_key]
        key, _bend_ids = self.groups[group_index]
        action = self.actions[(key, rotation)]
        self.stats["envelopes"] += 1
        result = envelope.compute_envelope(
            self.graph, self.theta(done_mask), action, punch, die,
            self.machine, margin=self.margin)
        self._envelope_cache[cache_key] = result
        return result

    def first_feasible_pair(self, done_mask, group_index, rotation):
        """
        Lazily find one feasible (punch, die) for a move, or None.
        """
        key, _ = self.groups[group_index]
        for punch, die in self.candidates[key]:
            result = self.envelope_for(done_mask, group_index, rotation, punch, die)
            if result.feasible:
                return punch, die, result
        return None

    def pair_feasible(self, done_mask, group_index, rotation, punch, die):
        return self.envelope_for(
            done_mask, group_index, rotation, punch, die).feasible


def search_sequences(graph, machine=None, punches=None, dies=None, config=None):
    """
    Enumerate feasible orderings of the sister groups and rank complete
    process plans (sequence + setup assignment + concrete tooling).
    """
    config = config or SearchConfig()
    punches = punches or {}
    dies = dies or {}

    groups = sorted(graph.sister_groups().items())
    result = SearchResult()

    for _key, bend_ids in groups:
        if abs(graph.bends[bend_ids[0]].angle_target) > HEM_LIMIT:
            result.exhaustive = True
            result.stats["reason"] = "hem bend outside the air-bend model"
            return result

    if len(groups) > config.max_groups and config.beam_width is None:
        raise ValueError(
            "{} sister groups exceeds max_groups={}; set beam_width for a "
            "bounded search".format(len(groups), config.max_groups))

    evaluator = _Evaluator(graph, machine, punches, dies, groups, config.margin)
    full_mask = (1 << len(groups)) - 1
    dead_states = set()
    sequences = []
    aborted = [False]

    def moves(done_mask):
        found = []
        for group_index in range(len(groups)):
            if done_mask >> group_index & 1:
                continue
            for rotation in (0, 1):
                if not evaluator.candidates[groups[group_index][0]]:
                    continue
                if evaluator.self_collides(done_mask, group_index, rotation):
                    continue
                first = evaluator.first_feasible_pair(done_mask, group_index, rotation)
                if first is None:
                    continue
                # order heuristic: fewer forbidden gaps first (cleaner tooling)
                forbidden = first[2].forbidden_punch.union(first[2].forbidden_die)
                found.append(((len(forbidden), forbidden.measure()),
                              group_index, rotation))
        found.sort(key=lambda move: move[0])
        if config.beam_width is not None:
            found = found[:config.beam_width]
        return found

    def dfs(done_mask, path):
        evaluator.stats["states"] += 1
        if done_mask == full_mask:
            sequences.append(list(path))
            return len(sequences) < config.max_solutions
        if done_mask in dead_states:
            return True
        progressed = False
        for _score, group_index, rotation in moves(done_mask):
            progressed = True
            path.append((group_index, rotation))
            keep_going = dfs(done_mask | (1 << group_index), path)
            path.pop()
            if not keep_going:
                aborted[0] = True
                return False
        if not progressed:
            dead_states.add(done_mask)
            evaluator.stats["dead_states"] += 1
        return True

    dfs(0, [])
    result.exhaustive = not aborted[0] and config.beam_width is None

    for sequence_path in sequences:
        plan = _assemble_plan(graph, evaluator, groups, sequence_path, machine)
        if plan is not None:
            result.plans.append(plan)

    result.plans.sort(key=lambda plan: plan.objective)
    result.stats = dict(evaluator.stats)
    result.stats["sequences_found"] = len(sequences)
    return result


# --- setup assignment --------------------------------------------------------


def _assemble_plan(graph, evaluator, groups, sequence_path, machine):
    """
    Build the ProcessPlan for one complete ordering: minimal consecutive
    setup blocks via DP over sequence positions, full lexicographic tuples
    as DP values.
    """
    steps = []
    masks = []
    done_mask = 0
    for group_index, rotation in sequence_path:
        key, bend_ids = groups[group_index]
        action = evaluator.actions[(key, rotation)]
        steps.append(ProcessStep(
            bend_ids=tuple(bend_ids), sister_group=key, action=action))
        masks.append(done_mask)
        done_mask |= 1 << group_index

    count = len(steps)
    INFINITY = (float("inf"),)
    cost = [INFINITY] * (count + 1)
    cost[0] = (0, 0.0, 0.0, 0.0)
    parent = [None] * (count + 1)

    block_cache = {}

    def block_setup(start, end):
        if (start, end) not in block_cache:
            block_cache[(start, end)] = _best_block_setup(
                evaluator, sequence_path, masks, start, end, machine)
        return block_cache[(start, end)]

    for end in range(1, count + 1):
        for start in range(end):
            if cost[start] == INFINITY:
                continue
            setup = block_setup(start, end)
            if setup is None:
                continue
            candidate = (
                cost[start][0] + 1,
                cost[start][1] + setup.objective[0],
                cost[start][2] + setup.objective[1],
                cost[start][3] + setup.objective[2],
            )
            if candidate < cost[end]:
                cost[end] = candidate
                parent[end] = start

    if cost[count] == INFINITY:
        return None

    setups = []
    end = count
    boundaries = []
    while end > 0:
        start = parent[end]
        boundaries.append((start, end))
        end = start
    for start, end in reversed(boundaries):
        setup = block_setup(start, end)
        setup.step_indices = list(range(start, end))
        setups.append(setup)

    flips = sum(
        1 for a, b in zip(steps[:-1], steps[1:])
        if a.action.flip != b.action.flip
    )
    unique = len({setup.signature for setup in setups})
    objective = (
        len(setups) - 1,                                   # setup changes
        unique,                                            # unique setups
        int(cost[count][1]),                               # section count
        round(cost[count][2], 3),                          # installed length
        round(cost[count][3], 6),                          # installed mass
        flips,                                             # flip transitions
    )
    return ProcessPlan(steps=steps, setups=setups, objective=objective)


def _best_block_setup(evaluator, sequence_path, masks, start, end, machine):
    """
    Best single SetupPlan covering steps start..end-1, or None: one
    (punch, die) pair must be feasible for every step, then the shared
    placement must solve.
    """
    indices = range(start, end)
    first_group, _first_rotation = sequence_path[start]
    key = evaluator.groups[first_group][0]
    pair_pool = evaluator.candidates[key]

    best = None
    for punch, die in pair_pool:
        envelopes = []
        for index in indices:
            group_index, rotation = sequence_path[index]
            if (punch, die) not in evaluator.candidates[evaluator.groups[group_index][0]]:
                envelopes = None
                break
            env = evaluator.envelope_for(
                masks[index], group_index, rotation, punch, die)
            if not env.feasible:
                envelopes = None
                break
            envelopes.append(env)
        if envelopes is None:
            continue
        setup = tooling.solve_setup(
            envelopes, punch, die, machine, step_indices=list(indices))
        if not setup.feasible:
            continue
        if best is None or setup.objective < best.objective:
            best = setup
    return best


def _tool_candidates(graph, bend, punches, dies):
    """
    (punch, die) pairs that can process a bend: angle capability on both
    sides, die thickness window.  Shared with plan.py.
    """
    candidates = []
    for punch in punches.values():
        if not punch.fits_angle(bend.angle_target):
            continue
        for die in dies.values():
            if not die.fits_thickness(graph.thickness):
                continue
            if not die.fits_angle(bend.angle_target):
                continue
            candidates.append((punch, die))
    return candidates
