// Serializable viewport state: HOW the part is rendered and sectioned,
// independent of which lens paints it (the lens decides WHAT is shown).
// Owned by the v2 store; the viewer controller receives it through
// setViewportState and applies it to the scene. Lens, scope, viewport and
// interaction tool are orthogonal — switching one never resets the others.

export type RenderStyle = 'solid' | 'mesh' | 'xray';
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
  /** Show the true BREP boundary polylines. */
  brepEdges: boolean;
  /** Opacity of the lens colours on non-finding faces (0 = hidden). */
  lensOpacity: number;
  /** Opacity of the lens colours on FINDING faces — independent, so
   * findings can stay fully visible while the rest fades (or vice versa). */
  findingsOpacity: number;
  projection: Projection;
  section: SectionState;
  /** What happens to everything outside the selection. */
  context: ContextMode;
}

export const DEFAULT_SECTION: SectionState = {
  enabled: false, axis: 'x', normal: [1, 0, 0], offset: 0, flip: false,
};

export const DEFAULT_VIEWPORT: ViewportState = {
  style: 'solid',
  brepEdges: false,
  lensOpacity: 1,
  findingsOpacity: 1,
  projection: 'perspective',
  section: DEFAULT_SECTION,
  context: 'all',
};
