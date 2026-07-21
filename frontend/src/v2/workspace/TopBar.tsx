import { ChevronRight } from 'lucide-react';
import { Badge } from '../../catalyst/badge';
import { useStore } from '../../state/store';

/**
 * Slim workspace header: breadcrumb + units. Part selection lives in the
 * global sidebar (nav/AppSidebar); process is not a top-level switch — the
 * inspection lenses and checks carry it (see docs/PLAN-ARCHITECTURE.md).
 */
export function TopBar() {
  const manifest = useStore((s) => s.manifest);
  const partName = manifest?.part.name ?? '—';

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-zinc-950/5 px-4 dark:border-white/10">
      <div className="flex items-center gap-1.5 text-sm/6 text-zinc-500 dark:text-zinc-400">
        <span>Workspace</span>
        <ChevronRight className="size-4" />
        <span className="font-medium text-zinc-950 dark:text-white">{partName}</span>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <Badge color="zinc">mm</Badge>
      </div>
    </header>
  );
}
