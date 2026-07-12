# CLAUDE.md

@AGENTS.md

The file above is the operating manual for this repo — hard rules, commands, tests
and conventions. Deeper references, in reading order per task:

- APPROACH.md — algorithm/methodology background (read before geometry changes)
- TESTING.md — end-to-end workflows and performance knobs
- docs/CODEMAP.md — file map, cache/data contracts, API routes, frontend seam
- docs/RECIPES.md — step-by-step procedures for common change types

Quick reminders (details in AGENTS.md): keep the `tollerance` spelling; never run
meshlib concurrently; face indices into `fine_faces.npy` are stable and sacred;
bump `DirectionCache.VERSION` when changing zcache field semantics; root
`test_*.py` are plain scripts (`python test_zmap.py`), not pytest.
