import { useEffect, useRef, useState } from 'react';
import {
  Boxes, ClipboardCheck, FileText, FolderKanban, Layers, Moon, Package,
  Plus, RefreshCw, SlidersHorizontal, Sun, Upload, Wrench,
} from 'lucide-react';
import {
  Sidebar, SidebarBody, SidebarFooter, SidebarHeader, SidebarHeading,
  SidebarItem, SidebarLabel, SidebarSection, SidebarSpacer,
} from '../../catalyst/sidebar';
import { Switch } from '../../catalyst/switch';
import { cancelJob, fetchReports } from '../../api/client';
import type { Part, ReportSummary } from '../../api/types';
import { useStore } from '../../state/store';
import { selectPart, uploadAndSelect } from '../../viewer/controller';
import { reprocessPart } from '../../viewer/jobs';
import { useV2 } from '../store';

const ACCEPT = '.stl,.stp,.step';

const NAV = [
  { id: 'projects', label: 'Projects', icon: FolderKanban, enabled: false },
  { id: 'parts', label: 'Parts', icon: Boxes, enabled: true },
  { id: 'review', label: 'Review queue', icon: ClipboardCheck, enabled: false },
  { id: 'presets', label: 'Stack presets', icon: Layers, enabled: false },
  { id: 'materials', label: 'Materials', icon: Wrench, enabled: false },
];

/** Published report bundles of the selected part (newest first); refetches
 * when the manifest refreshes (the publish flow bumps it). */
function ReportsSection() {
  const partId = useStore((s) => s.partId);
  const manifestVersion = useStore((s) => s.manifestVersion);
  const [reports, setReports] = useState<ReportSummary[]>([]);

  useEffect(() => {
    if (!partId) { setReports([]); return; }
    let live = true;
    fetchReports(partId)
      .then((list) => { if (live) setReports(list); })
      .catch(() => { if (live) setReports([]); });
    return () => { live = false; };
  }, [partId, manifestVersion]);

  if (!partId || !reports.length) return null;
  return (
    <SidebarSection>
      <SidebarHeading>Reports</SidebarHeading>
      {reports.slice().reverse().map((r) => (
        <SidebarItem
          key={r.rid}
          onClick={() => {
            window.location.hash =
              `#report=${encodeURIComponent(partId)}/${encodeURIComponent(r.rid)}`;
          }}
        >
          <FileText data-slot="icon" />
          <SidebarLabel>
            {r.title} · rev {r.plan_revision}
          </SidebarLabel>
        </SidebarItem>
      ))}
    </SidebarSection>
  );
}

export function AppSidebar() {
  const parts = useStore((s) => s.parts);
  const partId = useStore((s) => s.partId);
  const advanced = useV2((s) => s.advanced);
  const setAdvanced = useV2((s) => s.setAdvanced);
  const theme = useV2((s) => s.theme);
  const toggleTheme = useV2((s) => s.toggleTheme);

  const jobs = useStore((s) => s.jobs);
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);

  // parts with a queued/running job — their reprocess icon spins and is disabled
  const busyParts = new Set(
    jobs.filter((j) => j.status === 'queued' || j.status === 'running')
      .map((j) => j.part_id),
  );

  async function upload(file: File) {
    setBusy(true);
    try {
      await uploadAndSelect(file);
    } catch (err) {
      useStore.getState().set({ error: String(err) });
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = '';
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void upload(file);
  }

  async function onReprocess(part: Part) {
    try {
      await reprocessPart(part.id);
    } catch (err) {
      useStore.getState().set({ error: String(err) });
    }
  }

  /** The spinning icon doubles as a cancel button: clicking it cancels the
   * part's active job (queued instantly; running cooperatively at its next
   * progress report), freeing the single worker for the next job. */
  async function onCancel(part: Part) {
    const active = jobs.find((j) => j.part_id === part.id
      && (j.status === 'queued' || j.status === 'running'));
    if (!active) return;
    try {
      const updated = await cancelJob(active.id);
      useStore.getState().set({
        jobs: useStore.getState().jobs.map(
          (j) => (j.id === updated.id ? updated : j)),
      });
    } catch (err) {
      useStore.getState().set({ error: String(err) });
    }
  }

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

        <SidebarSection
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={dragging
            ? 'rounded-lg outline-2 outline-dashed outline-blue-500 dark:outline-blue-400'
            : undefined}
        >
          <div className="flex items-center justify-between">
            <SidebarHeading>Parts</SidebarHeading>
            <button
              type="button"
              title="Add a part (STEP or STL)"
              disabled={busy}
              onClick={() => fileInput.current?.click()}
              className="mr-1 mb-1 rounded-md p-1 text-zinc-500 hover:bg-zinc-950/5 hover:text-zinc-950 disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-white/5 dark:hover:text-white"
            >
              <Plus className="size-4" />
            </button>
          </div>

          {parts.length === 0 && (
            <button
              type="button"
              disabled={busy}
              onClick={() => fileInput.current?.click()}
              className="flex flex-col items-center gap-1 rounded-lg border border-dashed border-zinc-950/15 px-2 py-4 text-sm/5 text-zinc-500 hover:border-zinc-950/25 hover:text-zinc-700 disabled:opacity-50 dark:border-white/15 dark:text-zinc-400 dark:hover:border-white/25 dark:hover:text-zinc-200"
            >
              <Upload className="size-5" />
              {busy ? 'Uploading…' : 'Drop a file or click to upload'}
            </button>
          )}

          {parts.map((p) => (
            <div key={p.id} className="group/part flex items-center">
              <SidebarItem
                current={p.id === partId}
                onClick={() => void selectPart(p.id)}
                className="min-w-0 flex-1"
              >
                <Package data-slot="icon" />
                <SidebarLabel>{p.name}</SidebarLabel>
              </SidebarItem>
              <button
                type="button"
                title={busyParts.has(p.id)
                  ? 'Cancel the running job'
                  : 'Reprocess — rebuild from the original file'}
                onClick={(e) => {
                  e.stopPropagation();
                  if (busyParts.has(p.id)) void onCancel(p);
                  else void onReprocess(p);
                }}
                className={`ml-0.5 shrink-0 rounded-md p-1.5 text-zinc-400 group-hover/part:opacity-100 hover:bg-zinc-950/5 hover:text-zinc-950 focus:opacity-100 dark:text-zinc-500 dark:hover:bg-white/10 dark:hover:text-white ${busyParts.has(p.id) ? 'opacity-100' : 'opacity-0'}`}
              >
                <RefreshCw className={busyParts.has(p.id) ? 'size-4 animate-spin' : 'size-4'} />
              </button>
            </div>
          ))}
        </SidebarSection>

        <ReportsSection />

        <input
          ref={fileInput}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }}
        />

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
