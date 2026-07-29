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
import { cellOf, type DirectionRow, type ToolSpec } from './columns';

export interface AggregateCell {
  /** Area share covered by at least one selected direction. */
  value: number | null;
  /** Selected rows whose own cell is not computed, so cannot contribute. */
  missing: number;
}

export type AggregateRow = Record<string, AggregateCell>;

/** How a column's field encodes "covered". The role field is a category
 * (0 off-axis, 1 compatible, 2 swept), everything else a 0/1 mask. */
export type CoverageRule = 'nonzero' | 'ge1' | 'eq2';

export function coverageRule(column: string): CoverageRule {
  if (column === 'compatible') return 'ge1';
  if (column === 'swept') return 'eq2';
  return 'nonzero';
}

/** The field a cell's value was read from, so the union can re-read the mask
 * behind it. Mirrors the ids the manifest publishes. */
export function fieldIdFor(row: DirectionRow, column: string): string | null {
  const cell = cellOf(row, column);
  if (cell.state !== 'value' || !cell.lens) return null;
  const p = cell.lens.params as Record<string, any>;
  if (column === 'accessible') return `accessibility.${row.index}`;
  if (column === 'compatible' || column === 'swept') {
    return `results.cnc.turning_scan.${p.scanHash}.axis_role_${p.scanAxis}`;
  }
  if (column.startsWith('tool')) {
    return `results.cnc.reach_study.${p.reachHash}`
      + `.reach_${p.reachDirection}_${p.reachTool}`;
  }
  return null;
}

export function covered(rule: CoverageRule, v: number): boolean {
  if (rule === 'ge1') return v >= 1;
  if (rule === 'eq2') return v === 2;
  return v !== 0;
}

const covers = (column: string, v: number) =>
  covered(coverageRule(column), v);

async function fetchMask(
  manifest: Manifest, id: string,
): Promise<Uint8Array | null> {
  const desc: FieldDescriptor | undefined = manifest.fields.find((f) => f.id === id);
  if (!desc) return null;
  return await fetchField(desc) as Uint8Array;
}

async function unionShare(
  manifest: Manifest, rows: DirectionRow[], column: string,
  areas: Float64Array, total: number,
): Promise<AggregateCell> {
  const ids = rows.map((row) => fieldIdFor(row, column));
  const usable = ids.filter((id): id is string => !!id);
  if (!usable.length) return { value: null, missing: rows.length };

  const union = new Uint8Array(areas.length);
  for (const id of usable) {
    const mask = await fetchMask(manifest, id);
    if (!mask) continue;
    for (let f = 0; f < union.length; f++) {
      if (covers(column, mask[f])) union[f] = 1;
    }
  }
  let covered = 0;
  for (let f = 0; f < union.length; f++) if (union[f]) covered += areas[f];
  return { value: covered / total, missing: ids.length - usable.length };
}

/** Column labels for the coverage lens's legend. */
const COVERAGE_LABEL: Record<string, string> = {
  accessible: 'visible',
  compatible: 'revolution-compatible',
  swept: 'swept',
};

/** Paint the union behind an aggregate cell — the same fields and the same
 * rule the total was computed from, so the picture is that number. */
export function showCoverage(rows: DirectionRow[], column: string): void {
  const ids = rows
    .map((row) => fieldIdFor(row, column))
    .filter((id): id is string => !!id);
  if (!ids.length) return;
  const store = useStore.getState();
  store.setViewerParam('cnc', 'coverageFields', ids);
  store.setViewerParam('cnc', 'coverageRule', coverageRule(column));
  store.setViewerParam('cnc', 'coverageLabel',
    COVERAGE_LABEL[column] ?? 'reachable');
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
function keyFor(rows: DirectionRow[], columns: string[]): string {
  return rows.map((row) => row.key).join(',') + '|'
    + columns.map((c) => rows.map((r) => fieldIdFor(r, c) ?? '-').join('~')).join('|');
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
  const columns = ['accessible', 'compatible', 'swept',
    ...tools.map((_, t) => `tool${t}`)];
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
        row[column] = await unionShare(manifest, selected, column, areas,
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
