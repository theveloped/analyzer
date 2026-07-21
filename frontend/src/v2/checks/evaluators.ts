import type { DispositionEvent, PlanCheck, ResultEntry } from '../../api/types';
import type { Analysis } from '../analyses';
import type { VerdictState } from './status';

/**
 * Check evaluators: derive a verdict + findings from a stored result and the
 * check's PINNED policy (never the live viewer slider — that stays free on
 * the lens). Deterministic by construction: the inputs are content-addressed
 * result stats and the policy carried by the plan revision, so re-evaluating
 * always reproduces the same findings. Keep every evaluator in this module —
 * a later Python mirror (cross-part dashboards, publish flow) should be a
 * port, not a hunt (docs/PLAN-ARCHITECTURE.md).
 */

export interface Finding {
  /** Stable identity for dispositions: check id + finding code — deliberately
   * excludes the result hash, so an accepted deviation survives a re-run
   * that reproduces the same issue. */
  id: string;
  code: string;
  label: string;
  detail: string;
  severity: 'review' | 'fail';
}

export interface Evaluation {
  verdict: VerdictState;
  findings: Finding[];
}

/** Minimum-vs-threshold evaluator: the four field checks (thickness, gaps,
 * ray variants) store the field minimum in stats; a minimum past the pinned
 * limit means there is geometry to review. */
export function evaluateCheck(
  a: Analysis, check: PlanCheck, result: ResultEntry | null,
): Evaluation {
  if (!result) return { verdict: 'unknown', findings: [] };
  const threshold = Number(check.policy?.threshold ?? a.thresholdDefault);
  const min = (result.stats as Record<string, unknown>).min;
  if (typeof min !== 'number' || !isFinite(threshold)) {
    return { verdict: 'unknown', findings: [] };
  }
  if (min >= threshold) return { verdict: 'pass', findings: [] };
  return {
    verdict: 'review',
    findings: [{
      id: `${check.id}:min_below`,
      code: 'min_below_limit',
      label: `${a.label} below limit`,
      detail: `minimum ${min.toFixed(2)} ${a.unit} < policy ${threshold} ${a.unit}`,
      severity: 'review',
    }],
  };
}

/** A finding's effective disposition state ('open' when never judged). */
export function dispositionOf(
  finding: Finding,
  dispositions: Record<string, DispositionEvent> | undefined,
): DispositionEvent['state'] {
  return dispositions?.[finding.id]?.state ?? 'open';
}
