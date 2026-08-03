import clsx from 'clsx';
import { Pin, Settings2, Sparkles } from 'lucide-react';
import type { RouteCheck, RouteCheckStatus } from '../../api/types';
import { Button } from '../../catalyst/button';
import { Input } from '../../catalyst/input';
import { useStore } from '../../state/store';
import type { Analysis } from '../analyses';
import { evaluateCheck } from '../checks/evaluators';
import {
  planCheckState, resultForHash, statusKindOf, type CheckState,
} from '../checks/status';
import {
  Rail, RailDisclosure, RailDivider, RailHeader, RailRunButton, RailSection,
  RailStats,
} from '../components/rail';
import { useV2 } from '../store';
import { BoolRow, ComputeInput } from './computeFields';
import { FindingRow } from './findings';
import {
  pinPolicy, useActiveAnalysis, useActiveRouteCheck, useCheckState,
} from './hooks';
import { runAnalysis, runRouteCheck, useBusy } from './run';
import { hintCls, labelCls } from '../components/styles';

function ThresholdField({ a }: { a: Analysis }) {
  const params = useStore((s) => s.viewerParams[a.process]) ?? {};
  const setParam = useStore((s) => s.setViewerParam);
  const value = params[a.thresholdParam] ?? a.thresholdDefault;
  return (
    <div>
      <label className={labelCls}>{a.thresholdLabel}</label>
      <div className="mt-2 flex items-center gap-2">
        <Input
          type="number"
          step="0.1"
          value={String(value)}
          onChange={(e) => setParam(a.process, a.thresholdParam, e.target.value)}
        />
        <span className="text-sm/6 text-zinc-500 dark:text-zinc-400">{a.unit}</span>
      </div>
      <p className={clsx('mt-2', hintCls)}>Faces past this limit are flagged. Adjusts instantly — no recompute.</p>
    </div>
  );
}

function DisplayAdvanced({ a }: { a: Analysis }) {
  const params = useStore((s) => s.viewerParams[a.process]) ?? {};
  const setParam = useStore((s) => s.setViewerParam);
  const isSphere = a.id === 'thickness' || a.id === 'gaps';
  return (
    <>
      <div>
        <label className={labelCls}>{a.scaleLabel} ({a.unit})</label>
        <div className="mt-2">
          <Input
            type="number"
            step="0.1"
            placeholder="auto"
            value={params[a.scaleParam] == null ? '' : String(params[a.scaleParam])}
            onChange={(e) => setParam(a.process, a.scaleParam, e.target.value)}
          />
        </div>
      </div>
      {isSphere && (
        <BoolRow
          label="Hide edge artifacts"
          hint="Show readings explained by sharp edges as OK."
          checked={params.maskExplained !== false}
          onChange={(v) => setParam(a.process, 'maskExplained', v)}
        />
      )}
    </>
  );
}

/** Pinned policy vs the live exploration slider: the slider recolors freely;
 * only pinning it changes what the verdict is judged against (plan revision). */
function PolicyRow({ a, check }: { a: Analysis; check: RouteCheck }) {
  const params = useStore((s) => s.viewerParams[a.process]) ?? {};
  const slider = Number(params[a.thresholdParam] ?? a.thresholdDefault);
  const pinned = Number(check.policy?.threshold ?? a.thresholdDefault);
  const differs = isFinite(slider) && slider !== pinned;
  return (
    <div className="rounded-lg bg-zinc-950/2.5 p-2.5 dark:bg-white/5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs/5 font-medium text-zinc-700 dark:text-zinc-300">
          Policy: minimum ≥ {pinned} {a.unit}
        </span>
        {differs && (
          <Button plain onClick={() => void pinPolicy(check, { threshold: slider })}>
            <Pin data-slot="icon" /> Pin {slider}
          </Button>
        )}
      </div>
      <p className={hintCls}>
        The verdict follows the pinned limit; the slider above explores freely.
      </p>
    </div>
  );
}

