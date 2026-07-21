// Serializable viewport state: HOW the part is rendered and sectioned,
// independent of which lens paints it (the lens decides WHAT is shown).
// Owned by the v2 store; the viewer controller receives it through
// setViewportState and applies it to the scene. Lens, scope, viewport and
// interaction tool are orthogonal — switching one never resets the others.

export type RenderStyle = 'shaded' | 'facets' | 'xray';
export type EdgeMode = 'none' | 'brep' | 'tessellation';
export type Projection = 'perspective' | 'orthographic';
export type ContextMode = 'all' | 'ghost' | 'isolate';

export interface SectionState {
  enabled: boolean;
  axis: 'x' | 'y' | 'z' | 'custom';
  /** Unit plane normal (before flip). */
  normal: [number, number, number];
  /** Signed offset along the normal, model units. */
  offset: number;
  flip: boolean;
}

export interface ViewportState {
  style: RenderStyle;
  edgeMode: EdgeMode;
  /** Lens overlay shown at all, its opacity, and the findings-only filter. */
  lensVisible: boolean;
  lensOpacity: number;
  findingsOnly: boolean;
  projection: Projection;
  section: SectionState;
  /** What happens to everything outside the selection. */
  context: ContextMode;
}

export const DEFAULT_SECTION: SectionState = {
  enabled: false, axis: 'x', normal: [1, 0, 0], offset: 0, flip: false,
};

export const DEFAULT_VIEWPORT: ViewportState = {
  style: 'shaded',
  edgeMode: 'none',
  lensVisible: true,
  lensOpacity: 1,
  findingsOnly: false,
  projection: 'perspective',
  section: DEFAULT_SECTION,
  context: 'all',
};
