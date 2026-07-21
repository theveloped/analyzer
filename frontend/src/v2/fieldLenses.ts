import type { FieldDescriptor, Manifest, ResultEntry } from '../api/types';
import { fetchField } from '../fields/fields';
import { ANALYSIS_BY_ID, type ComputeField } from './analyses';

/**
 * Field lenses (spike): scalar-field lenses that MATERIALIZE THEMSELVES —
 * clicking one runs the backing analysis with plain defaults when nothing
 * is cached, and paints the un-thresholded heatmap over the real data
 * range. All interpretation (the clipping band, units, references) lives
 * in the side panel and only becomes durable when saved as a check.
 */

export interface FieldLensDef {
  /** Lens key (`processId:modeId`) this definition backs. */
  lensKey: string;
  process: string;
  analysis: string;
  modeId: string;
  /** npz member the lens paints (field id suffix in the manifest). */
  fieldName: string;
  /** Viewer params the heatmap reads: flag tick + colormap domain bounds. */
  thresholdParam: string;
  minParam: string;
  scaleParam: string;
  /** Viewer params of the HIGHLIGHT band (selection over the unchanged
   * heatmap; a blank bound is open-ended). */
  bandLoParam: string;
  bandHiParam: string;
  /** Paint flag hiding edge-explained faces — forced off for the plain view. */
  maskParam?: string;
  flagDirection: 'below' | 'above';
  unit: string;
  /** Compute-time knobs (the Advanced section); defaults are the run params. */
  computeFields: ComputeField[];
  /** Stored-result params this lens REQUIRES (e.g. contact_angles: true —
   * a plain thickness run has no contact_angle field to paint). */
  matchParams?: Record<string, unknown>;
}

const A = ANALYSIS_BY_ID;

export const FIELD_LENSES: Record<string, FieldLensDef> = {
  'injection_molding:thickness': {
    lensKey: 'injection_molding:thickness',
    process: 'injection_molding', analysis: 'thickness', modeId: 'thickness',
    fieldName: 'thickness',
    thresholdParam: 'minThickness', minParam: 'thicknessMin',
    scaleParam: 'thicknessScale',
    bandLoParam: 'thicknessBandLo', bandHiParam: 'thicknessBandHi', maskParam: 'maskExplained',
    flagDirection: 'below', unit: 'mm',
    computeFields: A.thickness?.advancedFields ?? [],
  },
  'injection_molding:gaps': {
    lensKey: 'injection_molding:gaps',
    process: 'injection_molding', analysis: 'gaps', modeId: 'gaps',
    fieldName: 'gap',
    thresholdParam: 'minGap', minParam: 'gapMin',
    scaleParam: 'gapScale',
    bandLoParam: 'gapBandLo', bandHiParam: 'gapBandHi', maskParam: 'maskExplained',
    flagDirection: 'below', unit: 'mm',
    computeFields: A.gaps?.advancedFields ?? [],
  },
  'injection_molding:rayThickness': {
    lensKey: 'injection_molding:rayThickness',
    process: 'injection_molding', analysis: 'ray_thickness', modeId: 'rayThickness',
    fieldName: 'ray_thickness',
    thresholdParam: 'minRayThickness', minParam: 'rayThicknessMin',
    scaleParam: 'rayThicknessScale',
    bandLoParam: 'rayThicknessBandLo', bandHiParam: 'rayThicknessBandHi',
    flagDirection: 'below', unit: 'mm',
    computeFields: A.rayThickness?.advancedFields ?? [],
  },
  'injection_molding:rayGap': {
    lensKey: 'injection_molding:rayGap',
    process: 'injection_molding', analysis: 'ray_gap', modeId: 'rayGap',
    fieldName: 'ray_gap',
    thresholdParam: 'minRayGap', minParam: 'rayGapMin',
    scaleParam: 'rayGapScale',
    bandLoParam: 'rayGapBandLo', bandHiParam: 'rayGapBandHi',
    flagDirection: 'below', unit: 'mm',
    computeFields: A.rayGap?.advancedFields ?? [],
  },
  'injection_molding:thicknessAngle': {
    lensKey: 'injection_molding:thicknessAngle',
    process: 'injection_molding', analysis: 'thickness', modeId: 'thicknessAngle',
    fieldName: 'contact_angle',
    thresholdParam: 'minAngle', minParam: 'angleMin',
    scaleParam: 'angleScale',
    bandLoParam: 'angleBandLo', bandHiParam: 'angleBandHi',
    flagDirection: 'below', unit: '°',
    computeFields: (A.thickness?.advancedFields ?? []).map((f) =>
      (f.key === 'contact_angles' ? { ...f, default: true } : f)),
    matchParams: { contact_angles: true },
  },
  'injection_molding:gapAngle': {
    lensKey: 'injection_molding:gapAngle',
    process: 'injection_molding', analysis: 'gaps', modeId: 'gapAngle',
    fieldName: 'contact_angle',
    thresholdParam: 'minAngle', minParam: 'angleMin',
    scaleParam: 'angleScale',
    bandLoParam: 'angleBandLo', bandHiParam: 'angleBandHi',
    flagDirection: 'below', unit: '°',
    computeFields: (A.gaps?.advancedFields ?? []).map((f) =>
      (f.key === 'contact_angles' ? { ...f, default: true } : f)),
    matchParams: { contact_angles: true },
  },
  'injection_molding:thinSpan': {
    lensKey: 'injection_molding:thinSpan',
    process: 'injection_molding', analysis: 'thin_span', modeId: 'thinSpan',
    fieldName: 'span_ratio',
    thresholdParam: 'maxSpanRatio', minParam: 'spanMin',
    scaleParam: 'spanScale',
    bandLoParam: 'spanBandLo', bandHiParam: 'spanBandHi',
    flagDirection: 'above', unit: '×',
    computeFields: [],
  },
};

