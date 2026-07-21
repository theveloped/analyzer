// Imperative viewer controller: owns the Scene3D and the raw mesh arrays,
// reacts to store changes, and runs the active plugin's view mode to paint
// the mesh (the port of viewer.html's update()/boot()).

import {
  fetchCatalog, fetchConfig, fetchHighlights, fetchManifest,
  fetchOverrides, fetchParts, uploadPart,
} from '../api/client';
import type { Manifest } from '../api/types';
import { brepFacesOf, currentDirections } from '../processes/directions/build';
import { clearFieldCache, fetchBin, fetchField } from '../fields/fields';
import { getPlugin } from '../registry';
import type { LegendFocus, ViewCtx } from '../registry/types';
import { useStore } from '../state/store';
import { edgeDescriptors } from '../splits/splits';
import { nakedEdgeSegments } from './brepEdges';
import { setColorBackground, VIEWER_BG, type ViewerBackground } from './colormaps';
import { Scene3D } from './scene';
import { DEFAULT_VIEWPORT, type ViewportState } from './viewportState';

let scene: Scene3D | null = null;
// how the scene renders/sections, independent of the active lens. The v1 app
// never changes it (classic look); the v2 shell pushes its store slice here.
let viewportState: ViewportState = DEFAULT_VIEWPORT;
// which (part, manifest) the scene's BREP boundary polylines were built for
let brepEdgesKey = '';
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
  scene.setViewport(viewportState);
  scene.onPick = (face, point) => void inspect(face, point);
  // arrow clicks (directions view): stash the selected arrow + screen position
  // so the tooltip can show its provenance / delete control, and highlight the
  // BREP faces the direction was built from (hole / surface-normal sources).
  // Only consume the click (return true) when an arrow is hit.
  scene.onPickArrow = (index, screen) => {
    const store = useStore.getState();
    if (store.processId !== 'directions') return false;
    const dir = index >= 0 ? currentDirections[index] : null;
    store.setViewerParam('directions', 'selectedArrow',
      dir ? { index, x: screen[0], y: screen[1] } : null);
    store.setViewerParam('directions', 'highlightBrep', dir ? brepFacesOf(dir) : []);
    return index >= 0;
  };

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

/** Upload a STEP/STL file, refresh the part list and select the new part. */
export async function uploadAndSelect(file: File) {
  const part = await uploadPart(file);
  await refreshParts();
  await selectPart(part.id);
}

/** Fly the camera to a legend entry's face group. */
export function flyToFocus(focus: LegendFocus) {
  scene?.flyTo(focus.center, focus.direction, focus.radius);
}

/** Snapshot the viewer (PNG + camera pose) for report evidence. */
export function captureViewer() {
  return scene?.capture() ?? null;
}

/** Apply a new viewport state (render style, projection, section, …). Does
 * NOT schedule a repaint — the lens data is unchanged; the scene re-composes
 * its layers directly. */
export function setViewportState(vs: ViewportState) {
  viewportState = vs;
  scene?.setViewport(vs);
  void ensureBrepEdges();
}

/** Fit the whole part in view, keeping the current view direction. */
export function fitPart() {
  scene?.fit();
}

/** Part bounding box (posed) — sizes the section offset slider. */
export function partBounds() {
  return scene?.getBounds() ?? null;
}

/** Camera view direction — seeds the "custom" section plane normal. */
export function viewDirection(): [number, number, number] {
  return scene?.getViewDirection() ?? [0, 0, 1];
}

/** Fit the current legend-group selection in view. */
export function fitSelection() {
  scene?.fitSelection();
}

/** Select a legend entry's face group (fit-selection/isolate/ghost act on
 * it); null clears. Toggled from the v2 legend. */
export function selectLegendGroup(label: string, faces: number[] | null) {
  const selection = faces && faces.length ? { label, faces } : null;
  useStore.getState().set({ selection });
  scene?.setSelectionFaces(selection ? selection.faces : null);
}

/** Fetch + install the BREP boundary polylines when the edge mode wants
 * them: the served interior segments (subface-aware) plus the naked mesh
 * boundary edges the backend omits. STL parts have neither — no-op. */
async function ensureBrepEdges() {
  const { manifest, partId, manifestVersion } = useStore.getState();
  if (!scene || !manifest || !partId || !verts || !faces) return;
  if (viewportState.edgeMode !== 'brep') return;
  const key = `${partId}:${manifestVersion}`;
  if (key === brepEdgesKey) return;
  const descriptors = edgeDescriptors(manifest);
  if (!descriptors) return;
  brepEdgesKey = key;
  try {
    const interior = await fetchField(descriptors.edges) as Float32Array;
    const naked = nakedEdgeSegments(verts, faces);
    const segments = new Float32Array(interior.length + naked.length);
    segments.set(interior);
    segments.set(naked, interior.length);
    scene?.setBrepEdges(segments);
  } catch (err) {
    brepEdgesKey = '';
    useStore.getState().set({ error: String(err) });
  }
}

