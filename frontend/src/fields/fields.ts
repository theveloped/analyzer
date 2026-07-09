// Typed-array fetching with an in-memory cache keyed by URL (the port of
// viewer.html's fetchBin). The browser's HTTP cache revalidates via ETag
// underneath; this layer avoids re-decoding on every repaint.

import type { FieldDescriptor } from '../api/types';

const cache = new Map<string, Promise<Float32Array | Uint8Array | Uint32Array>>();

type TypedCtor<T> = new (buffer: ArrayBuffer) => T;

export function fetchBin<T extends Float32Array | Uint8Array | Uint32Array>(
  url: string, Type: TypedCtor<T>,
): Promise<T> {
  if (!cache.has(url)) {
    cache.set(url, (async () => {
      const res = await fetch(url);
      if (!res.ok) {
        cache.delete(url);
        throw new Error(`missing ${url} (${res.status})`);
      }
      return new Type(await res.arrayBuffer());
    })());
  }
  return cache.get(url) as Promise<T>;
}

export function fetchField(desc: FieldDescriptor): Promise<Float32Array | Uint8Array> {
  return desc.dtype === 'u1'
    ? fetchBin(desc.url, Uint8Array)
    : fetchBin(desc.url, Float32Array);
}

/** Drop cached entries, e.g. when switching parts or after recompute. */
export function clearFieldCache(prefix?: string) {
  if (!prefix) {
    cache.clear();
    return;
  }
  for (const key of [...cache.keys()]) if (key.startsWith(prefix)) cache.delete(key);
}
