import { create } from 'zustand';
import { DEFAULT_VIEWPORT, type ViewportState } from '../viewer/viewportState';
import { ANALYSES, defaultCompute } from './analyses';
import type { BandBound } from './fieldLenses';

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

  setAdvanced: (advanced: boolean) => void;
  toggleTheme: () => void;
  setCompute: (analysisId: string, key: string, value: unknown) => void;
  setActiveCheck: (id: string | null) => void;
  setBand: (lensKey: string, band: { lo: BandBound; hi: BandBound }) => void;
  setViewport: (patch: Partial<ViewportState>) => void;
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

  setAdvanced: (advanced) => set({ advanced }),
  setViewport: (patch) =>
    set((s) => ({ viewport: { ...s.viewport, ...patch } })),
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
