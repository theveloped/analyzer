import type { PmiData, PmiDimension, PmiTolerance } from '../../api/types';

/**
 * Client-side grouping of pmi.json tolerances/dimensions into the sections the
 * `3a` PMI panel shows: datum-referenced control frames, patterns (one N× card
 * per repeated frame), datum-free form tolerances, and the dimension split.
 *
 * pmi.json carries no explicit pattern identity, so a "pattern" is derived: two
 * or more tolerances that share an identical control-frame *signature* (type,
 * magnitude, zone/material modifiers, and the ordered datum reference frame).
 * The heuristic is deliberately conservative — differing datums, values or
 * modifiers keep frames apart, so unrelated tolerances never merge; a lone
 * frame is never a pattern. See docs/PMI-VIEWER-EDITOR.md (phase 1).
 */

export interface PmiPattern {
  /** shared control-frame signature (also a stable React key) */
  key: string;
  /** the repeated tolerances, in original order */
  tolerances: PmiTolerance[];
  /** union of every instance's bridged faces, de-duplicated */
  faceIds: number[];
  /** representative frame for rendering the card glyphs */
  sample: PmiTolerance;
}

export interface PmiGroups {
  /** single (non-patterned) tolerances that name at least one datum */
  datumReferenced: PmiTolerance[];
  /** repeated frames collapsed to one card each */
  patterns: PmiPattern[];
  /** single tolerances with no datum reference (form control) */
  noDatum: PmiTolerance[];
  /** dimensions carrying a magnitude or ± tolerance (feature-of-size) */
  sizes: PmiDimension[];
  /** value-less reference/location dimensions */
  refDims: PmiDimension[];
}

/** True when a dimension carries an independent magnitude or a ± tolerance. */
export function hasMagnitude(d: PmiDimension): boolean {
  return !!d.value || d.upper_tolerance != null || d.lower_tolerance != null;
}

const sortedCopy = (xs: readonly string[] | undefined): string[] =>
  [...(xs ?? [])].sort();

/** A control-frame signature that is equal iff two frames read identically
 * (ignoring which faces they land on and their datum-ref ordering position,
 * but preserving datum precedence order). */
export function toleranceSignature(t: PmiTolerance): string {
  return JSON.stringify([
    t.type ?? '',
    t.value ?? null,
    t.type_of_value ?? '',
    t.material_modifier ?? '',
    t.zone_modifier ?? '',
    // datum_names is already precedence-ordered upstream; keep that order
    t.datum_names ?? [],
    sortedCopy(t.modifiers),
  ]);
}

function unionFaces(tols: PmiTolerance[]): number[] {
  const seen = new Set<number>();
  for (const t of tols) for (const f of t.face_ids) seen.add(f);
  return [...seen];
}

/** True when the tolerance's datum reference frame names at least one datum. */
export function isDatumReferenced(t: PmiTolerance): boolean {
  return (t.datum_names ?? []).some(Boolean);
}

export function groupPmi(pmi: PmiData | null): PmiGroups {
  const empty: PmiGroups = {
    datumReferenced: [], patterns: [], noDatum: [], sizes: [], refDims: [],
  };
  if (!pmi) return empty;

  // bucket tolerances by signature, preserving first-seen order
  const buckets = new Map<string, PmiTolerance[]>();
  for (const t of pmi.tolerances) {
    const sig = toleranceSignature(t);
    const bucket = buckets.get(sig);
    if (bucket) bucket.push(t);
    else buckets.set(sig, [t]);
  }

  const patterns: PmiPattern[] = [];
  const singles: PmiTolerance[] = [];
  for (const [key, tols] of buckets) {
    if (tols.length >= 2) {
      patterns.push({ key, tolerances: tols, faceIds: unionFaces(tols), sample: tols[0] });
    } else {
      singles.push(tols[0]);
    }
  }

  const datumReferenced = singles.filter(isDatumReferenced);
  const noDatum = singles.filter((t) => !isDatumReferenced(t));

  return {
    datumReferenced,
    patterns,
    noDatum,
    sizes: pmi.dimensions.filter(hasMagnitude),
    refDims: pmi.dimensions.filter((d) => !hasMagnitude(d)),
  };
}
