import { ChevronRight } from 'lucide-react';
import { useStore } from '../../state/store';
import { selectPart } from '../../viewer/controller';
import { Badge } from '../components/ui/badge';
import { SidebarTrigger } from '../components/ui/sidebar';
import { cn } from '../lib/utils';

const PROCESS_TABS = [
  { id: 'injection', label: 'Injection', enabled: true },
  { id: 'cnc', label: 'CNC', enabled: false },
  { id: 'sheet', label: 'Sheet', enabled: false },
];

function PartPicker() {
  const parts = useStore((s) => s.parts);
  const partId = useStore((s) => s.partId);
  return (
    <select
      value={partId ?? ''}
      onChange={(e) => void selectPart(e.target.value)}
      className="h-8 rounded-md border border-input bg-transparent px-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {!partId && <option value="">Select a part…</option>}
      {parts.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}{p.status !== 'meshed' ? ' (not meshed)' : ''}
        </option>
      ))}
    </select>
  );
}

export function TopBar() {
  const manifest = useStore((s) => s.manifest);
  const partName = manifest?.part.name ?? '—';

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b bg-background px-3">
      <SidebarTrigger />
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <span>Workspace</span>
        <ChevronRight className="size-3" />
        <span className="font-medium text-foreground">{partName}</span>
      </div>

      <div className="ml-auto flex items-center gap-1.5">
        {PROCESS_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            disabled={!t.enabled}
            title={t.enabled ? undefined : 'Coming soon'}
            className={cn(
              'rounded-full border px-3 py-1 text-xs transition-colors',
              t.enabled
                ? 'border-foreground bg-foreground text-background'
                : 'border-border text-muted-foreground opacity-60',
            )}
          >
            {t.label}
          </button>
        ))}
        <span className="mx-1 h-5 w-px bg-border" />
        <PartPicker />
        <Badge variant="outline" className="hidden sm:flex">mm</Badge>
      </div>
    </header>
  );
}
