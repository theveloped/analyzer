import clsx from 'clsx';
import { Play, RotateCw } from 'lucide-react';
import type { Plan, PlanCheck, PlanCheckStatus } from '../../api/types';
import { Button } from '../../catalyst/button';
import { useStore } from '../../state/store';
import { describeCheck, useCheckEvaluation } from '../checks/catalog';
import { planCheckState, statusKindOf } from '../checks/status';
import { StatusBadge } from '../components/status';
import { FindingRow } from './findings';
import { usePlanSection, useSelectedPlanCheck } from './hooks';
import { runPlanCheck, useBusy } from './run';

const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';

/**
 * Right rail for a non-threshold plan check (reach study / per-operation /
 * route aggregate): execution + verdict against the pinned policy, run
 * control, and the findings with their dispositions. The lens params were
 * bound by selectPlanCheck; the viewer paints the same slice being judged.
 */
export function PlanCheckRail() {
  const selected = useSelectedPlanCheck();
  const section = usePlanSection();
  if (!selected || !section) return null;
  return <Rail check={selected.check} status={selected.status}
    plan={section.plan} />;
}

function Rail({ check, status, plan }: {
  check: PlanCheck; status: PlanCheckStatus | undefined; plan: Plan;
}) {
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const meshReady = useStore((s) => s.meshReady);
  const busy = useBusy();
  const evaluation = useCheckEvaluation(check, plan, status, manifest);
  const view = describeCheck(check, plan);
  if (!view) return null;

  const [process, analysis] = check.analysis.split('/');
  const state = planCheckState(status, jobs, partId, { process, analysis },
    evaluation?.verdict ?? 'unknown');
  const evaluating = !evaluation && state.execution === 'current';
  const badgeText = evaluating ? 'evaluating…'
    : state.note
    || (state.verdict === 'pass' ? 'ok'
      : state.verdict === 'review' ? 'review'
      : state.verdict === 'fail' ? 'not producible'
      : state.verdict === 'na' ? 'data' : 'computed');
  const Icon = view.icon;

  return (
    <div className="flex h-full w-72 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div>
        <div className="flex items-center gap-2">
          <Icon className="size-4 text-blue-600 dark:text-blue-400" />
          <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">{view.label}</h2>
          <StatusBadge status={statusKindOf(state)}>{badgeText}</StatusBadge>
        </div>
        <p className={clsx('mt-1', hintCls)}>{view.blurb}</p>
      </div>

      <Button
        onClick={() => runPlanCheck(check, status)}
        disabled={!meshReady || busy || !!status?.error}
        className="w-full"
      >
        {busy ? (
          <><RotateCw data-slot="icon" className="animate-spin" /> Running…</>
        ) : state.execution === 'current' ? (
          <><RotateCw data-slot="icon" /> Re-run</>
        ) : (
          <><Play data-slot="icon" /> Run</>
        )}
      </Button>
      {status?.error && (
        <p className="text-xs/5 text-red-600 dark:text-red-500">⚠ {status.error}</p>
      )}
      {(view.kind === 'reach_op' || view.kind === 'reach_route') && (
        <p className={hintCls}>
          Shares the reach study's result — running any reach check computes
          for all of them; direction changes only re-slice.
        </p>
      )}

      <div className="h-px bg-zinc-950/10 dark:bg-white/10" />

      <div>
        <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">Findings</div>
        {!partId ? null : evaluating ? (
          <p className={hintCls}>Evaluating against the plan…</p>
        ) : state.execution === 'not_run' || state.execution === 'queued'
          || state.execution === 'running' ? (
          <p className={hintCls}>Run the check to evaluate it.</p>
        ) : evaluation?.verdict === 'pass' ? (
          <p className={hintCls}>Within policy — nothing to review.</p>
        ) : evaluation?.verdict === 'na' ? (
          <p className={hintCls}>
            Exploration data — the operation and route checks carry the verdicts.
          </p>
        ) : evaluation?.findings.length ? (
          <div className="flex flex-col gap-2">
            {evaluation.findings.map((f) => (
              <FindingRow key={f.id} finding={f} partId={partId} />
            ))}
          </div>
        ) : (
          <p className={hintCls}>No findings.</p>
        )}
      </div>

      <div>
        <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">In view</div>
        {error ? (
          <p className="whitespace-pre-wrap text-xs/5 text-red-600 dark:text-red-500">⚠ {error}</p>
        ) : (
          <p className="whitespace-pre-wrap text-xs/5 text-zinc-500 dark:text-zinc-400">{stats}</p>
        )}
      </div>
    </div>
  );
}
