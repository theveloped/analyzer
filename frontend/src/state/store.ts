import { create } from 'zustand';
import type { Job, Manifest, Part, ProcessInfo } from '../api/types';
import type { LegendEntry } from '../registry/types';

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
  stats: string;
  pick: string;
  error: string | null;
  jobs: Job[];

  set: (partial: Partial<AppState>) => void;
  setViewerParam: (processId: string, name: string, value: any) => void;
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
  stats: '',
  pick: 'click a face to inspect',
  error: null,
  jobs: [],

  set: (partial) => set(partial),
  setViewerParam: (processId, name, value) =>
    set((state) => ({
      viewerParams: {
        ...state.viewerParams,
        [processId]: { ...state.viewerParams[processId], [name]: value },
      },
    })),
}));
