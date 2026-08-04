import clsx from 'clsx';
import {
  Disclosure, DisclosureButton, DisclosurePanel,
} from '@headlessui/react';
import { ChevronDown, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

/**
 * Slot 4. A collapsed group — "Advanced", "Compute".
 *
 * The trigger's 176-character class string existed in four literal copies, and
 * two more panels reached for a native `<details>` and a pair of `▾`/`▸` text
 * glyphs instead. One component, one keyboard behaviour, one chevron.
 *
 * `defaultOpen` is deliberately a prop rather than being wired to the global
 * advanced flag in here: a rail decides whether ITS collapsed group follows the
 * user's advanced-mode preference. Most should; the caller says so.
 */
export function RailDisclosure({
  icon: Icon, label, defaultOpen = false, gap = 4, children,
}: {
  icon?: LucideIcon;
  label: string;
  defaultOpen?: boolean;
  gap?: 2 | 4;
  children: ReactNode;
}) {
  return (
    <Disclosure defaultOpen={defaultOpen}>
      {({ open }) => (
        <div>
          <DisclosureButton className="flex w-full items-center justify-between rounded-lg px-1 py-1 text-xs/5 font-medium text-zinc-500 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white">
            <span className="flex items-center gap-1.5">
              {Icon && <Icon className="size-3.5" />}
              {label}
            </span>
            <ChevronDown
              className={clsx('size-3.5 transition-transform', open && 'rotate-180')}
            />
          </DisclosureButton>
          <DisclosurePanel
            className={clsx('mt-2 flex flex-col', gap === 2 ? 'gap-2' : 'gap-4')}
          >
            {children}
          </DisclosurePanel>
        </div>
      )}
    </Disclosure>
  );
}
