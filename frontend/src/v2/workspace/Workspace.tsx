import { useStore } from '../../state/store';
import { catalogAnalysisFor } from '../checks/catalog';
import { AnalysisToolbar } from './AnalysisToolbar';
import { DirectionsRail } from './DirectionsRail';
import { DirectionTooltip } from './DirectionTooltip';
import { FieldLensRail } from './FieldLensRail';
import {
  useActiveFieldLens, useActiveLens, useAutoRunFieldLens, useCheckActive,
  useDirectionsActive, useSelectedPlanCheck,
} from './hooks';
import { useV2 } from '../store';
import { Legend } from './Legend';
import { LensRail } from './LensRail';
import { MeasureRail } from './MeasureRail';
import { PipelineRail } from './PipelineRail';
import { PlanCheckRail } from './PlanCheckRail';
import { PmiRail } from './PmiRail';
import { SettingsRail } from './SettingsRail';
import { TopBar } from './TopBar';
import { Viewer } from './Viewer';
import { ViewportToolbar } from './ViewportToolbar';

/**
 * The single-part workspace that fills the floating content card: a top bar,
 * then three columns — the pipeline of checks, the 3D viewer with its floating
 * overlays, and a right rail scoped to what's active (check settings, lens
 * info/configure, directions, PMI).
 */
export function Workspace() {
  const partId = useStore((s) => s.partId);
  const meshReady = useStore((s) => s.meshReady);
  const stats = useStore((s) => s.stats);
  const modeId = useStore((s) => s.modeId);
  const directionsActive = useDirectionsActive();
  const checkActive = useCheckActive();
  const activeLens = useActiveLens();
  const activeFieldLens = useActiveFieldLens();
  const selected = useSelectedPlanCheck();
  useAutoRunFieldLens(); // field lenses materialize themselves on first look
  // a selected non-threshold plan check (reach study/op/route) gets its own
  // rail; field lenses get the band panel; other checks the SettingsRail
  const planCheckRail = selected && !catalogAnalysisFor(selected.check);
  const measuring = useV2((s) => s.measure.active);

  // the measure INTERACTION outranks every lens/check rail while active —
  // the lens stays visible in the viewport, only the rail switches
  const rightRail = measuring ? <MeasureRail />
    : modeId === 'pmi' ? <PmiRail />
    : directionsActive ? <DirectionsRail />
    : planCheckRail ? <PlanCheckRail />
    : activeFieldLens ? <FieldLensRail />
    : checkActive ? <SettingsRail />
    : activeLens ? <LensRail />
    : <SettingsRail />;

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <PipelineRail />

        <div className="@container relative min-w-0 flex-1 bg-zinc-100 dark:bg-zinc-950">
          <Viewer />
          <AnalysisToolbar />
          {directionsActive && <DirectionTooltip />}
          <Legend />
          <ViewportToolbar />
          {(!partId || !meshReady) && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="rounded-lg bg-white/90 px-3 py-2 text-sm/6 text-zinc-600 shadow-xs ring-1 ring-zinc-950/5 dark:bg-zinc-900/90 dark:text-zinc-300 dark:ring-white/10">
                {partId ? (stats || 'Loading part…') : 'No part selected — pick one from the sidebar.'}
              </div>
            </div>
          )}
        </div>

        {rightRail}
      </div>
    </div>
  );
}
