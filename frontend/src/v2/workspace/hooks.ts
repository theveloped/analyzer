import { putPlan } from '../../api/client';
import type { Plan, PlanCheck, PlanCheckStatus, PlanSection } from '../../api/types';
import { useStore } from '../../state/store';
import { refreshManifest } from '../../viewer/controller';
import type { Analysis } from '../analyses';
import { ANALYSIS_BY_ID, ANALYSES, defaultCompute } from '../analyses';
import { DEFAULT_TOOLS, describeCheck } from '../checks/catalog';
import { checkState, type CheckState } from '../checks/status';
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
 * A lens picked directly is free exploration — it drops the check scope. */
export function selectLens(l: Lens) {
  useStore.getState().set({ processId: l.processId, modeId: l.modeId });
  useV2.getState().setActiveCheck(null);
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

/** Apply an already-previewed plan edit (the impact modal's Apply). */
export async function applyPlanEdit(edit: Partial<Plan>) {
  const section = useStore.getState().manifest?.plan;
  if (!section) return;
  await storePlan({ ...section.plan, ...edit }, section.plan.revision);
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
      { id: 'op10', kind: 'cnc_setup', label: 'OP10',
        config: { direction_index: d10, tilt: 90 } },
      { id: 'op20', kind: 'cnc_setup', label: 'OP20',
        config: { direction_index: d20, tilt: 90 } },
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
