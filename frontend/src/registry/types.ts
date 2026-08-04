import type { FC } from 'react';
import type { FieldDescriptor, Manifest, ParamSpec } from '../api/types';

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
  /** Colour the mesh. Return null for a face to leave it UNPAINTED — it keeps
   * the native viewport style (solid/xray) so a lens can colour only its own
   * faces without tinting the rest. */
  paintFaces(colorOf: (f: number) => RGB | null): void;
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

/**
 * A param as a MODE declares it. The shape is `ParamSpec` — the same one the
 * backend serves for analyses, so one renderer covers both — plus a `hint`,
 * which a mode often needs ("run Flow voxels below if the view is empty") and
 * the served DTO has no business carrying: that one is mirrored from
 * `processes/base.py` and must not grow frontend-only fields.
 */
export type ViewParamSpec = ParamSpec & {
  hint?: string;
  /** Friendly text per option value. `ParamSpec.options` is a bare string[] —
   * fine for a backend enum, not for "cluster" meaning "clustered (medial
   * skeleton)". */
  optionLabels?: Record<string, string>;
};

export interface ViewMode {
  id: string;
  label: string;
  paint(ctx: ViewCtx): Promise<PaintInfo>;
  /** Optional click handler; return true when consumed (triggers repaint). */
  onPick?(face: number, ctx: ViewCtx): Promise<boolean>;
  /**
   * The `ctx.params` keys this paint reads, declared in the same `ParamSpec`
   * shape the backend serves for analysis params. The rail generates its
   * settings from these — so a mode showing no knobs declares none and gets
   * no settings section, rather than inheriting a panel from its plugin.
   *
   * This is per-MODE on purpose. `ProcessPlugin.Controls` is per-PROCESS and
   * takes no props, which is why one CNC panel serves eighteen lenses and
   * shows the tool holder to all of them.
   */
  params?: ViewParamSpec[];
}

/** A process contributes view modes, viewer controls and click-inspection. */
export interface ProcessPlugin {
  processId: string;
  label: string;
  modes: ViewMode[];
  /** Initial viewer params when a part manifest loads. */
  defaults(manifest: Manifest): Record<string, any>;
  /** Extra viewer-side controls (tolerance, holder, ...).
   *
   * BEING RETIRED. One component per process with no props, so it cannot know
   * which lens is active and every mode of the process gets the same panel.
   * Declare `ViewMode.params` instead and supply `paramWidgets` for the knobs
   * a generated field cannot express. */
  Controls?: FC;
  /**
   * Widgets for params the generated form cannot render from the spec alone —
   * options that come from the manifest rather than a static `options` array
   * (the CNC direction and tool-tip selects), or a list type that needs a real
   * editor. Keyed by param name; the mode still declares the param.
   */
  paramWidgets?: Record<string, FC<{
    spec: ParamSpec;
    value: unknown;
    values: Record<string, unknown>;
    onChange: (value: unknown) => void;
  }>>;
  /** Lines for the click-to-inspect panel. */
  inspect?(face: number, ctx: ViewCtx): Promise<string[]>;
  /**
   * First look at a mesh click (face + 3D hit point). Return true to
   * consume it (e.g. gate placement) instead of the default inspect.
   */
  onPick?(face: number, point: [number, number, number], ctx: ViewCtx): boolean;
}
