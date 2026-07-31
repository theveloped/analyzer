import type {
  Job, MachineSummary, Manifest, Part, ProcessInfo, Route, RouteSection,
} from './types';

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status} ${await res.text()}`);
  return res.json();
}

export const fetchConfig = () => getJSON<{ preload: string | null }>('/api/config');
export const fetchCatalog = () => getJSON<ProcessInfo[]>('/api/processes');
export const fetchParts = () => getJSON<Part[]>('/api/parts');
export const fetchManifest = (partId: string) =>
  getJSON<Manifest>(`/api/parts/${encodeURIComponent(partId)}/manifest`);
export const fetchJobs = (partId?: string) =>
  getJSON<Job[]>(partId ? `/api/jobs?part_id=${encodeURIComponent(partId)}` : '/api/jobs');

export async function fetchHighlights(url: string): Promise<number[] | null> {
  const res = await fetch(url);
  if (!res.ok) return null;
  return (await res.json()).faces ?? null;
}

export async function uploadPart(
  file: File,
): Promise<{ part: Part; job: Job | null }> {
  const body = new FormData();
  body.append('file', file);
  const res = await fetch('/api/parts', { method: 'POST', body });
  if (!res.ok) throw new Error(`upload failed: ${res.status} ${await res.text()}`);
  return res.json();
}

export async function reprocessPart(
  partId: string,
): Promise<{ part: Part; job: Job | null }> {
  const res = await fetch(
    `/api/parts/${encodeURIComponent(partId)}/reprocess`, { method: 'POST' });
  if (!res.ok) throw new Error(`reprocess failed: ${res.status} ${await res.text()}`);
  return res.json();
}

export async function submitJob(
  partId: string, process: string, analysis: string, params: Record<string, any>,
): Promise<Job> {
  const res = await fetch('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ part_id: partId, process, analysis, params }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(detail ?? `job submit failed: ${res.status}`);
  }
  return res.json();
}

export const fetchJob = (id: number) => getJSON<Job>(`/api/jobs/${id}`);

/** Cancel a queued/running job (running ones cancel cooperatively at their
 * next progress report). */
export async function cancelJob(id: number): Promise<Job> {
  const res = await fetch(`/api/jobs/${id}/cancel`, { method: 'POST' });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(detail ?? `cancel failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchOverrides(
  url: string,
): Promise<Record<string, Record<string, number>>> {
  const res = await fetch(url);
  return res.ok ? res.json() : {};
}

export async function putOverrides(
  url: string, data: Record<string, Record<string, number>>,
): Promise<void> {
  const res = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`saving overrides failed: ${res.status}`);
}

export interface SplitCut {
  face_orig: number;
  face_at_cut: number;
  start: number;
  end: number;
  separated: boolean;
  created: number[];
  polyline: number[][];
}

export interface SplitsState {
  n_brep: number;
  n_effective: number;
  parents: number[];
  stale: boolean;
  cuts: SplitCut[];
}

const splitsUrl = (partId: string) =>
  `/api/parts/${encodeURIComponent(partId)}/splits`;

export const fetchSplits = (partId: string) =>
  getJSON<SplitsState>(splitsUrl(partId));

async function splitsMutation(url: string, method: string, body?: any):
Promise<SplitsState> {
  const res = await fetch(url, {
    method,
    ...(body ? {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    } : {}),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(detail ?? `split request failed: ${res.status}`);
  }
  return res.json();
}

export const postSplit = (
  partId: string, body: { face: number; start: number; end: number },
) => splitsMutation(splitsUrl(partId), 'POST', body);

export const deleteLastSplit = (partId: string) =>
  splitsMutation(`${splitsUrl(partId)}/last`, 'DELETE');

export const clearSplits = (partId: string) =>
  splitsMutation(splitsUrl(partId), 'DELETE');

const routeUrl = (partId: string) =>
  `/api/parts/${encodeURIComponent(partId)}/route`;

/** Store a new route revision. `revision` is the revision the client edited;
 * a concurrent edit surfaces as a 409 — reload the manifest and retry. */
export async function putRoute(
  partId: string, route: Route, revision: number,
): Promise<RouteSection> {
  const res = await fetch(routeUrl(partId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ route, revision }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(detail ?? `saving the route failed: ${res.status}`);
  }
  return res.json();
}

export interface PmiSaveResult {
  schema: number;
  counts: { tolerances: number; dimensions: number; datums: number };
  warnings: string[];
}

/** Author/replace a part's semantic PMI (pmi.json). The server validates the
 * payload and re-derives the AP242 round-trip warnings before writing; a bad
 * payload surfaces as a 400 with a human-readable detail. */
export async function putPmi(
  partId: string, pmi: unknown,
): Promise<PmiSaveResult> {
  const res = await fetch(`/api/parts/${encodeURIComponent(partId)}/pmi`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pmi }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(detail ?? `saving PMI failed: ${res.status}`);
  }
  return res.json();
}

/** The machine profile library — names an operation can reference. */
export const fetchMachines = () => getJSON<MachineSummary[]>(
  '/api/catalogue/machines');

export async function postEjectorSimulate<T>(
  partId: string, body: Record<string, any>,
): Promise<T> {
  const res = await fetch(
    `/api/parts/${encodeURIComponent(partId)}/ejector/simulate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(detail ?? `ejector simulation failed: ${res.status}`);
  }
  return res.json();
}
