import type { FC } from 'react';
import type { FieldDescriptor, Manifest } from '../api/types';

export type RGB = readonly [number, number, number];

export interface LegendEntry {
  color: RGB;
  label: string;
}

export interface PaintInfo {
  legend: LegendEntry[];
  stats?: string;
}

/** Everything a view mode needs to paint the mesh, independent of process. */
export interface ViewCtx {
  manifest: Manifest;
  directions: number[][];
  faces: Uint32Array;
  normals: Float32Array; // per-face unit normals
  faceCount: number;
  params: Record<string, any>; // viewer params of the active process
  highlights: number[] | null;
  getField(desc: FieldDescriptor): Promise<Float32Array | Uint8Array | Uint32Array>;
  paintFaces(colorOf: (f: number) => RGB): void;
  /** Overlay line segments (flattened endpoint pairs, N*2*3 floats). */
  setLines(positions: Float32Array, color?: RGB): void;
  /** Overlay direction arrows pointing at the part. */
  setArrows(arrows: { direction: number[]; color: RGB }[]): void;
}

export interface ViewMode {
  id: string;
  label: string;
  paint(ctx: ViewCtx): Promise<PaintInfo>;
}

/** A process contributes view modes, viewer controls and click-inspection. */
export interface ProcessPlugin {
  processId: string;
  label: string;
  modes: ViewMode[];
  /** Initial viewer params when a part manifest loads. */
  defaults(manifest: Manifest): Record<string, any>;
  /** Extra viewer-side controls (tolerance, holder, ...). */
  Controls?: FC;
  /** Lines for the click-to-inspect panel. */
  inspect?(face: number, ctx: ViewCtx): Promise<string[]>;
}
