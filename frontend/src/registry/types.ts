import type { FC } from 'react';
import type { FieldDescriptor, Manifest } from '../api/types';

export type RGB = readonly [number, number, number];

/** Camera focus for one legend entry: where its faces live and from which
 * side to look at them. */
export interface LegendFocus {
  center: [number, number, number];
  direction: [number, number, number];
  radius: number;
  /** The group's fine-face indices — clicking the legend row selects them
   * (fit-selection / isolate / ghost act on the selection). */
  faces?: number[];
}

export interface LegendEntry {
  color: RGB;
  label: string;
  /** When set, clicking the legend row flies the camera to these faces. */
  focus?: LegendFocus;
}

export interface ColorBar {
  /** Domain endpoints. For a diverging bar these are symmetric (−M, +M). */
  min: number;
  max: number;
  unit?: string;
  /** true → 0 sits at the centre of the bar (symmetric domain). */
  diverging?: boolean;
  /** CSS linear-gradient matching the painted colormap (WYSIWYG). */
  gradient: string;
  /** Optional limit value to mark as a tick. */
  threshold?: number;
}

export interface PaintInfo {
  legend: LegendEntry[];
  stats?: string;
  /** A continuous colour scale to render as a colorbar legend. */
  colorbar?: ColorBar;
}

/** Everything a view mode needs to paint the mesh, independent of process. */
export interface ViewCtx {
  manifest: Manifest;
  directions: number[][];
  verts: Float32Array; // indexed vertex coordinates
  faces: Uint32Array;
  normals: Float32Array; // per-face unit normals
  faceCount: number;
  params: Record<string, any>; // viewer params of the active process
  highlights: number[] | null;
  getField(desc: FieldDescriptor): Promise<Float32Array | Uint8Array | Uint32Array>;
  paintFaces(colorOf: (f: number) => RGB): void;
  /** Per-corner colors (k in 0..2) — smooth vertex-interpolated fields. */
  paintCorners(colorOf: (f: number, k: number) => RGB): void;
  /** Overlay line segments (flattened endpoint pairs, N*2*3 floats).
   * depthTest true = on-surface lines (isolines); false = through-visible. */
  setLines(positions: Float32Array, color?: RGB, depthTest?: boolean): void;
  /** Overlay direction arrows pointing at the part. */
  setArrows(arrows: { direction: number[]; color: RGB }[]): void;
  /** Show a graph overlay (skeleton). Keyed: same key skips the rebuild. */
  setGraph(key: string, nodes: Float32Array, edges: Uint32Array, radii: Float32Array): void;
  /** Recolor the current graph overlay's nodes (edges interpolate). */
  paintGraph(colorOf: (node: number) => RGB): void;
  /** Display HINT: this mode wants a see-through body (e.g. to show a graph
   * overlay inside the part). Composed with — never overriding — the user's
   * viewport render style; reset to 1 on every repaint. */
  setMeshOpacity(alpha: number): void;
  /** Which faces this mode counts as findings (flagged/in-band); drives the
   * viewport's "findings only" filter. Reset on every repaint; modes with no
   * findings notion simply never call it. */
  setFindings(isFinding: ((f: number) => boolean) | null): void;
  /** Re-pose the mesh from indexed per-vertex positions (V*3); null
   * restores the original geometry. `smooth` recomputes lighting normals
   * (skip during playback, recompute on pause). */
  setVertexPositions(verts: Float32Array | null, smooth?: boolean): void;
  /** Extrude a YZ profile along X over spans as a translucent overlay
   * mesh (tool/machine sections). Cleared on every repaint. */
  addOverlayMesh(spec: {
    profile: [number, number][];
    spans: [number, number][];
    color: RGB;
    opacity?: number;
    yzOffset?: [number, number];
    tag?: string;
  }): void;
  /** Move tagged overlay meshes to world height dz (absolute). */
  shiftOverlay(tag: string, dz: number): void;
  /** Per-frame callback inside the render loop (null to remove). Reset on
   * every repaint — a mode must re-register in paint(). */
  setAnimator(fn: ((tMs: number) => void) | null): void;
}

export interface ViewMode {
  id: string;
  label: string;
  paint(ctx: ViewCtx): Promise<PaintInfo>;
  /** Optional click handler; return true when consumed (triggers repaint). */
  onPick?(face: number, ctx: ViewCtx): Promise<boolean>;
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
