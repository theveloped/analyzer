import { describe, expect, it } from 'vitest';
import type { PmiData, PmiDimension, PmiTolerance } from '../../api/types';
import { groupPmi, toleranceSignature } from './pmiGroups';

const tol = (over: Partial<PmiTolerance>): PmiTolerance => ({
  id: 0,
  kind: 'tolerance',
  name: null,
  type: 'Position',
  value: 0.2,
  type_of_value: 'Diameter',
  modifiers: [],
  material_modifier: 'M',
  zone_modifier: null,
  zone_value: null,
  max_value: null,
  datum_refs: [],
  datum_names: ['A', 'B', 'C'],
  face_ids: [],
  edge_ids: [],
  ...over,
});

const dim = (over: Partial<PmiDimension>): PmiDimension => ({
  id: 0,
  kind: 'dimension',
  type: 'DimensionalLocation',
  value: 0,
  upper_tolerance: null,
  lower_tolerance: null,
  qualifier: null,
  modifiers: [],
  angular: false,
  face_ids: [],
  secondary_face_ids: [],
  edge_ids: [],
  ...over,
});

const data = (over: Partial<PmiData>): PmiData => ({
  schema: 4, dimensions: [], tolerances: [], datums: [], ...over,
});

describe('toleranceSignature', () => {
  it('is equal for frames that read identically on different faces', () => {
    expect(toleranceSignature(tol({ id: 1, face_ids: [3] })))
      .toBe(toleranceSignature(tol({ id: 2, face_ids: [9] })));
  });
  it('differs when datum frame, value or modifiers differ', () => {
    const base = toleranceSignature(tol({}));
    expect(toleranceSignature(tol({ datum_names: ['A', 'B'] }))).not.toBe(base);
    expect(toleranceSignature(tol({ value: 0.5 }))).not.toBe(base);
    expect(toleranceSignature(tol({ material_modifier: 'L' }))).not.toBe(base);
  });
});

describe('groupPmi', () => {
  it('collapses repeated identical frames into one pattern, union of faces', () => {
    const g = groupPmi(data({
      tolerances: [
        tol({ id: 1, face_ids: [1] }),
        tol({ id: 2, face_ids: [2] }),
        tol({ id: 3, face_ids: [3] }),
      ],
    }));
    expect(g.patterns).toHaveLength(1);
    expect(g.patterns[0].tolerances).toHaveLength(3);
    expect(g.patterns[0].faceIds.sort()).toEqual([1, 2, 3]);
    expect(g.datumReferenced).toHaveLength(0);
  });

  it('never merges unrelated tolerances and never patterns a lone frame', () => {
    const g = groupPmi(data({
      tolerances: [
        tol({ id: 1, type: 'Position', face_ids: [1] }),
        tol({ id: 2, type: 'Perpendicularity', datum_names: ['A'], face_ids: [2] }),
      ],
    }));
    expect(g.patterns).toHaveLength(0);
    expect(g.datumReferenced).toHaveLength(2);
  });

  it('separates datum-free form tolerances from datum-referenced frames', () => {
    const g = groupPmi(data({
      tolerances: [
        tol({ id: 1, type: 'Flatness', material_modifier: null, datum_names: [], value: 0.1, face_ids: [1] }),
        tol({ id: 2, type: 'Position', datum_names: ['A', 'B'], face_ids: [2] }),
      ],
    }));
    expect(g.noDatum.map((t) => t.id)).toEqual([1]);
    expect(g.datumReferenced.map((t) => t.id)).toEqual([2]);
  });

  it('splits dimensions into sizes and value-less reference dims', () => {
    const g = groupPmi(data({
      dimensions: [
        dim({ id: 1, value: 10, upper_tolerance: 0.1, lower_tolerance: -0.1 }),
        dim({ id: 2, value: 0 }),
      ],
    }));
    expect(g.sizes.map((d) => d.id)).toEqual([1]);
    expect(g.refDims.map((d) => d.id)).toEqual([2]);
  });

  it('handles null/empty pmi', () => {
    expect(groupPmi(null).patterns).toHaveLength(0);
    expect(groupPmi(data({})).sizes).toHaveLength(0);
  });
});
