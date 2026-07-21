import clsx from 'clsx';
import { Frame } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { PmiData, PmiDatum, PmiDimension, PmiTolerance } from '../../api/types';
import { useStore } from '../../state/store';
import { lensByMode } from '../lenses';
import { DimensionCallout, ToleranceFrame } from './ControlFrame';

const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';
const sectionCls = 'mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400';
const PROCESS = lensByMode('pmi')!.processId;

const rowCls = (active: boolean) => clsx(
  'w-full rounded-lg border p-2 text-left transition',
  active
    ? 'border-blue-500/40 bg-blue-500/5'
    : 'border-transparent hover:bg-zinc-950/5 dark:hover:bg-white/5',
);

/** The PMI / GD&T panel: lists the semantic dimensions, tolerances and datums
 * from pmi.json as control-frame chips. Clicking an entry pushes its face set
 * (and, for a tolerance, its referenced datum faces) into viewerParams, which
 * the pmiMode painter reads — toleranced faces amber, datum faces teal. */
export function PmiRail() {
  const partId = useStore((s) => s.partId);
  const pmiUrl = useStore((s) => s.manifest?.pmi_url);
  const setParam = useStore((s) => s.setViewerParam);

  const [pmi, setPmi] = useState<PmiData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [showRefs, setShowRefs] = useState(false);

  // fetch pmi.json per part; reset the highlight params on every part change
  useEffect(() => {
    setPmi(null);
    setError(null);
    setSelected(null);
    setParam(PROCESS, 'pmiFaces', []);
    setParam(PROCESS, 'pmiDatumFaces', []);
    setParam(PROCESS, 'pmiCounts', undefined);
    if (!pmiUrl) return;
    let live = true;
    fetch(pmiUrl)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: PmiData) => {
        if (!live) return;
        setPmi(data);
        setParam(PROCESS, 'pmiCounts', {
          tolerances: data.tolerances.length,
          dimensions: data.dimensions.length,
          datums: data.datums.length,
        });
      })
      .catch(() => live && setError('could not load PMI'));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partId, pmiUrl]);

  function highlight(id: number, faces: number[], datumFaces: number[]) {
    if (selected === id) {
      setSelected(null);
      setParam(PROCESS, 'pmiFaces', []);
      setParam(PROCESS, 'pmiDatumFaces', []);
      return;
    }
    setSelected(id);
    setParam(PROCESS, 'pmiFaces', faces);
    setParam(PROCESS, 'pmiDatumFaces', datumFaces);
  }

  function datumFacesFor(t: PmiTolerance): number[] {
    if (!pmi) return [];
    const names = new Set(t.datum_names);
    const out: number[] = [];
    for (const d of pmi.datums) if (d.name && names.has(d.name)) out.push(...d.face_ids);
    return out;
  }

  const header = (
    <div>
      <div className="flex items-center gap-2">
        <Frame className="size-4 text-blue-600 dark:text-blue-400" />
        <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">PMI / GD&T</h2>
      </div>
      <p className={clsx('mt-1', hintCls)}>
        Semantic tolerances, dimensions and datums from the STEP. Click an entry to
        highlight its faces — <span style={{ color: 'rgb(242,155,41)' }}>toleranced</span> and{' '}
        <span style={{ color: 'rgb(51,173,168)' }}>referenced datums</span>.
      </p>
    </div>
  );

  const container = 'flex h-full w-72 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900';

  if (!pmiUrl) {
    return (
      <div className={container}>
        {header}
        <p className={hintCls}>No PMI in this part — import a STEP (AP242) that carries semantic GD&T.</p>
      </div>
    );
  }
  if (error) return <div className={container}>{header}<p className={hintCls}>⚠ {error}</p></div>;
  if (!pmi) return <div className={container}>{header}<p className={hintCls}>Loading…</p></div>;

  const empty = !pmi.tolerances.length && !pmi.dimensions.length && !pmi.datums.length;

  // datum letters: those with geometry (clickable) unioned with letters that
  // are only referenced by a control frame (OCCT gave no geometry — greyed).
  const datumWithGeom = new Map<string, PmiDatum>();
  for (const d of pmi.datums) if (d.name) datumWithGeom.set(d.name, d);
  const datumLetters = Array.from(new Set<string>([
    ...datumWithGeom.keys(),
    ...pmi.tolerances.flatMap((t) => t.datum_names).filter(Boolean),
  ])).sort();

  // split dimensions: toleranced sizes vs value-less reference/location dims
  const hasMagnitude = (d: PmiDimension) =>
    !!d.value || d.upper_tolerance != null || d.lower_tolerance != null;
  const sizes = pmi.dimensions.filter(hasMagnitude);
  const refDims = pmi.dimensions.filter((d) => !hasMagnitude(d));

  return (
    <div className={container}>
      {header}

      {empty && <p className={hintCls}>No semantic PMI entities found in this part.</p>}

      {datumLetters.length > 0 && (
        <div>
          <div className={sectionCls}>Datums</div>
          <div className="flex flex-wrap gap-1.5">
            {datumLetters.map((letter) => {
              const d = datumWithGeom.get(letter);
              const clickable = !!d && d.face_ids.length > 0;
              return (
                <button
                  key={letter}
                  type="button"
                  disabled={!clickable}
                  onClick={() => d && highlight(d.id, [], d.face_ids)}
                  title={clickable ? 'Highlight datum faces'
                    : 'datum geometry not readable — OCCT skips set-representation datum features'}
                  className={clsx(
                    'inline-flex size-7 items-center justify-center rounded border text-sm font-semibold transition',
                    clickable && d && selected === d.id
                      ? 'border-teal-500 bg-teal-500/10 text-teal-700 dark:text-teal-300'
                      : clickable
                        ? 'border-zinc-500/50 text-zinc-700 hover:bg-zinc-950/5 dark:text-zinc-200 dark:hover:bg-white/5'
                        : 'border-dashed border-zinc-300 text-zinc-400 dark:border-zinc-700 dark:text-zinc-600',
                  )}
                >
                  {letter}
                </button>
              );
            })}
          </div>
          {datumLetters.some((l) => !datumWithGeom.get(l)?.face_ids.length) && (
            <p className={clsx('mt-1', hintCls)}>Dashed = referenced datum whose geometry this STEP doesn’t expose.</p>
          )}
        </div>
      )}

      {pmi.tolerances.length > 0 && (
        <div>
          <div className={sectionCls}>Tolerances</div>
          <div className="flex flex-col gap-1.5">
            {pmi.tolerances.map((t: PmiTolerance) => (
              <button
                key={t.id}
                type="button"
                onClick={() => highlight(t.id, t.face_ids, datumFacesFor(t))}
                className={rowCls(selected === t.id)}
              >
                <ToleranceFrame t={t} />
                {t.face_ids.length === 0 && (
                  <div className="mt-1 text-[10px] text-amber-600 dark:text-amber-500">no bridged faces</div>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {sizes.length > 0 && (
        <div>
          <div className={sectionCls}>Dimensions</div>
          <div className="flex flex-col gap-1">
            {sizes.map((d: PmiDimension) => (
              <button
                key={d.id}
                type="button"
                onClick={() => highlight(d.id,
                  [...d.face_ids, ...(d.secondary_face_ids ?? [])], [])}
                className={rowCls(selected === d.id)}
              >
                <DimensionCallout d={d} />
              </button>
            ))}
          </div>
        </div>
      )}

      {refDims.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowRefs((v) => !v)}
            className="flex w-full items-center justify-between text-xs/5 font-medium text-zinc-500 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white"
          >
            <span>Reference / location dimensions ({refDims.length})</span>
            <span>{showRefs ? '▾' : '▸'}</span>
          </button>
          {showRefs && (
            <div className="mt-1.5 flex flex-col gap-1">
              {refDims.map((d: PmiDimension) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => highlight(d.id,
                    [...d.face_ids, ...(d.secondary_face_ids ?? [])], [])}
                  className={rowCls(selected === d.id)}
                  title="Basic/reference location — controlled by a tolerance, no independent value"
                >
                  <span className="text-sm text-zinc-500 dark:text-zinc-400">
                    {refLabel(d)}{d.angular ? ' (angular)' : ''}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Friendly label for a value-less reference/location dimension. */
function refLabel(d: PmiDimension): string {
  const t = (d.type ?? '').replace(/_None$/, '').replace(/_/g, ' ').trim();
  return t ? t.toLowerCase() : 'reference location';
}
