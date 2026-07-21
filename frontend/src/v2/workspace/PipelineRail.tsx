import clsx from 'clsx';
import { CircleDashed, Compass, FileUp, Plus, Route } from 'lucide-react';
import { useState } from 'react';
import type { PlanCheck, PlanCheckStatus, PlanOperation } from '../../api/types';
import { Button } from '../../catalyst/button';
import { Select } from '../../catalyst/select';
import { useStore } from '../../state/store';
import type { Analysis } from '../analyses';
import { describeCheck, useCheckEvaluation } from '../checks/catalog';
import {
  checkState, planCheckState, statusKindOf, type CheckState,
} from '../checks/status';
import { StatusDot } from '../components/status';
import { publishPlanReport } from '../report/publish';
import { useV2 } from '../store';
import { ImpactModal, type PendingEdit } from './ImpactModal';
import {
  catalogFor, seedExploration, seedPlan, selectAnalysis, selectPlanCheck,
  useActiveAnalysis, useCheckActive, usePlanSection, useVisibleAnalyses,
} from './hooks';

function CheckCard({ icon: Icon, label, tier, state, summary, isActive, onClick }: {
  icon: Analysis['icon']; label: string; tier: 'primary' | 'advanced';
  state: CheckState; summary: string; isActive: boolean; onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'w-full rounded-lg border p-2.5 text-left transition',
        isActive
          ? 'border-blue-500/30 bg-blue-500/5'
          : 'border-transparent hover:bg-zinc-950/5 dark:hover:bg-white/5',
      )}
    >
      <div className="flex items-center gap-2">
        <StatusDot status={statusKindOf(state)} />
        <Icon className="size-3.5 shrink-0 text-zinc-500 dark:text-zinc-400" />
        <span className="flex-1 text-sm/5 font-medium text-zinc-950 dark:text-white">{label}</span>
        {tier === 'advanced' && (
          <span className="text-[10px] uppercase tracking-wide text-zinc-400">adv</span>
        )}
      </div>
      <div className="ml-[22px] mt-1 text-xs/5 text-zinc-500 dark:text-zinc-400">
        {summary}
      </div>
    </button>
  );
}

function Connector() {
  return <div className="ml-[17px] h-2.5 w-px bg-zinc-950/10 dark:bg-white/10" />;
}

/** Plan-driven check card: execution from the server-derived expected hash,
 * verdict from the pinned policy (async mask unions for reach checks). */
function PlanCheckCard({ check, status, isActive }: {
  check: PlanCheck; status: PlanCheckStatus | undefined; isActive: boolean;
}) {
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const section = usePlanSection();
  const evaluation = useCheckEvaluation(
    check, section!.plan, status, manifest);
  const view = describeCheck(check, section!.plan);
  if (!view) return null;
  const [process, analysis] = check.analysis.split('/');
  const state = planCheckState(status, jobs, partId, { process, analysis },
    evaluation?.verdict ?? 'unknown');
  if (!evaluation && state.execution === 'current') state.note = 'evaluating…';

  let summary = state.note;
  if (state.execution === 'current' || state.execution === 'stale') {
    if (evaluation?.verdict === 'pass') summary = ['ok', state.note].filter(Boolean).join(' · ');
    else if (evaluation?.findings.length) {
      summary = [evaluation.findings[0].detail, state.note].filter(Boolean).join(' · ');
    } else if (view.kind === 'threshold' && view.analysis) {
      const threshold = Number(check.policy?.threshold ?? view.analysis.thresholdDefault);
      summary = summary || `limit ${threshold} ${view.analysis.unit}`;
    } else if (view.kind === 'reach_study') {
      summary = 'computed — sliced by the operation checks';
    }
  }
  return (
    <CheckCard
      icon={view.icon}
      label={view.label}
      tier={view.tier}
      state={state}
      summary={summary || view.blurb}
      isActive={isActive}
      onClick={() => selectPlanCheck(check)}
    />
  );
}

/** Operation header: the decision surface (direction + tilt); edits stage an
 * impact preview before anything is applied. */
function OperationCard({ op, stage }: {
  op: PlanOperation; stage: (edit: PendingEdit) => void;
}) {
  const manifest = useStore((s) => s.manifest);
  const section = usePlanSection();
  const directions = manifest?.directions ?? [];
  const sources = manifest?.direction_sources ?? [];
  const current = Number(op.config?.direction_index);

  const patchOps = (config: Record<string, unknown>) =>
    (section?.plan.operations ?? []).map((o) =>
      o.id === op.id ? { ...o, config: { ...o.config, ...config } } : o);

  return (
    <div className="mb-1 rounded-lg bg-zinc-950/2.5 p-2 dark:bg-white/5">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
        <Route className="size-3" /> {op.label ?? op.id}
        <span className="ml-auto normal-case tracking-normal">±{op.config?.tilt ?? 90}°</span>
      </div>
      {op.kind === 'cnc_setup' && directions.length > 0 && (
        <div className="mt-1.5">
          <Select
            value={Number.isFinite(current) ? String(current) : ''}
            onChange={(e) => {
              const next = parseInt(e.target.value, 10);
              if (next === current) return;
              stage({
                title: `${op.label ?? op.id}: direction ${current} → ${next}`,
                patch: { operations: patchOps({ direction_index: next }) },
              });
            }}
          >
            {directions.map((d, i) => (
              <option key={i} value={String(i)}>
                {`dir ${i}`}
                {sources[i]?.label ? ` — ${sources[i].label}` : ''}
              </option>
            ))}
          </Select>
        </div>
      )}
    </div>
  );
}

