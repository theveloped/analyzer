import {
  Axis3d, Drill, Expand, Eye, Layers, ListOrdered, ShieldCheck, Sigma,
  type LucideIcon,
} from 'lucide-react';
import { create } from 'zustand';
import { useStore } from '../../state/store';
import type {
  Manifest, Route, RouteCheck, RouteCheckStatus, Operation,
} from '../../api/types';
import { fetchField } from '../../fields/fields';
import type { ReachCtx } from '../../processes/cnc/reach';
import { ANALYSES, type Analysis } from '../analyses';
import type { ToolSpec } from '../table/columns';
import { FIELD_LENSES, type FieldLensDef } from '../fieldLenses';
import {
  evaluateBandCheck, evaluateCheck, evaluateExpression, evaluateReachOp,
  evaluateReachRoute, evaluateStatsCheck, isStatsRule,
  type Evaluation, type SourceHashes, type StatsRule,
} from './evaluators';
import {
  expressionText, resolveTerms, type ExprTerm, type MaskCtx,
} from '../../fields/expression';
import { meshArrays } from '../../viewer/controller';
import { resultForHash } from './status';

/**
 * Check descriptors: how a route check presents (label/icon), which lens it
 * activates with what scope bound in, and how its verdict is evaluated.
 * Threshold checks evaluate synchronously from stats; reach checks union
 * cached masks asynchronously, memoized by the eval key
 * (expected result hash + policy + the operation config it interprets) —
 * the derivation-cache identity from docs/ROUTE-ARCHITECTURE.md.
 */

/** The tool library, read from the served registry rather than mirrored: it
 * is already the declared default of `cnc/reach_study`'s `tools` param, which
 * `/api/processes` publishes verbatim. A hand-copy of a five-entry list of
 * floats is the kind of drift nobody notices until a check seeds tools the
 * backend never had. */
export function defaultTools(): ToolSpec[] {
  const catalog = useStore.getState().catalog;
  const param = catalog.find((p) => p.id === 'cnc')?.analyses
    .find((a) => a.id === 'reach_study')?.params
    .find((p) => p.name === 'tools');
  const tools = param?.default;
  if (!Array.isArray(tools) || !tools.length) {
    // the catalog is fetched once at boot, so this means it has not landed
    // (or the param was renamed) — loud, because the caller's fallback is an
    // empty tool list that silently seeds a check with nothing to run
    console.warn('no tool library in the process catalog — '
      + 'cnc/reach_study tools default missing');
    return [];
  }
  return tools;
}

export interface CheckView {
  kind: 'threshold' | 'reach_study' | 'reach_op' | 'reach_route' | 'stats'
    | 'expression';
  label: string;
  blurb: string;
  icon: LucideIcon;
  tier: 'primary' | 'advanced';
  /** Catalog entry for threshold checks (units, slider vocabulary). */
  analysis: Analysis | null;
  /** Viewer activation: shared-store mode + viewerParams patch. Takes the
   * whole status, not just a hash — an expression check has no single hash,
   * it has one per source. */
  activate(status: RouteCheckStatus | undefined): {
    processId: string; modeId: string; params: Record<string, unknown>;
  };
}

export function catalogAnalysisFor(check: RouteCheck): Analysis | null {
  return ANALYSES.find(
    (a) => `${a.process}/${a.analysis}` === check.analysis) ?? null;
}

/** How each stats rule presents. Keyed by `StatsRule`, so a rule with an
 * evaluator but no card (it used to render its raw id as the label) is a
 * compile error rather than a shrug in the rail. */
const STATS_VIEWS: Record<StatsRule, { label: string; blurb: string;
  icon: LucideIcon }> = {
  sheet_detect: {
    label: 'Sheet detection', icon: Layers,
    blurb: 'Is the part a constant-thickness sheet with a developable skin?',
  },
  flat_pattern: {
    label: 'Flat pattern', icon: Expand,
    blurb: 'Unfolds cleanly: developable, closed outline, volume preserved.',
  },
  bend_plan: {
    label: 'Bend route', icon: ListOrdered,
    blurb: 'A feasible tooling + sequence exists on the route\'s machine.',
  },
  features: {
    label: 'Feature recognition', icon: Drill,
    blurb: 'Holes and pockets recognized from the BREP — exploration data.',
  },
};

