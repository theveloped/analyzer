import clsx from 'clsx';
import { Download, Frame, Pencil } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { PmiData, PmiDatum, PmiDimension, PmiTolerance } from '../../api/types';
import { useStore } from '../../state/store';
import { lensByMode } from '../lenses';
import { DimensionCallout, ToleranceFrame } from './ControlFrame';
import { PmiEditor } from './PmiEditor';
import { usePmiEdit } from './pmiEditStore';
import { groupPmi, isDatumReferenced, type PmiGroups, type PmiPattern } from './pmiGroups';

const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';
const sectionCls = 'mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400';
const PROCESS = lensByMode('pmi')!.processId;

const rowCls = (active: boolean, dimmed: boolean) => clsx(
  'w-full rounded-lg border p-2 text-left transition',
  active
    ? 'border-blue-500/40 bg-blue-500/5'
    : 'border-transparent hover:bg-zinc-950/5 dark:hover:bg-white/5',
  dimmed && 'opacity-40',
);

/** the active rail scope: everything, a single datum's network, the patterns,
 * or the datum-free (form) frames. `datum:A` etc. carry the letter. */
type Scope = 'all' | 'pattern' | 'nodatum' | `datum:${string}`;

/** The PMI / GD&T panel (mockup `3a`): scope chips across the top, then the
 * semantic frames grouped into datum-referenced control frames, collapsed
 * patterns, datum-free form tolerances, and a toggleable dimensions layer.
 * Selecting a scope emphasises that network's faces; clicking one entry
 * isolates it. Face sets flow through viewerParams to the pmiMode painter
 * (toleranced amber, datums teal, dimensions blue). */
