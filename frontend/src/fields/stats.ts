import type { FieldDescriptor } from '../api/types';
import { fetchField } from './fields';

/**
 * The distribution of one scalar field. Lives here rather than in the v2
 * shell because both a lens band and an expression term resolve their bounds
 * against it, and the two must agree to the digit.
 *
 * Stats are ALWAYS over the whole field, never over the region a term is
 * scoped to. "Above 50 % of the mean thickness" then means the same number
 * whether or not an earlier term narrowed the area — a scoped reference
 * would silently move every threshold downstream of an edit.
 */

export interface FieldStats {
  min: number; max: number; mean: number;
  p5: number; p50: number; p95: number;
  /** 201 quantile samples (0.5 % steps) — percentile bounds read off it. */
  quantiles: number[];
}

const statsCache = new Map<string, Promise<FieldStats>>();

/** How the array is read. Defaults to the shared field cache, but a caller
 * that already has a fetcher — a lens's `ctx.getField` — passes its own, so
 * one array is never fetched down two paths. */
export type FieldFetcher = (desc: FieldDescriptor)
=> Promise<Float32Array | Uint8Array | Uint32Array>;

/** Distribution of a scalar field (finite values only). Cached per URL. */
export function fieldStats(
  desc: FieldDescriptor, getField: FieldFetcher = fetchField,
): Promise<FieldStats> {
  if (!statsCache.has(desc.url)) {
    statsCache.set(desc.url, (async () => {
      const raw = await getField(desc) as Float32Array;
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
  { id: 'abs', label: fieldUnit || 'value' },
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

/** How a bound reads in a rule summary ("≥ 50 % of mean"). */
export function boundText(bound: BandBound, fieldUnit: string): string {
  if (bound.value.trim() === '') return '';
  const unit = bound.unit === 'abs' ? (fieldUnit || '')
    : bound.unit === 'pct' ? ' percentile'
      : bound.unit === 'mean' ? ' % of mean' : ' % of median';
  return `${bound.value}${unit}`;
}
