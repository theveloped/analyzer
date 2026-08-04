import clsx from 'clsx';
import { Download, Frame, Pencil } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { PmiData, PmiDatum, PmiDimension, PmiTolerance } from '../../api/types';
import { Button } from '../../catalyst/button';
import { useStore } from '../../state/store';
import { lensByMode } from '../lenses';
import { DimensionCallout, ToleranceFrame } from './ControlFrame';
import { datumColorCss } from './datumColors';
import { PmiEditor } from './PmiEditor';
import { usePmiEdit } from './pmiEditStore';
import { groupPmi, isDatumReferenced, type PmiGroups, type PmiPattern } from './pmiGroups';
import { buildPmiView, type PmiSelection } from './pmiView';
import {
  Rail, RailAlert, RailCheckbox, RailDisclosure, RailError, RailHeader,
  RailSection,
} from '../components/rail';
import { hintCls } from '../components/styles';

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
/** an isolated entity clicked in the panel (overrides the scope) */
type SelEntity =
  | { kind: 'tolerance' | 'dimension'; id: number }
  | { kind: 'pattern'; key: string };

/** The PMI / GD&T panel (mockup `3a`): scope chips, then the semantic frames
 * grouped into datum-referenced control frames, collapsed patterns, datum-free
 * form tolerances, and a toggleable dimensions layer. A scope or one clicked
 * entry drives the viewer through `buildPmiView` — per-datum face colours,
 * toleranced amber, dimensions blue, plus the floating callouts. */
