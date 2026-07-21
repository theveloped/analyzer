import { publishReport } from '../../api/client';
import type { Report } from '../../api/types';
import { useStore } from '../../state/store';
import { captureViewer, refreshManifest } from '../../viewer/controller';
import { describeCheck, evaluateNow } from '../checks/catalog';
import { selectPlanCheck } from '../workspace/hooks';

/**
 * Publish the plan's visible checks as an immutable report bundle. For
 * every check: activate its lens (scope bound in), give the paint a beat,
 * capture the viewer (PNG + camera pose), evaluate against the pinned
 * policy, and collect the evidence — result hash, materialized params,
 * policy, lens key. The server copies the referenced result JSONs into the
 * bundle so later reprocessing can't orphan what was published.
 */
export async function publishPlanReport(
  title: string, onProgress?: (message: string) => void,
): Promise<Report | null> {
  const state = useStore.getState();
  const section = state.manifest?.plan;
  const partId = state.partId;
  const manifest = state.manifest;
  if (!section || !partId || !manifest) return null;

  const visible = section.plan.checks.filter((c) => c.visible !== false);
  const checks: Record<string, any>[] = [];
  for (const check of visible) {
    const view = describeCheck(check, section.plan);
    if (!view) continue;
    onProgress?.(`capturing ${view.label}…`);
    const status = section.checks[check.id];
    selectPlanCheck(check);
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const cap = captureViewer();
    const evaluation = await evaluateNow(
      check, section.plan, status, manifest);
    const [process, analysis] = check.analysis.split('/');
    checks.push({
      id: check.id,
      label: view.label,
      verdict: evaluation.verdict,
      findings: evaluation.findings,
      evidence: {
        process,
        analysis,
        result_hash: status?.expected_hash ?? null,
        params: status?.params ?? null,
        policy: check.policy ?? {},
        lens: check.lens ?? null,
        camera: cap?.camera ?? null,
        plan_revision: section.plan.revision,
      },
      shot: cap?.image ?? null,
    });
  }
  if (!checks.length) return null;
  onProgress?.('publishing…');
  const report = await publishReport(partId, {
    title, part: manifest.part.name, checks,
  });
  await refreshManifest(); // bumps the version → the sidebar list refetches
  return report;
}
