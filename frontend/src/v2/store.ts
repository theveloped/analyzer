import { create } from 'zustand';
import { ANALYSES, defaultCompute } from './analyses';

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

  setAdvanced: (advanced: boolean) => void;
  toggleTheme: () => void;
  setCompute: (analysisId: string, key: string, value: unknown) => void;
}

const initialCompute = Object.fromEntries(
  ANALYSES.map((a) => [a.id, defaultCompute(a)]),
);

export const useV2 = create<V2State>()((set) => ({
  advanced: false,
  theme: 'light',
  compute: initialCompute,

  setAdvanced: (advanced) => set({ advanced }),
  toggleTheme: () => set((s) => ({ theme: s.theme === 'light' ? 'dark' : 'light' })),
  setCompute: (analysisId, key, value) =>
    set((s) => ({
      compute: {
        ...s.compute,
        [analysisId]: { ...s.compute[analysisId], [key]: value },
      },
    })),
}));
