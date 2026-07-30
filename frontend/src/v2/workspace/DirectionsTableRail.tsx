import clsx from 'clsx';
import { X } from 'lucide-react';
import { Button } from '../../catalyst/button';
import {
  Table, TableBody, TableCell, TableHead, TableRow,
} from '../../catalyst/table';
import { useEffect, useRef, useSyncExternalStore } from 'react';
import {
  currentDirections, directionsVersion,
  type GeneratedDir, type SourceKind,
} from '../../processes/directions/build';
import { PROVENANCE_LABELS } from '../../processes/directions/modes';
import { provenanceCss } from '../../processes/directions/state';
import { useStore } from '../../state/store';
import {
  aggregateFor, aggregateVersion, showCoverage, subscribeAggregate,
} from '../decisions/aggregate';
import {
  buildRows, columnsFor, openCell, toolColumns,
  type Cell, type DirectionRow,
} from '../decisions/columns';
import { selectCandidates, useSelection } from '../decisions/directions';
import {
  closeStudy, showArrows, showArrowsLens, useActiveStudy,
} from '../studies';
import { SortableHeader } from '../table/SortableHeader';
import { applyControls, useTableControls, type CellValue } from '../table/useTableControls';
import { useBusy } from './run';
import { focusCls, hintCls } from '../components/styles';

const pct = (v: number) => `${(100 * v).toFixed(1)}%`;

/** One cell: a cached number you can click to SEE, or the job that would
 * produce it. Either way the click ends with that thing painted in the
 * viewer — the number and the picture are the same answer.
 *
 * Only the VALUE ITSELF (or the compute button) takes the click; the rest of
 * the cell falls through to the row, so selecting stays the default gesture
 * and hitting a number is the deliberate one. */
function ValueCell({ cell, onOpen, busy, title, dim }: {
  cell: Cell; onOpen: () => void; busy: boolean; title?: string; dim?: boolean;
}) {
  const stop = (e: React.MouseEvent) => { e.stopPropagation(); onOpen(); };

  if (cell.state === 'value') {
    return (
      <TableCell className={clsx('tabular-nums', dim && 'text-zinc-400')}>
        <button
          type="button"
          onClick={stop}
          title={title ?? 'Show this in the viewer'}
          className={clsx(focusCls,
            'underline-offset-2 hover:text-blue-600 hover:underline dark:hover:text-blue-400')}
        >
          {pct(cell.value!)}
        </button>
      </TableCell>
    );
  }
  if (cell.state === 'blocked') {
    return (
      <TableCell className="text-zinc-300 dark:text-zinc-600" title={cell.note}>
        —
      </TableCell>
    );
  }
  return (
    <TableCell>
      <button
        type="button"
        disabled={busy}
        onClick={stop}
        title={title ?? 'Not computed — click to compute it and show it'}
        className={clsx(focusCls,
          'rounded border border-dashed border-zinc-300 px-1.5 text-xs/5 text-zinc-400 hover:border-blue-500 hover:text-blue-600 disabled:opacity-50 dark:border-zinc-600 dark:hover:border-blue-400 dark:hover:text-blue-400')}
      >
        compute
      </button>
    </TableCell>
  );
}

