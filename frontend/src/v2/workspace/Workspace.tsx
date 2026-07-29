import { useEffect, useRef } from 'react';
import { useStore } from '../../state/store';
import type { ViewportState } from '../../viewer/viewportState';
import { catalogAnalysisFor } from '../checks/catalog';
import { AnalysisToolbar } from './AnalysisToolbar';
import { DirectionsRail } from './DirectionsRail';
import { DirectionsTableRail } from './DirectionsTableRail';
import { DirectionTooltip } from './DirectionTooltip';
import { FieldLensRail } from './FieldLensRail';
import {
  useActiveFieldLens, useActiveLens, useAutoRunFieldLens, useCheckActive,
  useDirectionsActive, useSelectedPlanCheck,
} from './hooks';
import { useV2 } from '../store';
import { useActiveStudy } from '../studies';
import { Legend } from './Legend';
import { LensRail } from './LensRail';
import { MeasureRail } from './MeasureRail';
import { SectionRail } from './SectionRail';
import { PipelineRail } from './PipelineRail';
import { PlanCheckRail } from './PlanCheckRail';
import { PmiCallouts } from './PmiCallout';
import { PmiRail } from './PmiRail';
import { RightRail } from './RightRail';
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
  const sectionRailOpen = useV2((s) => s.sectionRailOpen);
  const activeStudy = useActiveStudy();
  const setViewport = useV2((s) => s.setViewport);

  // PMI reads best as an xray shell with the BREP edges; only the annotated
  // faces are painted (the lens returns null elsewhere), so no opacity tricks
  // are needed. Restore the prior viewport when the lens closes.
  // brep_edges.npy is written by prep/mesh only, so on the coarse preview
  // asking for edges just turns on a toggle that renders nothing — leave it
  // alone there rather than lying about the viewport state.
  const hasFineMesh = useStore((s) => !!s.manifest?.mesh);
  const savedViewport = useRef<ViewportState | null>(null);
  useEffect(() => {
    if (modeId !== 'pmi') return;
    savedViewport.current = useV2.getState().viewport;
    setViewport(hasFineMesh
      ? { style: 'xray', brepEdges: true }
      : { style: 'xray' });
    return () => { if (savedViewport.current) setViewport(savedViewport.current); };
  }, [modeId, setViewport, hasFineMesh]);

  // the viewport INTERACTIONS outrank every lens/check rail while active —
  // the lens stays visible in the viewport, only the rail switches.
  // The id is what the remembered width is keyed by, so a rail that wants a
  // different default (the study table) gets its own.
  const [railId, railWidth, rightRail]: [string, number, React.ReactNode] =
    measuring ? ['measure', 288, <MeasureRail />]
      : sectionRailOpen ? ['section', 288, <SectionRail />]
        : activeStudy ? ['study', 672, <DirectionsTableRail />]
          : modeId === 'pmi' ? ['pmi', 288, <PmiRail />]
            : directionsActive ? ['directions', 288, <DirectionsRail />]
              : planCheckRail ? ['planCheck', 288, <PlanCheckRail />]
                : activeFieldLens ? ['fieldLens', 288, <FieldLensRail />]
                  : checkActive ? ['settings', 288, <SettingsRail />]
                    : activeLens ? ['lens', 288, <LensRail />]
                      : ['settings', 288, <SettingsRail />];

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <PipelineRail />

        <div className="@container relative min-w-0 flex-1 bg-zinc-100 dark:bg-zinc-950">
          <Viewer />
          {modeId === 'pmi' && <PmiCallouts />}
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

        <RightRail id={railId} defaultWidth={railWidth}>{rightRail}</RightRail>
      </div>
    </div>
  );
}
