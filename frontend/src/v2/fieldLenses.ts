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
  /** Paint flag hiding edge-explained faces — forced off for the plain view. */
  maskParam?: string;
  flagDirection: 'below' | 'above';
  unit: string;
  /** Compute-time knobs (the Advanced section); defaults are the run params. */
  computeFields: ComputeField[];
}

const A = ANALYSIS_BY_ID;

export const FIELD_LENSES: Record<string, FieldLensDef> = {
  'injection_molding:thickness': {
    lensKey: 'injection_molding:thickness',
    process: 'injection_molding', analysis: 'thickness', modeId: 'thickness',
    fieldName: 'thickness',
    thresholdParam: 'minThickness', minParam: 'thicknessMin',
    scaleParam: 'thicknessScale', maskParam: 'maskExplained',
    flagDirection: 'below', unit: 'mm',
    computeFields: A.thickness?.advancedFields ?? [],
  },
  'injection_molding:gaps': {
    lensKey: 'injection_molding:gaps',
    process: 'injection_molding', analysis: 'gaps', modeId: 'gaps',
    fieldName: 'gap',
    thresholdParam: 'minGap', minParam: 'gapMin',
    scaleParam: 'gapScale', maskParam: 'maskExplained',
    flagDirection: 'below', unit: 'mm',
    computeFields: A.gaps?.advancedFields ?? [],
  },
  'injection_molding:rayThickness': {
    lensKey: 'injection_molding:rayThickness',
    process: 'injection_molding', analysis: 'ray_thickness', modeId: 'rayThickness',
    fieldName: 'ray_thickness',
    thresholdParam: 'minRayThickness', minParam: 'rayThicknessMin',
    scaleParam: 'rayThicknessScale',
    flagDirection: 'below', unit: 'mm',
    computeFields: A.rayThickness?.advancedFields ?? [],
  },
  'injection_molding:rayGap': {
    lensKey: 'injection_molding:rayGap',
    process: 'injection_molding', analysis: 'ray_gap', modeId: 'rayGap',
    fieldName: 'ray_gap',
    thresholdParam: 'minRayGap', minParam: 'rayGapMin',
    scaleParam: 'rayGapScale',
    flagDirection: 'below', unit: 'mm',
    computeFields: A.rayGap?.advancedFields ?? [],
  },
  'injection_molding:thinSpan': {
    lensKey: 'injection_molding:thinSpan',
    process: 'injection_molding', analysis: 'thin_span', modeId: 'thinSpan',
    fieldName: 'span_ratio',
    thresholdParam: 'maxSpanRatio', minParam: 'spanMin',
    scaleParam: 'spanScale',
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
    (r) => r.process === def.process && r.analysis === def.analysis);
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
      };
    })());
  }
  return statsCache.get(desc.url)!;
}

export type BandReference = 'origin' | 'mean' | 'p5' | 'p50' | 'p95';
export type BandUnit = 'mm' | '%';

export const BAND_REFERENCES: { id: BandReference; label: string }[] = [
  { id: 'origin', label: 'origin (absolute)' },
  { id: 'mean', label: 'mean of the field' },
  { id: 'p5', label: 'p5' },
  { id: 'p50', label: 'median (p50)' },
  { id: 'p95', label: 'p95' },
];

export function referenceValue(ref: BandReference, stats: FieldStats): number {
  return ref === 'origin' ? 0 : stats[ref];
}

/** A band bound resolved to an absolute field value: mm offsets add to the
 * reference; % scales it (so "80–120 % of mean" reads literally). */
export function resolveBound(
  value: number, unit: BandUnit, ref: BandReference, stats: FieldStats,
): number {
  const base = referenceValue(ref, stats);
  return unit === '%' ? base * (value / 100) : base + value;
}
