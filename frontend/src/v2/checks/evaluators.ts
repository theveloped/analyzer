import type {
  DispositionEvent, PlanCheck, PlanOperation, ResultEntry,
} from '../../api/types';
import { findStudy, opReach, type ReachCtx } from '../../processes/cnc/reach';
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

/** Per-operation reach: faces visible somewhere in the operation's tilt
 * cone that NO library tool reaches. Async — unions cached masks. */
export async function evaluateReachOp(
  ctx: ReachCtx, check: PlanCheck, op: PlanOperation,
): Promise<Evaluation> {
  const primary = Number(op.config?.direction_index);
  const tilt = Number(op.config?.tilt ?? 90);
  if (!Number.isFinite(primary)) return { verdict: 'unknown', findings: [] };
  const study = findStudy(ctx);
  const { reach, visible } = await opReach(ctx, study, primary, tilt);
  let blocked = 0;
  for (let f = 0; f < ctx.faceCount; f++) if (visible[f] && !reach[f]) blocked++;
  if (!blocked) return { verdict: 'pass', findings: [] };
  return {
    verdict: 'review',
    findings: [{
      id: `${check.id}:tool_blocked`,
      code: 'op_tool_blocked',
      label: `${op.label ?? op.id}: faces no tool reaches`,
      detail: `${blocked} faces visible in the ±${tilt}° cone of direction `
        + `${primary} are blocked for every library tool`,
      severity: 'review',
    }],
  };
}

/** Route aggregate: faces unreachable in EVERY operation (geometry-union
 * of the per-op reach masks, inverted) — the customer-facing verdict. */
export async function evaluateReachRoute(
  ctx: ReachCtx, check: PlanCheck, ops: PlanOperation[],
): Promise<Evaluation> {
  const configured = ops.filter(
    (op) => Number.isFinite(Number(op.config?.direction_index)));
  if (!configured.length) return { verdict: 'unknown', findings: [] };
  const study = findStudy(ctx);
  const anyReach = new Uint8Array(ctx.faceCount);
  const anyVisible = new Uint8Array(ctx.faceCount);
  for (const op of configured) {
    const { reach, visible } = await opReach(
      ctx, study, Number(op.config!.direction_index),
      Number(op.config?.tilt ?? 90));
    for (let f = 0; f < ctx.faceCount; f++) {
      anyReach[f] |= reach[f];
      anyVisible[f] |= visible[f];
    }
  }
  let blocked = 0, hidden = 0;
  for (let f = 0; f < ctx.faceCount; f++) {
    if (anyReach[f]) continue;
    if (anyVisible[f]) blocked++; else hidden++;
  }
  if (!blocked && !hidden) return { verdict: 'pass', findings: [] };
  const findings: Finding[] = [];
  if (blocked) {
    findings.push({
      id: `${check.id}:route_tool_blocked`,
      code: 'route_tool_blocked',
      label: 'Not producible by the route (tooling)',
      detail: `${blocked} faces are visible from some operation but no `
        + `library tool reaches them in any operation`,
      severity: 'fail',
    });
  }
  if (hidden) {
    findings.push({
      id: `${check.id}:route_undercut`,
      code: 'route_undercut',
      label: 'Not producible by the route (undercut)',
      detail: `${hidden} faces are undercuts for every operation's cone`,
      severity: 'fail',
    });
  }
  return { verdict: 'fail', findings };
}

/** A finding's effective disposition state ('open' when never judged). */
export function dispositionOf(
  finding: Finding,
  dispositions: Record<string, DispositionEvent> | undefined,
): DispositionEvent['state'] {
  return dispositions?.[finding.id]?.state ?? 'open';
}
