# 2D contour nesting — exploration notes

Status: **standalone sandbox** (`nesting.py` + `test_nesting.py`), not wired into
the pipeline. This document records what was learned extracting the core nesting
algorithm from Deepnest, what the sandbox can do today, measured performance, and
the path to a real `sheet_metal` analysis once STEP→unfolded-contour conversion
lands.

## Why and what

Sheet-metal (and CNC plate) quoting needs to know how many parts fit on a sheet.
The validation target here matches the analyzer's current single-part scope: given
**one** part's unfolded 2D outer contour and a rectangular sheet, find a dense
nest (count, positions, rotations, utilization). Multi-part mixes, quantities and
multiple sheet sizes come later — the geometry core below carries over unchanged;
what's missing for that is an ordering optimizer (see roadmap).

## Anatomy of Deepnest, and what is worth extracting

[deepnest-next](https://github.com/deepnest-next/deepnest) (MIT, the maintained
fork of Deepnest, itself a fork of SVGNest) is an Electron app whose nesting core
is three separable ideas:

1. **No-fit polygons (NFP).** For placed part A and candidate part B, the NFP is
   the set of translations of B's reference point where B collides with A. It is
   computed as a Minkowski sum, `NFP(A,B) = A ⊕ (−B)` — Deepnest does this in a
   native addon (Boost.Polygon convolution) with a Clipper-based JS fallback.
   Once NFPs exist, placement validity is point-in-polygon, never a polygon
   overlap test. NFPs depend only on the shape pair + rotations, so they are
   cached and reused across all iterations.
2. **Greedy placement worker.** Parts are placed one at a time; valid positions
   for the next part are derived from the placed parts' NFPs intersected with the
   sheet's inner-fit polygon (IFP); the candidate minimizing a fitness metric
   wins ("gravity" = minimize `5·width + height` of the combined bounding box).
3. **Genetic algorithm** over part *order* and *rotations*, re-running the greedy
   placement per individual. This is the layer that pays off for mixed parts;
   with identical parts there is no ordering to evolve.

Porting the JS/Electron code as-is would drag in a Node runtime and IPC for ~1k
lines of actual algorithm. The extractable core is small once you have a robust
polygon-clipping kernel — and `pyclipper` (the same Clipper library Deepnest
builds on, C++ under a thin Cython wrapper) provides `MinkowskiSum`, boolean ops
and point-in-polygon directly. `nesting.py` reimplements the algorithm in ~300
lines of numpy + pyclipper. No code was copied; the NFP/Minkowski approach is
published literature (Burke et al. 2007) and SVGNest/Deepnest are MIT anyway.

## What the sandbox does (and where it deviates)

`nesting.py :: nest_single(contour, sheet_w, sheet_h, spacing, margin, rotations,
count, strategy)` fills a rectangular sheet with copies of one contour:

- **NFPs via Clipper Minkowski sums**, cached per rotation pair — `R²` sums for
  `R` rotations, computed once up front.
- **Exact inner-fit region for free**: containment in an axis-aligned rectangle
  depends only on the part's bbox, so the sheet IFP is a closed-form rectangle
  per rotation (no Minkowski erosion needed).
- **Exact spacing**: the placed-part contour is inflated by the full spacing with
  *round* joins before the Minkowski sum (`dist(A,B) ≥ s  ⇔  B misses A ⊕ disc_s`),
  so minimum web width holds at corners too, not just along edges. Sheet margin
  shrinks the IFP.
- **Greedy gravity placement with per-placement rotation choice** — Deepnest's
  placement worker minus the GA, which identical parts don't need.

Two deviations from Deepnest, both found the hard way (the test suite catches
them):

- **Candidate positions come from the constraint arrangement, not from a boolean
  difference.** Deepnest evaluates vertices of `difference(IFP, ⋃ NFPs)`. When
  parts tile exactly, the free region between them has zero area, the difference
  drops it, and valid touching placements silently disappear — a 10×10 square on
  a 100×100 sheet nests 84/100 that way. The sandbox instead maintains, per
  rotation, the pool of arrangement vertices (NFP vertices, NFP–NFP crossings,
  NFP–IFP crossings, computed incrementally via pairwise Clipper intersections of
  bbox-overlapping NFPs); points die permanently once a new NFP strictly swallows
  them. Any tight placement lies on such a vertex, so exact tilings survive:
  the square test packs 100/100 and the L-shape test hits the provable optimum.
  This is also cheaper — no ever-growing union/difference, new NFPs only interact
  with bbox-neighbours.
