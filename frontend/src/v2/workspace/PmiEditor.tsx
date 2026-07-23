import clsx from 'clsx';
import { Check, MousePointerClick, Plus, Save, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { PmiDatum, PmiDimension, PmiTolerance } from '../../api/types';
import { putPmi } from '../../api/client';
import { useStore } from '../../state/store';
import { refreshManifest } from '../../viewer/controller';
import { lensByMode } from '../lenses';
import { ToleranceFrame } from './ControlFrame';
import { usePmiEdit } from './pmiEditStore';
import {
  addEntity, deleteEntity, newDatum, newDimension, newTolerance,
  setDatumFrame, updateEntity,
} from './pmiModel';
import {
  CHARACTERISTIC_BY_TYPE, CHARACTERISTICS, DIMENSION_KINDS,
  DIMENSION_KIND_BY_TYPE, MATERIAL_MODIFIERS, TOLERANCE_MODIFIERS, ZONE_MODIFIERS,
} from './pmiVocab';

const PROCESS = lensByMode('pmi')!.processId;
const hint = 'text-xs/5 text-zinc-500 dark:text-zinc-400';
const section = 'mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400';
const field = 'w-full rounded-md border border-zinc-500/40 bg-transparent px-2 py-1 text-sm text-zinc-800 dark:text-zinc-100';
const smallBtn = 'inline-flex items-center gap-1 rounded-md border border-zinc-500/40 px-2 py-1 text-xs font-medium text-zinc-600 transition hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/5';

const num = (v: string): number | null => (v.trim() === '' ? null : Number(v));

/** The PMI editor: authors tolerance *features* (entities) whose face sets are
 * their geometry. Reuses the viewer face-pick (via pmiPickTool) to populate a
 * feature's geometry, constrains the type/modifier pickers to what survives the
 * AP242 round-trip, and PUTs the whole pmi.json on save. */
export function PmiEditor({ onDone }: { onDone: () => void }) {
  const partId = useStore((s) => s.partId);
  const setParam = useStore((s) => s.setViewerParam);
  const doc = usePmiEdit((s) => s.doc);
  const dirty = usePmiEdit((s) => s.dirty);
  const saving = usePmiEdit((s) => s.saving);
  const error = usePmiEdit((s) => s.error);
  const warnings = usePmiEdit((s) => s.warnings);
  const pick = usePmiEdit((s) => s.pick);
  const apply = usePmiEdit((s) => s.apply);
  const setPick = usePmiEdit((s) => s.setPick);
  const setSaving = usePmiEdit((s) => s.setSaving);
  const setError = usePmiEdit((s) => s.setError);
  const markSaved = usePmiEdit((s) => s.markSaved);

  const [newChar, setNewChar] = useState('Position');
  const [newDim, setNewDim] = useState('Size_Diameter');

  // live-highlight the entity currently being picked into
  useEffect(() => {
    if (!pick) { setParam(PROCESS, 'pmiFaces', []); setParam(PROCESS, 'pmiDatumFaces', []); return; }
    const list = doc[pick.key] as Array<{ id: number }>;
    const entity = list.find((e) => e.id === pick.id) as unknown as
      Record<string, number[]> | undefined;
    const faces = entity?.[pick.field] ?? [];
    setParam(PROCESS, 'pmiFaces', pick.key === 'datums' ? [] : faces);
    setParam(PROCESS, 'pmiDatumFaces', pick.key === 'datums' ? faces : []);
  }, [doc, pick, setParam]);

  async function save() {
    if (!partId) return;
    setSaving(true);
    try {
      const res = await putPmi(partId, doc);
      markSaved(res.warnings);
      await refreshManifest();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const container = 'flex h-full w-80 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900';

  return (
    <div className={container}>
      <div className="flex items-center justify-between">
        <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">Edit PMI / GD&T</h2>
        <div className="flex items-center gap-1.5">
          <button type="button" onClick={save} disabled={!dirty || saving}
            className={clsx(smallBtn, 'border-blue-500/40 text-blue-700 disabled:opacity-40 dark:text-blue-300')}>
            <Save className="size-3.5" /> {saving ? 'Saving…' : 'Save'}
          </button>
          <button type="button" onClick={onDone} className={smallBtn}>Done</button>
        </div>
      </div>
      <p className={hint}>
        Author tolerance <b>features</b>: define the frame, then pick the faces it
        controls. Datums are referenced by letter. Only constructs that survive an
        AP242 export are offered.
      </p>

      {pick && (
        <div className="flex items-center justify-between gap-2 rounded-md bg-blue-500/10 p-2 text-xs/5 text-blue-700 dark:text-blue-300">
          <span className="flex items-center gap-1.5">
            <MousePointerClick className="size-3.5 shrink-0" />
            Click faces for <b>{pick.label}</b>
          </span>
          <button type="button" onClick={() => setPick(null)} className="shrink-0 hover:underline">done</button>
        </div>
      )}
      {error && <p className={clsx(hint, 'rounded-md bg-red-500/10 p-2 text-red-600 dark:text-red-400')}>⚠ {error}</p>}
      {!dirty && warnings.length > 0 && (
        <details className="rounded-md bg-amber-500/5 p-2">
          <summary className={clsx(hint, 'cursor-pointer text-amber-700 dark:text-amber-500')}>
            saved · {warnings.length} round-trip caveat{warnings.length > 1 ? 's' : ''}
          </summary>
          <ul className={clsx('mt-1 list-disc pl-4', hint)}>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </details>
      )}

      {/* datums */}
      <div>
        <div className={section}>Datums</div>
        <div className="flex flex-col gap-1.5">
          {doc.datums.map((d) => (
            <DatumRow key={d.id} datum={d} pickActive={pick?.key === 'datums' && pick.id === d.id}
              onRename={(name) => apply((p) => updateEntity(p, 'datums', d.id, { name }))}
              onPick={() => setPick(pick?.id === d.id && pick.key === 'datums' ? null
                : { key: 'datums', id: d.id, field: 'face_ids', label: `datum ${d.name || '?'} feature` })}
              onDelete={() => { setPick(null); apply((p) => deleteEntity(p, 'datums', d.id)); }} />
          ))}
        </div>
        <button type="button" onClick={() => apply((p) => addEntity(p, 'datums', newDatum(p)))}
          className={clsx(smallBtn, 'mt-1.5')}><Plus className="size-3.5" /> Add datum</button>
      </div>

      {/* control frames */}
      <div>
        <div className={section}>Control frames</div>
        <div className="flex flex-col gap-2.5">
          {doc.tolerances.map((t) => (
            <ToleranceRow key={t.id} tol={t} datums={doc.datums}
              pickActive={pick?.key === 'tolerances' && pick.id === t.id}
              onChange={(patch) => apply((p) => updateEntity(p, 'tolerances', t.id, patch))}
              onDatumFrame={(letters) => apply((p) => setDatumFrame(p, t.id, letters))}
              onPick={() => setPick(pick?.id === t.id && pick.key === 'tolerances' ? null
                : { key: 'tolerances', id: t.id, field: 'face_ids', label: `${t.type} target faces` })}
              onDelete={() => { setPick(null); apply((p) => deleteEntity(p, 'tolerances', t.id)); }} />
          ))}
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <select value={newChar} onChange={(e) => setNewChar(e.target.value)} className={field}>
            {CHARACTERISTICS.map((c) => <option key={c.type} value={c.type}>{c.glyph} {c.label}</option>)}
          </select>
          <button type="button" onClick={() => apply((p) => addEntity(p, 'tolerances', newTolerance(p, newChar)))}
            className={smallBtn}><Plus className="size-3.5" /> Add</button>
        </div>
      </div>

      {/* dimensions */}
      <div>
        <div className={section}>Dimensions</div>
        <div className="flex flex-col gap-2.5">
          {doc.dimensions.map((d) => (
            <DimensionRow key={d.id} dim={d} pickActive={pick?.key === 'dimensions' && pick.id === d.id}
              pickField={pick?.key === 'dimensions' && pick.id === d.id ? pick.field : null}
              onChange={(patch) => apply((p) => updateEntity(p, 'dimensions', d.id, patch))}
              onPick={(f) => setPick(pick?.id === d.id && pick.key === 'dimensions' && pick.field === f ? null
                : { key: 'dimensions', id: d.id, field: f, label: `dimension ${f === 'face_ids' ? 'faces' : '2nd reference'}` })}
              onDelete={() => { setPick(null); apply((p) => deleteEntity(p, 'dimensions', d.id)); }} />
          ))}
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <select value={newDim} onChange={(e) => setNewDim(e.target.value)} className={field}>
            {DIMENSION_KINDS.map((k) => <option key={k.type} value={k.type}>{k.label}</option>)}
          </select>
          <button type="button" onClick={() => apply((p) => addEntity(p, 'dimensions', newDimension(p, newDim)))}
            className={smallBtn}><Plus className="size-3.5" /> Add</button>
        </div>
      </div>
    </div>
  );
}

function PickButton({ active, count, onClick }: { active: boolean; count: number; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className={clsx('inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium transition',
        active ? 'border-blue-500 bg-blue-500/10 text-blue-700 dark:text-blue-300'
          : 'border-zinc-500/40 text-zinc-600 hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/5')}>
      {active ? <Check className="size-3.5" /> : <MousePointerClick className="size-3.5" />}
      {count} face{count === 1 ? '' : 's'}
    </button>
  );
}

const iconBtn = 'rounded p-1 text-zinc-400 transition hover:bg-red-500/10 hover:text-red-600 dark:hover:text-red-400';

function DatumRow({ datum, pickActive, onRename, onPick, onDelete }: {
  datum: PmiDatum; pickActive: boolean;
  onRename: (name: string) => void; onPick: () => void; onDelete: () => void;
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-zinc-500/20 p-2">
      <input value={datum.name ?? ''} onChange={(e) => onRename(e.target.value.toUpperCase().slice(0, 2))}
        className={clsx(field, 'w-10 text-center font-mono font-semibold')} aria-label="datum letter" />
      <PickButton active={pickActive} count={datum.face_ids.length} onClick={onPick} />
      <button type="button" onClick={onDelete} className={clsx(iconBtn, 'ml-auto')} aria-label="delete datum">
        <Trash2 className="size-3.5" />
      </button>
    </div>
  );
}

function Lossy({ reason }: { reason: string }) {
  return <p className="mt-1 text-[10px] text-amber-600 dark:text-amber-500">⚠ {reason}</p>;
}

function ToleranceRow({ tol, datums, pickActive, onChange, onDatumFrame, onPick, onDelete }: {
  tol: PmiTolerance; datums: PmiDatum[]; pickActive: boolean;
  onChange: (patch: Partial<PmiTolerance>) => void;
  onDatumFrame: (letters: string[]) => void;
  onPick: () => void; onDelete: () => void;
}) {
  const spec = tol.type ? CHARACTERISTIC_BY_TYPE[tol.type] : undefined;
  const letters = datums.map((d) => d.name).filter((n): n is string => !!n);
  const activeMod = (m: string) => (tol.modifiers ?? []).includes(m);
  const toggleMod = (m: string) => onChange({
    modifiers: activeMod(m) ? tol.modifiers.filter((x) => x !== m) : [...tol.modifiers, m],
  });
  const toggleLetter = (l: string) => {
    const cur = tol.datum_names;
    onDatumFrame(cur.includes(l) ? cur.filter((x) => x !== l) : [...cur, l]);
  };

  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-zinc-500/20 p-2">
      <div className="flex items-center justify-between">
        <ToleranceFrame t={tol} />
        <button type="button" onClick={onDelete} className={iconBtn} aria-label="delete frame">
          <Trash2 className="size-3.5" />
        </button>
      </div>
      {spec?.lossy && <Lossy reason={spec.lossy} />}

      <div className="flex items-center gap-1.5">
        <input type="number" step="any" value={tol.value ?? ''} placeholder="value"
          onChange={(e) => onChange({ value: num(e.target.value) })}
          className={clsx(field, 'w-20')} aria-label="tolerance value" />
        <label className="flex items-center gap-1 text-xs text-zinc-600 dark:text-zinc-300">
          <input type="checkbox" checked={tol.type_of_value === 'Diameter'}
            onChange={(e) => onChange({ type_of_value: e.target.checked ? 'Diameter' : null })} />
          Ø zone
        </label>
        <select value={tol.material_modifier ?? ''} onChange={(e) => onChange({ material_modifier: e.target.value || null })}
          className={clsx(field, 'w-24')} aria-label="material modifier">
          <option value="">— MMC/LMC</option>
          {MATERIAL_MODIFIERS.map((m) => <option key={m.value} value={m.value}>{m.glyph} {m.value}</option>)}
        </select>
      </div>

      {/* frame-level modifiers (lossy ones flagged) */}
      <div className="flex flex-wrap gap-1">
        {TOLERANCE_MODIFIERS.map((m) => (
          <button key={m.value} type="button" onClick={() => toggleMod(m.value)}
            title={m.label + (m.lossy ? ` — ${m.lossy}` : '')}
            className={clsx('rounded border px-1.5 py-0.5 text-xs',
              activeMod(m.value) ? 'border-blue-500 bg-blue-500/10 text-blue-700 dark:text-blue-300'
                : 'border-zinc-500/40 text-zinc-500',
              m.lossy && 'italic')}>
            {m.glyph} {m.lossy ? '⚠' : ''}
          </button>
        ))}
        <select value={tol.zone_modifier ?? ''} onChange={(e) => onChange({ zone_modifier: e.target.value || null })}
          className={clsx(field, 'w-28')} aria-label="zone modifier">
          {ZONE_MODIFIERS.map((z) => <option key={z.value} value={z.value}>{z.label}</option>)}
        </select>
      </div>
      {tol.zone_modifier === 'Projected' && <Lossy reason="projected zone modifier is not carried by AP242 export" />}

      {/* datum reference frame — references datum letters in order */}
      {spec?.needsDatum && (
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wide text-zinc-400">Datum reference frame</div>
          {letters.length === 0 && <p className={hint}>Add a datum above to reference it.</p>}
          <div className="flex flex-wrap gap-1">
            {letters.map((l) => {
              const pos = tol.datum_names.indexOf(l);
              return (
                <button key={l} type="button" onClick={() => toggleLetter(l)}
                  className={clsx('inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-xs',
                    pos >= 0 ? 'border-teal-500 bg-teal-500/10 text-teal-700 dark:text-teal-300'
                      : 'border-zinc-500/40 text-zinc-500')}>
                  {l}{pos >= 0 && <span className="text-[9px] opacity-70">{pos + 1}</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}

      <PickButton active={pickActive} count={tol.face_ids.length} onClick={onPick} />
    </div>
  );
}

function DimensionRow({ dim, pickActive, pickField, onChange, onPick, onDelete }: {
  dim: PmiDimension; pickActive: boolean; pickField: 'face_ids' | 'secondary_face_ids' | null;
  onChange: (patch: Partial<PmiDimension>) => void;
  onPick: (field: 'face_ids' | 'secondary_face_ids') => void; onDelete: () => void;
}) {
  const kind = dim.type ? DIMENSION_KIND_BY_TYPE[dim.type] : undefined;
  const label = kind?.label ?? dim.type ?? 'dimension';
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-zinc-500/20 p-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">{label}</span>
        <button type="button" onClick={onDelete} className={iconBtn} aria-label="delete dimension">
          <Trash2 className="size-3.5" />
        </button>
      </div>
      <div className="flex items-center gap-1.5">
        <input type="number" step="any" value={dim.value ?? ''} placeholder="value"
          onChange={(e) => onChange({ value: num(e.target.value) ?? 0 })}
          className={clsx(field, 'w-20')} aria-label="dimension value" />
        <input type="number" step="any" value={dim.upper_tolerance ?? ''} placeholder="+tol"
          onChange={(e) => onChange({ upper_tolerance: num(e.target.value) })}
          className={clsx(field, 'w-16')} aria-label="upper tolerance" />
        <input type="number" step="any" value={dim.lower_tolerance ?? ''} placeholder="−tol"
          onChange={(e) => onChange({ lower_tolerance: num(e.target.value) })}
          className={clsx(field, 'w-16')} aria-label="lower tolerance" />
      </div>
      <div className="flex items-center gap-1.5">
        <PickButton active={pickActive && pickField === 'face_ids'} count={dim.face_ids.length}
          onClick={() => onPick('face_ids')} />
        <span className={hint}>to</span>
        <PickButton active={pickActive && pickField === 'secondary_face_ids'} count={(dim.secondary_face_ids ?? []).length}
          onClick={() => onPick('secondary_face_ids')} />
      </div>
    </div>
  );
}