/** Switch the viewer between light and dark: repaints the background and the
 * background-matched colour-map variants (batlowW/K, vik/berlin). */
export function setViewerTheme(bg: ViewerBackground) {
  setColorBackground(bg);
  scene?.setBackground(VIEWER_BG[bg], bg);
  schedulePaint(true);
}

/** Run an action that needs the live ViewCtx (e.g. a controls button);
    repaint if it reports a change. */
export async function runCtxAction(fn: (ctx: ViewCtx) => Promise<boolean>): Promise<void> {
  const ctx = buildCtx();
  if (!ctx) return;
  try {
    if (await fn(ctx)) schedulePaint(true);
  } catch (err) {
    useStore.getState().set({ error: err instanceof Error ? err.message : String(err) });
  }
}

export async function selectPart(partId: string) {
  const store = useStore.getState();
  clearFieldCache();
  verts = null;
  faces = null;
  normals = null;
  lastPaintKey = '';
  brepEdgesKey = '';
  scene?.clearMesh();
  store.set({
    partId, manifest: null, meshReady: false, highlights: null, selection: null,
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
    // render the fine mesh when present, else the cheap coarse preview so the
    // part is visible (and pickable) immediately after the first-load bundle
    const meshSrc = manifest.mesh ?? manifest.coarse_mesh ?? null;
    if (!meshSrc) {
      useStore.getState().set({
        stats: 'part not meshed yet — run prep/mesh below', legend: [],
      });
      return;
    }
    const previewing = !manifest.mesh && !!manifest.coarse_mesh;

    const [vertArr, faceIdx, faceNormals, highlights] = await Promise.all([
      fetchBin(meshSrc.verts_url, Float32Array),
      fetchBin(meshSrc.faces_url, Uint32Array),
      fetchBin(meshSrc.normals_url, Float32Array),
      manifest.highlights_url ? fetchHighlights(manifest.highlights_url) : null,
    ]);
    verts = vertArr;
    faces = faceIdx;
    normals = faceNormals;
    scene?.setMesh(vertArr, faceIdx, faceNormals);
    scene?.frame(firstDirection(manifest));
    useStore.getState().set({
      meshReady: true, highlights,
      stats: previewing ? 'coarse preview — fine mesh runs on demand' : '',
    });
  } catch (err) {
    useStore.getState().set({ error: String(err), stats: '' });
  }
}

/** Re-scan the workdir after a job finishes; reload the mesh if it changed. */
export async function refreshManifest() {
  const { partId, manifest } = useStore.getState();
  if (!partId) return;
  const fresh = await fetchManifest(partId);
  // reload when the fine mesh appears/changes, and also when the coarse preview
  // first appears (bundle finished) or is superseded by the fine mesh
  const meshChanged = JSON.stringify(fresh.mesh?.counts)
      !== JSON.stringify(manifest?.mesh?.counts)
    || JSON.stringify(fresh.coarse_mesh?.counts)
      !== JSON.stringify(manifest?.coarse_mesh?.counts);
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
    setMeshOpacity: (alpha) => theScene.setLensDisplayHint(alpha),
    setFindings: (isFinding) => theScene.setFindings(isFinding),
    setVertexPositions: (positions, smooth) =>
      theScene.setVertexPositions(positions, smooth),
    addOverlayMesh: (spec) => theScene.addOverlayMesh(spec),
    shiftOverlay: (tag, dz) => theScene.shiftOverlay(tag, dz),
    setAnimator: (fn) => theScene.setAnimator(fn),
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
  scene?.setLensDisplayHint(1);
  scene?.setFindings(null);
  // animation state never outlives a paint: modes re-register in paint()
  scene?.setAnimator(null);
  scene?.setVertexPositions(null);
  try {
    scene?.clearOverlays();
    const info = await mode.paint(ctx);
    useStore.getState().set({
      legend: info.legend, colorbar: info.colorbar ?? null,
      stats: info.stats ?? '', error: null,
    });
  } catch (err) {
    scene?.clearOverlays();
    ctx.paintFaces(() => [0.87, 0.9, 0.92]);
    useStore.getState().set({
      legend: [], colorbar: null, stats: `⚠ ${err instanceof Error ? err.message : err}`,
    });
  } finally {
    // modes that showed a graph re-key it every paint; anyone else clears it
    if (!graphTouched) scene?.clearGraph();
    // re-compose the persistent viewport state over the fresh paint (findings
    // alpha, selection colours, lens display hint)
    scene?.applyRenderState();
    void ensureBrepEdges();
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
