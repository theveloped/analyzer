import { useEffect, useRef } from 'react';
import type { PmiDimension, PmiTolerance } from '../../api/types';
import { useStore } from '../../state/store';
import { isOccluded, pmiFaceCentroid, worldToScreen } from '../../viewer/controller';
import { lensByMode } from '../lenses';
import { DimensionCallout, ToleranceFrame } from './ControlFrame';
import type { PmiCalloutData } from './pmiView';

const PROCESS = lensByMode('pmi')!.processId;
const BOX_DX = 48;   // callout box offset from the anchors' screen centroid
const BOX_DY = -56;

/**
 * The floating PMI callouts (mockup `3a`): every frame the rail selection floats
 * gets its FCF in the viewport with a leader to each of its anchor groups —
 * so a dimension across disjoint faces draws a leader to each while its value
 * floats once, and a pattern draws a leader to every instance. Anchored to
 * face-set surface points, re-projected each frame, occluded by the model
 * (dimmed like the manu.ninja annotations). Positions are written to the DOM.
 */
export function PmiCallouts() {
  const modeId = useStore((s) => s.modeId);
  const callouts = useStore((s) => s.viewerParams[PROCESS]?.pmiCallouts as PmiCalloutData[] | undefined);
  if (modeId !== 'pmi' || !callouts?.length) return null;
  // key by content so anchors re-resolve when the selection changes
  return (
    <>
      {callouts.map((c, i) => (
        <OneCallout key={`${c.kind}:${c.entity.id}:${i}`} data={c} />
      ))}
    </>
  );
}

function leaderColor(data: PmiCalloutData): string {
  return data.kind === 'dimension' ? '#4d85e6' : '#4f5bd5';
}

function OneCallout({ data }: { data: PmiCalloutData }) {
  const partId = useStore((s) => s.partId);
  const manifestVersion = useStore((s) => s.manifestVersion);
  const boxRef = useRef<HTMLDivElement>(null);
  const lineRefs = useRef<(SVGLineElement | null)[]>([]);
  const dotRefs = useRef<(SVGCircleElement | null)[]>([]);
  const anchors = useRef<([number, number, number] | null)[]>([]);
  const n = data.anchorGroups.length;

  // resolve one world anchor per group (async — reads the mesh + BREP ids)
  useEffect(() => {
    let live = true;
    anchors.current = [];
    Promise.all(data.anchorGroups.map((g) => pmiFaceCentroid(g)))
      .then((pts) => { if (live) anchors.current = pts; });
    return () => { live = false; };
  }, [data, partId, manifestVersion]);

  // project + position every frame; recompute occlusion whenever the view moves
  // (the BVH-accelerated raycast is cheap enough to run on every camera change)
  useEffect(() => {
    let raf = 0;
    let lastKey = ''; let hidden = false;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const box = boxRef.current;
      if (!box) return;
      const a = anchors.current;
      const pts = a.map((p) => (p ? worldToScreen(p) : null));
      const valid = pts.filter((p): p is [number, number] => !!p);
      if (!valid.length) {
        box.style.display = 'none';
        for (const el of lineRefs.current) if (el) el.style.display = 'none';
        for (const el of dotRefs.current) if (el) el.style.display = 'none';
        return;
      }
      const key = valid.map((p) => `${Math.round(p[0])},${Math.round(p[1])}`).join(';');
      if (key !== lastKey) { // camera (or anchors) moved → refresh occlusion
        lastKey = key;
        hidden = a.every((p, i) => !pts[i] || isOccluded(p!)); // dim if all behind
      }
      const op = hidden ? '0.3' : '1';
      box.style.display = '';
      box.style.opacity = op;
      const cx = valid.reduce((s, p) => s + p[0], 0) / valid.length;
      const cy = valid.reduce((s, p) => s + p[1], 0) / valid.length;
      const bx = cx + BOX_DX;
      const by = cy + BOX_DY;
      box.style.transform = `translate(${bx}px, ${by}px)`;
      a.forEach((_, i) => {
        const ln = lineRefs.current[i];
        const dt = dotRefs.current[i];
        const p = pts[i];
        if (!ln || !dt) return;
        if (!p) { ln.style.display = 'none'; dt.style.display = 'none'; return; }
        ln.style.display = ''; dt.style.display = '';
        ln.style.opacity = op; dt.style.opacity = op;
        ln.setAttribute('x1', String(bx)); ln.setAttribute('y1', String(by));
        ln.setAttribute('x2', String(p[0])); ln.setAttribute('y2', String(p[1]));
        dt.setAttribute('cx', String(p[0])); dt.setAttribute('cy', String(p[1]));
      });
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [data]);

  const color = leaderColor(data);
  return (
    <>
      <svg className="pointer-events-none absolute inset-0 z-10 h-full w-full overflow-visible">
        {Array.from({ length: n }, (_, i) => (
          <g key={i}>
            <line ref={(el) => { lineRefs.current[i] = el; }} stroke={color} strokeWidth={1.4} style={{ display: 'none' }} />
            <circle ref={(el) => { dotRefs.current[i] = el; }} r={4} fill={color} style={{ display: 'none' }} />
          </g>
        ))}
      </svg>
      <div ref={boxRef}
        className="pointer-events-none absolute left-0 top-0 z-10 origin-top-left"
        style={{ display: 'none' }}>
        <div className="rounded-md bg-white/95 px-1 py-0.5 shadow-lg ring-1 ring-zinc-950/10 dark:bg-zinc-800/95 dark:ring-white/15">
          {data.kind === 'tolerance'
            ? <ToleranceFrame t={data.entity as PmiTolerance} />
            : <DimensionCallout d={data.entity as PmiDimension} />}
        </div>
      </div>
    </>
  );
}