export function DirectionsTableRail() {
  const study = useActiveStudy();
  const manifest = useStore((s) => s.manifest);
  const busy = useBusy();
  const controls = useTableControls();

  // the candidate set is the live one the directions lens builds — re-read it
  // whenever a repaint regenerates it, so editing candidates updates the table
  useSyncExternalStore(
    (onChange) => {
      directionsVersion.listeners.add(onChange);
      return () => { directionsVersion.listeners.delete(onChange); };
    },
    () => directionsVersion.n,
  );

  const candidates: GeneratedDir[] = currentDirections;
  const selected = useSelection();
  const tools = toolColumns(manifest);
  const columns = columnsFor(tools);
  const rows = buildRows(manifest, candidates, selected, tools);

  const byKey = new Map(columns.map((c) => [c.key, c]));
  const valueOf = (row: DirectionRow, column: string): CellValue => {
    if (column === 'label') return row.label;
    if (column === 'source') return row.source;
    const cell = byKey.get(column)?.cell(row);
    return cell?.state === 'value' ? cell.value : null;
  };
  const shown = applyControls(rows, controls, valueOf);

  // the arrows in the viewer ARE the selection — that is the whole point of
  // opening the study on a bare model
  const selectedKey = selected.join('|');
  useEffect(() => { showArrows(selected); }, [selectedKey]);

  const selectedRows = rows.filter((r) => r.selected);
  useSyncExternalStore(subscribeAggregate, () => aggregateVersion.n);
  const aggregate = aggregateFor(manifest, selectedRows, tools);

  // a shift-click is the browser's "extend the text selection" gesture too;
  // preventing the default on mousedown stops it dragging a highlight across
  // the table without touching the click that follows
  function onRowMouseDown(event: React.MouseEvent) {
    if (event.shiftKey) event.preventDefault();
  }

  // shift extends from the last plainly-clicked row, over what is CURRENTLY
  // shown — extending through rows a filter has hidden would select things
  // the user cannot see
  const anchor = useRef<string | null>(null);
  function onRowClick(row: DirectionRow, event: React.MouseEvent) {
    const order = shown.map((r) => r.key);
    let next: string[];
    if (event.shiftKey && anchor.current && order.includes(anchor.current)) {
      const a = order.indexOf(anchor.current);
      const b = order.indexOf(row.key);
      const [lo, hi] = a <= b ? [a, b] : [b, a];
      next = order.slice(lo, hi + 1);
    } else if (event.ctrlKey || event.metaKey) {
      next = selected.includes(row.key)
        ? selected.filter((k) => k !== row.key)
        : [...selected, row.key];
      anchor.current = row.key;
    } else {
      // clicking the only selected row again clears it — a way back to the
      // bare model without hunting for a button
      next = selected.length === 1 && selected[0] === row.key ? [] : [row.key];
      anchor.current = row.key;
    }
    selectCandidates(candidates, next);
    showArrowsLens();
  }

  return (
    <div className="flex min-h-full flex-col gap-3 p-4 [--gutter:--spacing(2)]">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">
            {study?.label ?? 'Study'}
          </h2>
          <p className={clsx('mt-1', hintCls)}>
            {study?.blurb}
          </p>
        </div>
        <Button plain onClick={closeStudy} aria-label="Close the study">
          <X data-slot="icon" />
        </Button>
      </div>

      {!candidates.length ? (
        <p className={hintCls}>
          No candidate directions yet — add some in the candidate directions
          lens. Nothing is computed until you ask for it.
        </p>
      ) : (
        <>
          {controls.hasActiveFilters && (
            <div>
              <Button plain onClick={controls.clearAll}>Clear filters</Button>
            </div>
          )}

          {/* no `striped`: its even:bg-* is a pseudo-class, so it outranks the
              row's own background and would fight the selection highlight.
              select-none keeps ctrl/shift clicks from dragging a text range
              across the table. */}
          <Table dense grid className="select-none text-xs">
            <TableHead>
              <TableRow>
                <SortableHeader
                  column="label" label="Direction" sort={controls.sort}
                  onToggleSort={controls.toggleSort}
                  filter={controls.filters.label} onSetFilter={controls.setFilter}
                />
                <SortableHeader
                  column="source" label="From" sort={controls.sort}
                  onToggleSort={controls.toggleSort}
                  filter={controls.filters.source} onSetFilter={controls.setFilter}
                />
                {columns.map((column) => (
                  <SortableHeader
                    key={column.key} column={column.key} label={column.label}
                    sort={controls.sort} onToggleSort={controls.toggleSort}
                    filter={controls.filters[column.key]}
                    onSetFilter={controls.setFilter}
                    title={column.title}
                  />
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {shown.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={2 + columns.length} className="py-6 text-center text-zinc-500">
                    No candidate matches the filters.
                  </TableCell>
                </TableRow>
              ) : shown.map((row) => (
                <TableRow
                  key={row.key}
                  onMouseDown={onRowMouseDown}
                  onClick={(e) => onRowClick(row, e)}
                  title="Click to show this direction · ctrl to add · shift for a range"
                  className={clsx(
                    'cursor-pointer',
                    // Catalyst applies its row hover only to href rows, so the
                    // same tokens are spelled out here; selection borrows the
                    // PMI rail's blue so the two panels read alike
                    row.selected
                      ? 'bg-blue-500/10 hover:bg-blue-500/15 dark:bg-blue-500/15 dark:hover:bg-blue-500/20'
                      : 'hover:bg-zinc-950/5 dark:hover:bg-white/5')}
                >
                  <TableCell className={clsx(
                    // the PMI rail marks its active row with a blue edge; a
                    // <tr> cannot carry one reliably under border-collapse,
                    // so the first cell does
                    row.selected
                      && 'shadow-[inset_2px_0_0_0_var(--color-blue-500)]')}
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className="size-2.5 shrink-0 rounded-full ring-1 ring-inset ring-zinc-950/10 dark:ring-white/20"
                        style={{ backgroundColor: provenanceCss(row.source as SourceKind) }}
                      />
                      <span className={clsx('font-medium',
                        row.selected && 'text-blue-700 dark:text-blue-300')}
                      >
                        {row.label}
                      </span>
                    </div>
                    <div className="font-mono text-[10px]/4 text-zinc-400">
                      {row.vector.map((c) => c.toFixed(2)).join(', ')}
                    </div>
                  </TableCell>
                  <TableCell className="text-zinc-500 dark:text-zinc-400">
                    {PROVENANCE_LABELS[row.source as SourceKind] ?? row.source}
                  </TableCell>
                  {columns.map((column) => (
                    <ValueCell
                      key={column.key}
                      cell={column.cell(row)}
                      busy={busy}
                      dim={column.dim?.(row)}
                      title={column.cellTitle?.(row)}
                      onOpen={() => openCell(row, column, candidates, tools)}
                    />
                  ))}
                </TableRow>
              ))}
            </TableBody>
            {aggregate && (
              <tfoot className="border-t-2 border-zinc-950/10 dark:border-white/15">
                <TableRow>
                  <TableCell className="font-medium">
                    Together ({selectedRows.length})
                  </TableCell>
                  <TableCell className="text-zinc-500 dark:text-zinc-400">
                    covered by any
                  </TableCell>
                  {columns.map((column) => {
                    const cell = aggregate[column.key];
                    return (
                      <TableCell key={column.key} className="tabular-nums font-medium">
                        {cell?.value == null ? (
                          <span
                            className="text-zinc-400"
                            title={cell?.missing
                              ? `${cell.missing} selected direction(s) have not been computed here`
                              : undefined}
                          >
                            —
                          </span>
                        ) : (
                          <>
                            <button
                              type="button"
                              onClick={() => showCoverage(selectedRows, column)}
                              title="Show what these directions cover together"
                              className={clsx(focusCls,
                                'underline-offset-2 hover:text-blue-600 hover:underline dark:hover:text-blue-400')}
                            >
                              {pct(cell.value)}
                            </button>
                            {cell.missing > 0 && (
                              <span className="ml-1 text-zinc-400"
                                title={`${cell.missing} selected direction(s) not computed — not in this total`}
                              >
                                *
                              </span>
                            )}
                          </>
                        )}
                      </TableCell>
                    );
                  })}
                </TableRow>
              </tfoot>
            )}
          </Table>

          <p className={clsx('mt-auto', hintCls)}>
            {selected.length === 0
              ? `${rows.length} candidates. Click a row to show it; click a value to paint it, or a blank cell to compute it.`
              : selected.length === 1
                ? `1 of ${rows.length} selected — recorded on the plan as decisions.directions.`
                : `${selected.length} of ${rows.length} selected — click a total to see what they cover together.`}
          </p>
        </>
      )}
    </div>
  );
}