export function PmiRail() {
  const partId = useStore((s) => s.partId);
  const pmiUrl = useStore((s) => s.manifest?.pmi_url);
  const pmiMeta = useStore((s) => s.manifest?.pmi);
  const manifestVersion = useStore((s) => s.manifestVersion);
  const setParam = useStore((s) => s.setViewerParam);
  const editing = usePmiEdit((s) => s.active);

  const [pmi, setPmi] = useState<PmiData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope>('all');
  const [sel, setSel] = useState<SelEntity | null>(null);
  const [showDims, setShowDims] = useState(false);

  const groups = useMemo(() => groupPmi(pmi), [pmi]);

  const datumWithGeom = useMemo(() => {
    const m = new Map<string, PmiDatum>();
    for (const d of pmi?.datums ?? []) if (d.name) m.set(d.name, d);
    return m;
  }, [pmi]);
  const datumLetters = useMemo(() => Array.from(new Set<string>([
    ...datumWithGeom.keys(),
    ...(pmi?.tolerances ?? []).flatMap((t) => t.datum_names).filter(Boolean),
  ])).sort(), [datumWithGeom, pmi]);

  function selectScope(s: Scope) { setScope(s); setSel(null); }
  function toggleEntity(e: SelEntity) {
    setSel((cur) => {
      const same = cur && cur.kind === e.kind
        && ('id' in e ? 'id' in cur && cur.id === e.id : 'key' in cur && cur.key === e.key);
      return same ? null : e;
    });
  }

  // push the computed view (colour map + callouts + legend) to the painter
  useEffect(() => {
    if (!pmi) return;
    const selection: PmiSelection = sel
      ? (sel.kind === 'pattern' ? { kind: 'pattern', key: sel.key } : { kind: sel.kind, id: sel.id })
      : { kind: 'scope', scope };
    const view = buildPmiView(pmi, groups, selection, showDims);
    setParam(PROCESS, 'pmiColorMap', view.colorMap);
    setParam(PROCESS, 'pmiCallouts', view.callouts);
    setParam(PROCESS, 'pmiLegend', view.legend);
  }, [pmi, groups, scope, sel, showDims, setParam]);

  // fetch pmi.json per part; reset selection + params on every part change
  useEffect(() => {
    setPmi(null);
    setError(null);
    setSel(null);
    setScope('all');
    setShowDims(false);
    setParam(PROCESS, 'pmiColorMap', []);
    setParam(PROCESS, 'pmiCallouts', []);
    setParam(PROCESS, 'pmiLegend', []);
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
    // manifestVersion bumps after a save (refreshManifest) → re-read pmi.json
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partId, pmiUrl, manifestVersion]);

  const header = (
    <RailHeader
      icon={Frame}
      title="PMI / GD&T"
      blurb="Semantic frames exactly as authored. Scope the view, or click one
        frame to isolate it. Each datum has its own colour, shown on the model
        and in the frame."
      actions={(
        <Button plain onClick={() => usePmiEdit.getState().open(pmi)}
          title="Add, edit or remove semantic GD&T on this part">
          <Pencil data-slot="icon" /> Edit
        </Button>
      )}
    />
  );

  const degraded = !!(pmi?.degraded || pmiMeta?.degraded);
  const warnings = (pmi?.warnings ?? pmiMeta?.warnings ?? []);
  const statusBlock = (
    <>
      {degraded && (
        <RailAlert>
          PMI import degraded — OpenCASCADE’s GD&T transfer failed for this
          STEP, so no semantic entities were extracted.
        </RailAlert>
      )}
      {pmiMeta?.export_url && (
        <Button outline href={pmiMeta.export_url} download className="w-full">
          <Download data-slot="icon" /> Export AP242 STEP
        </Button>
      )}
      {warnings.length > 0 && (
        <RailDisclosure
          label={`${warnings.length} round-trip caveat`
            + `${warnings.length > 1 ? 's' : ''} (AP242 export)`}
          gap={2}
        >
          <ul className={clsx('list-disc pl-4', hintCls)}>
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </RailDisclosure>
      )}
    </>
  );

  if (editing) return <PmiEditor onDone={() => usePmiEdit.getState().close()} />;

  if (!pmiUrl) {
    return (
      <Rail>
        {header}
        <p className={hintCls}>
          No PMI in this part yet. Use <b>Edit</b> to author semantic GD&T — even on an
          AP203/AP214 import — then Export AP242.
        </p>
      </Rail>
    );
  }
  if (error) return <Rail>{header}<RailError>{error}</RailError></Rail>;
  if (!pmi) return <Rail>{header}<p className={hintCls}>Loading…</p></Rail>;

  const empty = !pmi.tolerances.length && !pmi.dimensions.length && !pmi.datums.length;

  // whether a card belongs to the active scope (out-of-scope cards dim). A
  // single isolated entity never dims the others.
  const inScope = (t: PmiTolerance): boolean => {
    if (sel) return true;
    switch (scope) {
      case 'all': return true;
      case 'pattern': return groups.patterns.some((p) => p.tolerances.includes(t));
      case 'nodatum': return !isDatumReferenced(t);
      default: return t.datum_names.includes(scope.slice('datum:'.length));
    }
  };
  const patternInScope = (p: PmiPattern): boolean => {
    if (sel) return true;
    if (scope === 'all' || scope === 'pattern') return true;
    if (scope === 'nodatum') return !isDatumReferenced(p.sample);
    return p.sample.datum_names.includes(scope.slice('datum:'.length));
  };
  const isTol = (id: number) => sel?.kind === 'tolerance' && sel.id === id;
  const isPat = (key: string) => sel?.kind === 'pattern' && sel.key === key;
  const isDim = (id: number) => sel?.kind === 'dimension' && sel.id === id;

  return (
    <Rail>
      {header}
      {statusBlock}

      {empty && !degraded && <p className={hintCls}>No semantic PMI entities found in this part.</p>}

      {!empty && (
        <ScopeChips
          scope={scope}
          datumLetters={datumLetters}
          datumWithGeom={datumWithGeom}
          groups={groups}
          onScope={selectScope}
        />
      )}

      {groups.datumReferenced.length > 0 && (
        <RailSection title="Control frames · datum-referenced">
          <div className="flex flex-col gap-1.5">
            {groups.datumReferenced.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => toggleEntity({ kind: 'tolerance', id: t.id })}
                className={rowCls(isTol(t.id), !inScope(t))}
              >
                <ToleranceFrame t={t} />
                {t.face_ids.length === 0 && (
                  <div className="mt-1 text-[10px] text-amber-600 dark:text-amber-500">no bridged faces</div>
                )}
              </button>
            ))}
          </div>
        </RailSection>
      )}

      {groups.patterns.length > 0 && (
        <RailSection title="Patterns">
          <div className="flex flex-col gap-1.5">
            {groups.patterns.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => toggleEntity({ kind: 'pattern', key: p.key })}
                className={clsx(rowCls(isPat(p.key), !patternInScope(p)), 'flex items-center gap-2')}
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
        </RailSection>
      )}

      {groups.noDatum.length > 0 && (
        <RailSection title="No datum reference">
          <div className="flex flex-col gap-1.5">
            {groups.noDatum.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => toggleEntity({ kind: 'tolerance', id: t.id })}
                className={clsx(rowCls(isTol(t.id), !inScope(t)),
                  'border-l-[3px] border-l-slate-400/70 dark:border-l-slate-500/70')}
              >
                <ToleranceFrame t={t} />
                <div className="mt-0.5 text-[10px] text-zinc-400">form · no reference</div>
              </button>
            ))}
          </div>
        </RailSection>
      )}

      {(groups.sizes.length > 0 || pmi.dimensions.length > 0) && (
        <RailSection
          title="Dimensions"
          action={(
            <RailCheckbox
              label="Show on model"
              checked={showDims}
              onChange={setShowDims}
              title="Tint the dimensioned faces on the model (blue)"
            />
          )}
        >
          {groups.sizes.length > 0 && (
            <div className="flex flex-col gap-1">
              {groups.sizes.map((d: PmiDimension) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => toggleEntity({ kind: 'dimension', id: d.id })}
                  className={rowCls(isDim(d.id), false)}
                >
                  <DimensionCallout d={d} />
                </button>
              ))}
            </div>
          )}
        </RailSection>
      )}

      {groups.refDims.length > 0 && (
        <RailDisclosure
          label={`Reference / location dimensions (${groups.refDims.length})`}
          gap={2}
        >
            <div className="flex flex-col gap-1">
              {groups.refDims.map((d: PmiDimension) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => toggleEntity({ kind: 'dimension', id: d.id })}
                  className={rowCls(isDim(d.id), false)}
                  title="Basic/reference location — controlled by a tolerance, no independent value"
                >
                  <span className="text-sm text-zinc-500 dark:text-zinc-400">
                    {refLabel(d)}{d.angular ? ' (angular)' : ''}
                  </span>
                </button>
              ))}
            </div>
        </RailDisclosure>
      )}
    </Rail>
  );
}

