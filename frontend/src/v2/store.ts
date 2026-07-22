import { create } from 'zustand';
import type { MeasureFrame, MeasurePick } from '../viewer/measure';
import { DEFAULT_VIEWPORT, type ViewportState } from '../viewer/viewportState';
import { ANALYSES, defaultCompute } from './analyses';
import type { BandBound } from './fieldLenses';

export interface MeasureState {
  /** The Measure interaction tool owns mesh clicks while active. */
  active: boolean;
  a: MeasurePick | null;
  b: MeasurePick | null;
  /** How the A→B delta decomposes into component legs. */
  frame: MeasureFrame;
}

/**
 * v2-only UI state. The 3D viewer, part manifest, jobs, legend and stats all
 * live in the shared `state/store.ts` (reused from the original app); this
 * store only holds what the new shell adds on top: the advanced-mode reveal,
 * theme, and the per-analysis compute parameters an engineer can override.
 */

export interface V2State {
  /** Reveal computational-geometry controls and advanced analyses. */
  advanced: boolean;
  theme: 'light' | 'dark';
  /** Compute params per analysis id (backend job payload). */
  compute: Record<string, Record<string, unknown>>;
  /** The plan check the right rail is scoped to (several checks can share
   * one analysis, so the active modeId alone cannot identify it). */
  activeCheckId: string | null;
  /** Highlight-band bounds PER LENS — each lens keeps its own band; editing
   * one must never bleed into another. */
  bands: Record<string, { lo: BandBound; hi: BandBound }>;
  /** How the viewport renders/sections the part — orthogonal to the lens and
   * check scope: none of the lens/check switchers may touch it. */
  viewport: ViewportState;
  /** The two-point measurement session — same orthogonality rule. */
  measure: MeasureState;
  /** The section controls rail (right side, like the measure rail). */
  sectionRailOpen: boolean;

  setAdvanced: (advanced: boolean) => void;
  setSectionRailOpen: (open: boolean) => void;
  toggleTheme: () => void;
  setCompute: (analysisId: string, key: string, value: unknown) => void;
  setActiveCheck: (id: string | null) => void;
  setBand: (lensKey: string, band: { lo: BandBound; hi: BandBound }) => void;
  setViewport: (patch: Partial<ViewportState>) => void;
  setMeasureActive: (active: boolean) => void;
  setMeasureFrame: (frame: MeasureFrame) => void;
  /** FSM: empty → A; A → B; A+B → new A (third click starts over). */
  pushMeasurePick: (pick: MeasurePick) => void;
  /** Drop the picks but stay active. */
  clearMeasurePicks: () => void;
}

const initialCompute = Object.fromEntries(
  ANALYSES.map((a) => [a.id, defaultCompute(a)]),
);

export const useV2 = create<V2State>()((set) => ({
  advanced: false,
  theme: 'light',
  compute: initialCompute,
  activeCheckId: null,
  bands: {},
  viewport: DEFAULT_VIEWPORT,
  measure: { active: false, a: null, b: null, frame: 'xyz' },
  sectionRailOpen: false,

  setAdvanced: (advanced) => set({ advanced }),
  setSectionRailOpen: (sectionRailOpen) => set({ sectionRailOpen }),
  setViewport: (patch) =>
    set((s) => ({ viewport: { ...s.viewport, ...patch } })),
  setMeasureActive: (active) =>
    set((s) => ({ measure: { ...s.measure, active } })),
  setMeasureFrame: (frame) =>
    set((s) => ({ measure: { ...s.measure, frame } })),
  pushMeasurePick: (pick) =>
    set((s) => ({
      measure: !s.measure.a || s.measure.b
        ? { ...s.measure, a: pick, b: null }
        : { ...s.measure, b: pick },
    })),
  clearMeasurePicks: () =>
    set((s) => ({ measure: { ...s.measure, a: null, b: null } })),
  setActiveCheck: (activeCheckId) => set({ activeCheckId }),
  setBand: (lensKey, band) =>
    set((s) => ({ bands: { ...s.bands, [lensKey]: band } })),
  toggleTheme: () => set((s) => ({ theme: s.theme === 'light' ? 'dark' : 'light' })),
  setCompute: (analysisId, key, value) =>
    set((s) => ({
      compute: {
        ...s.compute,
        [analysisId]: { ...s.compute[analysisId], [key]: value },
      },
    })),
}));