/** A check's preferred lens ("processId:modeId") as an activation target. */
function lensTarget(check: RouteCheck, params: Record<string, unknown> = {}) {
  const [processId, modeId] = (check.lens ?? ':').split(':');
  return () => ({ processId, modeId, params });
}

export function describeCheck(check: RouteCheck, route: Route): CheckView | null {
  if (check.policy?.kind === 'expression') {
    const terms = (check.policy?.terms ?? []) as ExprTerm[];
    return {
      kind: 'expression',
      label: check.label || 'Expression',
      blurb: terms.length ? expressionText(terms)
        : 'No terms yet — open the check to build one.',
      icon: Sigma,
      tier: 'primary',
      analysis: null,
      activate: (status) => ({
        processId: EXPRESSION_LENS[0], modeId: EXPRESSION_LENS[1],
        params: { exprTerms: resolveTerms(terms, sourceHashes(check, status)).resolved },
      }),
    };
  }
  const rule = check.policy?.kind === 'stats'
    ? String(check.policy?.rule ?? '') : null;
  if (rule) {
    // an unknown rule still gets a card — its evaluation is `unknown`, and a
    // check you can see and delete beats one that renders as nothing
    const view = isStatsRule(rule) ? STATS_VIEWS[rule] : null;
    return {
      kind: 'stats',
      label: view?.label ?? rule,
      blurb: view?.blurb ?? '',
      icon: view?.icon ?? Layers,
      tier: 'primary',
      analysis: null,
      activate: lensTarget(check),
    };
  }
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
    const op = route.operations.find((o) => o.id === check.operation);
    return {
      kind: 'reach_op',
      label: `Reach — ${op?.label ?? check.operation ?? '?'}`,
      blurb: 'Faces no library tool reaches from this operation\'s direction.',
      icon: Axis3d,
      tier: 'primary',
      analysis: null,
      activate: (status) => ({
        processId: 'cnc', modeId: 'reach_op',
        params: {
          reachHash: status?.expected_hash ?? null,
          opDirection: op?.config?.direction_index ?? null,
          reachFeatureMask: check.policy?.mask === 'features',
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
      activate: (status) => ({
        processId: 'cnc', modeId: 'reach_aggregate',
        params: { reachHash: status?.expected_hash ?? null, reachOps: routeOps(route) },
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
    activate: (status) => ({
      processId: 'cnc', modeId: 'reach_study',
      params: { reachHash: status?.expected_hash ?? null },
    }),
  };
}

/** The lens-facing op list for the aggregate view. Operations are ATOMIC —
 * one approach direction each — so the aggregate unions the directions
 * actually authored rather than every direction inside a tilt cone. */
export function routeOps(route: Route) {
  return route.operations
    .filter((op) => op.kind === 'milling'
      && Number.isFinite(Number(op.config?.direction_index)))
    .map((op) => ({
      direction: Number(op.config!.direction_index),
      label: op.label ?? op.id,
    }));
}

/** Which lens paints an expression. Process-independent, so it is hosted on
 * the injection plugin beside the other shared modes (brepFaces, faceAttrs,
 * pmi) rather than given a plugin of its own. */
export const EXPRESSION_LENS: [string, string] = ['injection_molding', 'expression'];

/** (source id) -> the analysis it names and the hash the server derived for
 * it. This is what binds a stored term — which keeps only (source, member) so
 * it survives a re-run — to a live field. */
export function sourceHashes(
  check: RouteCheck, status: RouteCheckStatus | undefined,
): SourceHashes {
  const out: SourceHashes = {};
  for (const source of check.sources ?? []) {
    out[source.id] = {
      analysis: source.analysis,
      hash: status?.sources?.[source.id]?.expected_hash ?? null,
    };
  }
  return out;
}

/** MaskCtx from the live mesh — null until the fine mesh is loaded. */
function maskCtx(manifest: Manifest): MaskCtx | null {
  const mesh = meshArrays();
  const faceCount = manifest.part.counts?.faces;
  if (!mesh || !faceCount) return null;
  return { manifest, ...mesh, faceCount, getField: fetchField };
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

function opFor(check: RouteCheck, route: Route): Operation | null {
  return route.operations.find((o) => o.id === check.operation) ?? null;
}

function checkRef(check: RouteCheck): { process: string; analysis: string } {
  const [process, analysis] = (check.analysis ?? '/').split('/');
  return { process, analysis };
}

/** Latest non-stale cnc/features result (feature-masked reach scoping). */
function latestFeatures(manifest: Manifest) {
  const list = manifest.results.filter(
    (r) => r.process === 'cnc' && r.analysis === 'features' && !r.stale);
  return list[list.length - 1] ?? null;
}

/** feature_id per FINE face. The result is stored per BREP face — recognition
 * never needed the fine mesh — so it is broadcast here, against the same
 * index space the reach masks use. */
async function featureMaskOf(manifest: Manifest): Promise<Uint32Array | null> {
  const result = latestFeatures(manifest);
  const faceCount = manifest.part.counts?.faces;
  if (!result || !faceCount) return null;
  const desc = manifest.fields.find(
    (f) => f.id === `results.cnc.features.${result.hash}.feature_id`);
  const brepDesc = manifest.fields.find((f) => f.id === 'brep_faces');
  if (!desc || !brepDesc) return null;
  const [byBrep, brepIds] = await Promise.all([
    fetchField(desc) as Promise<Uint32Array>,
    fetchField(brepDesc) as Promise<Uint32Array>,
  ]);
  const out = new Uint32Array(faceCount);
  for (let f = 0; f < out.length; f++) out[f] = byBrep[brepIds[f]] ?? 0;
  return out;
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

/**
 * One dispatch, two entry points.
 *
 * A check's verdict is derived the same way whoever asks — but the rail wants
 * an answer THIS render (returning null while the slow half is in flight) and
 * the publish flow wants to await it. So the dispatch is split by what it
 * needs rather than by caller: `evaluateResolved` answers everything that
 * reads only stats, `evaluateDeferred` is the field/mask math, and the two
 * entry points differ only in what they do with the second half.
 *
 * They used to be written out twice, and had already drifted: publish
 * evaluated a banded check against whatever result was newest while the rail
 * showed `unknown`. The rail's reading is the strict one and the one below —
 * a check pinned to an expected hash is not answerable from another result.
 */
function evaluateResolved(
  view: CheckView, check: RouteCheck, status: RouteCheckStatus | undefined,
  manifest: Manifest,
): Evaluation | null {
  if (view.kind === 'stats') {
    const result = resultForHash(manifest, checkRef(check),
      status?.expected_hash ?? null);
    return evaluateStatsCheck(String(check.policy?.rule ?? ''), check, result);
  }
  if (view.kind === 'threshold' && view.analysis) {
    // a plain threshold reads the stored minimum; only a BAND has to walk
    // the field, and only then does the pinned hash become load-bearing
    if (!bandOf(view, check)) {
      const result = resultForHash(manifest, view.analysis,
        status?.expected_hash ?? null);
      return evaluateCheck(view.analysis, check, result);
    }
  }
  if (view.kind === 'reach_study') return { verdict: 'na', findings: [] };
  // an expression is keyed per SOURCE, so the single-hash guard below would
  // reject it — it has no expected_hash of its own by construction
  if (view.kind === 'expression') {
    if (!status?.exists) return { verdict: 'unknown', findings: [] };
    return null;
  }
  if (!status?.exists || !status.expected_hash) {
    return { verdict: 'unknown', findings: [] };
  }
  if (view.kind === 'threshold' && view.analysis
      && !resultForHash(manifest, view.analysis, status.expected_hash)) {
    return { verdict: 'unknown', findings: [] };
  }
  return null; // needs the deferred half
}

/** The pinned band, or null when the check is a plain threshold. */
function bandOf(view: CheckView, check: RouteCheck): FieldLensDef | null {
  const a = view.analysis;
  const def = a ? FIELD_LENSES[`${a.process}:${a.id}`] : undefined;
  const band = (check.policy?.band ?? null) as
    [number | null, number | null] | null;
  const banded = Array.isArray(band) && (band[0] != null || band[1] != null);
  return def && banded ? def : null;
}

/** The half that walks fields or masks. Only reached with a pinned hash and,
 * for a band, a result that matches it. */
async function evaluateDeferred(
  view: CheckView, check: RouteCheck, route: Route, hash: string,
  manifest: Manifest, status?: RouteCheckStatus,
): Promise<Evaluation> {
  if (view.kind === 'expression') {
    const ctx = maskCtx(manifest);
    if (!ctx) return { verdict: 'unknown', findings: [] };
    return evaluateExpression(ctx, check, sourceHashes(check, status));
  }
  if (view.kind === 'threshold' && view.analysis) {
    const result = resultForHash(manifest, view.analysis, hash);
    if (!result) return { verdict: 'unknown', findings: [] };
    return evaluateBandCheck(manifest, bandOf(view, check)!, view.analysis,
      check, result);
  }
  const ctx = reachCtx(manifest, hash);
  if (!ctx) return { verdict: 'unknown', findings: [] };
  if (view.kind === 'reach_op') {
    const op = opFor(check, route);
    if (!op) return { verdict: 'unknown', findings: [] };
    const mask = check.policy?.mask === 'features'
      ? await featureMaskOf(manifest) : null;
    return evaluateReachOp(ctx, check, op, mask);
  }
  return evaluateReachRoute(ctx, check, route.operations);
}

/** Memo key: the pinned result, the policy, and whatever else the deferred
 * half reads — the operation config it slices, and the features result when
 * the policy masks by it. */
function deferredKey(
  view: CheckView, check: RouteCheck, route: Route, hash: string,
  manifest: Manifest, status?: RouteCheckStatus,
): string {
  if (view.kind === 'expression') {
    // every source hash, so a re-run of ANY of them re-evaluates
    return ['expr', check.id, JSON.stringify(sourceHashes(check, status)),
      JSON.stringify(check.policy ?? {})].join('|');
  }
  if (view.kind === 'threshold') {
    return ['band', check.id, hash, JSON.stringify(check.policy ?? {})].join('|');
  }
  const scopeConfig = view.kind === 'reach_op'
    ? opFor(check, route)?.config ?? {}
    : routeOps(route);
  const featuresHash = check.policy?.mask === 'features'
    ? latestFeatures(manifest)?.hash ?? 'none' : '';
  return ['reach', check.id, hash, featuresHash,
    JSON.stringify(check.policy ?? {}), JSON.stringify(scopeConfig)].join('|');
}

/** Non-hook evaluation (the publish flow): run to completion. */
export async function evaluateNow(
  check: RouteCheck, route: Route, status: RouteCheckStatus | undefined,
  manifest: Manifest,
): Promise<Evaluation> {
  const view = describeCheck(check, route);
  if (!view) return { verdict: 'unknown', findings: [] };
  const resolved = evaluateResolved(view, check, status, manifest);
  if (resolved) return resolved;
  return evaluateDeferred(view, check, route, status?.expected_hash ?? '',
    manifest, status);
}

/** Evaluation of a route check against its pinned policy. Plain threshold and
 * stats checks resolve synchronously; band and reach checks return null while
 * their field/mask math is in flight and re-render via the eval tick when
 * done. */
export function useCheckEvaluation(
  check: RouteCheck, route: Route, status: RouteCheckStatus | undefined,
  manifest: Manifest | null,
): Evaluation | null {
  useEvalTick((s) => s.n); // re-read the memo when an evaluation lands
  const view = describeCheck(check, route);
  if (!view || !manifest) return { verdict: 'unknown', findings: [] };
  const resolved = evaluateResolved(view, check, status, manifest);
  if (resolved) return resolved;
  const hash = status?.expected_hash ?? '';
  return runMemoized(deferredKey(view, check, route, hash, manifest, status),
    () => evaluateDeferred(view, check, route, hash, manifest, status));
}
