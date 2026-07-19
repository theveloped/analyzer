import { Radius, Ratio, Ruler, Spline, type LucideIcon } from 'lucide-react';

/**
 * The production-engineer analysis catalog. Each entry maps to an existing
 * injection-molding view mode + backend analysis, but exposes only the one
 * threshold an engineer actually sets; the computational-geometry knobs live
 * in `advancedFields` and stay hidden behind the Advanced disclosure.
 *
 * `id` is the viewer `modeId` (drives the shared store/painter); `analysis`
 * is the backend analysis id submitted to run the field.
 */

export type Tier = 'primary' | 'advanced';

export interface ComputeField {
  key: string;
  label: string;
  type: 'number' | 'bool';
  default: number | boolean | null;
  placeholder?: string;
  unit?: string;
  hint?: string;
}

export interface Analysis {
  id: string;
  process: string;
  analysis: string;
  label: string;
  blurb: string;
  icon: LucideIcon;
  tier: Tier;
  /** Viewer param the engineer sets (client-side, instant re-color). */
  thresholdParam: string;
  thresholdLabel: string;
  thresholdDefault: number;
  /** Viewer param for the heatmap upper bound (blank = auto). */
  scaleParam: string;
  scaleLabel: string;
  unit: string;
  /** Compute-time knobs — hidden by default, revealed under "Advanced". */
  advancedFields: ComputeField[];
}

const SPHERE_FIELDS: ComputeField[] = [
  {
    key: 'max_radius', type: 'number', default: null, placeholder: 'auto',
    label: 'Max sphere radius', unit: 'mm',
    hint: 'Largest inscribed sphere probed. Auto sizes from the part.',
  },
  {
    key: 'sharp_deg', type: 'number', default: 25, unit: '°',
    label: 'Sharp-edge angle',
    hint: 'Edges sharper than this are treated as geometric, not thin walls.',
  },
  {
    key: 'contact_angles', type: 'bool', default: false,
    label: 'Store contact angles',
    hint: 'Extra per-vertex separation-angle field for debugging.',
  },
];

const RAY_FIELDS: ComputeField[] = [
  {
    key: 'max_distance', type: 'number', default: null, placeholder: 'auto',
    label: 'Max ray distance', unit: 'mm',
    hint: 'Furthest the inward ray searches for the opposing wall.',
  },
];

export const ANALYSES: Analysis[] = [
  {
    id: 'thickness',
    process: 'injection_molding',
    analysis: 'thickness',
    label: 'Wall thickness',
    blurb: 'Rolling-sphere wall thickness — flags walls thinner than your limit.',
    icon: Ruler,
    tier: 'primary',
    thresholdParam: 'minThickness',
    thresholdLabel: 'Minimum wall thickness',
    thresholdDefault: 1.0,
    scaleParam: 'thicknessScale',
    scaleLabel: 'Heatmap max',
    unit: 'mm',
    advancedFields: SPHERE_FIELDS,
  },
  {
    id: 'gaps',
    process: 'injection_molding',
    analysis: 'gaps',
    label: 'Gap / clearance',
    blurb: 'Clearance between opposing walls — flags gaps tighter than your limit.',
    icon: Spline,
    tier: 'primary',
    thresholdParam: 'minGap',
    thresholdLabel: 'Minimum gap',
    thresholdDefault: 0.5,
    scaleParam: 'gapScale',
    scaleLabel: 'Heatmap max',
    unit: 'mm',
    advancedFields: SPHERE_FIELDS,
  },
  {
    id: 'rayThickness',
    process: 'injection_molding',
    analysis: 'ray_thickness',
    label: 'Ray thickness',
    blurb: 'Fast inward-ray wall thickness — cheaper, never under-reads at edges.',
    icon: Ratio,
    tier: 'advanced',
    thresholdParam: 'minRayThickness',
    thresholdLabel: 'Minimum wall thickness',
    thresholdDefault: 1.0,
    scaleParam: 'rayThicknessScale',
    scaleLabel: 'Heatmap max',
    unit: 'mm',
    advancedFields: RAY_FIELDS,
  },
  {
    id: 'rayGap',
    process: 'injection_molding',
    analysis: 'ray_gap',
    label: 'Ray gap',
    blurb: 'Fast inward-ray clearance between opposing walls.',
    icon: Radius,
    tier: 'advanced',
    thresholdParam: 'minRayGap',
    thresholdLabel: 'Minimum gap',
    thresholdDefault: 0.5,
    scaleParam: 'rayGapScale',
    scaleLabel: 'Heatmap max',
    unit: 'mm',
    advancedFields: RAY_FIELDS,
  },
];

export const ANALYSIS_BY_ID = Object.fromEntries(
  ANALYSES.map((a) => [a.id, a]),
) as Record<string, Analysis>;

/** Default compute payload for an analysis, from its advanced-field defaults. */
export function defaultCompute(a: Analysis): Record<string, unknown> {
  return Object.fromEntries(a.advancedFields.map((f) => [f.key, f.default]));
}
