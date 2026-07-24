import { useEffect, useRef } from 'react';
import type { PmiDatum, PmiDimension, PmiTolerance } from '../../api/types';
import { useStore } from '../../state/store';
import { isOccluded, pmiFaceCentroid, worldToScreen } from '../../viewer/controller';
import { lensByMode } from '../lenses';
import { DimensionCallout, ToleranceFrame } from './ControlFrame';

const PROCESS = lensByMode('pmi')!.processId;
const BOX_DX = 48;   // callout box offset from the projected anchor
const BOX_DY = -56;

/** What the rail pushes when one PMI entry is isolated: the entity to render as
 * a floating frame and the faces whose centroid the leader anchors to. */
export interface PmiCalloutData {
  kind: 'tolerance' | 'dimension' | 'datum';
  entity: PmiTolerance | PmiDimension | PmiDatum;
  faces: number[];
}

/**
 * The floating feature-control-frame callout (mockup `3a`): when the user
 * isolates one frame in the rail, its FCF floats in the viewport with a leader
 * line to the toleranced geometry, tracking the camera. First cut: the single
 * selected/isolated frame only — anchored to its face-set centroid, re-projected
 * every frame via the scene's worldToScreen. Positions are written straight to
 * the DOM (refs), never React state, so orbit stays smooth.
 */
export function PmiCallout() {
  const modeId = useStore((s) => s.modeId);
  const partId = useStore((s) => s.partId);
  const manifestVersion = useStore((s) => s.manifestVersion);
  const callout = useStore((s) => s.viewerParams[PROCESS]?.pmiCallout as PmiCalloutData | undefined);

  const boxRef = useRef<HTMLDivElement>(null);
  const lineRef = useRef<SVGLineElement>(null);
  const dotRef = useRef<SVGCircleElement>(null);
  const anchor = useRef<[number, number, number] | null>(null);

  // resolve the world anchor (async: reads the current mesh + BREP ids)
  useEffect(() => {
    let live = true;
    anchor.current = null;
    if (callout?.faces?.length) {
      void pmiFaceCentroid(callout.faces).then((p) => { if (live) anchor.current = p; });
    }
    return () => { live = false; };
  }, [callout, partId, manifestVersion]);

  // project + position every frame while mounted; occlusion is throttled (a
  // raycast is far dearer than a projection) so it doesn't run every frame
  useEffect(() => {
    let raf = 0;
    let frame = 0;
    let hidden = false;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const box = boxRef.current;
      const line = lineRef.current;
      const dot = dotRef.current;
      if (!box || !line || !dot) return;
      const a = anchor.current;
      if (a && (frame++ % 8 === 0)) hidden = isOccluded(a); // ~7Hz occlusion test
      const s = a && !hidden ? worldToScreen(a) : null;
      if (!s) { box.style.display = 'none'; line.style.display = 'none'; dot.style.display = 'none'; return; }
      box.style.display = '';
      line.style.display = '';
      dot.style.display = '';
      const bx = s[0] + BOX_DX;
      const by = s[1] + BOX_DY;
      box.style.transform = `translate(${bx}px, ${by}px)`;
      line.setAttribute('x1', String(s[0])); line.setAttribute('y1', String(s[1]));
      line.setAttribute('x2', String(bx)); line.setAttribute('y2', String(by));
      dot.setAttribute('cx', String(s[0])); dot.setAttribute('cy', String(s[1]));
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  if (modeId !== 'pmi' || !callout) return null;

  return (
    <>
      <svg className="pointer-events-none absolute inset-0 z-10 h-full w-full overflow-visible">
        <line ref={lineRef} stroke="#4f5bd5" strokeWidth={1.4} style={{ display: 'none' }} />
        <circle ref={dotRef} r={4} fill="#4f5bd5" style={{ display: 'none' }} />
      </svg>
      <div ref={boxRef}
        className="pointer-events-none absolute left-0 top-0 z-10 origin-top-left"
        style={{ display: 'none' }}>
        <div className="rounded-md bg-white/95 px-1 py-0.5 shadow-lg ring-1 ring-zinc-950/10 dark:bg-zinc-800/95 dark:ring-white/15">
          <CalloutFrame data={callout} />
        </div>
      </div>
    </>
  );
}

function CalloutFrame({ data }: { data: PmiCalloutData }) {
  if (data.kind === 'tolerance') return <ToleranceFrame t={data.entity as PmiTolerance} />;
  if (data.kind === 'dimension') return <DimensionCallout d={data.entity as PmiDimension} />;
  const d = data.entity as PmiDatum;
  return (
    <span className="inline-flex size-7 items-center justify-center rounded border-2 border-teal-600 font-mono text-sm font-bold text-teal-700 dark:text-teal-300">
      {d.name}
    </span>
  );
}
