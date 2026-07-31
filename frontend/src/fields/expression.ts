// The unified way to interpret stored data: turn any field into a boolean
// per-face mask, then combine masks.
//
// EVERY field-shaped question the workbench asks has this shape. A wall too
// thin is "thickness below 1 mm". A face nothing machines is "visible AND NOT
// reachable". A hole in the wrong region is "feature_id in {…} AND thickness
// above the mean". They differ only in which fields, which rule per field, and
// which operator joins them — so they are one check with a configuration, not
// five check kinds.
//
// Three rules per field, one per FieldRole that carries a per-element value:
//
//   scalar   → a BAND: two open-ended bounds in field units, % of mean,
//              % of median or percentile (fields/stats.ts)
//   mask     → nonzero, optionally negated
//   category → a SET of category values that mean "yes", optionally negated
//
// and three operators — and · or · and-not — folded left to right.
//
// A term names its field by (source id, npz member), never by result hash:
// the hash moves on every re-run, the pair does not. Resolution to a live
// FieldDescriptor happens at evaluation time from the per-source hashes the
// server derives, which is what lets a stored expression survive a recompute.

import type { FieldDescriptor, FieldRole, Manifest } from '../api/types';
import { fieldStats, resolveBound, boundText, type BandBound } from './stats';

export type TermOp = 'and' | 'or' | 'andNot';

export type TermRule =
  | { kind: 'band'; lo: BandBound; hi: BandBound }
  | { kind: 'mask'; negate?: boolean }
  | { kind: 'category'; values: number[]; negate?: boolean };

export interface ExprTerm {
  /** Which of the check's `sources` holds the field. */
  source: string;
  /** npz member name within that source's result. */
  field: string;
  rule: TermRule;
  /** How this term joins everything before it (ignored on the first). */
  op: TermOp;
  /** Display unit for band bounds; presentation only. */
  unit?: string;
  /** Human label for the field, captured when the term was authored. */
  label?: string;
}

/** What turns the composed mask into a verdict. `limit` is an area SHARE
 * (0–1) of the part: 0 means "any face at all is a finding". */
export interface ExprAggregate {
  limit: number;
  severity: 'review' | 'fail';
}

export interface ExprPolicy {
  kind: 'expression';
  terms: ExprTerm[];
  aggregate: ExprAggregate;
}

export const DEFAULT_AGGREGATE: ExprAggregate = { limit: 0, severity: 'review' };

/** Everything the mask builder needs — ViewCtx satisfies this structurally,
 * so a lens passes itself and the evaluator assembles one from the manifest. */
export interface MaskCtx {
  manifest: Manifest;
  verts: Float32Array;
  faces: Uint32Array;
  faceCount: number;
  getField(desc: FieldDescriptor): Promise<Float32Array | Uint8Array | Uint32Array>;
}

/** Field ids are `results.<process>.<analysis>.<hash>.<member>`; a term keeps
 * the hash-free half so it survives a re-run. */
export function fieldIdFor(
  analysis: string, hash: string, member: string,
): string {
  return `results.${analysis.replace('/', '.')}.${hash}.${member}`;
}

export interface ResolvedTerm extends ExprTerm {
  fieldId: string;
}

/** Bind terms to live field ids using the per-source result hashes. A term
 * whose source has no hash yet (never run) drops out — the caller reports
 * that as "not run" rather than silently evaluating a partial expression. */
export function resolveTerms(
  terms: ExprTerm[], sources: Record<string, { analysis: string; hash: string | null }>,
): { resolved: ResolvedTerm[]; missing: ExprTerm[] } {
  const resolved: ResolvedTerm[] = [];
  const missing: ExprTerm[] = [];
  for (const term of terms) {
    const source = sources[term.source];
    if (!source?.hash) { missing.push(term); continue; }
    resolved.push({
      ...term, fieldId: fieldIdFor(source.analysis, source.hash, term.field),
    });
  }
  return { resolved, missing };
}

/** Per-fine-face values of a field, whatever index space it lives in.
 *
 * `vertex` fields collapse by the faceValues convention (mean of the three
 * corners) for scalars; for a mask or a category a mean is meaningless, so
 * ALL THREE corners must agree — the conservative reading, and the only one
 * that keeps a category value intact.
 */
