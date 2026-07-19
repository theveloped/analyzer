"""Dependency resolver: auto-run prerequisites, reuse cached results.

Given a target ``"process/analysis"`` and its params, ``ensure`` walks the
``AnalysisDef.requires`` graph, runs any prerequisite whose on-disk artifact is
missing or stale, reuses the rest, then runs the target.

Framework-free (importable without fastapi) so the CLI and the API job worker
share it. It runs everything **inline on the caller's thread** — the API worker
is single-threaded on purpose (meshlib is not concurrency-safe), so prerequisites
execute in sequence within one job, never as separate concurrent jobs.

Scope of auto-running (see ``AnalysisDef.is_current``): only prerequisites that
declare an ``is_current`` gate (the ``prep`` stages) are auto-run. Results-tier
prerequisites — whose ``run`` already self-caches and whose params must be
derived from the target rather than defaulted — are left to the existing
runner/manual flow, so param-sensitive chains (e.g. CNC precompute→compose) keep
working exactly as before. Invalidation cascades by construction: re-running a
changed upstream (e.g. the mesh) changes its content fingerprint, which flips the
downstream ``is_current`` gate and re-salts every results-tier cache key.
"""

from processes.base import AnalysisResult, apply_defaults

# get_analysis is imported lazily inside the functions below: process modules
# import this resolver at their top level, and processes/__init__ defines
# get_analysis only after those module imports finish, so importing it here at
# module load would fail during package initialization.


def _scaled(progress, lo, hi):
    """Map a child's [0,1] progress into the [lo,hi] sub-range of ``progress``."""
    if progress is None:
        return None
    return lambda fraction, message: progress(lo + (hi - lo) * fraction, message)


def ensure(workdir, target_id, params=None, progress=None, *, seen=None):
    """Ensure ``target_id`` (``"process/analysis"``) is computed and return it.

    Gateable prerequisites (those declaring ``is_current``) are run only when
    stale or missing — the gate is checked at the point of recursion, so a
    current prerequisite is skipped entirely and never recomputed. The target
    itself is always run: results-tier runners self-cache (cheap, and they
    return the real stats their callers need), and an explicit request for a
    prep stage means the caller wants it (re)built. Returns the target's
    ``AnalysisResult``.
    """
    from processes import get_analysis

    if seen is None:
        seen = set()

    process_id, analysis_id = _split(target_id)
    analysis = get_analysis(process_id, analysis_id)
    merged = apply_defaults(analysis, params or {})

    # gateable prerequisites first (topological, deduped), sharing the front
    # third of the progress bar; the target gets the rest
    prereqs = [req for req in analysis.requires
               if _gateable(req) and req not in seen]
    if prereqs:
        span = 0.3 / len(prereqs)
        for index, req in enumerate(prereqs):
            seen.add(req)
            if _is_current(workdir, req):
                continue  # prerequisite already satisfied — skip its subtree
            ensure(workdir, req, None,
                   _scaled(progress, index * span, (index + 1) * span),
                   seen=seen)
        target_progress = _scaled(progress, 0.3, 1.0)
    else:
        target_progress = progress

    result = analysis.run(workdir, merged, target_progress)
    return result if result is not None else AnalysisResult()


def _split(target_id):
    if "/" not in target_id:
        raise ValueError(f"analysis id must be 'process/analysis', got {target_id!r}")
    process_id, analysis_id = target_id.split("/", 1)
    return process_id, analysis_id


def cache_key(workdir, target_id, params):
    """The content-addressed results-tier cache key for an analysis.

    One place builds every runner's key: the declared params, the analysis's
    `schema`, the fingerprints of its transitive prep prerequisites (each prep
    stage contributes via `salt_fields`), the `splits` salt when opted in, and
    any literal `key_extra` discriminators. Passed to `load_cached_result` /
    `store_result` (which apply `params_hash`). Replaces the per-runner
    hand-salted `{**params, "mesh": mesh_fingerprint(...), ...}` dicts, so
    invalidation is derived from `requires` rather than duplicated by hand.
    """
    from processes import get_analysis

    process_id, analysis_id = _split(target_id)
    analysis = get_analysis(process_id, analysis_id)
    # key on this analysis's own declared params only — so a cache-aware sub-run
    # that hands a runner a superset dict (e.g. sprue → wall_skeleton) keys the
    # sub-result on just its own knobs, sharing it with a standalone run
    own = {p.name for p in analysis.params}
    key = {name: value for name, value in params.items() if name in own}
    if analysis.schema is not None:
        key["schema"] = analysis.schema
    for salt in _prep_salts(workdir, analysis):
        key.update(salt)
    if "splits" in analysis.salts:
        import pipeline
        key["splits"] = pipeline.splits_fingerprint(workdir)
    if analysis.key_extra:
        key.update(analysis.key_extra)
    return key


def _prep_salts(workdir, analysis, *, seen=None):
    """Fingerprint contributions of every transitive prep prerequisite.

    Walks the full `requires` DAG (through results-tier nodes too, e.g.
    setup_verdict → setups → prep/directions) and yields each prep stage's
    `salt_fields(workdir)` dict once.
    """
    from processes import get_analysis

    if seen is None:
        seen = set()
    for req in analysis.requires:
        if req in seen:
            continue
        seen.add(req)
        req_analysis = get_analysis(*_split(req))
        if req_analysis.salt_fields is not None:
            yield req_analysis.salt_fields(workdir)
        yield from _prep_salts(workdir, req_analysis, seen=seen)


def _gateable(target_id):
    """True if the analysis declares an is_current gate (prep-tier)."""
    from processes import get_analysis
    process_id, analysis_id = _split(target_id)
    return get_analysis(process_id, analysis_id).is_current is not None


def _is_current(workdir, target_id):
    """Whether a gateable prerequisite is already satisfied on disk.

    Evaluated with the prerequisite's default params: any valid artifact is
    reusable, so a user who wants a non-default resolution / direction count
    re-runs that stage explicitly rather than through a downstream request.
    """
    from processes import get_analysis
    process_id, analysis_id = _split(target_id)
    analysis = get_analysis(process_id, analysis_id)
    return analysis.is_current(workdir, apply_defaults(analysis, {}))
