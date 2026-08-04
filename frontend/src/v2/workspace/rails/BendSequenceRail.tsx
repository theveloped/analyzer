import { Play } from 'lucide-react';
import { useEffect, useReducer } from 'react';
import { Button } from '../../../catalyst/button';
import {
  playhead, seekPlayhead, stepOfPos,
} from '../../../processes/sheetmetal/bendsequence';
import { useStore } from '../../../state/store';
import {
  Rail, RailHeader, RailSection, RailSegmented, RailStats,
} from '../../components/rail';
import { hintCls } from '../../components/styles';

const SPEEDS = [
  { id: '0.5', label: '0.5×' },
  { id: '1', label: '1×' },
  { id: '2', label: '2×' },
  { id: '4', label: '4×' },
] as const;

/**
 * The bend-sequence animation. A rail rather than a declaration because the
 * playhead is a module-global the viewer animates — this panel subscribes to
 * it and scrubs it, which is a transport control, not a setting.
 */
export function BendSequenceRail() {
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const [, bump] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    const listener = () => bump();
    playhead.listeners.add(listener);
    return () => { playhead.listeners.delete(listener); };
  }, []);

  const ready = !!playhead.steps;
  const step = ready ? stepOfPos(playhead.pos, playhead.steps) : 0;

  return (
    <Rail>
      <RailHeader
        icon={Play}
        title="Bend sequence"
        blurb="The planned order, folded step by step."
      />

      {!ready ? (
        <p className={hintCls}>
          No bend plan yet — run sheet_metal/bend_plan to animate one.
        </p>
      ) : (
        <RailSection title={`Step ${step + 1} of ${playhead.steps}`}>
          <input
            type="range"
            min={0}
            max={playhead.steps * 1000}
            value={Math.round(playhead.pos * 1000)}
            aria-label="Bend sequence position"
            className="w-full"
            onChange={(e) => {
              playhead.playing = false;
              seekPlayhead(Number(e.target.value) / 1000);
            }}
          />
          <div className="mt-2 flex items-center gap-2">
            <Button
              onClick={() => {
                // restarting from the end is what a play button means there
                if (!playhead.playing && playhead.pos >= playhead.steps) {
                  seekPlayhead(0);
                }
                playhead.playing = !playhead.playing;
                playhead.notify();
              }}
            >
              {playhead.playing ? '⏸ Pause' : '▶ Play'}
            </Button>
            <div className="min-w-0 flex-1">
              <RailSegmented
                options={SPEEDS as unknown as { id: string; label: string }[]}
                value={String(playhead.speed)}
                ariaLabel="Playback speed"
                onChange={(id) => {
                  playhead.speed = Number(id);
                  playhead.notify();
                }}
              />
            </div>
          </div>
        </RailSection>
      )}

      <RailStats text={stats} error={error} />
    </Rail>
  );
}

/** The flat-pattern DXF link, as a header action on the lens that has one.
 * It used to render for EVERY sheet lens whenever a flat-pattern result
 * existed, so opening `brep_faces` on a sheet part offered a DXF download. */
export function FlatPatternDownload() {
  const manifest = useStore((s) => s.manifest);
  const results = (manifest?.results ?? []).filter(
    (r) => r.process === 'sheet_metal' && r.analysis === 'flat_pattern'
      && !r.stale);
  const result = results[results.length - 1];
  if (!manifest || !result) return null;
  const url = `/api/parts/${manifest.part.id}/results/sheet_metal`
    + `/flat_pattern/${result.hash}/export/dxf`;
  return (
    <Button plain href={url} download title="Download the flat pattern as DXF">
      ⤓ DXF
    </Button>
  );
}
