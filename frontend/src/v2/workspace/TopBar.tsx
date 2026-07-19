import { ChevronRight } from 'lucide-react';
import { Badge } from '../../catalyst/badge';
import { Button } from '../../catalyst/button';
import { Select } from '../../catalyst/select';
import { useStore } from '../../state/store';
import { selectPart } from '../../viewer/controller';

const PROCESS_TABS = [
  { id: 'injection', label: 'Injection', enabled: true },
  { id: 'cnc', label: 'CNC', enabled: false },
  { id: 'sheet', label: 'Sheet', enabled: false },
];

function PartPicker() {
  const parts = useStore((s) => s.parts);
  const partId = useStore((s) => s.partId);
  return (
    <div className="w-52">
      <Select value={partId ?? ''} onChange={(e) => void selectPart(e.target.value)}>
        {!partId && <option value="">Select a part…</option>}
        {parts.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}{p.status !== 'meshed' ? ' (not meshed)' : ''}
          </option>
        ))}
      </Select>
    </div>
  );
}

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
        {PROCESS_TABS.map((t) =>
          t.enabled ? (
            <Button key={t.id}>{t.label}</Button>
          ) : (
            <Button key={t.id} plain disabled title="Coming soon">{t.label}</Button>
          ),
        )}
        <span className="mx-1 h-5 w-px bg-zinc-950/10 dark:bg-white/10" />
        <PartPicker />
        <Badge color="zinc">mm</Badge>
      </div>
    </header>
  );
}