export function PipelineRail() {
  const active = useActiveAnalysis();
  const checkActive = useCheckActive();
  const catalog = useVisibleAnalyses();
  const advanced = useV2((s) => s.advanced);
  const activeCheckId = useV2((s) => s.activeCheckId);
  const section = usePlanSection();
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const viewerParams = useStore((s) => s.viewerParams);
  const manifestVersion = useStore((s) => s.manifestVersion);
  void manifestVersion;
  const [pending, setPending] = useState<PendingEdit | null>(null);
  const [publishing, setPublishing] = useState<string | null>(null);

  const publish = () => {
    setPublishing('publishing…');
    void publishPlanReport('DFM report', setPublishing)
      .then((report) => {
        if (report && partId) {
          window.location.hash =
            `#report=${encodeURIComponent(partId)}/${encodeURIComponent(report.rid)}`;
        }
      })
      .catch((err) => useStore.getState().set({ error: String(err) }))
      .finally(() => setPublishing(null));
  };

  const planChecks = (section?.plan.checks ?? []).filter((c) => {
    const a = catalogFor(c);
    return a ? (advanced || a.tier === 'primary') : true;
  });
  const operations = section?.plan.operations ?? [];
  const hasPlan = planChecks.length > 0 || operations.length > 0;
  const hasCncOps = operations.some((o) => o.kind === 'cnc_setup');

  const groups = [
    ...operations.map((op) => ({
      op,
      label: op.label ?? op.id,
      checks: planChecks.filter((c) => c.operation === op.id),
    })),
    {
      op: null as PlanOperation | null,
      label: 'Review',
      checks: planChecks.filter((c) => c.operation == null),
    },
  ].filter((g) => g.op !== null || g.checks.length > 0);

  return (
    <div className="flex h-full w-64 shrink-0 flex-col gap-3 overflow-auto border-r border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div className="text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">
        {hasPlan ? `Plan · rev ${section?.plan.revision}` : 'Checks'}
      </div>

      {hasPlan ? (
        <div className="flex flex-col gap-3">
          {groups.map((group) => (
            <div key={group.op?.id ?? '__review'}>
              {group.op ? (
                <OperationCard op={group.op} stage={setPending} />
              ) : (
                <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
                  <Compass className="size-3" /> {group.label}
                </div>
              )}
              {group.checks.map((check, i) => (
                <div key={check.id}>
                  <PlanCheckCard
                    check={check}
                    status={section?.checks[check.id]}
                    isActive={activeCheckId === check.id}
                  />
                  {i < group.checks.length - 1 && <Connector />}
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-col">
          {catalog.map((a, i) => {
            const threshold = Number(
              (viewerParams[a.process] ?? {})[a.thresholdParam] ?? a.thresholdDefault,
            );
            const state = checkState(manifest, jobs, partId, a, threshold);
            const min = (state.result?.stats as Record<string, number> | undefined)?.min;
            const summary = state.verdict === 'pass'
              ? `ok · min ${min?.toFixed(2)} ${a.unit}`
              : state.verdict === 'review'
                ? `below ${threshold} ${a.unit} — review`
                : `${state.note} · limit ${threshold} ${a.unit}`;
            return (
              <div key={a.id}>
                <CheckCard
                  icon={a.icon}
                  label={a.label}
                  tier={a.tier}
                  state={state}
                  summary={summary}
                  isActive={checkActive && a.id === active.id}
                  onClick={() => selectAnalysis(a)}
                />
                {i < catalog.length - 1 && <Connector />}
              </div>
            );
          })}
        </div>
      )}

      <div className="flex flex-col gap-2">
        {!hasPlan && (
          <Button outline onClick={() => void seedPlan()} className="w-full"
            disabled={!manifest}>
            <Plus data-slot="icon" /> Create plan with these checks
          </Button>
        )}
        {!hasCncOps && (
          <Button outline onClick={() => void seedExploration()} className="w-full"
            disabled={!manifest}>
            <Route data-slot="icon" /> Add CNC exploration
          </Button>
        )}
        {hasPlan && (
          <Button onClick={publish} className="w-full"
            disabled={!manifest || !!publishing}>
            <FileUp data-slot="icon" />
            {publishing ?? 'Publish report'}
          </Button>
        )}
      </div>

      <div className="mt-2 flex items-start gap-2 rounded-lg bg-zinc-950/2.5 p-2.5 text-xs/5 text-zinc-500 dark:bg-white/5 dark:text-zinc-400">
        <CircleDashed className="mt-0.5 size-3.5 shrink-0" />
        {hasPlan
          ? 'Direction changes preview their impact first — the reach study covers every candidate, so re-slicing is free.'
          : 'Creating a plan pins the default limits as policies so verdicts become reproducible.'}
      </div>

      {pending && <ImpactModal edit={pending} onClose={() => setPending(null)} />}
    </div>
  );
}
