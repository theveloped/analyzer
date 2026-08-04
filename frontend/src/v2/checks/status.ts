import type { Job, Manifest, RouteCheckStatus, ResultEntry } from '../../api/types';
import type { StatusKind } from '../components/status';
import type { Analysis } from '../analyses';

/**
 * The check status model: independent axes, never one field (see
 * docs/ROUTE-ARCHITECTURE.md). Execution says whether/where the computation
 * ran; verdict says what it found. "Computed" is not "good".
 *
 * The verdict here is PROVISIONAL: it reads the live viewer threshold against
 * the stored stats. Phase 1 replaces it with policies pinned on plan checks
 * (evaluated in v2/checks/evaluators.ts) so verdicts become reproducible.
 */

export type ExecutionState =
  | 'not_run' | 'queued' | 'running' | 'current' | 'stale' | 'error';

export type VerdictState = 'pass' | 'review' | 'fail' | 'na' | 'unknown';

export interface CheckState {
  execution: ExecutionState;
  verdict: VerdictState;
  result: ResultEntry | null;
  /** Short execution note for summary lines ('', 'running…', 'stale — re-run'). */
  note: string;
}

/** Latest stored result for an analysis (manifest lists oldest→newest). */
export function resultFor(
  manifest: Manifest | null, a: AnalysisRef,
): ResultEntry | null {
  if (!manifest) return null;
  const list = manifest.results.filter(
    (r) => r.process === a.process && r.analysis === a.analysis,
  );
  return list[list.length - 1] ?? null;
}

/** Anything naming a backend analysis (catalog Analysis satisfies this). */
export interface AnalysisRef { process: string; analysis: string }

function latestJob(jobs: Job[], partId: string | null, a: AnalysisRef): Job | null {
  for (let i = jobs.length - 1; i >= 0; i--) {
    const j = jobs[i];
    if (j.part_id === partId && j.process === a.process && j.analysis === a.analysis) {
      return j;
    }
  }
  return null;
}

/** The execution axis alone, for anything that names a backend analysis —
 * a catalog check, a lens with a backing analysis, one column of the
 * directions table. Verdict is a separate axis and is not decided here. */
export function executionState(
  manifest: Manifest | null,
  jobs: Job[],
  partId: string | null,
  a: AnalysisRef,
): { execution: ExecutionState; note: string; result: ResultEntry | null } {
  const result = resultFor(manifest, a);
  const job = latestJob(jobs, partId, a);

  if (job?.status === 'queued') return { execution: 'queued', note: 'queued…', result };
  if (job?.status === 'running') return { execution: 'running', note: 'running…', result };
  if (job?.status === 'error' && !result) {
    return { execution: 'error', note: 'failed', result };
  }
  if (!result) return { execution: 'not_run', note: 'not run', result };
  if (result.stale) return { execution: 'stale', note: 'stale — re-run', result };
  return { execution: 'current', note: '', result };
}

export function checkState(
  manifest: Manifest | null,
  jobs: Job[],
  partId: string | null,
  a: Analysis,
  threshold: number,
): CheckState {
  const { execution, note, result } = executionState(manifest, jobs, partId, a);

  // Provisional verdict: flagged-direction analyses store the field minimum;
  // a minimum past the engineer's limit means there are findings to review.
  let verdict: VerdictState = 'unknown';
  if (execution === 'current' || execution === 'stale') {
    const min = (result?.stats as Record<string, unknown>)?.min;
    if (typeof min === 'number' && isFinite(threshold)) {
      verdict = min >= threshold ? 'pass' : 'review';
    }
  }

  return { execution, verdict, result, note };
}

/** State of a PLAN check: execution comes from the server-derived expected
 * hash (exists/stale) plus the live job overlay; the verdict is evaluated
 * separately against the pinned policy (evaluators.ts) and merged here. */
export function planCheckState(
  status: RouteCheckStatus | undefined,
  jobs: Job[],
  partId: string | null,
  a: AnalysisRef,
  verdict: VerdictState,
): CheckState {
  const job = latestJob(jobs, partId, a);
  let execution: ExecutionState;
  let note = '';
  if (job?.status === 'queued') {
    execution = 'queued'; note = 'queued…';
  } else if (job?.status === 'running') {
    execution = 'running'; note = 'running…';
  } else if (!status || status.error) {
    execution = 'error'; note = status?.error ? 'plan config error' : 'no status';
  } else if (status.exists) {
    execution = 'current';
  } else if (status.stale) {
    execution = 'stale'; note = 'stale — re-run';
  } else if (job?.status === 'error') {
    execution = 'error'; note = 'failed';
  } else {
    execution = 'not_run'; note = 'not run';
  }
  const effective = (execution === 'current' || execution === 'stale')
    ? verdict : 'unknown';
  return { execution, verdict: effective, result: null, note };
}

/** The stored result a plan check's expected hash points at, if present.
 * Hashes are per-param-dict, NOT per-analysis (two analyses with identical
 * params share a hash — the analysis id lives in the store directory), so
 * the analysis must be part of the match. */
export function resultForHash(
  manifest: Manifest | null, a: AnalysisRef, expectedHash: string | null,
): ResultEntry | null {
  if (!manifest || !expectedHash) return null;
  return manifest.results.find(
    (r) => r.process === a.process && r.analysis === a.analysis
      && r.hash === expectedHash) ?? null;
}

/** One dot can only show one thing: verdict when we have one, execution
 * otherwise. The execution text travels in `note` next to it. */
export function statusKindOf(s: CheckState): StatusKind {
  if (s.execution === 'queued' || s.execution === 'running') return 'active';
  if (s.execution === 'error') return 'critical';
  if (s.verdict === 'pass') return 'good';
  if (s.verdict === 'review') return 'warning';
  if (s.verdict === 'fail') return 'serious';
  return 'neutral';
}
