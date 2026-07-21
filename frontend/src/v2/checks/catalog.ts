import { Axis3d, Eye, ShieldCheck, type LucideIcon } from 'lucide-react';
import { create } from 'zustand';
import type {
  Manifest, Plan, PlanCheck, PlanCheckStatus, PlanOperation,
} from '../../api/types';
import { fetchField } from '../../fields/fields';
import type { ReachCtx } from '../../processes/cnc/reach';
import { ANALYSES, type Analysis } from '../analyses';
import { FIELD_LENSES } from '../fieldLenses';
import {
  evaluateBandCheck, evaluateCheck, evaluateReachOp, evaluateReachRoute,
  type Evaluation,
} from './evaluators';
import { resultForHash } from './status';

/**
 * Check descriptors: how a plan check presents (label/icon), which lens it
 * activates with what scope bound in, and how its verdict is evaluated.
 * Threshold checks evaluate synchronously from stats; reach checks union
 * cached masks asynchronously, memoized by the eval key
 * (expected result hash + policy + the operation config it interprets) —
 * the derivation-cache identity from docs/PLAN-ARCHITECTURE.md.
 */

/** Mirror of processes/cnc.py DEFAULT_TOOLS (template seeding). */
export const DEFAULT_TOOLS = [
  { diameter: 16.0, corner_radius: 0.0, stickout: 80.0, holder_radius: 8.0 },
  { diameter: 8.0, corner_radius: 0.0, stickout: 40.0, holder_radius: 4.0 },
  { diameter: 4.0, corner_radius: 0.0, stickout: 20.0, holder_radius: 2.0 },
  { diameter: 10.0, corner_radius: 5.0, stickout: 50.0, holder_radius: 5.0 },
  { diameter: 4.0, corner_radius: 2.0, stickout: 20.0, holder_radius: 2.0 },
];

export interface CheckView {
  kind: 'threshold' | 'reach_study' | 'reach_op' | 'reach_route';
  label: string;
  blurb: string;
  icon: LucideIcon;
  tier: 'primary' | 'advanced';
  /** Catalog entry for threshold checks (units, slider vocabulary). */
  analysis: Analysis | null;
  /** Viewer activation: shared-store mode + viewerParams patch. */
  activate(expectedHash: string | null): {
    processId: string; modeId: string; params: Record<string, unknown>;
  };
}

export function catalogAnalysisFor(check: PlanCheck): Analysis | null {
  return ANALYSES.find(
    (a) => `${a.process}/${a.analysis}` === check.analysis) ?? null;
}

export function describeCheck(check: PlanCheck, plan: Plan): CheckView | null {
  const a = catalogAnalysisFor(check);
  if (a) {
    // field-lens-backed checks open as the plain heatmap — the band comes
    // from the check's policy via the rail, everything else stays neutral
    const field = FIELD_LENSES[`${a.process}:${a.id}`];
    const params: Record<string, unknown> = field
      ? {
        [field.thresholdParam]: '', [field.minParam]: '',
        [field.scaleParam]: '',
        ...(field.maskParam ? { [field.maskParam]: false } : {}),
      }
      : {};
    return {
      kind: 'threshold',
      label: a.label,
      blurb: a.blurb,
      icon: a.icon,
      tier: a.tier,
      analysis: a,
      activate: () => ({ processId: a.process, modeId: a.id, params }),
    };
  }
  if (check.analysis !== 'cnc/reach_study') return null;
  const scope = (check.policy?.scope ?? 'study') as string;
  if (scope === 'operation') {
    const op = plan.operations.find((o) => o.id === check.operation);
    return {
      kind: 'reach_op',
      label: `Reach — ${op?.label ?? check.operation ?? '?'}`,
      blurb: 'Faces no library tool reaches within this operation\'s cone.',
      icon: Axis3d,
      tier: 'primary',
      analysis: null,
      activate: (hash) => ({
        processId: 'cnc', modeId: 'reach_op',
        params: {
          reachHash: hash,
          opPrimary: op?.config?.direction_index ?? null,
          opTilt: op?.config?.tilt ?? 90,
        },
      }),
    };
  }
  if (scope === 'route') {
    return {
      kind: 'reach_route',
      label: 'Route reach (aggregate)',
      blurb: 'Faces unreachable in every operation — the route verdict.',
      icon: ShieldCheck,
      tier: 'primary',
      analysis: null,
      activate: (hash) => ({
        processId: 'cnc', modeId: 'reach_aggregate',
        params: { reachHash: hash, reachOps: routeOps(plan) },
      }),
    };
  }
  return {
    kind: 'reach_study',
    label: 'Reach study',
    blurb: 'Per-(direction × tool) machinable masks — the exploration data '
      + 'the operation checks slice.',
    icon: Eye,
    tier: 'primary',
    analysis: null,
    activate: (hash) => ({
      processId: 'cnc', modeId: 'reach_study',
      params: { reachHash: hash },
    }),
  };
}

/** The lens-facing op list for the aggregate view. */
export function routeOps(plan: Plan) {
  return plan.operations
    .filter((op) => op.kind === 'cnc_setup'
      && Number.isFinite(Number(op.config?.direction_index)))
    .map((op) => ({
      primary: Number(op.config!.direction_index),
      tilt: Number(op.config?.tilt ?? 90),
      label: op.label ?? op.id,
    }));
}

// --- async evaluation memo -------------------------------------------------

