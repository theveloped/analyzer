import { useEffect } from 'react';
import { postPlanMachine, putPlan } from '../../api/client';
import type {
  Plan, PlanCheck, PlanCheckStatus, PlanOperation, PlanSection,
} from '../../api/types';
import { useStore } from '../../state/store';
import { refreshManifest } from '../../viewer/controller';
import { runAnalysisJob } from '../../viewer/jobs';
import type { Analysis } from '../analyses';
import { ANALYSIS_BY_ID, ANALYSES, defaultCompute } from '../analyses';
import { DEFAULT_TOOLS, describeCheck } from '../checks/catalog';
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
  const section = useStore.getState().manifest?.plan;
  const check = section?.plan.checks.find(
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
 * the rail instead of looping. */
const autoRunAttempted = new Set<string>();
export function useAutoRunFieldLens() {
  const def = useActiveFieldLens();
  const partId = useStore((s) => s.partId);
  const meshReady = useStore((s) => s.meshReady);
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  useEffect(() => {
    if (!def || !partId || !meshReady) return;
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
// Production plan (manifest.plan → plans.py sidecars)

/** The manifest's plan section (plan + derived per-check status). */
export function usePlanSection(): PlanSection | null {
  return useStore((s) => s.manifest?.plan ?? null);
}

/** Catalog UI metadata for a plan check (icon/label/threshold vocabulary). */
export function catalogFor(check: PlanCheck): Analysis | null {
  return ANALYSES.find(
    (a) => `${a.process}/${a.analysis}` === check.analysis) ?? null;
}

/** The plan check matching the active analysis, with its derived status. */
export function useActivePlanCheck():
{ check: PlanCheck; status: PlanCheckStatus | undefined } | null {
  const section = usePlanSection();
  const active = useActiveAnalysis();
  const checkActive = useCheckActive();
  if (!section || !checkActive) return null;
  const check = section.plan.checks.find(
    (c) => c.analysis === `${active.process}/${active.analysis}`);
  return check ? { check, status: section.checks[check.id] } : null;
}

/** Activate a plan check: scope the rail to it and drive the viewer to its
 * preferred lens with the check's scope bound into the viewer params. */
export function selectPlanCheck(check: PlanCheck) {
  const store = useStore.getState();
  const section = store.manifest?.plan;
  if (!section) return;
  const view = describeCheck(check, section.plan);
  if (!view) return;
  const status = section.checks[check.id];
  const target = view.activate(status?.expected_hash ?? null);
  useV2.getState().setActiveCheck(check.id);
  for (const [name, value] of Object.entries(target.params)) {
    store.setViewerParam(target.processId, name, value);
  }
  useStore.getState().set({
    processId: target.processId, modeId: target.modeId,
  });
}

/** The plan check the rail is scoped to (validated against the live plan). */
export function useSelectedPlanCheck():
{ check: PlanCheck; status: PlanCheckStatus | undefined } | null {
  const section = usePlanSection();
  const activeCheckId = useV2((s) => s.activeCheckId);
  if (!section || !activeCheckId) return null;
  const check = section.plan.checks.find((c) => c.id === activeCheckId);
  return check ? { check, status: section.checks[check.id] } : null;
}

async function storePlan(plan: Plan, revision: number) {
  const partId = useStore.getState().partId;
  if (!partId) return;
  try {
    await putPlan(partId, plan, revision);
    await refreshManifest();
  } catch (err) {
    useStore.getState().set({
      error: err instanceof Error ? err.message : String(err),
    });
  }
}

/** Seed the part's plan with one check per catalog analysis, pinning the
 * default thresholds as policies (the "standard checks" template). */
export async function seedPlan() {
  const section = useStore.getState().manifest?.plan;
  const plan: Plan = section
    ? { ...section.plan }
    : { schema: 1, revision: 0, decisions: {}, operations: [], checks: [] };
  plan.checks = ANALYSES.map((a) => ({
    id: `chk-${a.id}`,
    analysis: `${a.process}/${a.analysis}`,
    params: defaultCompute(a),
    policy: { threshold: a.thresholdDefault, unit: a.unit },
    lens: `${a.process}:${a.id}`,
    visible: true,
  }));
  await storePlan(plan, plan.revision);
}

/** Pin a new policy value on one check (a plan revision). */
export async function pinPolicy(check: PlanCheck, policy: Record<string, unknown>) {
  const section = useStore.getState().manifest?.plan;
  if (!section) return;
  const plan: Plan = {
    ...section.plan,
    checks: section.plan.checks.map((c) =>
      c.id === check.id ? { ...c, policy: { ...c.policy, ...policy } } : c),
  };
  await storePlan(plan, plan.revision);
}

/** Save a field lens's band as a plan check: the compute params become the
 * check's params (its cache identity) and the band its pinned policy.
 * With `checkId` the existing check updates in place; without it a NEW
 * check is added (unique id) — several checks may interpret one lens, each
 * with its own band. Returns the saved check's id. */
export async function saveLensCheck(
  def: FieldLensDef, policy: Record<string, unknown>,
  compute: Record<string, unknown>, checkId: string | null,
): Promise<string | null> {
  const section = useStore.getState().manifest?.plan;
  if (!section) return null;
  let id = checkId;
  let checks;
  if (id && section.plan.checks.some((c) => c.id === id)) {
    checks = section.plan.checks.map((c) => (c.id === id
      ? { ...c, params: compute, policy: { ...c.policy, ...policy } } : c));
  } else {
    const base = `chk-${def.modeId}`;
    id = base;
    for (let n = 2; section.plan.checks.some((c) => c.id === id); n++) {
      id = `${base}-${n}`;
    }
    checks = [...section.plan.checks, {
      id, analysis: `${def.process}/${def.analysis}`, params: compute,
      policy, lens: def.lensKey, visible: true,
    }];
  }
  await storePlan({ ...section.plan, checks }, section.plan.revision);
  return id;
}

/** Apply an already-previewed plan edit (the impact modal's Apply). The
 * active check re-binds afterwards: its lens params carry plan values
 * (direction, tilt, tools), which the edit may have changed. */
export async function applyPlanEdit(edit: Partial<Plan>) {
  const section = useStore.getState().manifest?.plan;
  if (!section) return;
  await storePlan({ ...section.plan, ...edit }, section.plan.revision);
  const activeId = useV2.getState().activeCheckId;
  const fresh = useStore.getState().manifest?.plan;
  const active = fresh?.plan.checks.find((c) => c.id === activeId);
  if (active) selectPlanCheck(active);
}

/** The standard checks an operation of a kind brings along — the same set
 * the route templates seed, so hand-built routes behave identically. */
function defaultChecksFor(
  kind: string, opId: string,
  machineData: Record<string, any>, snapshotPath: string | null,
): PlanCheck[] {
  if (kind === 'laser') {
    return [
      { id: `chk-${opId}-detect`, analysis: 'sheet_metal/detect', params: {},
        policy: { kind: 'stats', rule: 'sheet_detect' },
        operation: opId, lens: 'sheet_metal:sheet_roles', visible: true },
      { id: `chk-${opId}-pattern`, analysis: 'sheet_metal/flat_pattern',
        params: {}, policy: { kind: 'stats', rule: 'flat_pattern' },
        operation: opId, lens: 'sheet_metal:flat_pattern', visible: true },
    ];
  }
  if (kind === 'cnc_setup') {
    return [
      { id: `chk-${opId}-features`, analysis: 'cnc/features', params: {},
        policy: { kind: 'stats', rule: 'features' },
        operation: opId, lens: 'cnc:features', visible: true },
      { id: `chk-${opId}-reach`, analysis: 'cnc/reach_study',
        params: { direction_indices: [],
          tools: machineData.tools ?? DEFAULT_TOOLS },
        policy: { scope: 'operation', mask: 'features' },
        operation: opId, lens: 'cnc:reach_op', visible: true },
    ];
  }
  if (kind === 'press_brake') {
    return [
      { id: `chk-${opId}-bend`, analysis: 'sheet_metal/bend_plan',
        params: snapshotPath ? { machine_path: snapshotPath } : {},
        policy: { kind: 'stats', rule: 'bend_plan' },
        operation: opId, lens: 'sheet_metal:bend_sequence', visible: true },
    ];
  }
  return [];
}

export interface AddOperationInput {
  label: string;
  kind: string;
  machine?: string | null;
  directionIndex?: number | null;
}

/** Build the plan edit adding one operation (with its kind's standard
 * checks) — the caller stages it through the impact modal. Snapshots the
 * chosen machine template server-side first. */
export async function buildAddOperationEdit(
  input: AddOperationInput,
): Promise<{ title: string; patch: Partial<Plan> } | null> {
  const state = useStore.getState();
  const section = state.manifest?.plan;
  const partId = state.partId;
  if (!section || !partId) return null;

  const existing = new Set(section.plan.operations.map((op) => op.id));
  const base = input.label.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || input.kind;
  let id = base;
  for (let n = 2; existing.has(id); n++) id = `${base}-${n}`;

  let machineRef: PlanOperation['machine'];
  let machineData: Record<string, any> = {};
  let snapshotPath: string | null = null;
  if (input.machine) {
    const snap = await postPlanMachine(partId, input.machine);
    machineRef = { template: snap.template, sha: snap.sha };
    machineData = snap.machine;
    snapshotPath = snap.path;
  }

  const operation: PlanOperation = {
    id,
    kind: input.kind,
    label: input.label.trim() || id,
    config: {
      ...machineData.config,
      ...(input.kind === 'cnc_setup' ? {
        direction_index: input.directionIndex ?? 0,
        tilt: machineData.config?.tilt ?? 0,
      } : {}),
    },
    ...(machineRef ? { machine: machineRef } : {}),
    ...(input.kind === 'cnc_setup'
      ? { produces: { features: 'holes' } } : {}),
  };
  return {
    title: `Add operation ${operation.label}`,
    patch: {
      operations: [...section.plan.operations, operation],
      checks: [...section.plan.checks,
        ...defaultChecksFor(input.kind, id, machineData, snapshotPath)],
    },
  };
}

/** The plan edit removing one operation and every check it owns. */
export function buildRemoveOperationEdit(
  op: PlanOperation,
): { title: string; patch: Partial<Plan> } | null {
  const section = useStore.getState().manifest?.plan;
  if (!section) return null;
  return {
    title: `Remove operation ${op.label ?? op.id}`,
    patch: {
      operations: section.plan.operations.filter((o) => o.id !== op.id),
      checks: section.plan.checks.filter((c) => c.operation !== op.id),
    },
  };
}

/** The plan edit removing one check. */
export function buildRemoveCheckEdit(
  check: PlanCheck, label: string,
): { title: string; patch: Partial<Plan> } | null {
  const section = useStore.getState().manifest?.plan;
  if (!section) return null;
  return {
    title: `Remove check ${label}`,
    patch: {
      checks: section.plan.checks.filter((c) => c.id !== check.id),
    },
  };
}

/** Seed the CNC exploration route: OP10/OP20 (±Z when sampled) plus a reach
 * study over every candidate direction and the default tool library, with
 * per-operation and route-aggregate checks slicing the SAME study result —
 * flipping an operation's direction never recomputes geometry. */
export async function seedExploration() {
  const state = useStore.getState();
  const section = state.manifest?.plan;
  if (!section) return;
  const directions = state.manifest?.directions ?? [];
  if (!directions.length) {
    state.set({ error: 'no candidate directions yet — run prep/directions '
      + '(the crosshair view) before seeding the exploration' });
    return;
  }
  // default the two ops to the most ±Z-like candidates (by vector, not by
  // index — the axes prefix is a convention, not a guarantee)
  const dotZ = directions.map((d) => d[2]);
  const d10 = dotZ.indexOf(Math.max(...dotZ));
  const d20 = dotZ.indexOf(Math.min(...dotZ));
  const studyParams = { direction_indices: [], tools: DEFAULT_TOOLS };
  const plan: Plan = {
    ...section.plan,
    operations: [
      ...section.plan.operations,
      // tilt 0 = plain 3-axis: each op covers exactly its own direction, so
      // flipping a direction visibly changes the slice (tilt 90 would make
      // every direction's cone identical over a small study)
      { id: 'op10', kind: 'cnc_setup', label: 'OP10',
        config: { direction_index: d10, tilt: 0 } },
      { id: 'op20', kind: 'cnc_setup', label: 'OP20',
        config: { direction_index: d20, tilt: 0 } },
    ],
    checks: [
      ...section.plan.checks,
      { id: 'chk-reach-study', analysis: 'cnc/reach_study',
        params: studyParams, policy: { scope: 'study' },
        lens: 'cnc:reach_study', visible: false },
      { id: 'chk-reach-op10', analysis: 'cnc/reach_study',
        params: studyParams, operation: 'op10',
        policy: { scope: 'operation' }, lens: 'cnc:reach_op', visible: true },
      { id: 'chk-reach-op20', analysis: 'cnc/reach_study',
        params: studyParams, operation: 'op20',
        policy: { scope: 'operation' }, lens: 'cnc:reach_op', visible: true },
      { id: 'chk-reach-route', analysis: 'cnc/reach_study',
        params: studyParams,
        policy: { scope: 'route', aggregation: 'geometry-union' },
        lens: 'cnc:reach_aggregate', visible: true },
    ],
  };
  await storePlan(plan, plan.revision);
}