function PlanFindings({ a, check, status }: {
  a: Analysis; check: RouteCheck; status: RouteCheckStatus | undefined;
}) {
  const manifest = useStore((s) => s.manifest);
  const partId = useStore((s) => s.partId);
  const result = resultForHash(manifest, a, status?.expected_hash ?? null);
  const { verdict, findings } = evaluateCheck(a, check, result);
  if (!partId) return null;
  if (!result) {
    return <p className={hintCls}>Run the check to evaluate it against the policy.</p>;
  }
  if (verdict === 'pass') {
    return <p className={hintCls}>Within policy — nothing to review.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      {findings.map((f) => <FindingRow key={f.id} finding={f} />)}
    </div>
  );
}

export function SettingsRail() {
  const a = useActiveAnalysis();
  const globalAdvanced = useV2((s) => s.advanced);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const meshReady = useStore((s) => s.meshReady);
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const busy = useBusy();
  const heuristic = useCheckState(a);
  const planCheck = useActiveRouteCheck();

  let state: CheckState = heuristic;
  if (planCheck) {
    const result = resultForHash(manifest, a, planCheck.status?.expected_hash ?? null);
    const { verdict } = evaluateCheck(a, planCheck.check, result);
    state = planCheckState(planCheck.status, jobs, partId, a, verdict);
  }
  const computed = state.execution === 'current' || state.execution === 'stale';
  const badgeText = state.note
    || (state.verdict === 'pass' ? 'ok'
      : state.verdict === 'review' ? 'review'
      : 'computed');

  return (
    <Rail>
      <RailHeader
        icon={a.icon}
        title={a.label}
        status={statusKindOf(state)}
        statusLabel={badgeText}
        blurb={a.blurb}
      />

      <ThresholdField a={a} />
      {planCheck && <PolicyRow a={a} check={planCheck.check} />}

      {/* slot 4 sits ABOVE the action: these knobs are inputs to the run —
          this rail's own copy says so — and a Run button above them would
          read as "run, then configure" */}
      <RailDisclosure icon={Settings2} label="Advanced settings"
        defaultOpen={globalAdvanced}>
        <div className="flex items-start gap-1.5 rounded-lg border border-dashed border-zinc-950/10 bg-zinc-950/2.5 p-2 text-xs/5 text-zinc-500 dark:border-white/10 dark:bg-white/5 dark:text-zinc-400">
          <Sparkles className="mt-0.5 size-3 shrink-0" />
          Set correctly by default — change only if you know the part geometry.
          Compute knobs re-run the check.
        </div>
        <DisplayAdvanced a={a} />
        <RailDivider />
        {planCheck ? (
          <div>
            <div className={clsx(labelCls, 'mb-1')}>Pinned compute params</div>
            <p className="whitespace-pre font-mono text-[11px]/4 text-zinc-500 dark:text-zinc-400">
              {Object.entries(planCheck.check.params ?? {})
                .map(([k, v]) => `${k}: ${v === null ? 'auto' : String(v)}`)
                .join('\n')}
            </p>
            <p className={clsx('mt-1', hintCls)}>
              Runs use the route's params so results land under the expected
              hash.
            </p>
          </div>
        ) : (
          a.advancedFields.map((field) => (
            <ComputeInput key={field.key} computeId={a.id} field={field} />
          ))
        )}
      </RailDisclosure>

      <RailRunButton
        execution={state.execution}
        busy={busy}
        onRun={() => (planCheck
          ? runRouteCheck(planCheck.check, planCheck.status)
          : runAnalysis(a))}
        disabled={!meshReady || !!planCheck?.status?.error}
        runLabel="Run check"
        rerunLabel="Re-run check"
      />

      <RailSection title="Findings">
        {planCheck ? (
          <PlanFindings a={a} check={planCheck.check} status={planCheck.status} />
        ) : (
          <p className={hintCls}>
            {computed
              ? 'Adjust the limit or inspect faces in the viewer.'
              : 'Run the check to see findings.'}
          </p>
        )}
      </RailSection>

      <RailStats text={stats} error={error} />
    </Rail>
  );
}
