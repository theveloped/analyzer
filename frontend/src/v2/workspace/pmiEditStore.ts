import { create } from 'zustand';
import type { PmiData } from '../../api/types';
import { emptyDoc, toggleFace, type EntityKey } from './pmiModel';

/** Which entity's geometry set the next viewer face-pick feeds into. */
export interface PmiPickTarget {
  key: EntityKey;
  id: number;
  field: 'face_ids' | 'secondary_face_ids';
  /** shown in the pick prompt, e.g. "Position ⌖ target faces" */
  label: string;
}

/**
 * The PMI editor's working state — a mutable clone of the part's pmi.json plus
 * the active face-pick target. Separate from the shared viewer store (which
 * only carries the transient highlight params) so a discarded edit never
 * touches persisted state. The viewer face-pick tool (pmiPickTool) routes clicks
 * here; PmiEditor renders and PUTs it.
 */
interface PmiEditState {
  active: boolean;
  doc: PmiData;
  dirty: boolean;
  saving: boolean;
  error: string | null;
  /** warnings from the most recent successful save (authoritative) */
  warnings: string[];
  pick: PmiPickTarget | null;

  open: (doc: PmiData | null) => void;
  close: () => void;
  /** Apply an immutable reducer to the working doc and mark it dirty. */
  apply: (fn: (doc: PmiData) => PmiData) => void;
  setPick: (target: PmiPickTarget | null) => void;
  /** Toggle a picked BREP face id into the current pick target's set. */
  pickFace: (faceId: number) => void;
  setSaving: (saving: boolean) => void;
  setError: (error: string | null) => void;
  markSaved: (warnings: string[]) => void;
}

export const usePmiEdit = create<PmiEditState>((set, get) => ({
  active: false,
  doc: emptyDoc(),
  dirty: false,
  saving: false,
  error: null,
  warnings: [],
  pick: null,

  open: (doc) => set({
    active: true,
    doc: doc ? structuredClone(doc) : emptyDoc(),
    dirty: false, error: null, warnings: doc?.warnings ?? [], pick: null,
  }),
  close: () => set({ active: false, pick: null, error: null }),
  apply: (fn) => set((s) => ({ doc: fn(s.doc), dirty: true, error: null })),
  setPick: (pick) => set({ pick }),
  pickFace: (faceId) => {
    const { pick } = get();
    if (!pick) return;
    set((s) => ({
      doc: toggleFace(s.doc, pick.key, pick.id, faceId, pick.field),
      dirty: true,
    }));
  },
  setSaving: (saving) => set({ saving }),
  setError: (error) => set({ error, saving: false }),
  markSaved: (warnings) => set({ dirty: false, saving: false, warnings, error: null }),
}));
