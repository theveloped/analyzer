import { Pencil } from 'lucide-react';
import type { Route, RouteCheck, RouteCheckStatus } from '../../api/types';
import { Button } from '../../catalyst/button';
import { useStore } from '../../state/store';
import { describeCheck, useCheckEvaluation } from '../checks/catalog';
import { planCheckState, statusKindOf } from '../checks/status';
import {
  Rail, RailError, RailHeader, RailRunButton, RailSection, RailStats,
} from '../components/rail';
import { hintCls } from '../components/styles';
import { FindingRow } from './findings';
import {
  editExpressionCheck, useRouteSection, useSelectedRouteCheck,
} from './hooks';
import { runRouteCheck, useBusy } from './run';

/**
 * Right rail for a non-threshold route check (reach study / per-operation /
 * route aggregate / expression): execution + verdict against the pinned
 * policy, the run control, and its derived findings. The lens params were
 * bound by selectRouteCheck; the viewer paints the same slice being judged.
 *
 * The first rail converted to the shared skeleton (components/rail) — it has no
 * params and no collapsed groups, so it is slots 1 · 2 · 5 · 6 · 7 and nothing
 * else.
 */
export function RouteCheckRail() {
  const selected = useSelectedRouteCheck();
  const section = useRouteSection();
  if (!selected || !section) return null;
  return <CheckRail check={selected.check} status={selected.status}
    route={section.route} />;
}

function CheckRail({ check, status, route }: {
  check: RouteCheck; status: RouteCheckStatus | undefined; route: Route;
}) {
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const meshReady = useStore((s) => s.meshReady);
  const busy = useBusy();
  const evaluation = useCheckEvaluation(check, route, status, manifest);
  const view = describeCheck(check, route);
  if (!view) return null;

  const [process, analysis] = (check.analysis ?? '/').split('/');
  const state = planCheckState(status, jobs, partId, { process, analysis },
    evaluation?.verdict ?? 'unknown');
  const evaluating = !evaluation && state.execution === 'current';
  const badgeText = evaluating ? 'evaluating…'
    : state.note
    || (state.verdict === 'pass' ? 'ok'
      : state.verdict === 'review' ? 'review'
      : state.verdict === 'fail' ? 'not producible'
        : state.verdict === 'na' ? 'data' : 'computed');

  // what the action means, per kind — the hint belongs to the button, so it
  // sits under it rather than floating between sections
  const actionHint = view.kind === 'expression'
    ? 'Runs every field the expression reads that is not already cached. '
      + 'Editing the rule itself recomputes nothing — a band is '
      + 'interpretation, not a param.'
    : view.kind === 'reach_op' || view.kind === 'reach_route'
      ? 'Shares the reach study\'s result — running any reach check computes '
        + 'for all of them; direction changes only re-slice.'
      : null;

  return (
    <Rail>
      <RailHeader
        icon={view.icon}
        title={view.label}
        status={statusKindOf(state)}
        statusLabel={badgeText}
        blurb={view.blurb}
        actions={view.kind === 'expression' && (
          <Button plain onClick={() => editExpressionCheck(check)}
            title="Edit this expression">
            <Pencil data-slot="icon" /> Edit
          </Button>
        )}
      />

      {/* slot 2: a params error blocks the run below it, so it reads first */}
      <RailError>{status?.error}</RailError>

      <div>
        <RailRunButton
          execution={state.execution}
          busy={busy}
          onRun={() => runRouteCheck(check, status)}
          disabled={!meshReady || !!status?.error}
        />
        {actionHint && <p className={`mt-2 ${hintCls}`}>{actionHint}</p>}
      </div>

      <RailSection title="Findings">
        {!partId ? null : evaluating ? (
          <p className={hintCls}>Evaluating against the route…</p>
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
                <FindingRow key={f.id} finding={f} />
              ))}
            </div>
          ) : (
            <p className={hintCls}>No findings.</p>
          )}
      </RailSection>

      <RailStats text={stats} error={error} />
    </Rail>
  );
}
