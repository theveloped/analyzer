import { Disclosure, DisclosureButton, DisclosurePanel } from '@headlessui/react';
import clsx from 'clsx';
import { ChevronDown, Pin, Play, RotateCw, Settings2, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { postDisposition } from '../../api/client';
import type { PlanCheck, PlanCheckStatus } from '../../api/types';
import { Button } from '../../catalyst/button';
import { Input } from '../../catalyst/input';
import { Switch } from '../../catalyst/switch';
import { useStore } from '../../state/store';
import { refreshManifest } from '../../viewer/controller';
import type { Analysis, ComputeField } from '../analyses';
import { dispositionOf, evaluateCheck, type Finding } from '../checks/evaluators';
import {
  planCheckState, resultForHash, statusKindOf, type CheckState,
} from '../checks/status';
import { StatusBadge } from '../components/status';
import { useV2 } from '../store';
import {
  pinPolicy, useActiveAnalysis, useActivePlanCheck, useCheckState,
  usePlanSection,
} from './hooks';
import { runAnalysis, runPlanCheck, useBusy } from './run';

const labelCls = 'text-sm/6 font-medium text-zinc-950 dark:text-white';
const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';

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

function BoolRow({ label, hint, checked, onChange }: {
  label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div className={labelCls}>{label}</div>
        {hint && <p className={hintCls}>{hint}</p>}
      </div>
      <Switch checked={checked} onChange={onChange} aria-label={label} />
    </div>
  );
}

function ComputeInput({ a, field }: { a: Analysis; field: ComputeField }) {
  const value = useV2((s) => s.compute[a.id]?.[field.key]);
  const setCompute = useV2((s) => s.setCompute);
  if (field.type === 'bool') {
    return (
      <BoolRow
        label={field.label}
        hint={field.hint}
        checked={value === true}
        onChange={(v) => setCompute(a.id, field.key, v)}
      />
    );
  }
  return (
    <div>
      <label className={labelCls}>{field.label}{field.unit ? ` (${field.unit})` : ''}</label>
      <div className="mt-2">
        <Input
          type="number"
          step="0.1"
          placeholder={field.placeholder}
          value={value == null ? '' : String(value)}
          onChange={(e) => {
            const raw = e.target.value;
            setCompute(a.id, field.key, raw === '' ? null : Number(raw));
          }}
        />
      </div>
      {field.hint && <p className={clsx('mt-1', hintCls)}>{field.hint}</p>}
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
function PolicyRow({ a, check }: { a: Analysis; check: PlanCheck }) {
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

const DISPOSITION_BADGE = {
  open: 'neutral', accepted: 'good',
  customer_approval: 'warning', resolved: 'good',
} as const;

function FindingRow({ finding, partId }: { finding: Finding; partId: string }) {
  const section = usePlanSection();
  const [note, setNote] = useState('');
  const state = dispositionOf(finding, section?.dispositions);
  const judge = (next: 'accepted' | 'open') => {
    void postDisposition(partId, {
      finding_id: finding.id, state: next, by: 'engineer', why: note,
    }).then(() => refreshManifest())
      .catch((err) => useStore.getState().set({ error: String(err) }));
  };
  return (
    <div className="rounded-lg border border-zinc-950/5 p-2 dark:border-white/10">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs/5 font-medium text-zinc-950 dark:text-white">
          {finding.label}
        </span>
        <StatusBadge status={DISPOSITION_BADGE[state]}>{state.replace('_', ' ')}</StatusBadge>
      </div>
      <p className={hintCls}>{finding.detail}</p>
      <div className="mt-1.5 flex items-center gap-1.5">
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="why…"
          className="w-full rounded-md bg-zinc-950/5 px-2 py-1 text-xs/5 text-zinc-950 outline-none placeholder:text-zinc-400 dark:bg-white/10 dark:text-white"
        />
        {state === 'open' ? (
          <Button plain onClick={() => judge('accepted')}>Accept</Button>
        ) : (
          <Button plain onClick={() => judge('open')}>Reopen</Button>
        )}
      </div>
    </div>
  );
}

function PlanFindings({ a, check, status }: {
  a: Analysis; check: PlanCheck; status: PlanCheckStatus | undefined;
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
      {findings.map((f) => <FindingRow key={f.id} finding={f} partId={partId} />)}
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
  const planCheck = useActivePlanCheck();

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
    <div className="flex h-full w-72 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div>
        <div className="flex items-center gap-2">
          <a.icon className="size-4 text-blue-600 dark:text-blue-400" />
          <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">{a.label}</h2>
          <StatusBadge status={statusKindOf(state)}>{badgeText}</StatusBadge>
        </div>
        <p className={clsx('mt-1', hintCls)}>{a.blurb}</p>
      </div>

      <ThresholdField a={a} />
      {planCheck && <PolicyRow a={a} check={planCheck.check} />}

      <Button
        onClick={() => (planCheck
          ? runPlanCheck(planCheck.check, planCheck.status)
          : runAnalysis(a))}
        disabled={!meshReady || busy || !!planCheck?.status?.error}
        className="w-full"
      >
        {busy ? (
          <><RotateCw data-slot="icon" className="animate-spin" /> Running…</>
        ) : computed ? (
          <><RotateCw data-slot="icon" /> Re-run check</>
        ) : (
          <><Play data-slot="icon" /> Run check</>
        )}
      </Button>

      <Disclosure defaultOpen={globalAdvanced}>
        {({ open }) => (
          <>
            <DisclosureButton className="flex w-full items-center justify-between rounded-lg px-1 py-1 text-xs/5 font-medium text-zinc-500 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white">
              <span className="flex items-center gap-1.5">
                <Settings2 className="size-3.5" /> Advanced settings
              </span>
              <ChevronDown className={clsx('size-3.5 transition-transform', open && 'rotate-180')} />
            </DisclosureButton>
            <DisclosurePanel className="mt-2 flex flex-col gap-4">
              <div className="flex items-start gap-1.5 rounded-lg border border-dashed border-zinc-950/10 bg-zinc-950/2.5 p-2 text-xs/5 text-zinc-500 dark:border-white/10 dark:bg-white/5 dark:text-zinc-400">
                <Sparkles className="mt-0.5 size-3 shrink-0" />
                Set correctly by default — change only if you know the part geometry. Compute knobs re-run the check.
              </div>
              <DisplayAdvanced a={a} />
              <div className="h-px bg-zinc-950/10 dark:bg-white/10" />
              {planCheck ? (
                <div>
                  <div className={clsx(labelCls, 'mb-1')}>Pinned compute params</div>
                  <p className="whitespace-pre-wrap font-mono text-[11px]/4 text-zinc-500 dark:text-zinc-400">
                    {Object.entries(planCheck.check.params)
                      .map(([k, v]) => `${k}: ${v === null ? 'auto' : String(v)}`)
                      .join('\n')}
                  </p>
                  <p className={clsx('mt-1', hintCls)}>
                    Runs use the plan's params so results land under the
                    expected hash. Param editing moves into the plan next phase.
                  </p>
                </div>
              ) : (
                a.advancedFields.map((field) => (
                  <ComputeInput key={field.key} a={a} field={field} />
                ))
              )}
            </DisclosurePanel>
          </>
        )}
      </Disclosure>

      <div className="mt-1 h-px bg-zinc-950/10 dark:bg-white/10" />

      <div>
        <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">Findings</div>
        {planCheck ? (
          <PlanFindings a={a} check={planCheck.check} status={planCheck.status} />
        ) : error ? (
          <p className="whitespace-pre-wrap text-xs/5 text-red-600 dark:text-red-500">⚠ {error}</p>
        ) : stats ? (
          <p className="whitespace-pre-wrap text-xs/5 text-zinc-500 dark:text-zinc-400">{stats}</p>
        ) : (
          <p className={hintCls}>
            {computed ? 'Adjust the limit or inspect faces in the viewer.' : 'Run the check to see findings.'}
          </p>
        )}
      </div>
    </div>
  );
}
