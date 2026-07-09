import type { Job, Manifest, Part, ProcessInfo } from './types';

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
