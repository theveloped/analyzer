import { Axis3d, type LucideIcon } from 'lucide-react';
import { useStore } from '../state/store';
import { useV2 } from './store';

/**
 * A STUDY is the third surface beside lenses and checks
 * (docs/ROUTE-ARCHITECTURE.md): a broad comparison over many candidates at
 * once. A lens paints one thing over the model; a check judges one thing
 * against a policy; a study lays the candidates out side by side so the
 * engineer can pick.
 *
 * Opened from the left rail, rendered in the right one. Studies are
 * exploration, not plan state — nothing is persisted by opening one.
 */

export interface Study {
  id: string;
  label: string;
  blurb: string;
  icon: LucideIcon;
  /** Activated with the study so the viewer shows what the table is about. */
  lens?: { processId: string; modeId: string };
}

export const STUDIES: Study[] = [
  {
    id: 'machining_directions',
    label: 'Machining directions',
    blurb: 'Every candidate direction side by side: what it sees, whether it '
      + 'turns, and what each tool reaches from it.',
    icon: Axis3d,
    lens: { processId: 'directions', modeId: 'directions' },
  },
];

export function studyById(id: string | null): Study | null {
  return STUDIES.find((s) => s.id === id) ?? null;
}

export function useActiveStudy(): Study | null {
  return studyById(useV2((s) => s.activeStudy));
}

export function openStudy(study: Study): void {
  useV2.getState().setActiveStudy(study.id);
  if (!study.lens) return;
  const store = useStore.getState();
  // open on the bare model: the lens is active so rows can add their arrow
  // back, but a wall of every candidate at once tells you nothing
  store.setViewerParam(study.lens.processId, 'shownKeys', []);
  store.set({ processId: study.lens.processId, modeId: study.lens.modeId });
}

export function closeStudy(): void {
  useV2.getState().setActiveStudy(null);
  // hand the lens back its normal behaviour: show every candidate again
  useStore.getState().setViewerParam('directions', 'shownKeys', null);
}

/** Draw exactly these candidates' arrows (by `GeneratedDir.key`). */
export function showArrows(keys: string[]): void {
  useStore.getState().setViewerParam('directions', 'shownKeys', keys);
}

/** Back to the arrows view. A cell click leaves the viewer on a CNC lens, so
 * without this the next row click would set arrows no active mode draws. */
export function showArrowsLens(): void {
  const store = useStore.getState();
  if (store.processId === 'directions' && store.modeId === 'directions') return;
  store.set({ processId: 'directions', modeId: 'directions' });
}
