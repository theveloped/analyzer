// The PMI editor's face-pick tool: while the editor has an active pick target,
// it owns mesh clicks (BEFORE plugin/mode onPick), resolves the clicked fine
// face to its 0-based BREP id — the exact space pmi.json face_ids live in and
// step_export._LabelResolver inverts — and toggles it into the target entity's
// geometry set. Mirrors the measure tool's bridge; the v1 app never imports it.

import { fetchField } from '../../fields/fields';
import { effectiveDescriptor } from '../../splits/splits';
import { useStore } from '../../state/store';
import { setPickInterceptor } from '../../viewer/controller';
import { usePmiEdit } from './pmiEditStore';

let initialized = false;
let interceptorOn = false;

function interceptor(face: number, _point: [number, number, number]): boolean {
  const manifest = useStore.getState().manifest;
  const desc = manifest ? effectiveDescriptor(manifest) : undefined;
  if (!desc) return true; // consumed, but no BREP ids to map (e.g. STL) — ignore
  void fetchField(desc)
    .then((ids) => usePmiEdit.getState().pickFace(ids[face]))
    .catch(() => { /* no BREP ids — nothing to attach */ });
  return true;
}

function sync(active: boolean) {
  if (active === interceptorOn) return;
  interceptorOn = active;
  setPickInterceptor(active ? interceptor : null);
}

/** Wire once per app: the editor's pick target drives the interceptor, and a
 * part switch clears any dangling pick (the ids belonged to the old geometry). */
export function initPmiPickTool() {
  if (initialized) return;
  initialized = true;
  usePmiEdit.subscribe((s, prev) => {
    if (s.pick !== prev.pick) sync(s.active && s.pick !== null);
  });
  let lastPart = useStore.getState().partId;
  useStore.subscribe((s) => {
    if (s.partId !== lastPart) {
      lastPart = s.partId;
      if (usePmiEdit.getState().active) usePmiEdit.getState().close();
    }
  });
  sync(usePmiEdit.getState().active && usePmiEdit.getState().pick !== null);
}
