// The `directions` decision slot.
//
// Candidates are defined for free in the directions lens — a live client-side
// set, no job and no artifact — so this file does NOT build anything. It only
// records which of them the plan is working with, and snapshots enough of each
// candidate (vector, provenance, and the directions.npy row when one exists)
// for the plan to mean something later.
//
// The slot's `value` is derived server-side from `selected`
// (plans.normalize_decisions): it projects to `direction_indices`, which is
// empty until an accessibility run has given the chosen candidates a row. That
// degrades honestly — a selection made before any computation is still a
// recorded decision, it just cannot bind an analysis param yet.

import type { Candidate, DecisionSlot, Manifest } from '../../api/types';
import type { GeneratedDir } from '../../processes/directions/build';
import { useStore } from '../../state/store';
import { useV2 } from '../store';
import { storePlan } from '../workspace/hooks';
import { indexOf } from './columns';

export const DIRECTIONS_SLOT = 'directions';

export function directionsDecision(manifest: Manifest | null): DecisionSlot | null {
  const decision = manifest?.plan?.plan.decisions?.[DIRECTIONS_SLOT];
  return decision && decision.kind === 'direction_set' ? decision : null;
}

export function useDirectionsDecision(): DecisionSlot | null {
  return directionsDecision(useStore((s) => s.manifest));
}

/** Selected candidate ids as an array (the slot allows a bare id). */
export function selectedIds(decision: DecisionSlot | null): string[] {
  if (!decision) return [];
  const { selected } = decision;
  if (selected == null) return [];
  return Array.isArray(selected) ? selected : [selected];
}

function snapshot(
  manifest: Manifest | null, candidate: GeneratedDir,
): Candidate {
  const index = indexOf(manifest, candidate.vector);
  const provenance = candidate.provenances[0];
  return {
    id: candidate.key,
    vector: candidate.vector,
    label: provenance?.label ?? 'direction',
    source: provenance?.source ?? 'uniform',
    ...(index == null ? {} : { index }),
  };
}

/** Record the selection. The candidate list is rewritten from the live set
 * each time, because that set is the source of truth — the decision is a
 * record of a choice, not a second place candidates live. */
async function writeSelection(
  candidates: GeneratedDir[], selected: string[],
): Promise<void> {
  const manifest = useStore.getState().manifest;
  const section = manifest?.plan;
  if (!section) return;
  const previous = directionsDecision(manifest);
  const next: DecisionSlot = {
    kind: 'direction_set',
    candidates: candidates.map((c) => snapshot(manifest, c)),
    selected,
    state: previous?.state ?? 'provisional',
  };
  const plan = {
    ...section.plan,
    decisions: { ...section.plan.decisions, [DIRECTIONS_SLOT]: next },
  };
  await storePlan(plan, section.plan.revision);
}

// Each selection change is a plan revision, so the writes have to be
// single-file: `storePlan` sends the revision it read, and a second write
// issued before the first one's manifest refresh lands would send a stale
// one and 409. One in flight at a time, with bursts (a shift-range, a fast
// series of ctrl-clicks) collapsing to the last state — the intermediate
// selections are not interesting, only where the user stopped.
let pendingWrite: { candidates: GeneratedDir[]; selected: string[] } | null = null;
let writing = false;

async function flushSelection(): Promise<void> {
  if (writing) return;
  writing = true;
  try {
    while (pendingWrite) {
      const job = pendingWrite;
      pendingWrite = null;
      await writeSelection(job.candidates, job.selected);
    }
  } finally {
    writing = false;
  }
}

/** The selection the table renders: the optimistic local copy, re-seeded from
 * the plan whenever it belongs to another part. */
export function useSelection(): string[] {
  const partId = useStore((s) => s.partId);
  const selection = useV2((s) => s.selection);
  const decision = useDirectionsDecision();
  if (selection.part === partId) return selection.keys;
  return selectedIds(decision);
}

/** Set the selection: instantly on screen, eventually on the plan. */
export function selectCandidates(
  candidates: GeneratedDir[], selected: string[],
): void {
  const partId = useStore.getState().partId;
  useV2.getState().setSelection(partId, selected);
  pendingWrite = { candidates, selected };
  void flushSelection();
}
