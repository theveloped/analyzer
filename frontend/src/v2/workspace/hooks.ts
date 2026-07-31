import { useEffect } from 'react';
import { putRoute } from '../../api/client';
import type {
  Operation, OperationKind, Route, RouteCheck, RouteCheckStatus, RouteSection,
} from '../../api/types';
import { useStore } from '../../state/store';
import { refreshManifest } from '../../viewer/controller';
import { runAnalysisJob } from '../../viewer/jobs';
import type { Analysis } from '../analyses';
import { ANALYSIS_BY_ID, ANALYSES } from '../analyses';
import { describeCheck } from '../checks/catalog';
import { checkState, type CheckState } from '../checks/status';
import {
  FIELD_LENSES, fieldLensCompute, latestResult, type FieldLensDef,
} from '../fieldLenses';
import type { Lens } from '../lenses';
import { lensFor } from '../lenses';
import { useV2 } from '../store';

/** The active analysis is the shared store's modeId (falls back to thickness). */
export function useActiveAnalysis(): Analysis {
  const modeId = useStore((s) => s.modeId);
  return ANALYSIS_BY_ID[modeId] ?? ANALYSES[0];
}

/** Whether the active mode is one of the runnable checks (vs a plain lens). */
export function useCheckActive(): boolean {
  return useStore((s) => s.modeId in ANALYSIS_BY_ID);
}

/** Analyses visible in the shell — advanced ones only when advanced mode is on. */
export function useVisibleAnalyses(): Analysis[] {
  const advanced = useV2((s) => s.advanced);
  return ANALYSES.filter((a) => advanced || a.tier === 'primary');
}

/** Switch the active analysis (drives the shared viewer's mode + process).
 * Scopes the rail to the matching plan check when the plan has one. */
export function selectAnalysis(a: Analysis) {
  useStore.getState().set({ processId: a.process, modeId: a.id });
  const section = useStore.getState().manifest?.route;
  const check = section?.route.checks.find(
    (c) => c.analysis === `${a.process}/${a.analysis}`);
  useV2.getState().setActiveCheck(check?.id ?? null);
}

/** Execution + verdict state of a check, from the live store (manifest,
 * jobs, and the engineer's current threshold — provisional until Phase 1
 * pins policies on plan checks). */
export function useCheckState(a: Analysis): CheckState {
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const params = useStore((s) => s.viewerParams[a.process]);
  const threshold = Number((params ?? {})[a.thresholdParam] ?? a.thresholdDefault);
  return checkState(manifest, jobs, partId, a, threshold);
}

/** The candidate-directions view is its own (cross-process) mode with a
 * dedicated toolbar button — active when the shared modeId is 'directions'. */
export function useDirectionsActive(): boolean {
  return useStore((s) => s.modeId) === 'directions';
}

/** Open the directions view (the shared controller paints the directionsPlugin). */
export function activateDirections() {
  useStore.getState().set({ processId: 'directions', modeId: 'directions' });
  useV2.getState().setActiveCheck(null);
}

/** The active inspection lens, if the shared process/mode is registered as
 * one (checks and directions also resolve — rails decide precedence). */
export function useActiveLens(): Lens | null {
  const processId = useStore((s) => s.processId);
  const modeId = useStore((s) => s.modeId);
  return lensFor(processId, modeId);
}

/** Activate an inspection lens (drives the shared viewer's mode + process).
 * A lens picked directly is free exploration — it drops the check scope.
 * Field lenses open as the PLAIN heatmap: no threshold, full data range,
 * edge artifacts visible — interpretation lives in the side panel. */
export function selectLens(l: Lens) {
  const store = useStore.getState();
  const field = FIELD_LENSES[l.key];
  if (field) {
    store.setViewerParam(field.process, field.thresholdParam, '');
    store.setViewerParam(field.process, field.minParam, '');
    store.setViewerParam(field.process, field.scaleParam, '');
    store.setViewerParam(field.process, field.bandLoParam, '');
    store.setViewerParam(field.process, field.bandHiParam, '');
    if (field.maskParam) store.setViewerParam(field.process, field.maskParam, false);
  }
  store.set({ processId: l.processId, modeId: l.modeId });
  useV2.getState().setActiveCheck(null);
}

/** The field-lens definition backing the active lens, if any. */
export function useActiveFieldLens(): FieldLensDef | null {
  const lens = useActiveLens();
  return lens ? FIELD_LENSES[lens.key] ?? null : null;
}

