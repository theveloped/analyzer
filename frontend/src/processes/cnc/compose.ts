// Client-side field composition: the interactive sliders threshold these
// cached per-vertex fields with zero server round-trips.

import type { ViewCtx } from '../../registry/types';
import { CncSource, TipEntry, currentSource, holderCylinders } from './sources';

export async function vertexGap(ctx: ViewCtx, source: CncSource, tip: TipEntry) {
  return await ctx.getField(tip.field) as Float32Array;
}

/**
 * Per-vertex required stickout for the holder stack; null without a holder.
 * Uses the tip-aware sreq fields (holder evaluated at feasible axis
 * positions, flank contact included) when precomputed for this tip, and
 * falls back to the vertex-centred clearance fields (a zero-diameter tool
 * assumption) otherwise — `approx` says which one is active.
 */
export async function vertexMinStickout(
  ctx: ViewCtx, source: CncSource, tip: TipEntry | null,
): Promise<{ values: Float32Array; approx: boolean } | null> {
  const cylinders = holderCylinders(ctx.params.holder);
  if (!cylinders.length) return null;
  let out: Float32Array | null = null;
  let approx = false;
  for (const cyl of cylinders) {
    const sreq = tip?.stickouts.find((s) => Math.abs(s.radius - cyl.radius) < 1e-9);
    let field: Float32Array;
    if (sreq) {
      field = await ctx.getField(sreq.field) as Float32Array;
    } else {
      const clear = source.clearances.find((c) => Math.abs(c.radius - cyl.radius) < 1e-9);
      if (!clear) {
        throw new Error(
          `no field for holder radius ${cyl.radius} — precompute with clearances ${cyl.radius}`);
      }
      field = await ctx.getField(clear.field) as Float32Array;
      approx = true;
    }
    if (!out) out = new Float32Array(field.length).fill(-Infinity);
    for (let v = 0; v < field.length; v++) {
      const req = field[v] - cyl.start;
      if (req > out[v]) out[v] = req;
    }
  }
  if (!out) return null;
  for (let v = 0; v < out.length; v++) if (out[v] < 0) out[v] = 0;
  return { values: out, approx };
}

export async function faceAccess(
  ctx: ViewCtx, source: CncSource,
): Promise<Uint8Array | null> {
  if (!source.accessibility) return null;
  return await ctx.getField(source.accessibility) as Uint8Array;
}

/** Face filter hiding inaccessible faces when the mask toggle is on. */
export async function accessKeep(
  ctx: ViewCtx, source: CncSource,
): Promise<((f: number) => boolean) | null> {
  if (!ctx.params.mask) return null;
  const access = await faceAccess(ctx, source);
  return access ? (f) => !!access[f] : null;
}

export function requireSource(ctx: ViewCtx): CncSource {
  const source = currentSource(ctx.manifest, ctx.params);
  if (!source) {
    throw new Error(
      'no cached tool fields — run cnc/precompute below (or python main.py precompute)');
  }
  return source;
}