const evalCache = new Map<string, Evaluation>();
const evalPending = new Set<string>();
/** Bumped when an async evaluation lands so subscribers re-read the memo. */
const useEvalTick = create<{ n: number; bump: () => void }>()((set) => ({
  n: 0, bump: () => set((s) => ({ n: s.n + 1 })),
}));

function reachCtx(manifest: Manifest, expectedHash: string): ReachCtx | null {
  const faceCount = manifest.part.counts?.faces;
  if (!faceCount) return null;
  return {
    manifest,
    directions: manifest.directions,
    faceCount,
    params: { reachHash: expectedHash },
    getField: fetchField,
  };
}

function opFor(check: PlanCheck, plan: Plan): PlanOperation | null {
  return plan.operations.find((o) => o.id === check.operation) ?? null;
}

/** Cached-or-launch: returns the memoized evaluation, or kicks the async
 * run off (once) and returns null; the eval tick re-renders subscribers
 * when it lands. */
function runMemoized(
  key: string, run: () => Promise<Evaluation>,
): Evaluation | null {
  const hit = evalCache.get(key);
  if (hit) return hit;
  if (!evalPending.has(key)) {
    evalPending.add(key);
    run()
      .catch((err) => {
        console.warn(`evaluation ${key} failed:`, err);
        return { verdict: 'unknown', findings: [] } as Evaluation;
      })
      .then((evaluation) => {
        evalCache.set(key, evaluation);
        evalPending.delete(key);
        useEvalTick.getState().bump();
      });
  }
  return null; // evaluating…
}

/** Non-hook evaluation (the publish flow): same dispatch as the hook, run
 * to completion. */
export async function evaluateNow(
  check: PlanCheck, plan: Plan, status: PlanCheckStatus | undefined,
  manifest: Manifest,
): Promise<Evaluation> {
  const view = describeCheck(check, plan);
  if (!view) return { verdict: 'unknown', findings: [] };
  if (view.kind === 'threshold' && view.analysis) {
    const a = view.analysis;
    const def = FIELD_LENSES[`${a.process}:${a.id}`];
    const band = (check.policy?.band ?? null) as
      [number | null, number | null] | null;
    const hasBand = !!def && Array.isArray(band)
      && (band[0] != null || band[1] != null);
    const result = resultForHash(manifest, a, status?.expected_hash ?? null);
    if (!hasBand || !result) return evaluateCheck(a, check, result);
    return evaluateBandCheck(manifest, def!, a, check, result);
  }
  if (view.kind === 'reach_study') return { verdict: 'na', findings: [] };
  if (!status?.exists || !status.expected_hash) {
    return { verdict: 'unknown', findings: [] };
  }
  const ctx = reachCtx(manifest, status.expected_hash);
  if (!ctx) return { verdict: 'unknown', findings: [] };
  if (view.kind === 'reach_op') {
    const op = opFor(check, plan);
    if (!op) return { verdict: 'unknown', findings: [] };
    return evaluateReachOp(ctx, check, op);
  }
  return evaluateReachRoute(ctx, check, plan.operations);
}

/** Evaluation of a plan check against its pinned policy. Plain threshold
 * checks resolve synchronously from stats; band and reach checks return
 * null while their field/mask math is in flight and re-render via the eval
 * tick when done. */
export function useCheckEvaluation(
  check: PlanCheck, plan: Plan, status: PlanCheckStatus | undefined,
  manifest: Manifest | null,
): Evaluation | null {
  useEvalTick((s) => s.n); // re-read the memo when an evaluation lands
  const view = describeCheck(check, plan);
  if (!view || !manifest) return { verdict: 'unknown', findings: [] };

  if (view.kind === 'threshold' && view.analysis) {
    const a = view.analysis;
    const def = FIELD_LENSES[`${a.process}:${a.id}`];
    const band = (check.policy?.band ?? null) as
      [number | null, number | null] | null;
    const hasBand = !!def && Array.isArray(band)
      && (band[0] != null || band[1] != null);
    if (!hasBand) {
      const result = resultForHash(manifest, a, status?.expected_hash ?? null);
      return evaluateCheck(a, check, result);
    }
    if (!status?.exists || !status.expected_hash) {
      return { verdict: 'unknown', findings: [] };
    }
    const result = resultForHash(manifest, a, status.expected_hash);
    if (!result) return { verdict: 'unknown', findings: [] };
    const key = ['band', check.id, status.expected_hash,
      JSON.stringify(check.policy ?? {})].join('|');
    return runMemoized(key,
      () => evaluateBandCheck(manifest, def!, a, check, result));
  }
  if (view.kind === 'reach_study') return { verdict: 'na', findings: [] };
  if (!status?.exists || !status.expected_hash) {
    return { verdict: 'unknown', findings: [] };
  }

  const scopeConfig = view.kind === 'reach_op'
    ? opFor(check, plan)?.config ?? {}
    : routeOps(plan);
  const key = ['reach', check.id, status.expected_hash,
    JSON.stringify(check.policy ?? {}), JSON.stringify(scopeConfig)].join('|');
  const hash = status.expected_hash;
  return runMemoized(key, async () => {
    const ctx = reachCtx(manifest, hash);
    if (!ctx) return { verdict: 'unknown', findings: [] };
    if (view.kind === 'reach_op') {
      const op = opFor(check, plan);
      if (!op) return { verdict: 'unknown', findings: [] };
      return evaluateReachOp(ctx, check, op);
    }
    return evaluateReachRoute(ctx, check, plan.operations);
  });
}
