import type {
  PmiData, PmiDatum, PmiDatumRef, PmiDimension, PmiTolerance,
} from '../../api/types';
import { CHARACTERISTIC_BY_TYPE, DIMENSION_KIND_BY_TYPE } from './pmiVocab';

/**
 * Pure, immutable reducers over a pmi.json document for the editor. The unit of
 * editing is an *entity* (tolerance / dimension / datum) — a tolerance feature —
 * whose `face_ids` are the geometry it maps to (a set: one hole, a pattern of
 * many, a profile's surfaces, a feature-of-size's two faces). Nothing here is
 * face-first; faces are only toggled into an entity's geometry set.
 */

export type EntityKey = 'tolerances' | 'dimensions' | 'datums';
export type PmiEntity = PmiTolerance | PmiDimension | PmiDatum;

/** Datum letters skip I, O, Q by GD&T convention. */
const DATUM_LETTERS = 'ABCDEFGHJKLMNPRSTUVWXYZ'.split('');

export function emptyDoc(): PmiData {
  return { schema: 4, dimensions: [], tolerances: [], datums: [] };
}

/** The next free entity id (unique across all three families). */
export function nextId(pmi: PmiData): number {
  let max = 0;
  for (const key of ['tolerances', 'dimensions', 'datums'] as EntityKey[]) {
    for (const e of pmi[key]) if (e.id > max) max = e.id;
  }
  return max + 1;
}

/** The first datum letter not already in use (by a datum entity or referenced
 * by a control frame), or '' if the 23 conventional letters are exhausted. */
export function nextDatumLetter(pmi: PmiData): string {
  const used = new Set<string>();
  for (const d of pmi.datums) if (d.name) used.add(d.name);
  for (const t of pmi.tolerances) for (const n of t.datum_names) used.add(n);
  return DATUM_LETTERS.find((l) => !used.has(l)) ?? '';
}

export function newTolerance(pmi: PmiData, type: string): PmiTolerance {
  const spec = CHARACTERISTIC_BY_TYPE[type];
  return {
    id: nextId(pmi), kind: 'tolerance', name: null, type,
    value: 0, type_of_value: spec?.group === 'location' ? 'Diameter' : null,
    modifiers: [], material_modifier: null, zone_modifier: null,
    zone_value: null, max_value: null, datum_refs: [], datum_names: [],
    face_ids: [], edge_ids: [],
  };
}

export function newDimension(pmi: PmiData, type: string): PmiDimension {
  const spec = DIMENSION_KIND_BY_TYPE[type];
  return {
    id: nextId(pmi), kind: 'dimension', type, value: 0,
    upper_tolerance: null, lower_tolerance: null, qualifier: null,
    modifiers: [], angular: !!spec?.angular,
    face_ids: [], secondary_face_ids: [], edge_ids: [],
  };
}

export function newDatum(pmi: PmiData): PmiDatum {
  return {
    id: nextId(pmi), kind: 'datum', name: nextDatumLetter(pmi),
    face_ids: [], edge_ids: [],
  };
}

function replace<T extends PmiEntity>(list: T[], id: number, patch: Partial<T>): T[] {
  return list.map((e) => (e.id === id ? { ...e, ...patch } : e));
}

export function addEntity(pmi: PmiData, key: EntityKey, entity: PmiEntity): PmiData {
  return { ...pmi, [key]: [...pmi[key], entity as never] };
}

export function updateEntity(
  pmi: PmiData, key: EntityKey, id: number, patch: Partial<PmiEntity>,
): PmiData {
  return { ...pmi, [key]: replace(pmi[key] as PmiEntity[], id, patch) as never };
}

/** Delete an entity. Deleting a datum also strips its letter from every control
 * frame that referenced it (so no frame points at a datum that no longer
 * exists) — the reference frame is a property of the tolerance feature. */
export function deleteEntity(pmi: PmiData, key: EntityKey, id: number): PmiData {
  let next: PmiData = { ...pmi, [key]: pmi[key].filter((e) => e.id !== id) as never };
  if (key === 'datums') {
    const gone = pmi.datums.find((d) => d.id === id)?.name;
    if (gone) {
      next = {
        ...next,
        tolerances: next.tolerances.map((t) => t.datum_names.includes(gone)
          ? {
              ...t,
              datum_names: t.datum_names.filter((n) => n !== gone),
              datum_refs: t.datum_refs.filter((r) => r.name !== gone),
            }
          : t),
      };
    }
  }
  return next;
}

/** Toggle one BREP face id in an entity's geometry set (`face_ids` by default,
 * or `secondary_face_ids` for a dimension's second reference). */
export function toggleFace(
  pmi: PmiData, key: EntityKey, id: number, faceId: number,
  field: 'face_ids' | 'secondary_face_ids' = 'face_ids',
): PmiData {
  const list = pmi[key] as PmiEntity[];
  const entity = list.find((e) => e.id === id);
  if (!entity) return pmi;
  const current: number[] = (entity as any)[field] ?? [];
  const nextFaces = current.includes(faceId)
    ? current.filter((f) => f !== faceId)
    : [...current, faceId].sort((a, b) => a - b);
  return updateEntity(pmi, key, id, { [field]: nextFaces } as Partial<PmiEntity>);
}

/** Set a control frame's datum reference frame from an ordered list of letters,
 * keeping any per-datum material modifiers already attached. */
export function setDatumFrame(
  pmi: PmiData, id: number, letters: string[],
): PmiData {
  const tol = pmi.tolerances.find((t) => t.id === id);
  if (!tol) return pmi;
  const prev = new Map(tol.datum_refs.map((r) => [r.name, r]));
  const datum_refs: PmiDatumRef[] = letters.map((name, i) => ({
    name,
    position: i + 1,
    modifiers: prev.get(name)?.modifiers ?? [],
  }));
  return updateEntity(pmi, 'tolerances', id, {
    datum_refs, datum_names: [...letters],
  });
}
