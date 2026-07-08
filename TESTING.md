# Testing the endmill accessibility analysis locally

## Setup

```bash
git fetch origin
git checkout claude/cnc-tool-accessibility-analysis-rmm8wq
pip install meshlib numpy loguru
```

Requires meshlib >= 3 (the code also still runs on 2.x via API fallbacks).
STEP input needs the pip meshlib wheel (bundles the STEP reader); STL always works.

## Sanity check

```bash
python test_endmill.py
```

Builds a synthetic pocket + slot part and verifies ball / bull-nose / flat
behaviour end to end (takes a few minutes).

## Analyzing a part

Stage 1 — mesh (accepts .stl, .stp, .step; the healed result is cached in a
working directory named after the part):

```bash
python main.py mesh tests/testpart_42.stp --heal --tollerance 0.15
```

Stage 2 — accessibility per direction. `--axes` prepends exact ±X/±Y/±Z as
indices 0..5 (so +Z is index 4). `--relax` is important for endmill work:
without it, strictly vertical walls count as undercuts and can never be
flagged by the tool checks:

```bash
python main.py directions testpart_42 --count 16 --axes --relax
```

Stage 3 — tool tip check for one (direction, diameter, corner radius):

```bash
# flat endmill D6
python main.py endmill testpart_42 4 --diameter 6 --corner_radius 0 --tollerance 0.15
# bull nose D6 rc1
python main.py endmill testpart_42 4 --diameter 6 --corner_radius 1 --tollerance 0.15
# ball nose D6
python main.py endmill testpart_42 4 --diameter 6 --corner_radius 3 --tollerance 0.15
```

Unreachable-face indices land in `<dir>/highlights.json`; add `--serve` to
open the three.js viewer (Windows only as written — elsewhere run
`python -m http.server` in the repo root and open `index.html` served from
the working directory).

Tool length checks are still the separate `length` command (ball model):

```bash
python main.py length testpart_42 4 --diameter 6 --length 120
```

## Knobs vs runtime

Reference timings, testpart_42 (100 x 100 x 40 mm) at voxel 0.15 mm, one
direction: ball ~1 min, flat ~4.5 min, bull nose ~6 min. The flat/bull cost
is dominated by the anisotropic stretch (`--scale`, default 10): the voxel
grid grows ~scale x along the tool axis.

- `--tollerance` is the voxel size; halving it costs ~8x. 0.3 mm is fine for
  roughing-level answers, 0.1-0.15 mm for fine detail.
- `--scale` bounds flat-floor sensitivity: deviations below
  ~0.41 * (D - 2 rc) / scale can't be distinguished from the disk
  approximation residual (the command warns and raises its threshold when
  that bound exceeds the tolerance).
