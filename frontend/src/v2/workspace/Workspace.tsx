import { useStore } from '../../state/store';
import { AnalysisToolbar } from './AnalysisToolbar';
import { DirectionsRail } from './DirectionsRail';
import { DirectionTooltip } from './DirectionTooltip';
import { useDirectionsActive } from './hooks';
import { Legend } from './Legend';
import { PipelineRail } from './PipelineRail';
import { SettingsRail } from './SettingsRail';
import { TopBar } from './TopBar';
import { Viewer } from './Viewer';

/**
 * The single-part workspace that fills the floating content card: a top bar,
 * then three columns — the pipeline of checks, the 3D viewer with its floating
 * overlays, and the active check's settings (all in-card, scoped to what's on
 * screen).
 */
export function Workspace() {
  const partId = useStore((s) => s.partId);
  const meshReady = useStore((s) => s.meshReady);
  const stats = useStore((s) => s.stats);
  const directionsActive = useDirectionsActive();

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <PipelineRail />

        <div className="relative min-w-0 flex-1 bg-zinc-100 dark:bg-zinc-950">
          <Viewer />
          <AnalysisToolbar />
          {directionsActive && <DirectionTooltip />}
          <Legend />
          {(!partId || !meshReady) && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="rounded-lg bg-white/90 px-3 py-2 text-sm/6 text-zinc-600 shadow-xs ring-1 ring-zinc-950/5 dark:bg-zinc-900/90 dark:text-zinc-300 dark:ring-white/10">
                {partId ? (stats || 'Loading part…') : 'No part selected — pick one from the top bar.'}
              </div>
            </div>
          )}
        </div>

        {directionsActive ? <DirectionsRail /> : <SettingsRail />}
      </div>
    </div>
  );
}