- **Clipper's `MinkowskiSum` returns the pattern swept along the path *outline***
  (an annular band), not the filled sum. Its inner contours are **not** NFP holes
  — a solid part strictly inside another's outline still collides. The sandbox
  keeps only positive-orientation contours after `SimplifyPolygons` (Deepnest's
  "largest area child" trick, done properly). Consequence: positions where a part
  would float inside an *enclosed* cavity of another part are conservatively
  treated as collisions — irrelevant until part holes are modelled at all.

Contours are plain `(N,2)` float arrays in mm — exactly what an unfold step will
produce; Clipper runs on int64 at 0.1 µm resolution. Results carry placements
`(rotation, x, y)`, utilization, and can be dumped to SVG (`write_svg`) for
eyeballing.

## Validation & performance

`python test_nesting.py` asserts analytic expectations (see file): exact grid
counts for squares/rects incl. spacing (pitch arithmetic) and margin, L-shape
interlocking reaching the provable 30-part optimum on 100×100 (a bounding-box
grid manages 25), no pairwise overlap > 1 µm², containment, pairwise clearance.

Benchmark: irregular 16-vertex concave bracket (120×80 mm bbox, 5922 mm² area),
spacing 2 mm, margin 5 mm, 4 rotations, `gravity`, pure Python on one core:

| sheet (mm) | parts | utilization | bbox-grid util | runtime |
|---|---|---|---|---|
| 500×500 | 22 | 52% | 57% (24 parts) | 0.3 s |
| 1000×500 | 49 | 58% | 57% | 0.5 s |
| 2000×1000 | 215 | 64% | 57% | 3.0 s |
| 3000×1500 | 486 | 64% | 57% | 12.6 s |

~10–25 ms per placed part, growing mildly with density (candidate-pool filtering
is the O(n²)-ish term). Small-sheet counts are where greedy leaves value on the
table (22 vs 24 on 500×500 — edge slivers; `strategy="bbox"` with 8 rotations
recovers 24 at 2.7 s). That gap, not raw speed, is what the GA layer buys.

Performance headroom, in order of leverage: candidate evaluation is already
numpy-vectorized and all heavy geometry is C++ (Clipper) — the remaining Python
cost is the per-point `PointInPolygon` filtering loop, which could batch through
a prepared numpy winding test; Clipper2 (`pyclipper` successor) is ~2× on
booleans; and unlike everything meshlib-touching, **nesting is safe to
parallelize** (per-sheet-size / per-strategy runs in processes). NP-hardness
lives in placement *quality*, not runtime blowup: the greedy is polynomial, and
quality is bought with restarts/GA generations, which scale linearly and
parallelize embarrassingly.

## Tiling patterns: nest once, estimate any sheet instantly

`find_tiling(contour, max_parts=N, rotations=R, spacing=s)` answers a
different question than `nest_single`: instead of packing one specific sheet,
it finds the best **periodic pattern** — a motif of up to N placed parts plus
two lattice vectors `v1, v2` such that the motif repeated at every
`m·v1 + n·v2` never collides. This is minimum-area lattice packing: the
pattern with the smallest cell area `|v1 × v2| / k` wins, and its asymptotic
utilization is a property of the part alone, independent of any sheet.

The search reuses the NFP machinery end to end. Motifs are built from
touching positions on the pairwise NFP arrangement (beam-pruned by convex
hull area per rotation multiset); each motif's **self-NFP** is the union of
pairwise NFPs offset by the parts' relative positions; candidate generators
are sampled on the self-NFP boundary (vertices for interlock optima, exact
axis crossings for grid/brick optima, step-multiple edge samples in between)
and scanned in ascending cell-area order — the first *valid* basis (no
lattice combination strictly inside the self-NFP, nearest neighbours probed
first) is optimal up to boundary sampling. Two finite-sheet realities shaped
the implementation:

