import type {
  Manifest, Operation, ResultEntry, RouteCheck,
} from '../../api/types';
import { fetchBin, fetchField } from '../../fields/fields';
import {
  buildMask, DEFAULT_AGGREGATE, expressionText, resolveTerms, summarize,
  type ExprPolicy, type MaskCtx,
} from '../../fields/expression';
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
 * port, not a hunt (docs/ROUTE-ARCHITECTURE.md).
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

/** Per-source result hashes, as the server derives them — what binds a
 * stored term (source id + npz member) to a live field. */
export type SourceHashes = Record<string, { analysis: string; hash: string | null }>;

export interface Evaluation {
  verdict: VerdictState;
  findings: Finding[];
}

/** Minimum-vs-threshold evaluator: the four field checks (thickness, gaps,
 * ray variants) store the field minimum in stats; a minimum past the pinned
 * limit means there is geometry to review. */
export function evaluateCheck(
  a: Analysis, check: RouteCheck, result: ResultEntry | null,
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
export function bandText(check: RouteCheck, unit: string): string {
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
  manifest: Manifest, def: FieldLensDef, a: Analysis, check: RouteCheck,
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

/** Per-operation reach: faces visible from the operation's direction that
 * NO library tool reaches. Async — unions cached masks. With a
 * `featureMask` (cnc/features feature_id per face) only machined-feature
 * faces count: "are the features this operation produces reachable" —
 * the declarative workpiece-state scoping, on the final-part mesh. */
export async function evaluateReachOp(
  ctx: ReachCtx, check: RouteCheck, op: Operation,
  featureMask?: Uint32Array | null,
): Promise<Evaluation> {
  const direction = Number(op.config?.direction_index);
  if (!Number.isFinite(direction)) return { verdict: 'unknown', findings: [] };
  const study = findStudy(ctx);
  const { reach, visible } = await opReach(ctx, study, direction);
  let blocked = 0;
  let scoped = 0;
  for (let f = 0; f < ctx.faceCount; f++) {
    if (featureMask && !featureMask[f]) continue;
    scoped++;
    if (visible[f] && !reach[f]) blocked++;
  }
  if (featureMask && !scoped) return { verdict: 'unknown', findings: [] };
  if (!blocked) return { verdict: 'pass', findings: [] };
  const what = featureMask ? 'machined-feature faces' : 'faces';
  return {
    verdict: 'review',
    findings: [{
      id: `${check.id}:tool_blocked`,
      code: 'op_tool_blocked',
      label: `${op.label ?? op.id}: ${what} no tool reaches`,
      detail: `${blocked} ${what} visible from direction ${direction} are `
        + 'blocked for every library tool',
      severity: 'review',
    }],
  };
}

/** The stats-rule vocabulary. Two tables are keyed by it — the evaluators
 * below and the card presentation in `catalog.ts` — so TS refuses a rule that
 * has logic but no label, or a label with no logic. `route.py` `STATS_RULES`
 * validates the same names where a route enters, so a typo raises instead of
 * storing a check that quietly evaluates to `unknown` forever;
 * `test_vocab.py` asserts the two sides list the same rules. */
export type StatsRule = 'sheet_detect' | 'flat_pattern' | 'bend_plan'
  | 'features';

/** Emits a finding under this check's identity. */
type Emit = (code: string, label: string, detail: string) => Finding;

const STATS_EVALUATORS: Record<
  StatsRule, (stats: Record<string, any>, finding: Emit) => Evaluation
> = {
  sheet_detect: (stats, finding) => {
    if (stats.verdict === 'sheet') return { verdict: 'pass', findings: [] };
    const reasons: string[] = stats.reasons ?? [];
    return {
      verdict: 'review',
      findings: [finding('not_sheet', 'Not detected as sheet metal',
        reasons.join('; ') || `verdict: ${stats.verdict}`)],
    };
  },

  flat_pattern: (stats, finding) => {
    const findings: Finding[] = [];
    if (stats.developable === false) {
      findings.push(finding('not_developable', 'Not developable',
        'the skin contains non-developable regions'));
    }
    if (stats.open_wires) {
      findings.push(finding('open_wires', 'Open wires in the pattern',
        `${stats.open_wires} open wires in the unfolded outline`));
    }
    if (stats.volume_ok === false) {
      findings.push(finding('volume_error', 'Unfold volume mismatch',
        `volume error ${Number(stats.volume_error_pct).toFixed(1)} %`));
    }
    return findings.length
      ? { verdict: 'review', findings }
      : { verdict: 'pass', findings: [] };
  },

  bend_plan: (stats, finding) => {
    if (stats.feasible) return { verdict: 'pass', findings: [] };
    return {
      verdict: 'fail',
      findings: [finding('infeasible', 'No feasible bend plan',
        'no tooling/sequence combination bends this part on the selected '
        + 'machine')],
    };
  },

  // recognition is exploration data, not a judgement
  features: () => ({ verdict: 'na', findings: [] }),
};

export function isStatsRule(rule: string): rule is StatsRule {
  return Object.prototype.hasOwnProperty.call(STATS_EVALUATORS, rule);
}

/** Stats-verdict checks: judged directly from the stored result's stats
 * (sheet detection / flat pattern / bend plan / feature recognition).
 * Findings carry stable per-reason ids so dispositions survive re-runs. */
export function evaluateStatsCheck(
  rule: string, check: RouteCheck, result: ResultEntry | null,
): Evaluation {
  if (!result || !isStatsRule(rule)) return { verdict: 'unknown', findings: [] };
  const finding: Emit = (code, label, detail) =>
    ({ id: `${check.id}:${code}`, code, label, detail, severity: 'review' });
  return STATS_EVALUATORS[rule](result.stats as Record<string, any>, finding);
}

/** Route aggregate: faces unreachable in EVERY operation (geometry-union
 * of the per-op reach masks, inverted) — the customer-facing verdict. */
export async function evaluateReachRoute(
  ctx: ReachCtx, check: RouteCheck, ops: Operation[],
): Promise<Evaluation> {
  const configured = ops.filter(
    (op) => Number.isFinite(Number(op.config?.direction_index)));
  if (!configured.length) return { verdict: 'unknown', findings: [] };
  const study = findStudy(ctx);
  const anyReach = new Uint8Array(ctx.faceCount);
  const anyVisible = new Uint8Array(ctx.faceCount);
  for (const op of configured) {
    const { reach, visible } = await opReach(
      ctx, study, Number(op.config!.direction_index));
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


/**
 * Expression check: the composed mask's AREA SHARE against the pinned limit.
 *
 * This is the general form the other field checks are special cases of — a
 * threshold is one band term, a band check is one band term, an unreachable
 * region is `visible and not reachable`. It reports area rather than a face
 * count because a dense corner would otherwise outvote a large flat wall.
 */
export async function evaluateExpression(
  ctx: MaskCtx, check: RouteCheck, sources: SourceHashes,
): Promise<Evaluation> {
  const policy = check.policy as unknown as ExprPolicy | undefined;
  const terms = policy?.terms ?? [];
  if (!terms.length) return { verdict: 'unknown', findings: [] };
  const { resolved, missing } = resolveTerms(terms, sources);
  // an expression evaluated from only some of its fields is not a weaker
  // answer, it is a different question — say `unknown` instead
  if (missing.length || resolved.length !== terms.length) {
    return { verdict: 'unknown', findings: [] };
  }
  const { mask, unresolved } = await buildMask(ctx, resolved);
  if (unresolved.length) return { verdict: 'unknown', findings: [] };

  const summary = summarize(ctx, mask);
  const aggregate = policy?.aggregate ?? DEFAULT_AGGREGATE;
  if (summary.share <= aggregate.limit) return { verdict: 'pass', findings: [] };
  const limitText = aggregate.limit > 0
    ? ` (limit ${(100 * aggregate.limit).toFixed(1)} %)` : '';
  return {
    verdict: aggregate.severity,
    findings: [{
      id: `${check.id}:expression`,
      code: 'expression',
      label: 'Faces the expression selects',
      detail: `${summary.faces} faces · ${summary.area.toFixed(0)} mm² · `
        + `${(100 * summary.share).toFixed(1)} % of the part${limitText}`
        + ` — ${expressionText(terms)}`,
      severity: aggregate.severity,
    }],
  };
}