/** Default compute payload for a field lens (its plain run). */
export function fieldLensCompute(def: FieldLensDef): Record<string, unknown> {
  return Object.fromEntries(def.computeFields.map((f) => [f.key, f.default]));
}

/** Latest stored result of a field lens's backing analysis. Non-stale
 * results win — a re-meshed part leaves orphaned results in the manifest
 * whose fields no longer align with the current mesh; painting one as
 * "current" (and skipping the auto-run) would be silently wrong. */
export function latestResult(
  manifest: Manifest | null, def: FieldLensDef,
): ResultEntry | null {
  if (!manifest) return null;
  const list = manifest.results.filter(
    (r) => r.process === def.process && r.analysis === def.analysis
      && Object.entries(def.matchParams ?? {}).every(
        ([key, value]) => r.params[key] === value));
  const fresh = list.filter((r) => !r.stale);
  return fresh[fresh.length - 1] ?? list[list.length - 1] ?? null;
}

/** The painted field's descriptor within a result. */
export function fieldDescriptor(
  manifest: Manifest, result: ResultEntry, def: FieldLensDef,
): FieldDescriptor | null {
  const id = `results.${def.process}.${def.analysis}.${result.hash}.${def.fieldName}`;
  return manifest.fields.find((f) => f.id === id) ?? null;
}

export interface FieldStats {
  min: number; max: number; mean: number;
  p5: number; p50: number; p95: number;
  /** 201 quantile samples (0.5 % steps) — percentile bounds read off it. */
  quantiles: number[];
}

const statsCache = new Map<string, Promise<FieldStats>>();

/** Distribution of the painted field (finite values only) — the band's
 * default range and its reference values. Cached per field URL. */
export function fieldStats(desc: FieldDescriptor): Promise<FieldStats> {
  if (!statsCache.has(desc.url)) {
    statsCache.set(desc.url, (async () => {
      const raw = await fetchField(desc) as Float32Array;
      const finite = Array.from(raw).filter((v) => isFinite(v));
      finite.sort((a, b) => a - b);
      const n = finite.length || 1;
      const at = (q: number) => finite[Math.min(n - 1, Math.floor(q * n))] ?? 0;
      const mean = finite.reduce((s, v) => s + v, 0) / n;
      return {
        min: finite[0] ?? 0, max: finite[n - 1] ?? 0, mean,
        p5: at(0.05), p50: at(0.5), p95: at(0.95),
        quantiles: Array.from({ length: 201 }, (_, i) => at(i / 200)),
      };
    })());
  }
  return statsCache.get(desc.url)!;
}

/** One band bound = a number in a unit. A blank value is an open bound, so
 * every target expression is one or two number-and-dropdown rows:
 * "≥ 5 mm" → from 5 mm · "the bottom p5" → to 5 percentile ·
 * "0–70 % of the median" → to 70 % of median ·
 * "70–130 % of the mean" → from 70, to 130, % of mean. */
export type BoundUnit = 'abs' | 'mean' | 'p50' | 'pct';

export interface BandBound {
  /** Raw input; '' = open bound. */
  value: string;
  unit: BoundUnit;
}

export const BOUND_UNITS = (fieldUnit: string):
{ id: BoundUnit; label: string }[] => [
  { id: 'abs', label: fieldUnit },
  { id: 'mean', label: '% of mean' },
  { id: 'p50', label: '% of median' },
  { id: 'pct', label: 'percentile' },
];

/** A bound resolved to an absolute field value (null = open). */
export function resolveBound(
  bound: BandBound, stats: FieldStats,
): number | null {
  if (bound.value.trim() === '') return null;
  const v = parseFloat(bound.value);
  if (!isFinite(v)) return null;
  if (bound.unit === 'abs') return v;
  if (bound.unit === 'pct') {
    const q = Math.min(100, Math.max(0, v));
    return stats.quantiles[Math.round(q * 2)];
  }
  return stats[bound.unit] * (v / 100);
}