async function facewise(
  ctx: MaskCtx, desc: FieldDescriptor,
): Promise<Float64Array | null> {
  const raw = await ctx.getField(desc);
  const out = new Float64Array(ctx.faceCount);
  if (desc.association === 'face') {
    for (let f = 0; f < ctx.faceCount; f++) out[f] = raw[f] ?? NaN;
    return out;
  }
  if (desc.association === 'vertex') {
    const smooth = desc.role === 'scalar';
    for (let f = 0; f < ctx.faceCount; f++) {
      const a = raw[ctx.faces[3 * f]], b = raw[ctx.faces[3 * f + 1]];
      const c = raw[ctx.faces[3 * f + 2]];
      out[f] = smooth ? (a + b + c) / 3 : (a === b && b === c ? a : NaN);
    }
    return out;
  }
  if (desc.association === 'brep_face') {
    // per-BREP values broadcast onto the fine mesh through the id map, the
    // same join faceAttrs and the feature mask use
    const map = ctx.manifest.fields.find((f) => f.id === 'brep_faces');
    if (!map) return null;
    const ids = await ctx.getField(map) as Uint32Array;
    for (let f = 0; f < ctx.faceCount; f++) out[f] = raw[ids[f]] ?? NaN;
    return out;
  }
  return null; // graph / none: not paintable per face, so not usable in a term
}

/** The boolean mask one term selects. */
export async function termMask(
  ctx: MaskCtx, term: ResolvedTerm,
): Promise<Uint8Array | null> {
  const desc = ctx.manifest.fields.find((f) => f.id === term.fieldId);
  if (!desc) return null;
  const values = await facewise(ctx, desc);
  if (!values) return null;

  const mask = new Uint8Array(ctx.faceCount);
  if (term.rule.kind === 'band') {
    const stats = await fieldStats(desc, ctx.getField);
    const lo = resolveBound(term.rule.lo, stats) ?? -Infinity;
    const hi = resolveBound(term.rule.hi, stats) ?? Infinity;
    for (let f = 0; f < ctx.faceCount; f++) {
      const v = values[f];
      if (isFinite(v) && v >= lo && v <= hi) mask[f] = 1;
    }
    return mask;
  }
  if (term.rule.kind === 'mask') {
    const negate = !!term.rule.negate;
    for (let f = 0; f < ctx.faceCount; f++) {
      const on = isFinite(values[f]) && values[f] !== 0;
      if (on !== negate) mask[f] = 1;
    }
    return mask;
  }
  const wanted = new Set(term.rule.values);
  const negate = !!term.rule.negate;
  for (let f = 0; f < ctx.faceCount; f++) {
    const on = isFinite(values[f]) && wanted.has(values[f]);
    if (on !== negate) mask[f] = 1;
  }
  return mask;
}

/** Fold the terms left to right. The first term's operator is ignored — it
 * has nothing to combine with, and treating a leading `andNot` as "everything
 * except" would make the same term list mean two things depending on order. */
export async function buildMask(
  ctx: MaskCtx, terms: ResolvedTerm[],
): Promise<{ mask: Uint8Array; unresolved: ResolvedTerm[] }> {
  const unresolved: ResolvedTerm[] = [];
  let acc: Uint8Array | null = null;
  for (const term of terms) {
    const mask = await termMask(ctx, term);
    if (!mask) { unresolved.push(term); continue; }
    if (!acc) { acc = mask; continue; }
    for (let f = 0; f < ctx.faceCount; f++) {
      if (term.op === 'or') acc[f] |= mask[f];
      else if (term.op === 'andNot') acc[f] = acc[f] && !mask[f] ? 1 : 0;
      else acc[f] &= mask[f];
    }
  }
  return { mask: acc ?? new Uint8Array(ctx.faceCount), unresolved };
}

/** Per-face triangle areas (mm²) — the mask's weight. A face COUNT would let
 * a dense corner outvote a large flat wall. */
