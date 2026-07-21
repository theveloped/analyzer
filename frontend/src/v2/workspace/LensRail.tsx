import { Disclosure, DisclosureButton, DisclosurePanel } from '@headlessui/react';
import clsx from 'clsx';
import { ChevronDown, Settings2 } from 'lucide-react';
import { getPlugin } from '../../registry';
import { useStore } from '../../state/store';
import { useActiveLens } from './hooks';
import './v1-controls.css';

const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';

/**
 * The right rail for an active inspection lens: label/blurb, the shared
 * paint stats, and — when the hosting plugin ships a Controls panel — a
 * Configure section rendering that panel verbatim under the `.v1-controls`
 * scope (the visual seam is accepted for now; see docs/PLAN-ARCHITECTURE.md).
 */
export function LensRail() {
  const lens = useActiveLens();
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const pick = useStore((s) => s.pick);
  if (!lens) return null;
  const Icon = lens.icon;
  const Controls = lens.hasControls ? getPlugin(lens.processId)?.Controls : undefined;

  return (
    <div className="flex h-full w-72 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div>
        <div className="flex items-center gap-2">
          <Icon className="size-4 text-blue-600 dark:text-blue-400" />
          <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">{lens.label}</h2>
        </div>
        {lens.blurb && <p className={clsx('mt-1', hintCls)}>{lens.blurb}</p>}
      </div>

      {Controls && (
        <Disclosure defaultOpen>
          {({ open }) => (
            <div>
              <DisclosureButton className="flex w-full items-center justify-between rounded-lg px-1 py-1 text-xs/5 font-medium text-zinc-500 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white">
                <span className="flex items-center gap-1.5">
                  <Settings2 className="size-3.5" /> Configure
                </span>
                <ChevronDown className={clsx('size-3.5 transition-transform', open && 'rotate-180')} />
              </DisclosureButton>
              <DisclosurePanel className="mt-2">
                <div className="v1-controls">
                  <Controls />
                </div>
              </DisclosurePanel>
            </div>
          )}
        </Disclosure>
      )}

      <div className="h-px bg-zinc-950/10 dark:bg-white/10" />

      <div>
        <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">In view</div>
        {error ? (
          <p className="whitespace-pre-wrap text-xs/5 text-red-600 dark:text-red-500">⚠ {error}</p>
        ) : stats ? (
          <p className="whitespace-pre-wrap text-xs/5 text-zinc-500 dark:text-zinc-400">{stats}</p>
        ) : (
          <p className={hintCls}>Loading…</p>
        )}
      </div>

      <div>
        <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">Inspect</div>
        <p className="whitespace-pre-wrap font-mono text-[11px]/4 text-zinc-500 dark:text-zinc-400">{pick}</p>
      </div>
    </div>
  );
}
