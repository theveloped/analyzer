import { Popover, PopoverButton, PopoverPanel } from '@headlessui/react';
import clsx from 'clsx';
import { Crosshair, MoreHorizontal, Search, Wrench } from 'lucide-react';
import { useState } from 'react';
import { LENS_CATEGORIES, lensesIn, PINNED_LENSES, type Lens } from '../lenses';
import { useV2 } from '../store';
import {
  activateDirections, selectLens, useActiveLens, useCheckActive,
  useDirectionsActive,
} from './hooks';

const btnCls = 'flex size-8 items-center justify-center rounded-lg transition';
const activeCls = 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900';
const idleCls = 'text-zinc-500 hover:bg-zinc-950/5 hover:text-zinc-950 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white';

function LensButton({ lens, isActive }: { lens: Lens; isActive: boolean }) {
  const Icon = lens.icon;
  return (
    <button
      type="button"
      onClick={() => selectLens(lens)}
      title={lens.blurb ? `${lens.label} — ${lens.blurb}` : lens.label}
      aria-pressed={isActive}
      className={clsx(btnCls, isActive ? activeCls : idleCls)}
    >
      <Icon className="size-4" />
    </button>
  );
}

/** Grouped, searchable menu over every inspection lens ("all the tools"). */
function LensMenu({ activeLens }: { activeLens: Lens | null }) {
  const advanced = useV2((s) => s.advanced);
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const matches = (l: Lens) =>
    !q || l.label.toLowerCase().includes(q) || (l.blurb ?? '').toLowerCase().includes(q);
  const menuActive = !!activeLens && !activeLens.pinned;

  return (
    <Popover className="relative">
      <PopoverButton
        title="All inspection tools"
        aria-label="All inspection tools"
        className={clsx(btnCls, menuActive ? activeCls : idleCls)}
      >
        <Wrench className="size-4" />
      </PopoverButton>
      <PopoverPanel
        anchor="bottom"
        className="z-20 mt-2 w-72 rounded-xl border border-zinc-950/10 bg-white/95 p-2 shadow-lg ring-1 ring-zinc-950/5 backdrop-blur dark:border-white/10 dark:bg-zinc-800/95 dark:ring-white/10"
      >
        <div className="mb-2 flex items-center gap-2 rounded-lg bg-zinc-950/5 px-2 py-1.5 dark:bg-white/10">
          <Search className="size-3.5 shrink-0 text-zinc-400" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tools…"
            className="w-full bg-transparent text-sm/5 text-zinc-950 outline-none placeholder:text-zinc-400 dark:text-white"
          />
        </div>
        <div className="max-h-96 overflow-y-auto">
          {LENS_CATEGORIES.map((cat) => {
            const lenses = lensesIn(cat.id, advanced).filter(matches);
            if (!lenses.length) return null;
            return (
              <div key={cat.id} className="mb-1">
                <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
                  {cat.label}
                </div>
                {lenses.map((l) => {
                  const Icon = l.icon;
                  const isActive = activeLens?.key === l.key;
                  return (
                    <PopoverButton
                      as="button"
                      key={l.key}
                      type="button"
                      onClick={() => selectLens(l)}
                      className={clsx(
                        'flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm/5 transition',
                        isActive
                          ? 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900'
                          : 'text-zinc-700 hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/10',
                      )}
                    >
                      <Icon className="size-3.5 shrink-0 opacity-70" />
                      {l.label}
                    </PopoverButton>
                  );
                })}
              </div>
            );
          })}
        </div>
      </PopoverPanel>
    </Popover>
  );
}

/**
 * The floating toolbar over the viewer: the pinned inspection lenses
 * (model data + the always-visible field lenses), the searchable all-tools
 * menu, the candidate-directions view and the advanced reveal. Field lenses
 * materialize themselves — clicking one runs the backing analysis when
 * nothing is cached. Catalyst ships no tooltip, so hints use native `title`.
 */
export function AnalysisToolbar() {
  const activeLens = useActiveLens();
  const checkActive = useCheckActive();
  const advanced = useV2((s) => s.advanced);
  const setAdvanced = useV2((s) => s.setAdvanced);
  const inDirections = useDirectionsActive();

  return (
    <div className="absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-1 rounded-xl border border-zinc-950/10 bg-white/90 p-1 shadow-lg ring-1 ring-zinc-950/5 backdrop-blur dark:border-white/10 dark:bg-zinc-800/90 dark:ring-white/10">
      {PINNED_LENSES.map((l) => (
        <LensButton key={l.key} lens={l} isActive={activeLens?.key === l.key} />
      ))}
      <LensMenu activeLens={checkActive || inDirections ? null : activeLens} />
      <span className="mx-0.5 h-5 w-px bg-zinc-950/10 dark:bg-white/10" />

      {/* candidate-directions view — the orientations direction-scoped
          analyses run from */}
      <button
        type="button"
        onClick={activateDirections}
        title="Candidate directions"
        aria-pressed={inDirections}
        className={clsx(btnCls, inDirections ? activeCls : idleCls)}
      >
        <Crosshair className="size-4" />
      </button>

      <span className="mx-0.5 h-5 w-px bg-zinc-950/10 dark:bg-white/10" />

      <button
        type="button"
        onClick={() => setAdvanced(!advanced)}
        title={advanced ? 'Hide advanced tools' : 'More tools'}
        aria-pressed={advanced}
        className={clsx(
          btnCls,
          advanced
            ? 'bg-zinc-950/5 text-zinc-950 dark:bg-white/10 dark:text-white'
            : idleCls,
        )}
      >
        <MoreHorizontal className="size-4" />
      </button>
    </div>
  );
}
