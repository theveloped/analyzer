import clsx from 'clsx';
import {
  Circle, CircleDashed, Compass, Hammer, Plus, Route, Sigma, Telescope, X,
  Zap,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { fetchMachines } from '../../api/client';
import type {
  MachineSummary, Operation, OperationKind, RouteCheck, RouteCheckStatus,
} from '../../api/types';
import { Button } from '../../catalyst/button';
import { Input } from '../../catalyst/input';
import { Select } from '../../catalyst/select';
import { hintCls } from '../components/styles';
import { useStore } from '../../state/store';
import type { Analysis } from '../analyses';
import { describeCheck, useCheckEvaluation } from '../checks/catalog';
import {
  checkState, planCheckState, statusKindOf, type CheckState,
} from '../checks/status';
import { RailSection } from '../components/rail';
import { StatusDot } from '../components/status';
import { useV2 } from '../store';
import { closeStudy, openStudy, STUDIES } from '../studies';
import {
  addOperation, catalogFor, newExpressionCheck, removeCheck, removeOperation,
  selectAnalysis, selectRouteCheck, updateOperation, useActiveAnalysis,
  useCheckActive, useRouteSection, useVisibleAnalyses,
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
      <div className={clsx('ml-[22px] mt-1', hintCls)}>
        {summary}
      </div>
    </button>
  );
}

function Connector() {
  return <div className="ml-[17px] h-2.5 w-px bg-zinc-950/10 dark:bg-white/10" />;
}

/** Route-driven check card: execution from the server-derived expected hash,
 * verdict from the pinned policy (async mask unions for reach checks). */
function RouteCheckCard({ check, status, isActive }: {
  check: RouteCheck; status: RouteCheckStatus | undefined; isActive: boolean;
}) {
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const section = useRouteSection();
  const evaluation = useCheckEvaluation(
    check, section!.route, status, manifest);
  const view = describeCheck(check, section!.route);
  if (!view) return null;
  const [process, analysis] = (check.analysis ?? '/').split('/');
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
      onClick={() => selectRouteCheck(check)}
    />
  );
}

const KIND_ICON: Record<string, typeof Route> = {
  laser: Zap, press_brake: Hammer, milling: Compass, turning: Circle,
};

/** Operation header. An operation is ATOMIC — for milling/turning that is one
 * approach direction and nothing else. There is no tilt cone: a 3+2 machine
 * holding one fixturing across several approaches is several operations that
 * a later grouping recognises as one setup. */
