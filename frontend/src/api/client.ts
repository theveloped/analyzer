import type {
  DispositionEvent, Job, Manifest, Part, Plan, PlanSection, ProcessInfo,
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

export async function uploadPart(file: File): Promise<Part> {
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

const planUrl = (partId: string) =>
  `/api/parts/${encodeURIComponent(partId)}/plan`;

/** Store a new plan revision. `revision` is the revision the client edited;
 * a concurrent edit surfaces as a 409 — reload the manifest and retry. */
export async function putPlan(
  partId: string, plan: Plan, revision: number,
): Promise<PlanSection> {
  const res = await fetch(planUrl(partId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan, revision }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(detail ?? `saving plan failed: ${res.status}`);
  }
  return res.json();
}

/** Dry-run a plan edit: per check `unchanged` | `revalidates` | `recomputes`
 * | `error`. Pure hash arithmetic server-side — never enqueues a job. */
export async function postPlanImpact(
  partId: string, patch: Record<string, any>,
): Promise<Record<string, { outcome: string; expected_hash: string | null;
  error: string | null }>> {
  const res = await fetch(`${planUrl(partId)}/impact`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ patch }),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(detail ?? `impact preview failed: ${res.status}`);
  }
  return res.json();
}

export async function postDisposition(
  partId: string,
  body: { finding_id: string; state: DispositionEvent['state']; by: string;
    why?: string; evidence?: Record<string, any> },
): Promise<{ stored: DispositionEvent;
  dispositions: Record<string, DispositionEvent> }> {
  const res = await fetch(
    `/api/parts/${encodeURIComponent(partId)}/dispositions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(detail ?? `saving disposition failed: ${res.status}`);
  }
  return res.json();
}

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
