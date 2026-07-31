import { describe, expect, it } from 'vitest';
import type { FieldDescriptor, Manifest } from '../api/types';
import {
  buildMask, expressionFields, expressionText, resolveTerms, summarize,
  type ExprTerm, type MaskCtx, type ResolvedTerm,
} from './expression';

/**
 * Four unit-area triangles laid flat, so an area share is a face share and
 * the expected numbers are countable by hand.
 *
 * Faces:      0        1        2        3
 * thickness   0.5      1.0      2.0      4.0
 * angle       0        30       45       90
 * role        0        1        1        2
 */

const VERTS = new Float32Array([
  0, 0, 0, 1, 0, 0, 0, 2, 0, // face 0 — area 1
  2, 0, 0, 3, 0, 0, 2, 2, 0, // face 1
  4, 0, 0, 5, 0, 0, 4, 2, 0, // face 2
  6, 0, 0, 7, 0, 0, 6, 2, 0, // face 3
]);
const FACES = new Uint32Array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);

const DATA: Record<string, Float32Array | Uint32Array> = {
  thickness: new Float32Array([0.5, 1.0, 2.0, 4.0]),
  angle: new Float32Array([0, 30, 45, 90]),
  role: new Uint32Array([0, 1, 1, 2]),
  flag: new Float32Array([1, 0, 1, 0]),
};

function descriptor(member: string, role: FieldDescriptor['role']): FieldDescriptor {
  return {
    id: `results.p.a.HASH.${member}`,
    association: 'face', dtype: 'f4', role,
    length: 4, url: `/${member}`, params: {},
  };
}

const MANIFEST = {
  fields: [
    descriptor('thickness', 'scalar'),
    descriptor('angle', 'scalar'),
    descriptor('role', 'category'),
    descriptor('flag', 'mask'),
  ],
  results: [{ process: 'p', analysis: 'a', hash: 'HASH', params: {}, stats: {},
    fields: [], stale: false }],
  part: { counts: { faces: 4 } },
} as unknown as Manifest;

const ctx: MaskCtx = {
  manifest: MANIFEST,
  verts: VERTS,
  faces: FACES,
  faceCount: 4,
  getField: async (desc) => DATA[desc.id.split('.').pop()!],
};

const term = (
  field: string, rule: ExprTerm['rule'], op: ExprTerm['op'] = 'and',
): ResolvedTerm => ({
  source: 's1', field, rule, op, fieldId: `results.p.a.HASH.${field}`,
});

const band = (lo: string, hi: string, unit: 'abs' | 'mean' = 'abs') =>
  ({ kind: 'band' as const, lo: { value: lo, unit }, hi: { value: hi, unit } });

const hit = async (terms: ResolvedTerm[]) => {
  const { mask } = await buildMask(ctx, terms);
  return [...mask].flatMap((v, f) => (v ? [f] : []));
};

describe('term rules', () => {
  it('bands a scalar inclusively', async () => {
    expect(await hit([term('thickness', band('1', '2'))])).toEqual([1, 2]);
  });

  it('treats a blank bound as open', async () => {
    expect(await hit([term('thickness', band('', '1'))])).toEqual([0, 1]);
    expect(await hit([term('thickness', band('2', ''))])).toEqual([2, 3]);
  });

  it('resolves % of mean against the WHOLE field', async () => {
    // mean thickness = (0.5 + 1 + 2 + 4) / 4 = 1.875; 50 % = 0.9375
    expect(await hit([term('thickness', band('50', '', 'mean'))]))
      .toEqual([1, 2, 3]);
  });

  it('selects category values, and inverts them', async () => {
    expect(await hit([term('role', { kind: 'category', values: [1] })]))
      .toEqual([1, 2]);
    expect(await hit([term('role', { kind: 'category', values: [1], negate: true })]))
      .toEqual([0, 3]);
  });

  it('reads a mask as nonzero, and inverts it', async () => {
    expect(await hit([term('flag', { kind: 'mask' })])).toEqual([0, 2]);
    expect(await hit([term('flag', { kind: 'mask', negate: true })]))
      .toEqual([1, 3]);
  });
});