/** A field lens materializes itself: when it's active with nothing cached
 * and no job in flight, the backing analysis runs with plain defaults.
 * One attempt per (part, analysis) per session — a failed job surfaces in
 * the rail instead of looping.
 *
 * Gated on the FINE mesh, not on `meshReady`: the coarse preview also sets
 * meshReady, and every field lens needs `prep/mesh`, so firing here would
 * make merely opening the app kick off the heaviest build in the repo and
 * lock the part (one job per part) before the user has asked for anything. */
const autoRunAttempted = new Set<string>();
export function useAutoRunFieldLens() {
  const def = useActiveFieldLens();
  const partId = useStore((s) => s.partId);
  const meshReady = useStore((s) => s.meshReady);
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  useEffect(() => {
    if (!def || !partId || !meshReady || !manifest?.mesh) return;
    const existing = latestResult(manifest, def);
    if (existing && !existing.stale) return;
    const busy = jobs.some((j) => j.part_id === partId
      && (j.status === 'queued' || j.status === 'running'));
    // per LENS, not per analysis: the contact-angle lens re-runs thickness
    // with different compute params than the plain thickness lens did
    const key = `${partId}:${def.lensKey}`;
    if (busy || autoRunAttempted.has(key)) return;
    autoRunAttempted.add(key);
    runAnalysisJob(partId, def.process, def.analysis, fieldLensCompute(def))
      .catch((err) => useStore.getState().set({
        error: err instanceof Error ? err.message : String(err),
      }));
  }, [def, partId, meshReady, manifest, jobs]);
}

// ---------------------------------------------------------------------------
// The route: operations + checks (manifest.route → route.py sidecars)

/** The manifest's route section (route + derived per-check status). */
export function useRouteSection(): RouteSection | null {
  return useStore((s) => s.manifest?.route ?? null);
}

/** Catalog UI metadata for a check (icon/label/threshold vocabulary). */
export function catalogFor(check: RouteCheck): Analysis | null {
  return ANALYSES.find(
    (a) => `${a.process}/${a.analysis}` === check.analysis) ?? null;
}

/** The check matching the active analysis, with its derived status. */
export function useActiveRouteCheck():
{ check: RouteCheck; status: RouteCheckStatus | undefined } | null {
  const section = useRouteSection();
  const active = useActiveAnalysis();
  const checkActive = useCheckActive();
  if (!section || !checkActive) return null;
  const check = section.route.checks.find(
    (c) => c.analysis === `${active.process}/${active.analysis}`);
  return check ? { check, status: section.checks[check.id] } : null;
}

/** Activate a check: scope the rail to it and drive the viewer to its
 * preferred lens with the check's scope bound into the viewer params. */
export function selectRouteCheck(check: RouteCheck) {
  const store = useStore.getState();
  const section = store.manifest?.route;
  if (!section) return;
  const view = describeCheck(check, section.route);
  if (!view) return;
  const status = section.checks[check.id];
  const target = view.activate(status);
  useV2.getState().setActiveCheck(check.id);
  for (const [name, value] of Object.entries(target.params)) {
    store.setViewerParam(target.processId, name, value);
  }
  useStore.getState().set({
    processId: target.processId, modeId: target.modeId,
  });
}

/** The check the rail is scoped to (validated against the live route). */
export function useSelectedRouteCheck():
{ check: RouteCheck; status: RouteCheckStatus | undefined } | null {
  const section = useRouteSection();
  const activeCheckId = useV2((s) => s.activeCheckId);
  if (!section || !activeCheckId) return null;
  const check = section.route.checks.find((c) => c.id === activeCheckId);
  return check ? { check, status: section.checks[check.id] } : null;
}

export async function storeRoute(route: Route, revision: number) {
  const partId = useStore.getState().partId;
  if (!partId) return;
  try {
    await putRoute(partId, route, revision);
    await refreshManifest();
  } catch (err) {
    useStore.getState().set({
      error: err instanceof Error ? err.message : String(err),
    });
    return;
  }
  // re-bind the active check: its lens params carry route values (the
  // operation's direction, the tool list), which the edit may have moved
  const activeId = useV2.getState().activeCheckId;
  const active = useStore.getState().manifest?.route?.route.checks
    .find((c) => c.id === activeId);
  if (active) selectRouteCheck(active);
}

/** Pin a new policy value on one check (a route revision). */
export async function pinPolicy(check: RouteCheck, policy: Record<string, unknown>) {
  const section = useStore.getState().manifest?.route;
  if (!section) return;
  const route: Route = {
    ...section.route,
    checks: section.route.checks.map((c) =>
      c.id === check.id ? { ...c, policy: { ...c.policy, ...policy } } : c),
  };
  await storeRoute(route, route.revision);
}

