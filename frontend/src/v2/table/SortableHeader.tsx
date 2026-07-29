import { ArrowUpDown, ChevronDown, ChevronUp } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { TableHeader } from '../../catalyst/table';
import type { SortState } from './useTableControls';

/**
 * Ported verbatim from the Wefabricate Partner Portal
 * (wf-api/src/components/SortableHeader.tsx) — the vendored Catalyst table is
 * byte-identical, so this keeps both apps' tables behaving the same: click the
 * label to filter the column inline, a blue dot marks an active filter, and
 * the icon cycles the sort asc -> desc -> none.
 */

interface SortableHeaderProps {
  column: string;
  label: string;
  sort: SortState | null;
  onToggleSort: (column: string) => void;
  filter?: string;
  onSetFilter?: (column: string, value: string) => void;
  title?: string;
}

export const SortableHeader: React.FC<SortableHeaderProps> = ({
  column, label, sort, onToggleSort, filter, onSetFilter, title,
}) => {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const isActive = sort?.column === column;
  const hasFilter = !!filter;

  useEffect(() => {
    if (editing && inputRef.current) inputRef.current.focus();
  }, [editing]);

  function startEdit() {
    setDraft(filter || '');
    setEditing(true);
  }

  function commitFilter() {
    setEditing(false);
    onSetFilter?.(column, draft);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') commitFilter();
    if (e.key === 'Escape') setEditing(false);
  }

  const SortIcon = isActive
    ? sort!.order === 'asc' ? ChevronUp : ChevronDown
    : ArrowUpDown;

  return (
    <TableHeader>
      <div className="flex items-center gap-1" title={title}>
        <div className="relative min-w-0 flex-1">
          {/* label always rendered, for a stable column width */}
          <button
            type="button"
            className={`flex items-center gap-1 whitespace-nowrap hover:text-zinc-900 dark:hover:text-white ${editing ? 'invisible' : ''}`}
            onClick={startEdit}
          >
            {hasFilter && (
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-blue-500" />
            )}
            {label}
          </button>
          {editing && (
            <input
              ref={inputRef}
              className="absolute inset-0 w-full rounded border border-zinc-300 bg-white px-1.5 py-0.5 text-sm dark:border-zinc-600 dark:bg-zinc-800"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitFilter}
              onKeyDown={handleKeyDown}
            />
          )}
        </div>
        <button
          type="button"
          className="ml-auto shrink-0 p-0.5 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
          onClick={() => onToggleSort(column)}
          aria-label={`Sort by ${label}`}
        >
          <SortIcon className="h-3.5 w-3.5" />
        </button>
      </div>
    </TableHeader>
  );
};
