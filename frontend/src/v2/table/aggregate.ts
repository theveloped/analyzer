// The study table's footer row: what the SELECTED directions cover together.
//
// One meaning in every column — the area reached by AT LEAST ONE of them —
// because a row that mixed unions with maxima would be unreadable. Turnability
// unions too: a part turned about two axes in two ops is unusual but not
// impossible, and this view exists to inform a judgement rather than to make
// one.
//
// The per-pair numbers in a result's stats cannot answer this (they overlap),
// so it genuinely needs the masks. It is therefore async, and memoized on the
// selection plus the result hashes that fed it — the same
// content-addressed-key discipline the check evaluators use.

import type { FieldDescriptor, Manifest } from '../../api/types';
import { fetchField } from '../../fields/fields';
import { faceAreas } from '../../processes/cnc/reach';
import { useStore } from '../../state/store';
import { meshArrays } from '../../viewer/controller';
import { covered } from '../../processes/cnc/coverage';
import {
  columnsFor, type ColumnDef, type DirectionRow, type ToolSpec,
} from './columns';

export interface AggregateCell {
  /** Area share covered by at least one selected direction. */
  value: number | null;
  /** Selected rows whose own cell is not computed, so cannot contribute. */
  missing: number;
}

export type AggregateRow = Record<string, AggregateCell>;

async function fetchMask(
  manifest: Manifest, id: string,
): Promise<Uint8Array | null> {
  const desc: FieldDescriptor | undefined = manifest.fields.find((f) => f.id === id);
  if (!desc) return null;
  return await fetchField(desc) as Uint8Array;
}

async function unionShare(
  manifest: Manifest, rows: DirectionRow[], column: ColumnDef,
  areas: Float64Array, total: number,
): Promise<AggregateCell> {
  const ids = rows.map((row) => column.fieldId(row));
  const usable = ids.filter((id): id is string => !!id);
  if (!usable.length) return { value: null, missing: rows.length };

  const union = new Uint8Array(areas.length);
  for (const id of usable) {
    const mask = await fetchMask(manifest, id);
    if (!mask) continue;
    for (let f = 0; f < union.length; f++) {
      if (covered(column.coverage, mask[f])) union[f] = 1;
    }
  }
  let coveredArea = 0;
  for (let f = 0; f < union.length; f++) if (union[f]) coveredArea += areas[f];
  return { value: coveredArea / total, missing: ids.length - usable.length };
}

/** Paint the union behind an aggregate cell — the same fields and the same
 * rule the total was computed from, so the picture is that number. */
export function showCoverage(rows: DirectionRow[], column: ColumnDef): void {
  const ids = rows
    .map((row) => column.fieldId(row))
    .filter((id): id is string => !!id);
  if (!ids.length) return;
  const store = useStore.getState();
  store.setViewerParam('cnc', 'coverageFields', ids);
  store.setViewerParam('cnc', 'coverageRule', column.coverage);
  store.setViewerParam('cnc', 'coverageLabel', column.coverageLabel);
  store.set({ processId: 'cnc', modeId: 'coverage' });
}

const cache = new Map<string, AggregateRow>();
const pending = new Set<string>();
const listeners = new Set<() => void>();

/** Bumped when a computation lands, so subscribers re-read the memo. */
export const aggregateVersion = { n: 0 };

export function subscribeAggregate(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** Cache key: the selection and every field the union would read. Two
 * selections that resolve to the same fields share an answer; recomputing a
 * cell changes its hash and so invalidates the row. */
function keyFor(rows: DirectionRow[], columns: ColumnDef[]): string {
  return rows.map((row) => row.key).join(',') + '|'
    + columns.map((c) => rows.map((r) => c.fieldId(r) ?? '-').join('~')).join('|');
}

/**
 * The aggregate row for a selection, or null while it is being computed.
 * Kicks the computation off once per key and notifies subscribers when it
 * lands — the table re-reads and renders it.
 */
export function aggregateFor(
  manifest: Manifest | null, selected: DirectionRow[], tools: ToolSpec[],
): AggregateRow | null {
  if (!manifest || !selected.length) return null;
  const columns = columnsFor(tools);
  const key = keyFor(selected, columns);
  const hit = cache.get(key);
  if (hit) return hit;
  if (pending.has(key)) return null;

  const mesh = meshArrays();
  const faceCount = manifest.part.counts?.faces;
  if (!mesh || !faceCount) return null;

  pending.add(key);
  void (async () => {
    try {
      const areas = faceAreas({ ...mesh, faceCount } as any);
      let total = 0;
      for (let f = 0; f < areas.length; f++) total += areas[f];
      const row: AggregateRow = {};
      for (const column of columns) {
        row[column.key] = await unionShare(manifest, selected, column, areas,
          total || 1);
      }
      cache.set(key, row);
    } catch (err) {
      console.warn('aggregate failed:', err);
      cache.set(key, {});
    } finally {
      pending.delete(key);
      aggregateVersion.n += 1;
      for (const listener of listeners) listener();
    }
  })();
  return null;
}
