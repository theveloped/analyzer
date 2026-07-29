import { useCallback, useState } from 'react';

/**
 * Client-side sort + per-column filter, keeping the interaction contract of
 * the Wefabricate Partner Portal's `useTableControls` (wf-api) so tables read
 * the same in both apps: click a column label to filter it inline, and cycle
 * its sort asc -> desc -> none.
 *
 * The wf-api original builds a Stripe-style query string and pages against
 * the API. Here the rows are a bounded in-memory set (one per candidate
 * direction), so that half is replaced by `applyControls` — same API surface,
 * no server round trip and no pagination.
 */

export interface SortState {
  column: string;
  order: 'asc' | 'desc';
}

export interface TableControls {
  sort: SortState | null;
  toggleSort: (column: string) => void;
  filters: Record<string, string>;
  setFilter: (column: string, value: string) => void;
  clearFilter: (column: string) => void;
  clearAll: () => void;
  hasActiveFilters: boolean;
}

export function useTableControls(): TableControls {
  const [sort, setSort] = useState<SortState | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});

  const toggleSort = useCallback((column: string) => {
    setSort((prev) => {
      if (!prev || prev.column !== column) return { column, order: 'asc' };
      if (prev.order === 'asc') return { column, order: 'desc' };
      return null; // cycle: asc -> desc -> none
    });
  }, []);

  const setFilter = useCallback((column: string, value: string) => {
    setFilters((prev) => {
      if (!value.trim()) {
        const next = { ...prev };
        delete next[column];
        return next;
      }
      return { ...prev, [column]: value };
    });
  }, []);

  const clearFilter = useCallback((column: string) => {
    setFilters((prev) => {
      const next = { ...prev };
      delete next[column];
      return next;
    });
  }, []);

  const clearAll = useCallback(() => {
    setSort(null);
    setFilters({});
  }, []);

  const hasActiveFilters = sort !== null || Object.keys(filters).length > 0;

  return {
    sort, toggleSort, filters, setFilter, clearFilter, clearAll,
    hasActiveFilters,
  };
}

/** A cell's value for filtering and sorting: numbers compare numerically,
 * everything else as lowercase text. `null` sorts last either way. */
export type CellValue = number | string | null;

function matches(value: CellValue, raw: string): boolean {
  const query = raw.trim();
  if (!query) return true;
  if (typeof value === 'number') {
    // numeric comparisons mirror the wf-api date operators: >, >=, <, <=
    const op = query.match(/^(>=|<=|>|<)\s*(.+)$/);
    if (op) {
      const limit = parseFloat(op[2]);
      if (!isFinite(limit)) return true;
      if (op[1] === '>') return value > limit;
      if (op[1] === '>=') return value >= limit;
      if (op[1] === '<') return value < limit;
      return value <= limit;
    }
  }
  return String(value ?? '').toLowerCase().includes(query.toLowerCase());
}

function compare(a: CellValue, b: CellValue): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1; // blanks last, whichever direction
  if (b === null) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b));
}

/** Filter then sort `rows` using `valueOf(row, column)` as the accessor. */
export function applyControls<T>(
  rows: T[],
  controls: TableControls,
  valueOf: (row: T, column: string) => CellValue,
): T[] {
  const filtered = rows.filter((row) => Object.entries(controls.filters)
    .every(([column, query]) => matches(valueOf(row, column), query)));
  if (!controls.sort) return filtered;
  const { column, order } = controls.sort;
  const sign = order === 'asc' ? 1 : -1;
  // blanks stay last in both directions, so the sign is applied to the
  // comparison of present values only
  return [...filtered].sort((a, b) => {
    const va = valueOf(a, column);
    const vb = valueOf(b, column);
    if (va === null || vb === null) return compare(va, vb);
    return sign * compare(va, vb);
  });
}