const chipCls = (active: boolean, tone: 'neutral' | 'indigo' | 'slate') => clsx(
  'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition',
  active
    ? {
        neutral: 'border-zinc-900 bg-zinc-900 text-white dark:border-white dark:bg-white dark:text-zinc-900',
        indigo: 'border-indigo-500 bg-indigo-500/10 text-indigo-700 dark:text-indigo-300',
        slate: 'border-slate-500 bg-slate-500/10 text-slate-700 dark:text-slate-300',
      }[tone]
    : 'border-zinc-500/40 text-zinc-600 hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/5',
);

/** the scope selector row: All · one chip per datum (in its colour) · Pattern · No datum. */
function ScopeChips({ scope, datumLetters, datumWithGeom, groups, onScope }: {
  scope: Scope;
  datumLetters: string[];
  datumWithGeom: Map<string, PmiDatum>;
  groups: PmiGroups;
  onScope: (s: Scope) => void;
}) {
  return (
    <RailSection title="Scope">
      <div className="flex flex-wrap gap-1.5">
        <button type="button" onClick={() => onScope('all')} className={chipCls(scope === 'all', 'neutral')}>
          All
        </button>
        {datumLetters.map((letter) => {
          const hasGeom = !!datumWithGeom.get(letter)?.face_ids.length;
          const active = scope === `datum:${letter}`;
          return (
            <button
              key={letter}
              type="button"
              onClick={() => onScope(`datum:${letter}`)}
              style={active
                ? { borderColor: datumColorCss(letter), backgroundColor: datumColorCss(letter, 0.12) }
                : { borderColor: datumColorCss(letter, 0.5) }}
              className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition hover:bg-zinc-950/5 dark:hover:bg-white/5"
              title={hasGeom ? `Datum ${letter} and its referencing frames`
                : `Datum ${letter} (referenced only — geometry not exposed by this STEP)`}
            >
              <span className={clsx('font-mono', !hasGeom && 'opacity-60')}
                style={{ color: datumColorCss(letter) }}>{letter}</span>
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
    </RailSection>
  );
}

/** Friendly label for a value-less reference/location dimension. */
function refLabel(d: PmiDimension): string {
  const t = (d.type ?? '').replace(/_None$/, '').replace(/_/g, ' ').trim();
  return t ? t.toLowerCase() : 'reference location';
}
