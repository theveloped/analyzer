import {
  Boxes, ClipboardCheck, FolderKanban, Layers, Moon, Package, SlidersHorizontal,
  Sun, Wrench,
} from 'lucide-react';
import { useStore } from '../../state/store';
import { selectPart } from '../../viewer/controller';
import { Switch } from '../components/ui/switch';
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupLabel,
  SidebarHeader, SidebarLabelText, SidebarMenu, SidebarMenuButton,
  SidebarMenuItem, SidebarSeparator, useSidebar,
} from '../components/ui/sidebar';
import { cn } from '../lib/utils';
import { useV2 } from '../store';

const NAV = [
  { id: 'projects', label: 'Projects', icon: FolderKanban, enabled: false },
  { id: 'parts', label: 'Parts', icon: Boxes, enabled: true },
  { id: 'review', label: 'Review queue', icon: ClipboardCheck, enabled: false },
  { id: 'presets', label: 'Stack presets', icon: Layers, enabled: false },
  { id: 'materials', label: 'Materials', icon: Wrench, enabled: false },
];

function GlobalSettings() {
  const { open } = useSidebar();
  const advanced = useV2((s) => s.advanced);
  const setAdvanced = useV2((s) => s.setAdvanced);
  const theme = useV2((s) => s.theme);
  const toggleTheme = useV2((s) => s.toggleTheme);

  if (!open) {
    return (
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton
            tooltip={advanced ? 'Advanced mode: on' : 'Advanced mode: off'}
            isActive={advanced}
            onClick={() => setAdvanced(!advanced)}
          >
            <SlidersHorizontal />
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton tooltip="Toggle theme" onClick={toggleTheme}>
            {theme === 'dark' ? <Sun /> : <Moon />}
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    );
  }

  return (
    <div className="flex flex-col gap-2 rounded-md bg-sidebar-accent/50 p-2">
      <label className="flex items-center justify-between gap-2 text-sm">
        <span className="flex items-center gap-2">
          <SlidersHorizontal className="size-4" /> Advanced mode
        </span>
        <Switch checked={advanced} onCheckedChange={setAdvanced} aria-label="Advanced mode" />
      </label>
      <button
        type="button"
        onClick={toggleTheme}
        className="flex items-center gap-2 rounded-md px-1 py-1 text-sm text-muted-foreground hover:text-foreground"
      >
        {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
        {theme === 'dark' ? 'Light theme' : 'Dark theme'}
      </button>
    </div>
  );
}

export function AppSidebar() {
  const parts = useStore((s) => s.parts);
  const partId = useStore((s) => s.partId);
  const { open } = useSidebar();

  return (
    <Sidebar>
      <SidebarHeader>
        <div className={cn('flex items-center gap-2 px-1 py-1', !open && 'justify-center')}>
          <div className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Package className="size-4" />
          </div>
          <SidebarLabelText className="text-sm font-semibold">DFM Studio</SidebarLabelText>
        </div>
      </SidebarHeader>

      <SidebarSeparator />

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Navigate</SidebarGroupLabel>
          <SidebarMenu>
            {NAV.map((item) => (
              <SidebarMenuItem key={item.id}>
                <SidebarMenuButton
                  isActive={item.enabled}
                  disabled={!item.enabled}
                  tooltip={item.enabled ? item.label : `${item.label} — coming soon`}
                  className={cn(!item.enabled && 'opacity-50')}
                >
                  <item.icon />
                  <SidebarLabelText>{item.label}</SidebarLabelText>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>

        <SidebarGroup>
          <SidebarGroupLabel>Parts</SidebarGroupLabel>
          <SidebarMenu>
            {parts.length === 0 && open && (
              <p className="px-2 py-1 text-xs text-muted-foreground">No parts yet.</p>
            )}
            {parts.map((p) => (
              <SidebarMenuItem key={p.id}>
                <SidebarMenuButton
                  isActive={p.id === partId}
                  tooltip={p.name}
                  onClick={() => void selectPart(p.id)}
                >
                  <Package />
                  <SidebarLabelText>{p.name}</SidebarLabelText>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <SidebarSeparator />
        <GlobalSettings />
      </SidebarFooter>
    </Sidebar>
  );
}
