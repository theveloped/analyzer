import { describe, expect, it } from 'vitest';
import {
  addEntity, deleteEntity, emptyDoc, nextDatumLetter, nextId,
  newDimension, newTolerance, setDatumFrame, toggleFace, updateEntity,
} from './pmiModel';

describe('id + datum-letter allocation', () => {
  it('nextId is unique across all three families', () => {
    const pmi = {
      schema: 4,
      tolerances: [{ ...newTolerance(emptyDoc(), 'Flatness'), id: 3 }],
      dimensions: [{ ...newDimension(emptyDoc(), 'Size_Diameter'), id: 7 }],
      datums: [{ id: 5, kind: 'datum' as const, name: 'A', face_ids: [], edge_ids: [] }],
    };
    expect(nextId(pmi)).toBe(8);
  });

  it('nextDatumLetter skips used letters and the I/O/Q convention', () => {
    expect(nextDatumLetter(emptyDoc())).toBe('A');
    const withAB = {
      ...emptyDoc(),
      datums: [
        { id: 1, kind: 'datum' as const, name: 'A', face_ids: [], edge_ids: [] },
        { id: 2, kind: 'datum' as const, name: 'B', face_ids: [], edge_ids: [] },
      ],
    };
    expect(nextDatumLetter(withAB)).toBe('C');
  });

  it('counts letters referenced only by a control frame as used', () => {
    const pmi = {
      ...emptyDoc(),
      tolerances: [{ ...newTolerance(emptyDoc(), 'Position'), id: 1,
        datum_names: ['A'], datum_refs: [{ name: 'A', position: 1, modifiers: [] }] }],
    };
    expect(nextDatumLetter(pmi)).toBe('B');
  });
});

describe('entity factories', () => {
  it('a Position tolerance defaults to a Ø zone', () => {
    expect(newTolerance(emptyDoc(), 'Position').type_of_value).toBe('Diameter');
  });
  it('a Flatness tolerance has no datum requirement and no Ø zone', () => {
    expect(newTolerance(emptyDoc(), 'Flatness').type_of_value).toBeNull();
  });
  it('an angular dimension kind sets the angular flag', () => {
    expect(newDimension(emptyDoc(), 'Location_Angular').angular).toBe(true);
    expect(newDimension(emptyDoc(), 'Size_Diameter').angular).toBe(false);
  });
});

describe('add / update / delete', () => {
  it('toggleFace adds then removes a face from the geometry set (sorted)', () => {
    let pmi = addEntity(emptyDoc(), 'tolerances', { ...newTolerance(emptyDoc(), 'Flatness'), id: 1 });
    pmi = toggleFace(pmi, 'tolerances', 1, 5);
    pmi = toggleFace(pmi, 'tolerances', 1, 2);
    expect(pmi.tolerances[0].face_ids).toEqual([2, 5]);
    pmi = toggleFace(pmi, 'tolerances', 1, 5);
    expect(pmi.tolerances[0].face_ids).toEqual([2]);
  });

  it('toggleFace targets a dimension secondary reference', () => {
    let pmi = addEntity(emptyDoc(), 'dimensions', { ...newDimension(emptyDoc(), 'Location_LinearDistance'), id: 1 });
    pmi = toggleFace(pmi, 'dimensions', 1, 9, 'secondary_face_ids');
    expect(pmi.dimensions[0].secondary_face_ids).toEqual([9]);
    expect(pmi.dimensions[0].face_ids).toEqual([]);
  });

  it('deleting a datum strips its letter from referencing frames', () => {
    let pmi = addEntity(emptyDoc(), 'datums', { id: 1, kind: 'datum', name: 'A', face_ids: [0], edge_ids: [] });
    pmi = addEntity(pmi, 'datums', { id: 2, kind: 'datum', name: 'B', face_ids: [1], edge_ids: [] });
    pmi = addEntity(pmi, 'tolerances', { ...newTolerance(pmi, 'Position'), id: 3 });
    pmi = setDatumFrame(pmi, 3, ['A', 'B']);
    expect(pmi.tolerances[0].datum_names).toEqual(['A', 'B']);
    pmi = deleteEntity(pmi, 'datums', 1); // delete A
    expect(pmi.datums.map((d) => d.name)).toEqual(['B']);
    expect(pmi.tolerances[0].datum_names).toEqual(['B']);
    expect(pmi.tolerances[0].datum_refs.map((r) => r.name)).toEqual(['B']);
  });

  it('setDatumFrame preserves per-datum modifiers and re-numbers positions', () => {
    let pmi = addEntity(emptyDoc(), 'tolerances', {
      ...newTolerance(emptyDoc(), 'Position'), id: 1,
      datum_refs: [{ name: 'A', position: 1, modifiers: ['M'] }], datum_names: ['A'],
    });
    pmi = setDatumFrame(pmi, 1, ['B', 'A']);
    const refs = pmi.tolerances[0].datum_refs;
    expect(refs.map((r) => r.name)).toEqual(['B', 'A']);
    expect(refs.map((r) => r.position)).toEqual([1, 2]);
    expect(refs.find((r) => r.name === 'A')?.modifiers).toEqual(['M']); // preserved
  });

  it('updateEntity patches immutably', () => {
    const pmi = addEntity(emptyDoc(), 'tolerances', { ...newTolerance(emptyDoc(), 'Flatness'), id: 1 });
    const next = updateEntity(pmi, 'tolerances', 1, { value: 0.05 });
    expect(next.tolerances[0].value).toBe(0.05);
    expect(pmi.tolerances[0].value).toBe(0); // original untouched
  });
});