- **Equal-area bases tie-break toward axis-aligned generators.** A sheared
  lattice that matches the grid's density asymptotically loses parts to edge
  effects on every real sheet.
- **Near-optimal alternates ride along.** The scan keeps every valid basis
  within 8% of the best cell area; `TilePattern.count(w, h, margin)` evaluates
  primary + alternates (and optimizes the lattice phase over a small grid) and
  reports whichever fits most. A 0.2% asymptotic win can cost 30% of the parts
  on a small sheet — counting per sheet against a handful of patterns fixes
  that for free, since counting is closed-form interval arithmetic per lattice
  row (~milliseconds per sheet size).

Counts are conservative estimates: the pattern never exploits edge slivers a
greedy nest could still fill — except it turns out the *pattern* usually wins
anyway. For the benchmark bracket (2 mm spacing, 4 rotations), the optimal
pattern reaches **79.6% asymptotic utilization vs the greedy's 64%**, because
the greedy's local gravity metric never discovers the globally best repeat:

| sheet (mm) | pattern est. | est. time | greedy | greedy time |
|---|---|---|---|---|
| 500×500 | 23 | 21 ms | 22 | 0.3 s |
| 1000×500 | 50 | 33 ms | 49 | 0.6 s |
| 2000×1000 | 233 | 123 ms | 215 | 3.3 s |
| 3000×1500 | 547 | 269 ms | 486 | 12.1 s |

The pattern search itself runs once per part: 0.33 s at `max_parts=1`, 5.2 s
at `max_parts=2` (which bought only +0.1% here — single-part lattices already
alternate rotations between rows via the motif choice), 187 s at
`max_parts=3` for nothing. **Default `max_parts=2` and stop there**; larger
motifs grow the search combinatorially and pay off only for exotic shapes.
`TilePattern.realize(w, h, margin)` materializes a pattern as a `NestResult`
(same invariant checks and SVG dump as greedy results), and test_nesting.py
asserts exact optima where they are provable: 100 mm²/part squares,
200 mm²/part 20×10 rects, and the L-shape rep-tile at 300 mm²/part — 100%
utilization from translations alone.

A greedy `nest_single` on the target sheet remains useful as a cross-check
and for one-off sheets; the pattern is what quoting wants: one search per
part, then instant count curves over candidate sheet sizes.

## Roadmap to a real analysis

1. **Contour input.** The in-progress STEP→unfold work should hand over the
   outer boundary as an mm polyline (arcs tessellated at ~0.05 mm sagitta; kerf
   compensation, if any, belongs upstream of nesting). Holes can come along for
   area/utilization math even while ignored for collision.
2. **Multi-part mixes** (the "later" goal): keep the identical geometry core; add
   the NFP cache keyed `(shape_a, rot_a, shape_b, rot_b)` and a GA (or simpler:
   biased-random restarts) over part order + rotation assignment, fitness =
   sheets used, then tail-sheet utilization — this is precisely Deepnest's GA and
   is ~150 lines on top of `nest_single`'s loop generalized to a part sequence.
3. **Part holes / parts-in-holes**: needs true NFPs with holes — either
   Boost.Polygon convolution like Deepnest's addon, or hole-aware Minkowski
   composition in Clipper. Worth it only if real parts have large cutouts.
4. **Registry wiring** (docs/RECIPES.md recipe applies): `processes/sheet_metal.py`
   already exists as a placeholder — an `AnalysisDef("nesting", params: sheet
   W/H, spacing, margin, rotations, quantity)` storing placements JSON via
   `store_result`, plus a `frontend/src/processes/sheet_metal/` plugin rendering
   the 2D nest (orthographic three.js or inline SVG panel) and readouts
   (count, utilization). Nesting is meshlib-free, so it can even run outside the
   single job worker if it ever needs to.

Alternatives weighed: running deepnest-next as a Node sidecar (rejected: Electron
-grade dependency and IPC for a core we can hold in 300 lines); `libnest2d` /
`pynest2d` (C++ engine from PrusaSlicer lineage — strong, but packaging is rough
and it optimizes the same NFP+meta-heuristic loop we now own end-to-end).