export function faceAreas(ctx: MaskCtx): Float64Array {
  const areas = new Float64Array(ctx.faceCount);
  const { verts, faces } = ctx;
  for (let f = 0; f < ctx.faceCount; f++) {
    const a = 3 * faces[3 * f], b = 3 * faces[3 * f + 1], c = 3 * faces[3 * f + 2];
    const ux = verts[b] - verts[a], uy = verts[b + 1] - verts[a + 1],
      uz = verts[b + 2] - verts[a + 2];
    const vx = verts[c] - verts[a], vy = verts[c + 1] - verts[a + 1],
      vz = verts[c + 2] - verts[a + 2];
    const cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
    areas[f] = 0.5 * Math.sqrt(cx * cx + cy * cy + cz * cz);
  }
  return areas;
}

export interface MaskSummary {
  faces: number;
  area: number;
  totalArea: number;
  share: number;
}

export function summarize(ctx: MaskCtx, mask: Uint8Array): MaskSummary {
  const areas = faceAreas(ctx);
  let area = 0, totalArea = 0, faces = 0;
  for (let f = 0; f < ctx.faceCount; f++) {
    totalArea += areas[f];
    if (mask[f]) { faces++; area += areas[f]; }
  }
  return { faces, area, totalArea, share: totalArea ? area / totalArea : 0 };
}

// --- reading an expression back -------------------------------------------

const OP_TEXT: Record<TermOp, string> = {
  and: 'and', or: 'or', andNot: 'and not',
};

export function ruleText(term: ExprTerm): string {
  const unit = term.unit ?? '';
  if (term.rule.kind === 'band') {
    const lo = boundText(term.rule.lo, unit);
    const hi = boundText(term.rule.hi, unit);
    if (lo && hi) return `${lo} – ${hi}`;
    if (lo) return `≥ ${lo}`;
    if (hi) return `≤ ${hi}`;
    return 'any value';
  }
  if (term.rule.kind === 'mask') return term.rule.negate ? 'not set' : 'set';
  const list = term.rule.values.join(', ');
  return term.rule.negate ? `not in {${list}}` : `in {${list}}`;
}

/** One-line reading of the whole expression, for the check card. */
export function expressionText(terms: ExprTerm[]): string {
  return terms.map((term, i) => {
    const head = i === 0 ? '' : `${OP_TEXT[term.op]} `;
    return `${head}${term.label ?? term.field} ${ruleText(term)}`;
  }).join(' ');
}

// --- what a term can point at ----------------------------------------------

/** Field roles a term can interpret, and the rule each one takes. */
export const ROLE_RULE: Partial<Record<FieldRole, TermRule['kind']>> = {
  scalar: 'band', mask: 'mask', category: 'category',
};

export interface FieldOption {
  /** Manifest field id (carries the hash — for browsing, not for storing). */
  id: string;
  process: string;
  analysis: string;
  hash: string;
  /** npz member — what the term stores. */
  member: string;
  label: string;
  role: FieldRole;
  rule: TermRule['kind'];
  unit: string;
  params: Record<string, unknown>;
  stale: boolean;
}

/**
 * Every stored field a term could interpret, newest result first.
 *
 * A view over WHATEVER THE CACHE HOLDS, exactly like the study table: you
 * build an expression out of what has been computed, and a field you want
 * that is not there yet is one lens click away. That avoids inventing a
 * second catalogue of fields that could drift from the results themselves.
 */
export function expressionFields(manifest: Manifest | null): FieldOption[] {
  if (!manifest) return [];
  const byResult = new Map(manifest.results.map((r) => [
    `${r.process}.${r.analysis}.${r.hash}`, r]));
  const out: FieldOption[] = [];
  for (const field of manifest.fields) {
    const match = /^results\.([^.]+)\.([^.]+)\.([^.]+)\.(.+)$/.exec(field.id);
    if (!match) continue;
    const [, process, analysis, hash, member] = match;
    const rule = ROLE_RULE[field.role];
    if (!rule) continue;
    if (!['face', 'vertex', 'brep_face'].includes(field.association)) continue;
    const result = byResult.get(`${process}.${analysis}.${hash}`);
    out.push({
      id: field.id, process, analysis, hash, member,
      label: `${analysis} · ${member}`,
      role: field.role, rule, unit: field.units ?? '',
      params: result?.params ?? {},
      stale: !!result?.stale,
    });
  }
  return out.reverse();
}