export function PmiRail() {
  const partId = useStore((s) => s.partId);
  const pmiUrl = useStore((s) => s.manifest?.pmi_url);
  const pmiMeta = useStore((s) => s.manifest?.pmi);
  const manifestVersion = useStore((s) => s.manifestVersion);
  const setParam = useStore((s) => s.setViewerParam);
  const editing = usePmiEdit((s) => s.active);

  const [pmi, setPmi] = useState<PmiData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [scope, setScope] = useState<Scope>('all');
  const [showRefs, setShowRefs] = useState(false);
  const [showDims, setShowDims] = useState(false);

  const groups = useMemo(() => groupPmi(pmi), [pmi]);

  // datum letters: those with geometry (clickable) unioned with letters only
  // referenced by a control frame (OCCT gave no geometry — greyed/dashed).
  const datumWithGeom = useMemo(() => {
    const m = new Map<string, PmiDatum>();
    for (const d of pmi?.datums ?? []) if (d.name) m.set(d.name, d);
    return m;
  }, [pmi]);
  const datumLetters = useMemo(() => Array.from(new Set<string>([
    ...datumWithGeom.keys(),
    ...(pmi?.tolerances ?? []).flatMap((t) => t.datum_names).filter(Boolean),
  ])).sort(), [datumWithGeom, pmi]);

  // faces a datum reference frame lands on, unioned by referenced letters
  function datumFacesFor(t: PmiTolerance): number[] {
    const names = new Set(t.datum_names);
    const out: number[] = [];
    for (const d of pmi?.datums ?? []) if (d.name && names.has(d.name)) out.push(...d.face_ids);
    return out;
  }

  // the amber/teal face sets a scope emphasises (no per-entity selection)
  function scopeFaces(s: Scope): { anno: number[]; datum: number[] } {
    if (!pmi) return { anno: [], datum: [] };
    if (s === 'all') {
      return {
        anno: pmi.tolerances.flatMap((t) => t.face_ids),
        datum: pmi.datums.flatMap((d) => d.face_ids),
      };
    }
    if (s === 'pattern') {
      return { anno: groups.patterns.flatMap((p) => p.faceIds), datum: [] };
    }
    if (s === 'nodatum') {
      return { anno: groups.noDatum.flatMap((t) => t.face_ids), datum: [] };
    }
    const letter = s.slice('datum:'.length);
    const referencing = pmi.tolerances.filter((t) => t.datum_names.includes(letter));
    return {
      anno: referencing.flatMap((t) => t.face_ids),
      datum: datumWithGeom.get(letter)?.face_ids ?? [],
    };
  }

  function applyScope(s: Scope) {
    setScope(s);
    setSelected(null);
    const { anno, datum } = scopeFaces(s);
    setParam(PROCESS, 'pmiFaces', anno);
    setParam(PROCESS, 'pmiDatumFaces', datum);
  }

  // clicking one entry isolates it; clicking it again drops back to the scope
  function highlight(id: number, faces: number[], datumFaces: number[]) {
    if (selected === id) { applyScope(scope); return; }
    setSelected(id);
    setParam(PROCESS, 'pmiFaces', faces);
    setParam(PROCESS, 'pmiDatumFaces', datumFaces);
  }

  // fetch pmi.json per part; reset selection + params on every part change
  useEffect(() => {
    setPmi(null);
    setError(null);
    setSelected(null);
    setScope('all');
    setShowDims(false);
    setParam(PROCESS, 'pmiFaces', []);
    setParam(PROCESS, 'pmiDatumFaces', []);
    setParam(PROCESS, 'pmiDimFaces', []);
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
        // default scope 'all' — light every annotated face
        setParam(PROCESS, 'pmiFaces', data.tolerances.flatMap((t) => t.face_ids));
        setParam(PROCESS, 'pmiDatumFaces', data.datums.flatMap((d) => d.face_ids));
      })
      .catch(() => live && setError('could not load PMI'));
    return () => { live = false; };
    // manifestVersion bumps after a save (refreshManifest) → re-read pmi.json
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partId, pmiUrl, manifestVersion]);

  // the dimensions layer is an independent toggle (blue faces on the model)
  function toggleDims() {
    const next = !showDims;
    setShowDims(next);
    const faces = next
      ? (pmi?.dimensions ?? []).flatMap((d) => [...d.face_ids, ...(d.secondary_face_ids ?? [])])
      : [];
    setParam(PROCESS, 'pmiDimFaces', faces);
  }

  const header = (
    <div>
      <div className="flex items-center gap-2">
        <Frame className="size-4 text-blue-600 dark:text-blue-400" />
        <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">PMI / GD&T</h2>
        <button
          type="button"
          onClick={() => usePmiEdit.getState().open(pmi)}
          className="ml-auto inline-flex items-center gap-1 rounded-md border border-zinc-500/40 px-2 py-1 text-xs font-medium text-zinc-600 transition hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/5"
          title="Add, edit or remove semantic GD&T on this part"
        >
          <Pencil className="size-3.5" /> Edit
        </button>
      </div>
      <p className={clsx('mt-1', hintCls)}>
        Semantic frames exactly as authored. Scope the view, or click one frame to
        isolate it — <span style={{ color: 'rgb(242,155,41)' }}>toleranced</span>,{' '}
        <span style={{ color: 'rgb(51,173,168)' }}>datums</span>,{' '}
        <span style={{ color: 'rgb(77,133,230)' }}>dimensions</span>.
      </p>
    </div>
  );

  const container = 'flex h-full w-72 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900';

  const degraded = !!(pmi?.degraded || pmiMeta?.degraded);
  const warnings = (pmi?.warnings ?? pmiMeta?.warnings ?? []);
  const statusBlock = (
    <>
      {pmiMeta?.export_url && (
        <a
          href={pmiMeta.export_url}
          download
          className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-blue-500/40 bg-blue-500/5 px-2 py-1.5 text-xs/5 font-medium text-blue-700 transition hover:bg-blue-500/10 dark:text-blue-300"
        >
          <Download className="size-3.5" /> Export AP242 STEP
        </a>
      )}
      {degraded && (
        <p className={clsx(hintCls, 'rounded-md bg-amber-500/10 p-2 text-amber-700 dark:text-amber-400')}>
          ⚠ PMI import degraded — OpenCASCADE’s GD&T transfer failed for this STEP,
          so no semantic entities were extracted.
        </p>
      )}
      {warnings.length > 0 && (
        <details className="rounded-md bg-amber-500/5 p-2">
          <summary className={clsx(hintCls, 'cursor-pointer text-amber-700 dark:text-amber-500')}>
            {warnings.length} round-trip caveat{warnings.length > 1 ? 's' : ''} (AP242 export)
          </summary>
          <ul className={clsx('mt-1 list-disc pl-4', hintCls)}>
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </details>
      )}
    </>
  );

  if (editing) return <PmiEditor onDone={() => usePmiEdit.getState().close()} />;

  if (!pmiUrl) {
    return (
      <div className={container}>
        {header}
        <p className={hintCls}>
          No PMI in this part yet. Use <b>Edit</b> to author semantic GD&T — even on an
          AP203/AP214 import — then Export AP242.
        </p>
      </div>
    );
  }
  if (error) return <div className={container}>{header}<p className={hintCls}>⚠ {error}</p></div>;
  if (!pmi) return <div className={container}>{header}<p className={hintCls}>Loading…</p></div>;

  const empty = !pmi.tolerances.length && !pmi.dimensions.length && !pmi.datums.length;

  // whether a card belongs to the active scope (out-of-scope cards dim)
  const inScope = (t: PmiTolerance): boolean => {
    if (selected != null) return true;
    switch (scope) {
      case 'all': return true;
      case 'pattern': return groups.patterns.some((p) => p.tolerances.includes(t));
      case 'nodatum': return !isDatumReferenced(t);
      default: return t.datum_names.includes(scope.slice('datum:'.length));
    }
  };
  const patternInScope = (p: PmiPattern): boolean => {
    if (selected != null) return true;
    if (scope === 'all' || scope === 'pattern') return true;
    if (scope === 'nodatum') return !isDatumReferenced(p.sample);
    return p.sample.datum_names.includes(scope.slice('datum:'.length));
  };

  return (
    <div className={container}>
      {header}
      {statusBlock}

      {empty && !degraded && <p className={hintCls}>No semantic PMI entities found in this part.</p>}

      {!empty && (
        <ScopeChips
          scope={scope}
          datumLetters={datumLetters}
          datumWithGeom={datumWithGeom}
          groups={groups}
          onScope={applyScope}
        />
      )}

      {groups.datumReferenced.length > 0 && (
        <div>
          <div className={sectionCls}>Control frames · datum-referenced</div>
          <div className="flex flex-col gap-1.5">
            {groups.datumReferenced.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => highlight(t.id, t.face_ids, datumFacesFor(t))}
                className={rowCls(selected === t.id, !inScope(t))}
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

      {groups.patterns.length > 0 && (
        <div>
          <div className={sectionCls}>Patterns</div>
          <div className="flex flex-col gap-1.5">
            {groups.patterns.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => highlight(-1 - p.sample.id, p.faceIds, datumFacesFor(p.sample))}
                className={clsx(rowCls(selected === -1 - p.sample.id, !patternInScope(p)),
                  'flex items-center gap-2')}
              >
                <span className="shrink-0 font-mono text-xs font-bold text-indigo-600 dark:text-indigo-400">
                  {p.tolerances.length}×
                </span>
                <ToleranceFrame t={p.sample} />
                <span className="ml-auto shrink-0 text-[10px] text-zinc-400">
                  {p.faceIds.length} face{p.faceIds.length === 1 ? '' : 's'}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {groups.noDatum.length > 0 && (
        <div>
          <div className={sectionCls}>No datum reference</div>
          <div className="flex flex-col gap-1.5">
            {groups.noDatum.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => highlight(t.id, t.face_ids, [])}
                className={clsx(rowCls(selected === t.id, !inScope(t)),
                  'border-l-[3px] border-l-slate-400/70 dark:border-l-slate-500/70')}
              >
                <ToleranceFrame t={t} />
                <div className="mt-0.5 text-[10px] text-zinc-400">form · no reference</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {(groups.sizes.length > 0 || pmi.dimensions.length > 0) && (
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <div className={sectionCls.replace('mb-1.5 ', '')}>Dimensions</div>
            <button
              type="button"
              onClick={toggleDims}
              className={clsx(
                'flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium transition',
                showDims
                  ? 'border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-300'
                  : 'border-zinc-500/40 text-zinc-500 hover:bg-zinc-950/5 dark:text-zinc-400 dark:hover:bg-white/5',
              )}
              title="Tint the dimensioned faces on the model (blue)"
            >
              <span className={clsx('inline-block size-3 rounded-sm border',
                showDims ? 'border-blue-500 bg-blue-500' : 'border-zinc-400')} />
              Show on model
            </button>
          </div>
          {groups.sizes.length > 0 && (
            <div className="flex flex-col gap-1">
              {groups.sizes.map((d: PmiDimension) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => highlight(d.id,
                    [...d.face_ids, ...(d.secondary_face_ids ?? [])], [])}
                  className={rowCls(selected === d.id, false)}
                >
                  <DimensionCallout d={d} />
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {groups.refDims.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowRefs((v) => !v)}
            className="flex w-full items-center justify-between text-xs/5 font-medium text-zinc-500 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white"
          >
            <span>Reference / location dimensions ({groups.refDims.length})</span>
            <span>{showRefs ? '▾' : '▸'}</span>
          </button>
          {showRefs && (
            <div className="mt-1.5 flex flex-col gap-1">
              {groups.refDims.map((d: PmiDimension) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => highlight(d.id,
                    [...d.face_ids, ...(d.secondary_face_ids ?? [])], [])}
                  className={rowCls(selected === d.id, false)}
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

const chipCls = (active: boolean, tone: 'neutral' | 'teal' | 'indigo' | 'slate') => clsx(
  'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition',
  active
    ? {
        neutral: 'border-zinc-900 bg-zinc-900 text-white dark:border-white dark:bg-white dark:text-zinc-900',
        teal: 'border-teal-500 bg-teal-500/10 text-teal-700 dark:text-teal-300',
        indigo: 'border-indigo-500 bg-indigo-500/10 text-indigo-700 dark:text-indigo-300',
        slate: 'border-slate-500 bg-slate-500/10 text-slate-700 dark:text-slate-300',
      }[tone]
    : 'border-zinc-500/40 text-zinc-600 hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/5',
);

/** the scope selector row: All · one chip per datum · Pattern · No datum. */
function ScopeChips({ scope, datumLetters, datumWithGeom, groups, onScope }: {
  scope: Scope;
  datumLetters: string[];
  datumWithGeom: Map<string, PmiDatum>;
  groups: PmiGroups;
  onScope: (s: Scope) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className={sectionCls}>Scope</div>
      <div className="flex flex-wrap gap-1.5">
        <button type="button" onClick={() => onScope('all')} className={chipCls(scope === 'all', 'neutral')}>
          All
        </button>
        {datumLetters.map((letter) => {
          const hasGeom = !!datumWithGeom.get(letter)?.face_ids.length;
          return (
            <button
              key={letter}
              type="button"
              onClick={() => onScope(`datum:${letter}`)}
              className={chipCls(scope === `datum:${letter}`, 'teal')}
              title={hasGeom ? `Datum ${letter} and its referencing frames`
                : `Datum ${letter} (referenced only — geometry not exposed by this STEP)`}
            >
              <span className={clsx('font-mono', !hasGeom && 'opacity-60')}>{letter}</span>
            </button>
          );
        })}
        {groups.patterns.length > 0 && (
          <button type="button" onClick={() => onScope('pattern')} className={chipCls(scope === 'pattern', 'indigo')}>
            ⌖ Pattern
          </button>
        )}
        {groups.noDatum.length > 0 && (
          <button type="button" onClick={() => onScope('nodatum')} className={chipCls(scope === 'nodatum', 'slate')}>
            ∅ No datum
          </button>
        )}
      </div>
    </div>
  );
}

/** Friendly label for a value-less reference/location dimension. */
function refLabel(d: PmiDimension): string {
  const t = (d.type ?? '').replace(/_None$/, '').replace(/_/g, ' ').trim();
  return t ? t.toLowerCase() : 'reference location';
}
