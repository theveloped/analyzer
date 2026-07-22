// "Pick target" for the section plane: arms a one-shot pick interceptor;
// the next mesh click snaps the section to the picked geometry — a planar
// face's own plane, the centerline plane of a cylinder/cone/torus, through
// a sphere's center, or (freeform/STL) through the nearest mesh vertex.
// Surface data comes from brep_meta.json (per-BREP-face analytic params),
// looked up through the per-triangle brep_faces field.

import { fetchField } from '../../fields/fields';
import { useStore } from '../../state/store';
import {
  meshArrays, setPickInterceptor, viewDirection,
} from '../../viewer/controller';
import {
  nearestCorner, snapSection, type SurfaceParams,
} from '../../viewer/sectionSnap';
import { useV2 } from '../store';

interface BrepMeta {
  surface_types?: string[];
  surface_params?: (SurfaceParams | null)[];
}

const metaCache = new Map<string, Promise<BrepMeta | null>>();

function fetchBrepMeta(): Promise<BrepMeta | null> {
  const { manifest, manifestVersion } = useStore.getState();
  const url = manifest?.brep_meta_url;
  if (!url) return Promise.resolve(null);
  const key = `${url}:${manifestVersion}`; // re-mesh keeps the same url
  if (!metaCache.has(key)) {
    metaCache.set(key,
      fetch(url).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  return metaCache.get(key)!;
}

let armed = false;

export function sectionSnapArmed() {
  return armed;
}

/** Cancel a pending snap pick (e.g. the measure tool taking over). */
export function disarmSectionSnap() {
  if (!armed) return;
  armed = false;
  setPickInterceptor(null);
}

/** The next mesh click snaps the section plane; one shot. */
export function armSectionSnap() {
  if (armed) return;
  // one pick owner at a time — measuring hands over to the snap
  useV2.getState().setMeasureActive(false);
  armed = true;
  setPickInterceptor((face, point) => {
    armed = false;
    setPickInterceptor(null);
    void applySnap(face, point);
    return true;
  });
}

async function applySnap(face: number, point: [number, number, number]) {
  const { manifest } = useStore.getState();
  let surface: SurfaceParams | null = null;
  // original BREP ids (not subfaces) — brep_meta is indexed by them
  const desc = manifest?.fields.find((f) => f.id === 'brep_faces');
  if (desc && manifest?.brep_meta_url) {
    try {
      const [ids, meta] = await Promise.all([fetchField(desc), fetchBrepMeta()]);
      surface = meta?.surface_params?.[(ids as Uint32Array)[face]] ?? null;
    } catch {
      // fall through to the point snap
    }
  }
  let anchor = point;
  const arrays = meshArrays();
  if (!surface && arrays) {
    anchor = nearestCorner(arrays.verts, arrays.faces, face, point);
  }
  const { viewport, setViewport } = useV2.getState();
  const snap = snapSection(
    surface, anchor, viewDirection(), viewport.section.normal);
  setViewport({
    section: {
      enabled: true, axis: 'custom',
      normal: snap.normal, offset: snap.offset, flip: false,
    },
  });
}
