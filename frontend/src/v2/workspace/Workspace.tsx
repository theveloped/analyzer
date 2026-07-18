import { useStore } from '../../state/store';
import { AnalysisToolbar } from './AnalysisToolbar';
import { Legend } from './Legend';
import { OrientationTriad } from './OrientationTriad';
import { PipelineRail } from './PipelineRail';
import { SettingsRail } from './SettingsRail';
import { TopBar } from './TopBar';
import { Viewer } from './Viewer';

/**
 * The single-part workspace that fills the floating content card (page 4a):
 * a top bar, then three columns — the pipeline of checks on the left, the 3D
 * viewer in the middle with its floating overlays, and the active check's
 * settings on the right (all in-card, i.e. scoped to what's on screen).
 */
export function Workspace() {
  const partId = useStore((s) => s.partId);
  const meshReady = useStore((s) => s.meshReady);
  const stats = useStore((s) => s.stats);

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <PipelineRail />

        <div className="relative min-w-0 flex-1 bg-[#21262c]">
          <Viewer />
          <AnalysisToolbar />
          <Legend />
          <OrientationTriad />
          {(!partId || !meshReady) && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="rounded-md bg-background/90 px-3 py-2 text-sm text-muted-foreground shadow">
                {partId ? (stats || 'Loading part…') : 'No part selected — pick one from the top bar.'}
              </div>
            </div>
          )}
        </div>

        <SettingsRail />
      </div>
    </div>
  );
}
