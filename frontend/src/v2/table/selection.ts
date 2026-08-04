// Which candidate directions the study is comparing.
//
// A STUDY PERSISTS NOTHING (docs/CONCEPTS.md): selecting rows is exploration,
// so this lives in the v2 store and never reaches the server. The moment a
// choice is worth keeping, it is recorded by ADDING AN OPERATION — the one
// place the route records a decision.
//
// This used to write a `decisions.directions` slot on every click, which meant
// two records of one choice (the slot and the operation's direction) with
// nothing keeping them honest.

import { useStore } from '../../state/store';
import { useV2 } from '../store';

/** The candidate keys the table is comparing, scoped to the current part. */
export function useSelection(): string[] {
  const partId = useStore((s) => s.partId);
  const selection = useV2((s) => s.selection);
  return selection.part === partId ? selection.keys : [];
}

/** Set the comparison set. Instant — there is nothing to wait for. */
export function selectCandidates(selected: string[]): void {
  useV2.getState().setSelection(useStore.getState().partId, selected);
}
