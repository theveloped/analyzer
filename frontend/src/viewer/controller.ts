// Imperative viewer controller: owns the Scene3D and the raw mesh arrays,
// reacts to store changes, and runs the active plugin's view mode to paint
// the mesh (the port of viewer.html's update()/boot()).

import {
  fetchCatalog, fetchConfig, fetchHighlights, fetchManifest, fetchOverrides,
  fetchParts,
} from '../api/client';
import type { Manifest } from '../api/types';
import { clearFieldCache, fetchBin, fetchField } from '../fields/fields';
import { getPlugin } from '../registry';
import type { LegendFocus, ViewCtx } from '../registry/types';
import { useStore } from '../state/store';
import { Scene3D } from './scene';

let scene: Scene3D | null = null;
let verts: Float32Array | null = null;
let faces: Uint32Array | null = null;
let normals: Float32Array | null = null;
let repaintQueued = false;
let lastPaintKey = '';
let graphTouched = false; // did the last paint show a graph overlay?

async function loadOverrides(manifest: Manifest) {
  const entries = await Promise.all(
    manifest.results
      .filter((r) => r.overrides_url)
      .map(async (r) => [
        `${r.process}.${r.analysis}.${r.hash}`,
        await fetchOverrides(r.overrides_url!),
      ] as const));
  useStore.getState().set({ overrides: Object.fromEntries(entries) });
}

export function attach(container: HTMLElement) {
  scene = new Scene3D(container);
  scene.onPick = (face, point) => void inspect(face, point);

  // repaint whenever a paint-relevant slice of the store changes
  const unsubscribe = useStore.subscribe(() => schedulePaint());
  void boot();
  return () => {
    unsubscribe();
    scene?.dispose();
    scene = null;
  };
}

async function boot() {
  const store = useStore.getState();
  try {
    const [config, catalog, parts] = await Promise.all([
      fetchConfig(), fetchCatalog(), fetchParts(),
    ]);
    store.set({ catalog, parts });
    const preload = config.preload && parts.find((p) => p.id === config.preload)
      ? config.preload
      : parts.find((p) => p.status === 'meshed')?.id ?? parts[0]?.id ?? null;
    if (preload) await selectPart(preload);
  } catch (err) {
    store.set({ error: String(err) });
  }
}

export async function refreshParts() {
  useStore.getState().set({ parts: await fetchParts() });
}

/** Fly the camera to a legend entry's face group. */
export function flyToFocus(focus: LegendFocus) {
  scene?.flyTo(focus.center, focus.direction, focus.radius);
}

export async function selectPart(partId: string) {
  const store = useStore.getState();
  clearFieldCache();
  verts = null;
  faces = null;
  normals = null;
  lastPaintKey = '';
  scene?.clearMesh();
  store.set({
    partId, manifest: null, meshReady: false, highlights: null,
    legend: [], stats: 'loading…', pick: 'click a face to inspect', error: null,
  });

  try {
    const manifest = await fetchManifest(partId);
    initViewerParams(manifest);
    await loadOverrides(manifest);
    useStore.getState().set({
      manifest,
      manifestVersion: useStore.getState().manifestVersion + 1,
    });
    if (!manifest.mesh) {
      useStore.getState().set({
        stats: 'part not meshed yet — run prep/mesh below', legend: [],
      });
      return;
    }

    const [vertArr, faceIdx, faceNormals, highlights] = await Promise.all([
      fetchBin(manifest.mesh.verts_url, Float32Array),
      fetchBin(manifest.mesh.faces_url, Uint32Array),
      fetchBin(manifest.mesh.normals_url, Float32Array),
      manifest.highlights_url ? fetchHighlights(manifest.highlights_url) : null,
    ]);
    verts = vertArr;
    faces = faceIdx;
    normals = faceNormals;
    scene?.setMesh(vertArr, faceIdx);
    scene?.frame(firstDirection(manifest));
    useStore.getState().set({ meshReady: true, highlights, stats: '' });
  } catch (err) {
    useStore.getState().set({ error: String(err), stats: '' });
  }
}

/** Re-scan the workdir after a job finishes; reload the mesh if it changed. */
export async function refreshManifest() {
  const { partId, manifest } = useStore.getState();
  if (!partId) return;
  const fresh = await fetchManifest(partId);
  const meshChanged = JSON.stringify(fresh.mesh?.counts)
    !== JSON.stringify(manifest?.mesh?.counts);
  if (meshChanged) {
    await refreshParts();
    await selectPart(partId); // mesh arrays are stale, reload everything
    return;
  }
  clearFieldCache(); // fields may have been recomputed under the same URL
  await loadOverrides(fresh);
  const highlights = fresh.highlights_url
    ? await fetchHighlights(fresh.highlights_url) : null;
  useStore.getState().set({
    manifest: fresh,
    manifestVersion: useStore.getState().manifestVersion + 1,
    highlights,
  });
}

