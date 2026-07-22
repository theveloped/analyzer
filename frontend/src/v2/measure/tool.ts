// The Measure interaction tool: registers a pick interceptor with the viewer
// controller while active (owning mesh clicks BEFORE plugin onPick handlers,
// with the current lens left visible underneath), builds MeasurePicks from
// the posed hit point + live surface normal + effective BREP id, and keeps
// the scene's annotation layer in sync with the session state. This module
// is the only bridge between the v2 store and the viewer controller for
// measuring — the v1 app never imports it.

import { fetchField } from '../../fields/fields';
import { effectiveDescriptor } from '../../splits/splits';
import { useStore } from '../../state/store';
import {
  faceNormal, setMeasureAnnotations, setPickInterceptor,
} from '../../viewer/controller';
import type { MeasurePick } from '../../viewer/measure';
import { disarmSectionSnap } from '../tools/sectionSnap';
import { useV2 } from '../store';

let initialized = false;
let interceptorOn = false;

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') useV2.getState().setMeasureActive(false);
}

function interceptor(
  face: number, point: [number, number, number],
): boolean {
  // the normal must be read synchronously — it is the LIVE (posed) normal
  // at click time; the BREP id resolves async and patches the pick after
  const pick: MeasurePick = {
    point, faceIndex: face, brepFace: null, normal: faceNormal(face),
  };
  const manifest = useStore.getState().manifest;
  const desc = manifest ? effectiveDescriptor(manifest) : undefined;
  useV2.getState().pushMeasurePick(pick);
  if (desc) {
    void fetchField(desc).then((ids) => {
      const m = useV2.getState().measure;
      const patched = { ...pick, brepFace: ids[face] };
      if (m.a === pick) useV2.setState({ measure: { ...m, a: patched } });
      else if (m.b === pick) useV2.setState({ measure: { ...m, b: patched } });
    }).catch(() => { /* no BREP ids (e.g. STL) — keep null */ });
  }
  return true;
}

function sync(measure: import('../store').MeasureState) {
  if (measure.active !== interceptorOn) {
    interceptorOn = measure.active;
    if (measure.active) disarmSectionSnap(); // one pick owner at a time
    setPickInterceptor(measure.active ? interceptor : null);
    if (measure.active) window.addEventListener('keydown', onKeyDown);
    else window.removeEventListener('keydown', onKeyDown);
  }
  setMeasureAnnotations(measure.a, measure.b, measure.frame);
}

/** Re-push the session's annotations into a freshly attached scene (the
 * remount pattern the theme and viewport state also follow). */
export function syncMeasureAnnotations() {
  const { measure } = useV2.getState();
  setMeasureAnnotations(measure.a, measure.b, measure.frame);
}

/** Wire the tool once per app: session → interceptor/annotations, and a
 * part switch drops picks made on the previous part's geometry. */
export function initMeasureTool() {
  if (initialized) return;
  initialized = true;
  useV2.subscribe((s, prev) => {
    if (s.measure !== prev.measure) sync(s.measure);
  });
  let lastPart = useStore.getState().partId;
  useStore.subscribe((s) => {
    if (s.partId !== lastPart) {
      lastPart = s.partId;
      useV2.getState().clearMeasurePicks();
    }
  });
  sync(useV2.getState().measure);
}
