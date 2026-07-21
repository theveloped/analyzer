import clsx from 'clsx';
import {
  CircleDashed, Compass, FileUp, Hammer, Plus, Route, X, Zap,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { fetchMachines, fetchRoutes, postPlanRoute } from '../../api/client';
import type {
  PlanCheck, PlanCheckStatus, PlanOperation, RouteSummary,
} from '../../api/types';
import { Button } from '../../catalyst/button';
import { Input } from '../../catalyst/input';
import { Select } from '../../catalyst/select';
import { refreshManifest } from '../../viewer/controller';
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
  buildAddOperationEdit, buildRemoveCheckEdit, buildRemoveOperationEdit,
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

const KIND_ICON: Record<string, typeof Route> = {
  laser: Zap, press_brake: Hammer, cnc_setup: Compass,
};

/** Operation header: the decision surface (direction + tilt for CNC,
 * machine label for the rest); edits stage an impact preview first. */
function OperationCard({ op, stage }: {
  op: PlanOperation; stage: (edit: PendingEdit) => void;
}) {
  const manifest = useStore((s) => s.manifest);
  const section = usePlanSection();
  const directions = manifest?.directions ?? [];
  const sources = manifest?.direction_sources ?? [];
  const current = Number(op.config?.direction_index);
  const KindIcon = KIND_ICON[op.kind ?? ''] ?? Route;

  const patchOps = (config: Record<string, unknown>) =>
    (section?.plan.operations ?? []).map((o) =>
      o.id === op.id ? { ...o, config: { ...o.config, ...config } } : o);

  const currentTilt = Number(op.config?.tilt ?? 90);
  const [tilt, setTilt] = useState(String(currentTilt));

  return (
    <div className="group/op mb-1 rounded-lg bg-zinc-950/2.5 p-2 dark:bg-white/5">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
        <KindIcon className="size-3" /> {op.label ?? op.id}
        <button
          type="button"
          title="Remove this operation (and its checks)"
          onClick={() => {
            const edit = buildRemoveOperationEdit(op);
            if (edit) stage(edit);
          }}
          className="ml-auto rounded p-0.5 opacity-0 transition group-hover/op:opacity-100 hover:bg-zinc-950/10 hover:text-zinc-700 dark:hover:bg-white/10 dark:hover:text-zinc-200"
        >
          <X className="size-3" />
        </button>
      </div>
      {op.machine && (
        <div className="mt-0.5 text-[11px]/4 text-zinc-500 dark:text-zinc-400">
          {op.machine.template} · {op.machine.sha.slice(0, 8)}
        </div>
      )}
      {op.kind === 'cnc_setup' && directions.length > 0 && (
        <div className="mt-1.5 flex items-center gap-1.5">
          <Select
            className="min-w-0 flex-1"
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
          <div className="w-16 shrink-0" title="3+2 tilt cone half-angle (0 = plain 3-axis)">
            <Input
              type="number"
              value={tilt}
              onChange={(e) => setTilt(e.target.value)}
              onBlur={() => {
                const next = parseFloat(tilt);
                if (!isFinite(next) || next === currentTilt) {
                  setTilt(String(currentTilt));
                  return;
                }
                stage({
                  title: `${op.label ?? op.id}: tilt ±${currentTilt}° → ±${next}°`,
                  patch: { operations: patchOps({ tilt: next }) },
                });
              }}
              aria-label="tilt"
            />
          </div>
        </div>
      )}
    </div>
  );
}

const OP_KINDS = [
  { id: 'cnc_setup', label: 'CNC setup' },
  { id: 'laser', label: 'Laser' },
  { id: 'press_brake', label: 'Press brake' },
];

/** Inline add-operation form: label, kind, optional machine template and
 * (for CNC) primary direction. The edit stages through the impact modal
 * and brings the kind's standard checks along. */
function AddOperationForm({ stage, onClose }: {
  stage: (edit: PendingEdit) => void; onClose: () => void;
}) {
  const manifest = useStore((s) => s.manifest);
  const [machines, setMachines] = useState<
    { name: string; label: string; kind: string | null }[]>([]);
  const [label, setLabel] = useState('');
  const [kind, setKind] = useState('cnc_setup');
  const [machine, setMachine] = useState('');
  const [direction, setDirection] = useState('0');
  const [building, setBuilding] = useState(false);

  useEffect(() => {
    let live = true;
    fetchMachines().then((m) => { if (live) setMachines(m); }).catch(() => {});
    return () => { live = false; };
  }, []);

  const kindMachines = machines.filter((m) => !m.kind || m.kind === kind);

  const add = () => {
    setBuilding(true);
    void buildAddOperationEdit({
      label: label || OP_KINDS.find((k) => k.id === kind)!.label,
      kind,
      machine: machine || null,
      directionIndex: parseInt(direction, 10),
    })
      .then((edit) => { if (edit) { stage(edit); onClose(); } })
      .catch((err) => useStore.getState().set({ error: String(err) }))
      .finally(() => setBuilding(false));
  };

  return (
    <div className="rounded-lg border border-zinc-950/10 p-2.5 dark:border-white/10">
      <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">
        New operation
      </div>
      <div className="flex flex-col gap-1.5">
        <Input placeholder="label (e.g. OP30)" value={label}
          onChange={(e) => setLabel(e.target.value)} aria-label="operation label" />
        <Select value={kind} onChange={(e) => { setKind(e.target.value); setMachine(''); }}
          aria-label="operation kind">
          {OP_KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
        </Select>
        <Select value={machine} onChange={(e) => setMachine(e.target.value)}
          aria-label="machine template">
          <option value="">no machine template</option>
          {kindMachines.map((m) => (
            <option key={m.name} value={m.name}>{m.label}</option>
          ))}
        </Select>
        {kind === 'cnc_setup' && (manifest?.directions.length ?? 0) > 0 && (
          <Select value={direction} onChange={(e) => setDirection(e.target.value)}
            aria-label="primary direction">
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
            {building ? 'Preparing…' : 'Add'}
          </Button>
        </div>
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
  const section = usePlanSection();
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const viewerParams = useStore((s) => s.viewerParams);
  const manifestVersion = useStore((s) => s.manifestVersion);
  void manifestVersion;
  const [pending, setPending] = useState<PendingEdit | null>(null);
  const [publishing, setPublishing] = useState<string | null>(null);
  const [routes, setRoutes] = useState<RouteSummary[]>([]);
  const [instantiating, setInstantiating] = useState(false);
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    let live = true;
    fetchRoutes().then((r) => { if (live) setRoutes(r); }).catch(() => {});
    return () => { live = false; };
  }, []);

  const addRoute = (name: string) => {
    if (!partId) return;
    setInstantiating(true);
    void postPlanRoute(partId, name)
      .then(() => refreshManifest())
      .catch((err) => useStore.getState().set({ error: String(err) }))
      .finally(() => setInstantiating(false));
  };

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
                <div key={check.id} className="group/check relative">
                  <PlanCheckCard
                    check={check}
                    status={section?.checks[check.id]}
                    isActive={activeCheckId === check.id}
                  />
                  <button
                    type="button"
                    title="Remove this check"
                    onClick={() => {
                      const view = section
                        ? describeCheck(check, section.plan) : null;
                      const edit = buildRemoveCheckEdit(
                        check, view?.label ?? check.id);
                      if (edit) setPending(edit);
                    }}
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
            <Compass data-slot="icon" /> Add CNC exploration
          </Button>
        )}
        {operations.length === 0 && routes.map((route) => (
          <Button key={route.name} outline className="w-full"
            onClick={() => addRoute(route.name)}
            disabled={!manifest || instantiating}>
            <Route data-slot="icon" />
            {instantiating ? 'Instantiating…' : `Add route: ${route.title}`}
          </Button>
        ))}
        {adding ? (
          <AddOperationForm stage={setPending} onClose={() => setAdding(false)} />
        ) : (
          <Button outline onClick={() => setAdding(true)} className="w-full"
            disabled={!manifest}>
            <Plus data-slot="icon" /> Add operation
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
