import {
  Boxes, ClipboardCheck, FolderKanban, Layers, Moon, Package,
  SlidersHorizontal, Sun, Wrench,
} from 'lucide-react';
import {
  Sidebar, SidebarBody, SidebarFooter, SidebarHeader, SidebarHeading,
  SidebarItem, SidebarLabel, SidebarSection, SidebarSpacer,
} from '../../catalyst/sidebar';
import { Switch } from '../../catalyst/switch';
import { useStore } from '../../state/store';
import { selectPart } from '../../viewer/controller';
import { useV2 } from '../store';

const NAV = [
  { id: 'projects', label: 'Projects', icon: FolderKanban, enabled: false },
  { id: 'parts', label: 'Parts', icon: Boxes, enabled: true },
  { id: 'review', label: 'Review queue', icon: ClipboardCheck, enabled: false },
  { id: 'presets', label: 'Stack presets', icon: Layers, enabled: false },
  { id: 'materials', label: 'Materials', icon: Wrench, enabled: false },
];

export function AppSidebar() {
  const parts = useStore((s) => s.parts);
  const partId = useStore((s) => s.partId);
  const advanced = useV2((s) => s.advanced);
  const setAdvanced = useV2((s) => s.setAdvanced);
  const theme = useV2((s) => s.theme);
  const toggleTheme = useV2((s) => s.toggleTheme);

  return (
    <Sidebar>
      <SidebarHeader>
        <img
          src="/wefabricate_Logo_Inline_Black.svg"
          alt="Wefabricate"
          className="h-8 w-auto self-start dark:invert"
        />
      </SidebarHeader>

      <SidebarBody>
        <SidebarSection>
          {NAV.map((item) => (
            <SidebarItem
              key={item.id}
              current={item.enabled && item.id === 'parts'}
              disabled={!item.enabled}
              title={item.enabled ? undefined : 'Coming soon'}
            >
              <item.icon data-slot="icon" />
              <SidebarLabel>{item.label}</SidebarLabel>
            </SidebarItem>
          ))}
        </SidebarSection>

        <SidebarSection>
          <SidebarHeading>Parts</SidebarHeading>
          {parts.length === 0 && (
            <p className="px-2 text-sm/5 text-zinc-500 dark:text-zinc-400">No parts yet.</p>
          )}
          {parts.map((p) => (
            <SidebarItem
              key={p.id}
              current={p.id === partId}
              onClick={() => void selectPart(p.id)}
            >
              <Package data-slot="icon" />
              <SidebarLabel>{p.name}</SidebarLabel>
            </SidebarItem>
          ))}
        </SidebarSection>

        <SidebarSpacer />
      </SidebarBody>

      <SidebarFooter>
        <SidebarSection>
          <div className="flex items-center justify-between gap-3 rounded-lg px-2 py-2.5 text-sm/5 font-medium text-zinc-950 sm:py-2 dark:text-white">
            <span className="flex items-center gap-3">
              <SlidersHorizontal className="size-5 text-zinc-500 dark:text-zinc-400" />
              Advanced mode
            </span>
            <Switch checked={advanced} onChange={setAdvanced} aria-label="Advanced mode" />
          </div>
          <SidebarItem onClick={toggleTheme}>
            {theme === 'dark' ? <Sun data-slot="icon" /> : <Moon data-slot="icon" />}
            <SidebarLabel>{theme === 'dark' ? 'Light theme' : 'Dark theme'}</SidebarLabel>
          </SidebarItem>
        </SidebarSection>
      </SidebarFooter>
    </Sidebar>
  );
}
