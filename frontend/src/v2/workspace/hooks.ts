import { putPlan } from '../../api/client';
import type { Plan, PlanCheck, PlanCheckStatus, PlanSection } from '../../api/types';
import { useStore } from '../../state/store';
import { refreshManifest } from '../../viewer/controller';
import type { Analysis } from '../analyses';
import { ANALYSIS_BY_ID, ANALYSES, defaultCompute } from '../analyses';
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

/** Switch the active analysis (drives the shared viewer's mode + process). */
export function selectAnalysis(a: Analysis) {
  useStore.getState().set({ processId: a.process, modeId: a.id });
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
}

/** The active inspection lens, if the shared process/mode is registered as
 * one (checks and directions also resolve — rails decide precedence). */
export function useActiveLens(): Lens | null {
  const processId = useStore((s) => s.processId);
  const modeId = useStore((s) => s.modeId);
  return lensFor(processId, modeId);
}

/** Activate an inspection lens (drives the shared viewer's mode + process). */
export function selectLens(l: Lens) {
  useStore.getState().set({ processId: l.processId, modeId: l.modeId });
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

/** Activate a plan check: switch the viewer to its catalog lens. */
export function selectPlanCheck(check: PlanCheck) {
  const a = catalogFor(check);
  if (a) selectAnalysis(a);
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
