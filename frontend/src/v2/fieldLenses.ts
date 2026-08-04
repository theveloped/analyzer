import type { FieldDescriptor, Manifest, ResultEntry } from '../api/types';
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

// The band vocabulary moved to `fields/stats.ts` when expression terms started
// resolving bounds the same way — one implementation, so a lens band and a
// check term cannot disagree about what "50 % of the mean" is.
export {
  BOUND_UNITS, fieldStats, resolveBound,
  type BandBound, type BoundUnit, type FieldStats,
} from '../fields/stats';
