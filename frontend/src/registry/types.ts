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
  /** Show a graph overlay (skeleton). Keyed: same key skips the rebuild. */
  setGraph(key: string, nodes: Float32Array, edges: Uint32Array, radii: Float32Array): void;
  /** Recolor the current graph overlay's nodes (edges interpolate). */
  paintGraph(colorOf: (node: number) => RGB): void;
  /** Mesh transparency, e.g. to see a graph overlay inside the part. */
  setMeshOpacity(alpha: number): void;
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
  /**
   * First look at a mesh click (face + 3D hit point). Return true to
   * consume it (e.g. gate placement) instead of the default inspect.
   */
  onPick?(face: number, point: [number, number, number], ctx: ViewCtx): boolean;
}