function firstDirection(manifest: Manifest): number[] | null {
  const withDir = manifest.fields.find((f) => f.params.direction != null);
  return withDir ? manifest.directions[withDir.params.direction] ?? null : null;
}

function initViewerParams(manifest: Manifest) {
  const store = useStore.getState();
  const viewerParams: Record<string, Record<string, any>> = {};
  for (const process of store.catalog) {
    const plugin = getPlugin(process.id);
    if (plugin) viewerParams[process.id] = plugin.defaults(manifest);
  }
  store.set({ viewerParams });
}

function buildCtx(): ViewCtx | null {
  const { manifest, processId, viewerParams, highlights } = useStore.getState();
  if (!manifest || !verts || !faces || !normals || !scene) return null;
  const theScene = scene;
  const theVerts = verts;
  const theFaces = faces;
  const theNormals = normals;
  return {
    manifest,
    directions: manifest.directions,
    verts: theVerts,
    faces: theFaces,
    normals: theNormals,
    faceCount: theFaces.length / 3,
    params: viewerParams[processId] ?? {},
    highlights,
    getField: fetchField,
    paintFaces: (colorOf) => theScene.paintFaces(colorOf),
    paintCorners: (colorOf) => theScene.paintCorners(colorOf),
    setLines: (positions, color, depthTest) =>
      theScene.setLines(positions, color, depthTest),
    setArrows: (arrows) => theScene.setArrows(arrows),
    setGraph: (key, nodes, edges, radii) => {
      graphTouched = true;
      theScene.setGraph(key, nodes, edges, radii);
    },
    paintGraph: (colorOf) => theScene.paintGraph(colorOf),
    setMeshOpacity: (alpha) => theScene.setMeshOpacity(alpha),
  };
}

function paintKey(): string {
  const s = useStore.getState();
  return JSON.stringify([
    s.partId, s.processId, s.modeId, s.viewerParams[s.processId],
    s.manifestVersion, s.meshReady, s.highlights?.length ?? -1, s.overrides,
  ]);
}

export function schedulePaint(force = false) {
  if (force) lastPaintKey = '';
  if (repaintQueued) return;
  repaintQueued = true;
  queueMicrotask(async () => {
    repaintQueued = false;
    const key = paintKey();
    if (key === lastPaintKey) return;
    lastPaintKey = key;
    await repaint();
  });
}

async function repaint() {
  const store = useStore.getState();
  const ctx = buildCtx();
  if (!ctx || !store.meshReady) return;
  const plugin = getPlugin(store.processId);
  const mode = plugin?.modes.find((m) => m.id === store.modeId) ?? plugin?.modes[0];
  if (!plugin || !mode) return;
  graphTouched = false;
  scene?.setMeshOpacity(1);
  try {
    scene?.clearOverlays();
    const info = await mode.paint(ctx);
    useStore.getState().set({ legend: info.legend, stats: info.stats ?? '', error: null });
  } catch (err) {
    scene?.clearOverlays();
    ctx.paintFaces(() => [0.87, 0.9, 0.92]);
    useStore.getState().set({
      legend: [], stats: `⚠ ${err instanceof Error ? err.message : err}`,
    });
  } finally {
    // modes that showed a graph re-key it every paint; anyone else clears it
    if (!graphTouched) scene?.clearGraph();
  }
}

async function inspect(face: number, point: [number, number, number]) {
  const store = useStore.getState();
  const ctx = buildCtx();
  if (!ctx) return;
  const plugin = getPlugin(store.processId);
  if (plugin?.onPick?.(face, point, ctx)) return; // consumed (e.g. gate placement)

  // the active mode may consume the click (e.g. assignment toggling)
  const mode = plugin?.modes.find((m) => m.id === store.modeId);
  if (mode?.onPick) {
    try {
      if (await mode.onPick(face, ctx)) schedulePaint(true);
    } catch (err) {
      useStore.getState().set({ error: String(err) });
    }
  }

  const lines = [
    `face ${face}  verts ${ctx.faces[3 * face]}, ${ctx.faces[3 * face + 1]}, ${ctx.faces[3 * face + 2]}`,
  ];
  try {
    if (plugin?.inspect) lines.push(...await plugin.inspect(face, ctx));
    if (ctx.highlights) {
      lines.push(`in highlights.json: ${new Set(ctx.highlights).has(face) ? 'yes' : 'no'}`);
    }
  } catch (err) {
    lines.push(`⚠ ${err instanceof Error ? err.message : err}`);
  }
  useStore.getState().set({ pick: lines.join('\n') });
}
