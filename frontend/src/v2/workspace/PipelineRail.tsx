import clsx from 'clsx';
import { CircleDashed, ClipboardList, Plus } from 'lucide-react';
import { Button } from '../../catalyst/button';
import type { PlanCheck, PlanCheckStatus } from '../../api/types';
import { useStore } from '../../state/store';
import type { Analysis } from '../analyses';
import { evaluateCheck } from '../checks/evaluators';
import {
  checkState, planCheckState, resultForHash, statusKindOf, type CheckState,
} from '../checks/status';
import { StatusDot } from '../components/status';
import { useV2 } from '../store';
import {
  catalogFor, seedPlan, selectAnalysis, selectPlanCheck, useActiveAnalysis,
  useCheckActive, usePlanSection, useVisibleAnalyses,
} from './hooks';

function summaryOf(a: Analysis, s: CheckState, threshold: number,
  min?: number): string {
  const minText = typeof min === 'number' ? `min ${min.toFixed(2)} ${a.unit}` : '';
  const verdict = s.verdict === 'pass' ? 'ok'
    : s.verdict === 'review' ? `below ${threshold} ${a.unit} — review`
    : '';
  return [verdict, minText, s.note].filter(Boolean).join(' · ')
    || `limit ${threshold} ${a.unit}`;
}

function CheckCard({ a, state, summary, isActive, onClick }: {
  a: Analysis; state: CheckState; summary: string;
  isActive: boolean; onClick: () => void;
}) {
  const Icon = a.icon;
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
        <span className="flex-1 text-sm/5 font-medium text-zinc-950 dark:text-white">{a.label}</span>
        {a.tier === 'advanced' && (
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
 * verdict from the pinned policy. */
function PlanCheckCard({ check, status, isActive }: {
  check: PlanCheck; status: PlanCheckStatus | undefined; isActive: boolean;
}) {
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const a = catalogFor(check);
  if (!a) return null;
  const result = resultForHash(manifest, a, status?.expected_hash ?? null);
  const { verdict } = evaluateCheck(a, check, result);
  const state = planCheckState(status, jobs, partId, a, verdict);
  const threshold = Number(check.policy?.threshold ?? a.thresholdDefault);
  const min = (result?.stats as Record<string, number> | undefined)?.min;
  return (
    <CheckCard
      a={a}
      state={state}
      summary={summaryOf(a, state, threshold, min)}
      isActive={isActive}
      onClick={() => selectPlanCheck(check)}
    />
  );
}

export function PipelineRail() {
  const active = useActiveAnalysis();
  const checkActive = useCheckActive();
  const catalog = useVisibleAnalyses();
  const advanced = useV2((s) => s.advanced);
  const section = usePlanSection();
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const viewerParams = useStore((s) => s.viewerParams);
  const manifestVersion = useStore((s) => s.manifestVersion);
  void manifestVersion;

  const planChecks = (section?.plan.checks ?? []).filter((c) => {
    const a = catalogFor(c);
    return a ? (advanced || a.tier === 'primary') : true;
  });
  const hasPlan = planChecks.length > 0;
  // Steps grouping: declared operations in order, then ungrouped checks in an
  // implicit "Review" step (the Phase-1 default — no operations yet)
  const groups = [
    ...(section?.plan.operations ?? []).map((op) => ({
      id: op.id,
      label: op.label ?? op.id,
      checks: planChecks.filter((c) => c.operation === op.id),
    })),
    {
      id: '__review',
      label: 'Review',
      checks: planChecks.filter((c) => c.operation == null),
    },
  ].filter((g) => g.checks.length > 0);

  return (
    <div className="flex h-full w-64 shrink-0 flex-col gap-3 overflow-auto border-r border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div className="text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">
        {hasPlan ? `Plan · rev ${section?.plan.revision}` : 'Checks'}
      </div>

      {hasPlan ? (
        <div className="flex flex-col gap-3">
          {groups.map((group) => (
            <div key={group.id}>
              <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
                <ClipboardList className="size-3" /> {group.label}
              </div>
              {group.checks.map((check, i) => (
                <div key={check.id}>
                  <PlanCheckCard
                    check={check}
                    status={section?.checks[check.id]}
                    isActive={checkActive
                      && check.analysis === `${active.process}/${active.analysis}`}
                  />
                  {i < group.checks.length - 1 && <Connector />}
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="flex flex-col">
            {catalog.map((a, i) => {
              const threshold = Number(
                (viewerParams[a.process] ?? {})[a.thresholdParam] ?? a.thresholdDefault,
              );
              const state = checkState(manifest, jobs, partId, a, threshold);
              const min = (state.result?.stats as Record<string, number> | undefined)?.min;
              return (
                <div key={a.id}>
                  <CheckCard
                    a={a}
                    state={state}
                    summary={summaryOf(a, state, threshold, min)}
                    isActive={checkActive && a.id === active.id}
                    onClick={() => selectAnalysis(a)}
                  />
                  {i < catalog.length - 1 && <Connector />}
                </div>
              );
            })}
          </div>
          <Button outline onClick={() => void seedPlan()} className="w-full"
            disabled={!manifest}>
            <Plus data-slot="icon" /> Create plan with these checks
          </Button>
        </>
      )}

      <div className="mt-2 flex items-start gap-2 rounded-lg bg-zinc-950/2.5 p-2.5 text-xs/5 text-zinc-500 dark:bg-white/5 dark:text-zinc-400">
        <CircleDashed className="mt-0.5 size-3.5 shrink-0" />
        {hasPlan
          ? 'Verdicts follow the pinned policies; operations and templates land next (docs/PLAN-ARCHITECTURE.md).'
          : 'Creating a plan pins the default limits as policies so verdicts become reproducible.'}
      </div>
    </div>
  );
}