/** Save a field lens's band as a check: the compute params become the
 * check's params (its cache identity) and the band its pinned policy.
 * With `checkId` the existing check updates in place; without it a NEW
 * check is added (unique id) — several checks may interpret one lens, each
 * with its own band. Returns the saved check's id. */
export async function saveLensCheck(
  def: FieldLensDef, policy: Record<string, unknown>,
  compute: Record<string, unknown>, checkId: string | null,
): Promise<string | null> {
  const section = useStore.getState().manifest?.route;
  if (!section) return null;
  let id = checkId;
  let checks;
  if (id && section.route.checks.some((c) => c.id === id)) {
    checks = section.route.checks.map((c) => (c.id === id
      ? { ...c, params: compute, policy: { ...c.policy, ...policy } } : c));
  } else {
    const base = `chk-${def.modeId}`;
    id = base;
    for (let n = 2; section.route.checks.some((c) => c.id === id); n++) {
      id = `${base}-${n}`;
    }
    checks = [...section.route.checks, {
      id, analysis: `${def.process}/${def.analysis}`, params: compute,
      policy, lens: def.lensKey,
    }];
  }
  await storeRoute({ ...section.route, checks }, section.route.revision);
  return id;
}

export interface AddOperationInput {
  label: string;
  kind: OperationKind;
  machine?: string | null;
  directionIndex?: number | null;
}

/** Add one operation. NOTHING is seeded with it — no checks, no machine copy.
 * A check on the route is one somebody meant to author, and an operation
 * names a machine from the catalogue rather than snapshotting it. */
export async function addOperation(input: AddOperationInput): Promise<void> {
  const section = useStore.getState().manifest?.route;
  if (!section) return;

  const existing = new Set(section.route.operations.map((op) => op.id));
  const base = input.label.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || input.kind;
  let id = base;
  for (let n = 2; existing.has(id); n++) id = `${base}-${n}`;

  // an operation is ATOMIC: one approach direction, no tilt cone. Grouping
  // ops onto one machine setup is a later inference over the list.
  const operation: Operation = {
    id,
    kind: input.kind,
    label: input.label.trim() || id,
    config: input.kind === 'milling' || input.kind === 'turning'
      ? { direction_index: input.directionIndex ?? 0 } : {},
    ...(input.machine ? { machine: input.machine } : {}),
  };
  await storeRoute(
    { ...section.route, operations: [...section.route.operations, operation] },
    section.route.revision);
}

/** Remove one operation and every check it owns. */
export async function removeOperation(op: Operation): Promise<void> {
  const section = useStore.getState().manifest?.route;
  if (!section) return;
  await storeRoute({
    ...section.route,
    operations: section.route.operations.filter((o) => o.id !== op.id),
    checks: section.route.checks.filter((c) => c.operation !== op.id),
  }, section.route.revision);
}

/** Remove one check. */
export async function removeCheck(check: RouteCheck): Promise<void> {
  const section = useStore.getState().manifest?.route;
  if (!section) return;
  await storeRoute({
    ...section.route,
    checks: section.route.checks.filter((c) => c.id !== check.id),
  }, section.route.revision);
}

/** Add an empty expression check and open the builder on it.
 *
 * Empty on purpose: the check is created so it has an id and a place on the
 * route, and everything it means is authored in the builder. Nothing is
 * seeded — a check that says something you did not write is the failure mode
 * the per-kind defaults had. */
export async function addExpressionCheck(
  operation?: string | null,
): Promise<void> {
  const section = useStore.getState().manifest?.route;
  if (!section) return;
  let id = 'chk-expr';
  for (let n = 2; section.route.checks.some((c) => c.id === id); n++) {
    id = `chk-expr-${n}`;
  }
  const check: RouteCheck = {
    id,
    label: 'Expression',
    sources: [],
    policy: { kind: 'expression', terms: [], aggregate: { limit: 0, severity: 'review' } },
    lens: 'injection_molding:expression',
    ...(operation ? { operation } : {}),
  };
  await storeRoute(
    { ...section.route, checks: [...section.route.checks, check] },
    section.route.revision);
  useV2.getState().setExpressionCheckId(id);
}

/** Edit one operation's config in place (e.g. its direction). */
export async function updateOperation(
  id: string, config: Record<string, unknown>,
): Promise<void> {
  const section = useStore.getState().manifest?.route;
  if (!section) return;
  await storeRoute({
    ...section.route,
    operations: section.route.operations.map((op) => (op.id === id
      ? { ...op, config: { ...op.config, ...config } } : op)),
  }, section.route.revision);
}
