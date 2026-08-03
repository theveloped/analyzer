import clsx from 'clsx';
import { BookmarkPlus, RotateCw, Settings2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '../../catalyst/button';
import { Input } from '../../catalyst/input';
import { Select } from '../../catalyst/select';
import { useStore } from '../../state/store';
import {
  BOUND_UNITS, fieldDescriptor, fieldLensCompute, fieldStats,
  latestResult, resolveBound, type BandBound, type BoundUnit,
  type FieldLensDef, type FieldStats,
} from '../fieldLenses';
import {
  Rail, RailDisclosure, RailDivider, RailHeader, RailSection, RailStats,
} from '../components/rail';
import { useV2 } from '../store';
import { ComputeInput } from './computeFields';
import {
  saveLensCheck, useActiveFieldLens, useActiveLens, useRouteSection,
} from './hooks';
import { useBusy } from './run';
import { runAnalysisJob } from '../../viewer/jobs';
import { hintCls } from '../components/styles';

/** The lens's compute payload: v2 store overrides on top of the defaults. */
function currentCompute(def: FieldLensDef): Record<string, unknown> {
  const stored = useV2.getState().compute[def.modeId] ?? {};
  return { ...fieldLensCompute(def), ...stored };
}

/** Whether a re-run would produce anything new: some compute knob differs
 * from the stored result's params (numbers compared loosely — JSON strips
 * float-ness) or the result is stale. */
function computeChanged(
  def: FieldLensDef, compute: Record<string, unknown>,
  resultParams: Record<string, unknown> | undefined, stale: boolean,
): boolean {
  if (stale) return true;
  if (!resultParams) return true;
  return def.computeFields.some((f) => {
    const a = compute[f.key];
    const b = resultParams[f.key];
    if (a == null || b == null) return (a ?? null) !== (b ?? null);
    return Number(a) !== Number(b) && String(a) !== String(b);
  });
}

function BoundRow({ label, bound, onChange, fieldUnit, resolved }: {
  label: string; bound: BandBound; onChange: (b: BandBound) => void;
  fieldUnit: string; resolved: number | null;
}) {
  return (
    <div className="mt-1.5 grid grid-cols-[2rem_4.5rem_1fr_2.75rem] items-center gap-1.5">
      <span className={hintCls}>{label}</span>
      <Input type="number" step="any" placeholder="—"
        value={bound.value}
        onChange={(e) => onChange({ ...bound, value: e.target.value })}
        aria-label={`band ${label}`} />
      <Select value={bound.unit}
        onChange={(e) => onChange({ ...bound, unit: e.target.value as BoundUnit })}
        aria-label={`band ${label} unit`}>
        {BOUND_UNITS(fieldUnit).map((u) => (
          <option key={u.id} value={u.id}>{u.label}</option>
        ))}
      </Select>
      <span className="text-right text-[11px]/4 tabular-nums text-zinc-400"
        title={`resolved bound (${fieldUnit})`}>
        {resolved == null ? '—' : `${+resolved.toFixed(2)}`}
      </span>
    </div>
  );
}

const EMPTY_BOUND: BandBound = { value: '', unit: 'abs' };
const EMPTY_BAND = { lo: EMPTY_BOUND, hi: EMPTY_BOUND };

function BandSection({ def, stats }: { def: FieldLensDef; stats: FieldStats }) {
  const setParam = useStore((s) => s.setViewerParam);
  const partId = useStore((s) => s.partId);
  const section = useRouteSection();
  const activeCheckId = useV2((s) => s.activeCheckId);
  const bands = useV2((s) => s.bands);
  const setBand = useV2((s) => s.setBand);
  const setActiveCheck = useV2((s) => s.setActiveCheck);
  const [saving, setSaving] = useState(false);

  // scope: navigating via a CHECK edits that check's band (button: Update);
  // navigating via the LENS edits a per-lens scratch band (button: Add) —
  // separate keys in the store, so bands never bleed across lenses/checks
  const selected = section?.route.checks.find(
    (c) => c.id === activeCheckId
      && c.analysis === `${def.process}/${def.analysis}`) ?? null;
  const bandKey = selected?.id ?? def.lensKey;
  const savedDef = (selected?.policy?.band_def ?? null) as
    { lo: BandBound; hi: BandBound } | null;
  const band = bands[bandKey] ?? savedDef ?? EMPTY_BAND;
  const { lo, hi } = band;
  const setLo = (next: BandBound) => setBand(bandKey, { ...band, lo: next });
  const setHi = (next: BandBound) => setBand(bandKey, { ...band, hi: next });

  const rLo = resolveBound(lo, stats);
  const rHi = resolveBound(hi, stats);
  const active = rLo != null || rHi != null;

  // the resolved bounds drive the highlight params: the heatmap itself
  // stays untouched, in-band faces paint the magenta selection color
  useEffect(() => {
    const fmt = (v: number | null) => (v == null ? '' : String(+v.toFixed(4)));
    setParam(def.process, def.bandLoParam, fmt(rLo));
    setParam(def.process, def.bandHiParam, fmt(rHi));
  }, [def, rLo, rHi, setParam]);

  const bandText = !active ? 'off'
    : rLo != null && rHi != null
      ? `${rLo.toFixed(2)} – ${rHi.toFixed(2)} ${def.unit}`
      : rLo != null ? `≥ ${rLo.toFixed(2)} ${def.unit}`
      : `≤ ${rHi!.toFixed(2)} ${def.unit}`;

  const save = () => {
    setSaving(true);
    void saveLensCheck(def, {
      band: [rLo, rHi],
      band_def: { lo, hi },
      threshold: def.flagDirection === 'below' ? (rLo ?? rHi) : (rHi ?? rLo),
      unit: def.unit,
    }, currentCompute(def), selected?.id ?? null)
      .then((id) => {
        if (id && !selected) {
          // the fresh check takes over the scratch band and becomes selected
          setBand(id, band);
          setBand(def.lensKey, EMPTY_BAND);
          setActiveCheck(id);
        }
      })
      .finally(() => setSaving(false));
  };

  return (
    <RailSection
      title={`Highlight band${selected ? ` — “${selected.id}”` : ''}`}
      action={active ? (
        <button type="button"
          onClick={() => setBand(bandKey, EMPTY_BAND)}
          className="text-[11px]/4 text-zinc-400 transition hover:text-zinc-600 dark:hover:text-zinc-200">
          clear
        </button>
      ) : undefined}
    >
      <BoundRow label="from" bound={lo} onChange={setLo}
        fieldUnit={def.unit} resolved={rLo} />
      <BoundRow label="to" bound={hi} onChange={setHi}
        fieldUnit={def.unit} resolved={rHi} />
      <p className={clsx('mt-1.5', hintCls)}>
        {active ? (
          <>
            <span className="mr-1 inline-block size-2.5 translate-y-px rounded-[3px]"
              style={{ background: 'rgb(255 38 166)' }} />
            highlighting {bandText} — the heatmap underneath is unchanged.
          </>
        ) : 'Blank bounds are open-ended: set only “from” for a floor, only “to” for a cap.'}
        {' '}Field spans {stats.min.toFixed(2)} – {stats.max.toFixed(2)} {def.unit}
        {' '}(mean {stats.mean.toFixed(2)}, p50 {stats.p50.toFixed(2)},
        p95 {stats.p95.toFixed(2)}).
      </p>
      <Button outline onClick={save} className="mt-2 w-full"
        disabled={saving || !partId || !active}>
        <BookmarkPlus data-slot="icon" />
        {selected ? 'Update check' : 'Add check'}
      </Button>
      {selected && (
        <p className={clsx('mt-1', hintCls)}>
          Editing “{selected.id}” — adding a different band goes through the
          lens (toolbar) instead.
        </p>
      )}
    </RailSection>
  );
}

/**
 * The field-lens side panel (spike): explain the view, advanced compute
 * knobs, a re-run that only arms when something actually changed, and the
 * clipping band — interpretation stays client-side until saved as a check.
 */
export function FieldLensRail() {
  const def = useActiveFieldLens();
  const lens = useActiveLens();
  if (!def || !lens) return null;
  return <Body def={def} lensLabel={lens.label} lensBlurb={lens.blurb} />;
}

function Body({ def, lensLabel, lensBlurb }: {
  def: FieldLensDef; lensLabel: string; lensBlurb?: string;
}) {
  const manifest = useStore((s) => s.manifest);
  const partId = useStore((s) => s.partId);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const busy = useBusy();
  const compute = useV2((s) => s.compute[def.modeId]);
  void compute; // subscribe: re-arm the re-run button on knob changes
  const result = latestResult(manifest, def);
  const [fieldDist, setFieldDist] = useState<FieldStats | null>(null);

  useEffect(() => {
    setFieldDist(null);
    if (!manifest || !result) return;
    const desc = fieldDescriptor(manifest, result, def);
    if (!desc) return;
    let live = true;
    void fieldStats(desc).then((s) => { if (live) setFieldDist(s); });
    return () => { live = false; };
  }, [manifest, result, def]);

  const merged = currentCompute(def);
  const changed = computeChanged(def, merged, result?.params, !!result?.stale);
  const rerun = () => {
    if (!partId) return;
    runAnalysisJob(partId, def.process, def.analysis, merged)
      .catch((err) => useStore.getState().set({ error: String(err) }));
  };

  return (
    <Rail>
      <RailHeader
        title={lensLabel}
        status={busy ? 'active' : !result ? 'neutral'
          : result.stale ? 'warning' : 'good'}
        statusLabel={busy ? 'computing…' : !result ? 'not run'
          : result.stale ? 'stale' : 'current'}
        blurb={(lensBlurb ?? 'Plain field heatmap over the real data range.')
          + (!result && !busy ? ' Runs automatically with plain defaults.' : '')}
      />

      {/* the band is slot 3, not slot 6: it is the primary knob, it recolours
          instantly, and it sits ABOVE the compute knobs that re-run the job */}
      {result && fieldDist ? (
        <BandSection def={def} stats={fieldDist} />
      ) : (
        <p className={hintCls}>
          {busy ? 'Computing the field…' : result
            ? 'Loading the field distribution…'
            : 'The clipping band appears once the field exists.'}
        </p>
      )}

      {def.computeFields.length > 0 && (
        <RailDisclosure icon={Settings2} label="Advanced">
          {def.computeFields.map((field) => (
            <ComputeInput key={field.key} computeId={def.modeId} field={field} />
          ))}
        </RailDisclosure>
      )}

      <Button onClick={rerun} disabled={busy || !changed} className="w-full"
        title={changed ? undefined : 'nothing changed since the stored run'}>
        <RotateCw data-slot="icon" className={busy ? 'animate-spin' : undefined} />
        {busy ? 'Computing…' : result ? 'Re-run analysis' : 'Run analysis'}
      </Button>

      <RailDivider />

      <RailStats text={stats} error={error} />
    </Rail>
  );
}