function OperationCard({ op }: { op: Operation }) {
  const manifest = useStore((s) => s.manifest);
  const directions = manifest?.directions ?? [];
  const sources = manifest?.direction_sources ?? [];
  const current = Number(op.config?.direction_index);
  const KindIcon = KIND_ICON[op.kind ?? ''] ?? Route;
  const directional = op.kind === 'milling' || op.kind === 'turning';

  return (
    <div className="group/op mb-1 rounded-lg bg-zinc-950/2.5 p-2 dark:bg-white/5">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
        <KindIcon className="size-3" /> {op.label ?? op.id}
        <button
          type="button"
          title="Remove this operation (and its checks)"
          onClick={() => { void removeOperation(op); }}
          className="ml-auto rounded p-0.5 opacity-0 transition group-hover/op:opacity-100 hover:bg-zinc-950/10 hover:text-zinc-700 dark:hover:bg-white/10 dark:hover:text-zinc-200"
        >
          <X className="size-3" />
        </button>
      </div>
      {op.machine && (
        <div className="mt-0.5 text-[11px]/4 text-zinc-500 dark:text-zinc-400">
          {op.machine}
        </div>
      )}
      {directional && directions.length > 0 && (
        <div className="mt-1.5">
          <Select
            value={Number.isFinite(current) ? String(current) : ''}
            onChange={(e) => {
              const next = parseInt(e.target.value, 10);
              if (next === current) return;
              void updateOperation(op.id, { direction_index: next });
            }}
            aria-label="approach direction"
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

// the kinds offered in the form, labelled. `satisfies` keeps the ids inside
// the OperationKind union that route.py validates, so a kind the backend
// would reject cannot reach the select.
const OP_KINDS = [
  { id: 'milling', label: 'Milling (one direction)' },
  { id: 'turning', label: 'Turning' },
  { id: 'laser', label: 'Laser' },
  { id: 'press_brake', label: 'Press brake' },
] satisfies { id: OperationKind; label: string }[];

/** Inline add-operation form: label, kind, optional machine and (for
 * milling/turning) the approach direction. Adding an operation adds ONLY the
 * operation — checks are authored deliberately, from a lens band or a
 * study, so every check on the route is one somebody meant. */
function AddOperationForm({ onClose }: { onClose: () => void }) {
  const manifest = useStore((s) => s.manifest);
  const [machines, setMachines] = useState<MachineSummary[]>([]);
  const [label, setLabel] = useState('');
  const [kind, setKind] = useState<OperationKind>('milling');
  const [machine, setMachine] = useState('');
  const [direction, setDirection] = useState('0');
  const [building, setBuilding] = useState(false);

  useEffect(() => {
    let live = true;
    fetchMachines().then((m) => { if (live) setMachines(m); }).catch(() => {});
    return () => { live = false; };
  }, []);

  const kindMachines = machines.filter((m) => !m.kind || m.kind === kind);
  const directional = kind === 'milling' || kind === 'turning';

  const add = () => {
    setBuilding(true);
    void addOperation({
      label: label || OP_KINDS.find((k) => k.id === kind)!.label,
      kind,
      machine: machine || null,
      directionIndex: parseInt(direction, 10),
    })
      .then(onClose)
      .catch((err) => useStore.getState().set({ error: String(err) }))
      .finally(() => setBuilding(false));
  };

  return (
    <div className="rounded-lg border border-zinc-950/10 p-2.5 dark:border-white/10">
      <RailSection title="New operation">
        <div className="flex flex-col gap-1.5">
          <Input placeholder="label (e.g. OP30)" value={label}
            onChange={(e) => setLabel(e.target.value)} aria-label="operation label" />
          <Select
            value={kind}
            onChange={(e) => {
              // narrow through the table rather than casting the DOM string
              const next = OP_KINDS.find((k) => k.id === e.target.value);
              if (next) { setKind(next.id); setMachine(''); }
            }}
            aria-label="operation kind"
          >
            {OP_KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
          </Select>
          <Select value={machine} onChange={(e) => setMachine(e.target.value)}
            aria-label="machine">
            <option value="">no machine</option>
            {kindMachines.map((m) => (
              <option key={m.name} value={m.name}>{m.label}</option>
            ))}
          </Select>
          {directional && (manifest?.directions.length ?? 0) > 0 && (
            <Select value={direction} onChange={(e) => setDirection(e.target.value)}
              aria-label="approach direction">
              {(manifest?.directions ?? []).map((d, i) => (
                <option key={i} value={String(i)}>
                  {`dir ${i}`}
                  {manifest?.direction_sources?.[i]?.label
                    ? ` — ${manifest.direction_sources[i].label}` : ''}
                </option>
              ))}
            </Select>
          )}
          <div className="flex gap-1.5">
            <Button outline onClick={onClose} className="flex-1">Cancel</Button>
            <Button onClick={add} disabled={building} className="flex-1">
              {building ? 'Adding…' : 'Add'}
            </Button>
          </div>
        </div>
      </RailSection>
    </div>
  );
}

/** Studies sit above the plan: broad comparisons over many candidates, opened
 * in the right rail. They are exploration, so opening one persists nothing. */
function StudySection() {
  const activeStudy = useV2((s) => s.activeStudy);
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
        <Telescope className="size-3" /> Studies
      </div>
      <div className="flex flex-col gap-1">
        {STUDIES.map((study) => {
          const Icon = study.icon;
          const isActive = activeStudy === study.id;
          return (
            <button
              key={study.id}
              type="button"
              title={study.blurb}
              onClick={() => (isActive ? closeStudy() : openStudy(study))}
              className={clsx(
                'flex items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm/5',
                isActive
                  ? 'bg-blue-50 text-blue-700 ring-1 ring-blue-600/20 dark:bg-blue-500/10 dark:text-blue-300 dark:ring-blue-400/20'
                  : 'text-zinc-700 hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/5')}
            >
              <Icon className="size-3.5 shrink-0" />
              <span className="flex-1">{study.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function PipelineRail() {
  const active = useActiveAnalysis();
  const checkActive = useCheckActive();
  const catalog = useVisibleAnalyses();
  const advanced = useV2((s) => s.advanced);
  const activeCheckId = useV2((s) => s.activeCheckId);
  const section = useRouteSection();
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const viewerParams = useStore((s) => s.viewerParams);
  const manifestVersion = useStore((s) => s.manifestVersion);
  void manifestVersion;
  const [adding, setAdding] = useState(false);

  const routeChecks = (section?.route.checks ?? []).filter((c) => {
    const a = catalogFor(c);
    return a ? (advanced || a.tier === 'primary') : true;
  });
  const operations = section?.route.operations ?? [];
  const hasRoute = routeChecks.length > 0 || operations.length > 0;

  const groups = [
    ...operations.map((op) => ({
      op,
      label: op.label ?? op.id,
      checks: routeChecks.filter((c) => c.operation === op.id),
    })),
    {
      op: null as Operation | null,
      label: 'Review',
      checks: routeChecks.filter((c) => c.operation == null),
    },
  ].filter((g) => g.op !== null || g.checks.length > 0);

  return (
    <div className="flex h-full w-64 shrink-0 flex-col gap-3 overflow-auto border-r border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <StudySection />

      <RailSection
        title={hasRoute ? `Route · rev ${section?.route.revision}` : 'Checks'}
      >
        {hasRoute ? (
          <div className="flex flex-col gap-3">
            {groups.map((group) => (
              <div key={group.op?.id ?? '__review'}>
                {group.op ? (
                  <OperationCard op={group.op} />
                ) : (
                  <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
                    <Compass className="size-3" /> {group.label}
                  </div>
                )}
                {group.checks.map((check, i) => (
                  <div key={check.id} className="group/check relative">
                    <RouteCheckCard
                      check={check}
                      status={section?.checks[check.id]}
                      isActive={activeCheckId === check.id}
                    />
                    <button
                      type="button"
                      title="Remove this check"
                      onClick={() => { void removeCheck(check); }}
                      className="absolute right-1.5 top-1.5 rounded p-0.5 text-zinc-400 opacity-0 transition group-hover/check:opacity-100 hover:bg-zinc-950/10 hover:text-zinc-700 dark:hover:bg-white/10 dark:hover:text-zinc-200"
                    >
                      <X className="size-3" />
                    </button>
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
      </RailSection>

      <div className="flex flex-col gap-2">
        {adding ? (
          <AddOperationForm onClose={() => setAdding(false)} />
        ) : (
          <Button outline onClick={() => setAdding(true)} className="w-full"
            disabled={!manifest}>
            <Plus data-slot="icon" /> Add operation
          </Button>
        )}
        <Button outline onClick={() => newExpressionCheck()}
          className="w-full" disabled={!manifest}>
          <Sigma data-slot="icon" /> Add check
        </Button>
      </div>

      <div className="mt-2 flex items-start gap-2 rounded-lg bg-zinc-950/2.5 p-2.5 text-xs/5 text-zinc-500 dark:bg-white/5 dark:text-zinc-400">
        <CircleDashed className="mt-0.5 size-3.5 shrink-0" />
        {!hasRoute
          ? 'Nothing is planned yet. Explore with a lens or a study, then add '
            + 'the operations you settle on — that is what records the choice.'
          : routeChecks.length === 0
            ? 'No checks yet — this route is UNASSESSED, not passing. Save a '
              + 'lens band or a study total as a check to judge it.'
            : 'Changing an operation\'s direction only re-slices the reach '
              + 'study — nothing recomputes.'}
      </div>
    </div>
  );
}
