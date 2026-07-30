// How a mask field encodes "covered".
//
// Most coverage fields are 0/1 masks, but the turning scan's `axis_role_<k>`
// is a category (0 off-axis, 1 revolution-compatible, 2 swept) and the two
// turnability columns read DIFFERENT thresholds of that same field. So the
// rule travels with the column rather than being implied by the field.
//
// It lives here, next to the coverage painter, because both sides need it:
// `v2/decisions` computes the union share for the study's footer and the
// painter draws the same union — one predicate, so the number and the picture
// cannot disagree.

export type CoverageRule = 'nonzero' | 'ge1' | 'eq2';

export function covered(rule: CoverageRule, v: number): boolean {
  if (rule === 'ge1') return v >= 1;
  if (rule === 'eq2') return v === 2;
  return v !== 0;
}
