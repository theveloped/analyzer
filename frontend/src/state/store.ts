import { create } from 'zustand';
import type { Job, Manifest, Part, ProcessInfo } from '../api/types';
import type { ColorBar, LegendEntry } from '../registry/types';

export interface AppState {
  catalog: ProcessInfo[];
  parts: Part[];
  partId: string | null;
  manifest: Manifest | null;
  manifestVersion: number; // bumped on refresh so the painter re-runs
  highlights: number[] | null;
  meshReady: boolean;

  processId: string;
  modeId: string;
  /** Viewer-side params (thresholds, selections) per process id. */
  viewerParams: Record<string, Record<string, any>>;

  legend: LegendEntry[];
  /** Continuous colour scale for the active heatmap (null for other views). */
  colorbar: ColorBar | null;
  stats: string;
  pick: string;
  error: string | null;
  jobs: Job[];

  /** Assignment overrides keyed `${process}.${analysis}.${hash}` →
      option (string) → brep face id (string) → feature index. */
  overrides: Record<string, Record<string, Record<string, number>>>;

  set: (partial: Partial<AppState>) => void;
  setViewerParam: (processId: string, name: string, value: any) => void;
  setOverride: (key: string, option: number, brepId: number,
                feature: number | null) => void;
}

export const useStore = create<AppState>()((set) => ({
  catalog: [],
  parts: [],
  partId: null,
  manifest: null,
  manifestVersion: 0,
  highlights: null,
  meshReady: false,

  processId: 'cnc',
  modeId: 'unified',
  viewerParams: {},

  legend: [],
  colorbar: null,
  stats: '',
  pick: 'click a face to inspect',
  error: null,
  jobs: [],
  overrides: {},

  set: (partial) => set(partial),
  setViewerParam: (processId, name, value) =>
    set((state) => ({
      viewerParams: {
        ...state.viewerParams,
        [processId]: { ...state.viewerParams[processId], [name]: value },
      },
    })),
  setOverride: (key, option, brepId, feature) =>
    set((state) => {
      const forResult = { ...(state.overrides[key] ?? {}) };
      const forOption = { ...(forResult[String(option)] ?? {}) };
      if (feature === null) delete forOption[String(brepId)];
      else forOption[String(brepId)] = feature;
      forResult[String(option)] = forOption;
      return { overrides: { ...state.overrides, [key]: forResult } };
    }),
}));
