import type {
  DispositionEvent, Manifest, PlanCheck, PlanOperation, ResultEntry,
} from '../../api/types';
import { fetchBin, fetchField } from '../../fields/fields';
import { findStudy, opReach, type ReachCtx } from '../../processes/cnc/reach';
import type { Analysis } from '../analyses';
import { fieldDescriptor, type FieldLensDef } from '../fieldLenses';
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

/** Human-readable band text from a pinned policy. */
export function bandText(check: PlanCheck, unit: string): string {
  const [lo, hi] = (check.policy?.band ?? [null, null]) as
    [number | null, number | null];
  if (lo != null && hi != null) return `${lo.toFixed(2)} – ${hi.toFixed(2)} ${unit}`;
  if (lo != null) return `≥ ${lo.toFixed(2)} ${unit}`;
  if (hi != null) return `≤ ${hi.toFixed(2)} ${unit}`;
  return '';
}

/** Band check: count the faces whose value falls inside the pinned band —
 * the exact rule the highlight paints (per-face mean of the three corner
 * values, the faceValues convention). Async: fetches the cached field and
 * mesh faces (both usually already in the fetchBin cache). */
export async function evaluateBandCheck(
  manifest: Manifest, def: FieldLensDef, a: Analysis, check: PlanCheck,
  result: ResultEntry,
): Promise<Evaluation> {
  const desc = fieldDescriptor(manifest, result, def);
  const facesUrl = manifest.mesh?.faces_url;
  if (!desc || !facesUrl) return { verdict: 'unknown', findings: [] };
  const [field, faces] = await Promise.all([
    fetchField(desc) as Promise<Float32Array>,
    fetchBin(facesUrl, Uint32Array),
  ]);
  const [lo, hi] = (check.policy?.band ?? [null, null]) as
    [number | null, number | null];
  const bLo = lo ?? -Infinity;
  const bHi = hi ?? Infinity;
  const faceCount = faces.length / 3;
  let inBand = 0;
  let finite = 0;
  for (let f = 0; f < faceCount; f++) {
    const v = (field[faces[3 * f]] + field[faces[3 * f + 1]]
      + field[faces[3 * f + 2]]) / 3;
    if (!isFinite(v)) continue;
    finite++;
    if (v >= bLo && v <= bHi) inBand++;
  }
  if (!inBand) return { verdict: 'pass', findings: [] };
  const share = finite ? ((100 * inBand) / finite).toFixed(1) : '0';
  return {
    verdict: 'review',
    findings: [{
      id: `${check.id}:in_band`,
      code: 'in_band',
      label: `${a.label}: faces inside the band`,
      detail: `${inBand} faces (${share} %) inside ${bandText(check, def.unit)}`,
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