describe('combining terms', () => {
  it('ands, ors and subtracts', async () => {
    const angled = term('angle', band('10', '60'));            // faces 1, 2
    const thick = term('thickness', band('2', ''), 'and');     // faces 2, 3
    expect(await hit([angled, thick])).toEqual([2]);
    expect(await hit([angled, { ...thick, op: 'or' }])).toEqual([1, 2, 3]);
    expect(await hit([angled, { ...thick, op: 'andNot' }])).toEqual([1]);
  });

  it('ignores the first term operator, so order cannot change its meaning',
    async () => {
      const a = term('angle', band('10', '60'), 'andNot');
      expect(await hit([a])).toEqual([1, 2]);
    });

  it('is left-associative across three terms', async () => {
    // (angle 10-60 AND thickness >= 1) AND NOT role in {2}
    const out = await hit([
      term('angle', band('10', '60')),
      term('thickness', band('1', ''), 'and'),
      term('role', { kind: 'category', values: [2] }, 'andNot'),
    ]);
    expect(out).toEqual([1, 2]);
  });

  it('drops a term whose field is missing rather than silently passing it',
    async () => {
      const ghost = { ...term('nope', band('0', '1')), fieldId: 'results.p.a.HASH.nope' };
      const { unresolved } = await buildMask(ctx, [term('flag', { kind: 'mask' }), ghost]);
      expect(unresolved).toHaveLength(1);
    });
});

describe('the user example', () => {
  it('flags thick faces inside the 10–60° contact-angle region', async () => {
    // "for all the areas where the contact angle is between 10 and 60 degrees,
    //  check whether any thickness is above 50 % of the mean thickness"
    const mask = (await buildMask(ctx, [
      term('angle', band('10', '60')),
      term('thickness', band('50', '', 'mean'), 'and'),
    ])).mask;
    const summary = summarize(ctx, mask);
    expect([...mask]).toEqual([0, 1, 1, 0]);
    expect(summary.faces).toBe(2);
    expect(summary.share).toBeCloseTo(0.5, 6);
  });
});

describe('area weighting', () => {
  it('weights by triangle area, not by face count', async () => {
    const big = new Float32Array([
      0, 0, 0, 1, 0, 0, 0, 2, 0, // area 1
      2, 0, 0, 12, 0, 0, 2, 2, 0, // area 10
      4, 0, 0, 5, 0, 0, 4, 2, 0,
      6, 0, 0, 7, 0, 0, 6, 2, 0,
    ]);
    const wide: MaskCtx = { ...ctx, verts: big };
    const summary = summarize(wide, new Uint8Array([0, 1, 0, 0]));
    expect(summary.faces).toBe(1);
    expect(summary.area).toBeCloseTo(10, 6);
    expect(summary.share).toBeCloseTo(10 / 13, 6);
  });
});

describe('binding and reading back', () => {
  it('resolves terms through per-source hashes', () => {
    const stored: ExprTerm[] = [
      { source: 's1', field: 'thickness', rule: band('1', '2'), op: 'and' }];
    const { resolved, missing } = resolveTerms(stored, {
      s1: { analysis: 'p/a', hash: 'HASH' } });
    expect(missing).toHaveLength(0);
    expect(resolved[0].fieldId).toBe('results.p.a.HASH.thickness');
  });

  it('reports a term whose source has never run', () => {
    const stored: ExprTerm[] = [
      { source: 's1', field: 'thickness', rule: band('1', '2'), op: 'and' }];
    const { resolved, missing } = resolveTerms(stored, {
      s1: { analysis: 'p/a', hash: null } });
    expect(resolved).toHaveLength(0);
    expect(missing).toHaveLength(1);
  });

  it('lists only fields a term can interpret', () => {
    const options = expressionFields(MANIFEST);
    expect(options.map((o) => o.member).sort())
      .toEqual(['angle', 'flag', 'role', 'thickness']);
    expect(options.find((o) => o.member === 'role')?.rule).toBe('category');
    expect(options.find((o) => o.member === 'flag')?.rule).toBe('mask');
  });

  it('reads an expression back as one line', () => {
    expect(expressionText([
      { source: 's', field: 'angle', label: 'angle', unit: '°',
        rule: band('10', '60'), op: 'and' },
      { source: 's', field: 'thickness', label: 'thickness',
        rule: { kind: 'band', lo: { value: '50', unit: 'mean' },
          hi: { value: '', unit: 'abs' } }, op: 'and' },
    ])).toBe('angle 10° – 60° and thickness ≥ 50 % of mean');
  });
});
