// Injection molding plugin: membership-based mold assignment (striped
// multi-valid BREP faces, click-to-cycle, parting line on BREP edges),
// rolling-sphere wall thickness and gaps/clearance heatmaps, and the
// wall-thickness skeleton graph with interactive fill flow.

import { putOverrides } from '../../api/client';
import type { FieldDescriptor, Manifest, ResultEntry } from '../../api/types';
import {
  brepFacesMode, COL, faceAttrsMode, faceValues, fade, FocusTracker, heatmapMode,
  pmiMode,
  highlightsMode, isolineSegments, nextSetBit, nthSetBit, percentile,
  popcount, rampColor, regionColor, smoothVertexField,
} from '../../colorizers/core';
import type {
  LegendEntry, PaintInfo, ProcessPlugin, RGB, ViewCtx, ViewMode,
} from '../../registry/types';
import { useStore } from '../../state/store';
import {
  buildAdjacency, dijkstra, loadSkeleton, meshSpecNote, nearestNode,
  SENTINEL, skeletonResults,
} from './skeleton';
import {
  loadSprue, markerGraph, sprueResults, weldSegments, type Proposal,
} from './sprue';
import {
  loadSticking, simulateCached, stickingResults,
  type EjectorSimResponse, type Pin,
} from './ejector';
import {
  flowFillResults, flowVoxelResults, FROZEN_OK, FROZEN_UNJUDGED, loadFill,
  loadVertVoxel, loadVoxelGrid, voxelPositions, type FlowFill,
} from './voxels';
import { runAnalysisJob } from '../../viewer/jobs';
import {
  drawSplitOverlays, edgeDescriptors, effectiveDescriptor, faceLabel,
  handleSplitPick, type SplitHost,
} from '../../splits/splits';
import { SplitControls } from '../../splits/SplitControls';
import { optimizeParting } from '../parting';
import { runCtxAction } from '../../viewer/controller';

const CONFLICT_FEATURE = 254;
const INTERNAL_FEATURE = 255;

// keep in sync with MOLD_SCHEMA in processes/injection_molding.py
const MOLD_SCHEMA = 4;

const ARROW_COLORS: Record<string, RGB> = {
  main_a: [0.44, 0.64, 0.86], // side A
  main_b: [0.62, 0.8, 0.58], // side B
};

function resultsFor(manifest: Manifest, analysis: string) {
  return manifest.results.filter(
    (r) => r.process === 'injection_molding' && r.analysis === analysis
      && (analysis !== 'mold_orientation' || r.stats.schema === MOLD_SCHEMA));
}

/** Latest stored result's field descriptor for one npz member. */
function scalarField(ctx: ViewCtx, analysis: string, member: string) {
  const results = resultsFor(ctx.manifest, analysis);
  const result = results[results.length - 1];
  const fieldId = result?.fields.find((f) => f.endsWith(`.${member}`));
  return fieldId ? ctx.manifest.fields.find((f) => f.id === fieldId) ?? null : null;
}

function resolveField(ctx: ViewCtx, result: ResultEntry, member: string) {
  const fieldId = result.fields.find((f) => f.endsWith(`.${member}`));
  return fieldId ? ctx.manifest.fields.find((f) => f.id === fieldId) ?? null : null;
}

/** Result by selector index; -1 / unset = latest (the manifest lists
    results oldest -> newest, so a recompute lands last). */
function pickResult<T>(list: T[], index: number | null | undefined): T | undefined {
  if (index != null && index >= 0 && index < list.length) return list[index];
  return list[list.length - 1];
}

interface AssignmentData {
  result: ResultEntry;
  option: number;
  desc: FieldDescriptor; // membership field (labels/colors in params)
  brepDesc: FieldDescriptor; // effective face ids (subfaces or brep_faces)
  membership: Uint32Array;
  region: Uint32Array;
  valid: Uint32Array;
  defaults: Uint8Array;
  brepIds: Uint32Array;
  current: Uint8Array; // per-brep-face selected feature (override-aware)
  overridesKey: string;
}

async function loadAssignment(ctx: ViewCtx): Promise<AssignmentData> {
  const results = resultsFor(ctx.manifest, 'mold_orientation');
  if (!results.length) {
    const legacy = ctx.manifest.results.some(
      (r) => r.process === 'injection_molding' && r.analysis === 'mold_orientation');
    throw new Error(legacy
      ? 'stored result predates the membership model — re-run mold orientation'
      : 'no mold_orientation result yet — run the analysis below');
  }
  const result = pickResult(results, ctx.params.result)!;
  const option = ctx.params.option ?? 0;

  // effective ids: user sub-faces when splits exist, plain BREP otherwise
  const brepDesc = effectiveDescriptor(ctx.manifest);
  if (!brepDesc) {
    throw new Error('assignment needs BREP face ids — re-mesh the part from its STEP file');
  }
  const desc = resolveField(ctx, result, `membership_${option}`);
  const regionDesc = resolveField(ctx, result, `internal_region_${option}`);
  const validDesc = resolveField(ctx, result, `brep_valid_${option}`);
  const defaultsDesc = resolveField(ctx, result, `brep_default_${option}`);
  if (!desc || !regionDesc || !validDesc || !defaultsDesc) {
    throw new Error('assignment fields missing — re-run mold orientation');
  }

  const [membership, region, valid, defaults, brepIds] = await Promise.all([
    ctx.getField(desc) as Promise<Uint32Array>,
    ctx.getField(regionDesc) as Promise<Uint32Array>,
    ctx.getField(validDesc) as Promise<Uint32Array>,
    ctx.getField(defaultsDesc) as Promise<Uint8Array>,
    ctx.getField(brepDesc) as Promise<Uint32Array>,
  ]);

  const overridesKey = `injection_molding.mold_orientation.${result.hash}`;
  const overrides = useStore.getState().overrides[overridesKey]?.[String(option)] ?? {};
  const current = new Uint8Array(defaults);
  for (const [brepId, feature] of Object.entries(overrides)) {
    const b = Number(brepId);
    if (b < valid.length && ((valid[b] >>> feature) & 1)) current[b] = feature;
  }

  return {
    result, option, desc, brepDesc, membership, region, valid, defaults,
    brepIds, current, overridesKey,
  };
}

/** Split-interaction wiring for the mold assignment view. */
const moldSplitHost: SplitHost = {
  processId: 'injection_molding',
  modeId: 'assignment',
  currentResult: (manifest, params) =>
    pickResult(resultsFor(manifest, 'mold_orientation'), params.result),
  analysisOf: () => 'mold_orientation',
  resultParam: 'result',
};

const assignmentMode: ViewMode = {
  id: 'assignment',
  label: 'Mold orientation assignment',
  async paint(ctx): Promise<PaintInfo> {
    const data = await loadAssignment(ctx);
    const { desc, membership, region, valid, brepIds, current } = data;
    const labels: string[] = desc.params.labels;
    const colors: RGB[] = desc.params.colors;
    const conflictColor: RGB = desc.params.conflict_color;

    // stripe width from the part size; a few stripes per mid-size face
    let min = Infinity;
    let max = -Infinity;
    for (let i = 0; i < ctx.verts.length; i++) {
      if (ctx.verts[i] < min) min = ctx.verts[i];
      if (ctx.verts[i] > max) max = ctx.verts[i];
    }
    const stripeWidth = Math.max((max - min) * 0.03, 1e-6);

    const counts = new Array(labels.length).fill(0);
    let conflictCount = 0;
    let internalCount = 0;
    const { faces, verts } = ctx;
    const tracker = new FocusTracker(ctx); // legend click -> fly to the group

    ctx.paintFaces((f) => {
      const b = brepIds[f];
      // ids past the arrays = new sub-faces the result predates — paint
      // them via the conflict path until the auto re-run lands
      const cat = b < current.length ? current[b] : CONFLICT_FEATURE;
      if (cat === INTERNAL_FEATURE) {
        internalCount++;
        tracker.add(`r${region[f]}`, f);
        return regionColor(region[f]);
      }
      if (cat === CONFLICT_FEATURE) {
        conflictCount++;
        tracker.add('conflict', f);
        // spatially truthful: each triangle shows which feature partially
        // reaches it (faded); unreachable triangles get the conflict color
        const m = membership[f];
        return m ? fade(colors[nthSetBit(m, 0)]) : conflictColor;
      }
      counts[cat]++;
      tracker.add(`c${cat}`, f);
      const v = valid[b];
      const n = popcount(v);
      if (n <= 1) return colors[cat];
      // striped multi-valid face: selected feature strong, others faded
      const a = faces[3 * f];
      const cx = verts[3 * a] + verts[3 * a + 1] + verts[3 * a + 2];
      const idx = ((Math.floor(cx / stripeWidth) % n) + n) % n;
      const feat = nthSetBit(v, idx);
      return feat === cat ? colors[feat] : fade(colors[feat]);
    });

    const legend: LegendEntry[] = labels
      .map((label, i) => ({
        color: colors[i],
        label: `${label} (${counts[i]})`,
        focus: tracker.focus(`c${i}`),
      }))
      .filter((_, i) => counts[i] > 0);
    if (conflictCount) {
      legend.push({
        color: conflictColor,
        label: `conflict / needs split (${conflictCount})`,
        focus: tracker.focus('conflict'),
      });
    }

    // internal undercut regions: real ones individually (click to view),
    // sub-speck ones (< SPECK faces, often single slivers) as one group
    const SPECK = 10;
    const regionKeys = tracker.keys('r')
      .sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
    for (const key of regionKeys) {
      if (tracker.count(key) < SPECK) continue;
      const id = Number(key.slice(1));
      legend.push({
        color: regionColor(id),
        label: `internal undercut ${id} (${tracker.count(key)})`,
        focus: tracker.focus(key),
      });
    }
    const specks = regionKeys.filter((key) => tracker.count(key) < SPECK);
    if (specks.length) {
      const faceTotal = specks.reduce((sum, key) => sum + tracker.count(key), 0);
      legend.push({
        color: regionColor(Number(specks[0].slice(1))),
        label: `tiny internal specks × ${specks.length} (${faceTotal} faces)`,
        focus: tracker.merged(specks),
      });
    }

    if (ctx.params.showLines !== false) {
      const lineDescs = edgeDescriptors(ctx.manifest);
      if (lineDescs) {
        const edges = await ctx.getField(lineDescs.edges) as Float32Array;
        const pairs = await ctx.getField(lineDescs.pairs) as Uint32Array;
        const kept: number[] = [];
        for (let e = 0; e < pairs.length / 2; e++) {
          const pa = pairs[2 * e];
          const pb = pairs[2 * e + 1];
          const a = pa < current.length ? current[pa] : CONFLICT_FEATURE;
          const b = pb < current.length ? current[pb] : CONFLICT_FEATURE;
          if (a !== b && a < CONFLICT_FEATURE && b < CONFLICT_FEATURE) {
            for (let i = 0; i < 6; i++) kept.push(edges[6 * e + i]);
          }
        }
        ctx.setLines(new Float32Array(kept));
      }
    }
    const splitLines = await drawSplitOverlays(ctx, moldSplitHost, brepIds);

    const opt = data.result.stats.options?.[data.option];
    if (ctx.params.showArrows !== false && opt) {
      ctx.setArrows(opt.arrows.map((arrow: any) => ({
        direction: arrow.direction,
        color: arrow.kind === 'slide'
          ? colors[2 + arrow.index] ?? COL.holder
          : ARROW_COLORS[arrow.kind] ?? COL.ok,
      })));
    }

    let stats = '';
    if (opt) {
      const slides = opt.slides.length
        ? ` [${opt.slides.map((s: any) => `d${s.direction} +${s.marginal}`).join(', ')}]`
        : '';
      stats = `${opt.feasible ? 'FEASIBLE' : 'infeasible'}`
        + ` · coverage ${(opt.coverage * 100).toFixed(1)}%`
        + ` · ${opt.slides.length} slide(s)${slides}`
        + ` · internal ${internalCount}`;
    }
    if (data.result.stale) {
      stats += '\n⚠ cuts or directions changed since this result — recomputing'
        + ' (or re-run below)';
    }
    stats += '\nstriped = multiple valid features — click a face to cycle';
    if (splitLines.length) stats += `\n${splitLines.join('\n')}`;
    return { legend, stats };
  },

  async onPick(face, ctx): Promise<boolean> {
    let data: AssignmentData;
    try {
      data = await loadAssignment(ctx);
    } catch {
      return false;
    }
    const b = data.brepIds[face];
    if (b >= data.valid.length) return false; // sub-face newer than result
    const v = data.valid[b];
    if (popcount(v) < 2) return false; // solid / conflict / internal

    const next = nextSetBit(v, data.current[b]);
    const { setOverride, overrides } = useStore.getState();
    setOverride(data.overridesKey, data.option, b,
                next === data.defaults[b] ? null : next);

    const payload = useStore.getState().overrides[data.overridesKey] ?? {};
    if (data.result.overrides_url) {
      putOverrides(data.result.overrides_url, payload).catch((err) =>
        useStore.getState().set({ error: String(err) }));
    }
    void overrides;
    return true;
  },
};

const ISO_LEVELS = 12;
const ISO_COLOR: RGB = [0.08, 0.09, 0.11];

const FREEZE: RGB = [1, 0.2, 0.15];

/** Fill-time surface plot, meshlib-style: per-vertex colors interpolated
 * smoothly across faces plus marching-triangle isolines at equal-time
 * levels. The node-quantized field is Laplacian-smoothed first so contours
 * read cleanly; NaN vertices (unreached / no skeleton node) stay grey.
 * `risk` optionally overrides vertices with the freeze-off color. */
function paintFillField(
  ctx: ViewCtx, vertexFill: Float32Array, tMax: number, risk?: Uint8Array,
) {
  const { faces, verts } = ctx;
  const field = smoothVertexField(ctx, vertexFill, 3);
  ctx.paintCorners((f, k) => {
    const vert = faces[3 * f + k];
    if (risk && risk[vert]) return FREEZE;
    const v = field[vert];
    return isNaN(v) ? COL.inaccess : rampColor(Math.min(v / tMax, 1));
  });

  let min = Infinity;
  let max = -Infinity;
  for (let i = 0; i < verts.length; i++) {
    if (verts[i] < min) min = verts[i];
    if (verts[i] > max) max = verts[i];
  }
  const lift = 0.0015 * (max - min) * Math.sqrt(3);
  const levels = Array.from(
    { length: ISO_LEVELS - 1 }, (_, i) => ((i + 1) * tMax) / ISO_LEVELS);
  const segments = isolineSegments(ctx, field, levels, lift);
  if (segments.length) ctx.setLines(segments, ISO_COLOR, true);
}


function pickSkeletonResult(ctx: ViewCtx) {
  const results = skeletonResults(ctx);
  const result = pickResult(results, ctx.params.skelResult);
  if (!result) {
    throw new Error('no wall_skeleton result yet — run the analysis below');
  }
  return result;
}

const GATE: RGB = [1, 1, 1];

const skeletonMode: ViewMode = {
  id: 'skeleton',
  label: 'Skeleton & fill flow',
  async paint(ctx): Promise<PaintInfo> {
    const result = pickSkeletonResult(ctx);
    const which = ctx.params.graph === 'raw' ? 'raw' : 'cluster';
    const sk = await loadSkeleton(ctx, result, which);
    const nodeCount = sk.nodes.length / 3;

    const gate = ctx.params.gate as [number, number, number] | null;
    const graphLabel = which === 'raw'
      ? `raw graph ${nodeCount} nodes` : `clustered graph ${nodeCount} nodes`;

    if (!gate) {
      // no gate yet: show the skeleton, colored by local wall radius
      ctx.setGraph(sk.key, sk.nodes, sk.edges, sk.radii);
      ctx.setMeshOpacity(0.35);
      const rMax = percentile(sk.radii, 0.98);
      ctx.paintGraph((n) => rampColor(1 - sk.radii[n] / rMax));
      ctx.paintFaces(() => COL.ok);
      return {
        legend: [
          { color: rampColor(1), label: 'thin channel' },
          { color: rampColor(0), label: `wide channel (≥ ${rMax.toFixed(2)} mm radius)` },
        ],
        stats: `${graphLabel} · ${sk.edges.length / 2} edges — `
          + `click the part to place an injection gate`
          + meshSpecNote(result.stats),
      };
    }

    // gate placed: the fill-time surface plot is the product — full-opacity
    // mesh with smooth colors and isolines, no skeleton overdraw; the gate
    // itself stays visible as a single marker dot
    const source = nearestNode(sk.nodes, gate);
    const dist = dijkstra(buildAdjacency(sk.key, sk), source);
    const tMax = Math.max(percentile(dist, 0.98), 1e-12);

    ctx.setGraph(`${sk.key}:gate:${source}`,
                 new Float32Array([sk.nodes[3 * source],
                                   sk.nodes[3 * source + 1],
                                   sk.nodes[3 * source + 2]]),
                 new Uint32Array(0),
                 new Float32Array([Math.max(2 * percentile(sk.radii, 0.98), 1)]));
    ctx.paintGraph(() => GATE);

    // back onto the mesh: per-vertex fill time via the vertex -> node map
    const vertexFill = new Float32Array(ctx.manifest.mesh!.counts.verts).fill(NaN);
    let reached = 0;
    for (let v = 0; v < vertexFill.length; v++) {
      const node = sk.vertNode[v];
      if (node !== SENTINEL && isFinite(dist[node])) {
        vertexFill[v] = dist[node];
        reached++;
      }
    }
    paintFillField(ctx, vertexFill, tMax);

    return {
      legend: [
        { color: GATE, label: 'gate (click to move)' },
        { color: rampColor(0), label: 'fills early' },
        { color: rampColor(1), label: 'fills late' },
        { color: ISO_COLOR, label: 'isolines: equal fill time' },
        { color: COL.inaccess, label: 'unreached / no skeleton node' },
      ],
      stats: `${graphLabel} — gate at node ${source}, `
        + `${((100 * reached) / vertexFill.length).toFixed(1)}% of vertices reached\n`
        + `fill time = Σ length / r⁴ along the skeleton (relative units)`
        + meshSpecNote(result.stats),
    };
  },
};

/** Marker color by rank: the winner white, runners-up gold fading to grey. */
function rankColor(rank: number, count: number): RGB {
  if (rank === 0) return GATE;
  const t = count > 1 ? rank / (count - 1) : 1;
  return [1 - 0.4 * t, 0.85 - 0.25 * t, 0.2 + 0.4 * t];
}

/** Last-painted proposal markers, so the synchronous onPick can snap to
 * them without re-fetching (same idea as the adjacency cache). */
let pickableProposals: { points: Float32Array; snap: number } | null = null;

const WELD: RGB = [1.0, 0.3, 0.75];

const sprueMode: ViewMode = {
  id: 'sprue',
  label: 'Sprue proposals',
  async paint(ctx): Promise<PaintInfo> {
    const results = sprueResults(ctx);
    const result = pickResult(results, ctx.params.sprueResult);
    if (!result) {
      throw new Error('no sprue_proposals result yet — run the analysis below');
    }
    const data = await loadSprue(ctx, result);
    const { skeleton: sk, proposals } = data;
    const showAll = ctx.params.showCandidates === true;

    const markers = showAll
      ? data.candidatePoints
      : new Float32Array(proposals.flatMap((p) => p.point));
    const { nodes, radii, markerBase } = markerGraph(sk, markers);
    const markerCount = markers.length / 3;
    ctx.setGraph(`${sk.key}:sprue:${result.hash}:${showAll ? 'all' : 'top'}`,
                 nodes, sk.edges, radii);
    ctx.setMeshOpacity(0.35);

    // snap radius for picking: 5% of the part's bounding-box diagonal
    let min = Infinity;
    let max = -Infinity;
    for (let i = 0; i < ctx.verts.length; i++) {
      if (ctx.verts[i] < min) min = ctx.verts[i];
      if (ctx.verts[i] > max) max = ctx.verts[i];
    }
    const proposalPoints = new Float32Array(proposals.flatMap((p) => p.point));
    pickableProposals = { points: proposalPoints, snap: 0.05 * (max - min) * Math.sqrt(3) };

    const markerColor = (m: number): RGB => {
      if (showAll) {
        // candidate heatmap: score 1 = best (cold), 0 = worst (hot)
        return rampColor(1 - data.candidateScore[m]);
      }
      return rankColor(m, markerCount);
    };

    const selected: number | null = ctx.params.proposal;
    const proposal = selected != null ? proposals[selected] : undefined;
    const stats = result.stats;
    const summary = proposals.slice(0, 3).map((p) =>
      `#${p.rank} ${p.score.toFixed(2)}`
      + (p.gate_style !== 'unknown' ? ` ${p.gate_style}` : '')
      + (p.side !== 'unknown' ? ` ${p.side}` : ''))
      .join(' · ');
    const confidence = (stats.confidence === 'full' ? ''
      : '\nno mold orientation result — side/parting filters skipped')
      + meshSpecNote(stats);

    if (!proposal) {
      const rMax = percentile(sk.radii, 0.98);
      ctx.paintGraph((n) => (n >= markerBase
        ? markerColor(n - markerBase)
        : rampColor(1 - sk.radii[n] / rMax)));
      ctx.paintFaces(() => COL.ok);
      return {
        legend: [
          { color: GATE, label: 'best gate proposal' },
          { color: rankColor(1, 3), label: 'runner-up proposals' },
        ],
        stats: `${proposals.length} proposals over ${stats.candidates.scored}`
          + ` scored candidates — click one (or pick from the list) to see`
          + ` its fill\n${summary}${confidence}`,
      };
    }

    const dist = dijkstra(buildAdjacency(sk.key, sk), proposal.node);
    const tMax = Math.max(percentile(dist, 0.98), 1e-12);
    ctx.paintGraph((n) => {
      if (n >= markerBase) {
        return (!showAll && n - markerBase === selected) ? GATE : markerColor(n - markerBase);
      }
      return isFinite(dist[n]) ? rampColor(dist[n] / tMax) : COL.inaccess;
    });

    const vertexFill = new Float32Array(ctx.manifest.mesh!.counts.verts).fill(NaN);
    for (let v = 0; v < vertexFill.length; v++) {
      const node = sk.vertNode[v];
      if (node !== SENTINEL && isFinite(dist[node])) vertexFill[v] = dist[node];
    }
    paintFillField(ctx, vertexFill, tMax);

    if (ctx.params.showWeld !== false) {
      const segments = weldSegments(sk, dist);
      if (segments.length) ctx.setLines(segments, WELD);
    }

    const reasons = [
      ...proposal.reasons.pros.map((r) => `+ ${r}`),
      ...proposal.reasons.cons.map((r) => `− ${r}`),
    ].join('\n');
    return {
      legend: [
        { color: GATE, label: `gate #${proposal.rank} (score ${proposal.score.toFixed(2)})` },
        { color: rampColor(0), label: 'fills early' },
        { color: rampColor(1), label: 'fills late' },
        { color: ISO_COLOR, label: 'isolines: equal fill time' },
        ...(ctx.params.showWeld !== false
          ? [{ color: WELD, label: 'weld-line indicator (fronts meet)' }] : []),
        { color: COL.inaccess, label: 'unreached / no skeleton node' },
      ],
      stats: `${reasons}${confidence}`,
    };
  },
};

/** Warning line when the voxel grid under-resolves the walls it measures
 * (the flow_voxels resolution gate; flow_fill embeds the same spec). */
function voxelSpecNote(stats: Record<string, any> | undefined): string {
  const spec = stats?.resolution;
  if (!spec || spec.status === 'ok') return '';
  return `\nvoxel grid is ${spec.status} for the measured walls `
    + `(${spec.voxels_through_thickness.toFixed(1)} voxels through the `
    + `median wall) — decrease the voxel size`;
}

function bboxDiagonal(ctx: ViewCtx): number {
  let min = Infinity;
  let max = -Infinity;
  for (let i = 0; i < ctx.verts.length; i++) {
    if (ctx.verts[i] < min) min = ctx.verts[i];
    if (ctx.verts[i] > max) max = ctx.verts[i];
  }
  return (max - min) * Math.sqrt(3);
}

/** One or two gate marker dots: the stored result's gate (white) plus the
 * pending clicked gate (gold) when it differs. */
function paintGateMarkers(
  ctx: ViewCtx, key: string,
  stored: number[] | null, pending: number[] | null,
) {
  const marker = Math.max(0.01 * bboxDiagonal(ctx), 1);
  const points: number[] = [];
  const colors: RGB[] = [];
  if (stored) {
    points.push(...stored);
    colors.push(GATE);
  }
  const moved = pending && (!stored || Math.hypot(
    pending[0] - stored[0], pending[1] - stored[1],
    pending[2] - stored[2]) > 1e-6);
  if (pending && moved) {
    points.push(...pending);
    colors.push([1, 0.8, 0.2]);
  }
  if (!points.length) return;
  ctx.setGraph(`${key}:${points.join(',')}`, new Float32Array(points),
               new Uint32Array(0),
               new Float32Array(colors.map(() => marker)));
  ctx.paintGraph((n) => colors[n]);
}

const flowFillMode: ViewMode = {
  id: 'flowFill',
  label: 'Flow fill (voxel)',
  async paint(ctx): Promise<PaintInfo> {
    const results = flowFillResults(ctx.manifest);
    const result = pickResult(results, ctx.params.fillResult);
    const gate = ctx.params.gate as [number, number, number] | null;

    if (!result) {
      ctx.paintFaces(() => COL.ok);
      paintGateMarkers(ctx, 'flowgate', null, gate);
      const haveVoxels = flowVoxelResults(ctx.manifest).length > 0;
      return {
        legend: gate ? [{ color: [1, 0.8, 0.2], label: 'pending gate' }] : [],
        stats: (gate
          ? 'gate placed — press "Compute fill" in the controls to run the solve'
          : 'click the part to place an injection gate')
          + (haveVoxels ? '' : '\nthe first run also voxelizes the part'),
      };
    }

    const fill = await loadFill(ctx, result);
    // freeze-off: ridge voxel not filled, on a channel the grid resolves
    const risk = new Uint8Array(fill.vertFrozen.length);
    let riskCount = 0;
    for (let v = 0; v < risk.length; v++) {
      const code = fill.vertFrozen[v];
      if (code !== FROZEN_OK && code !== FROZEN_UNJUDGED) {
        risk[v] = 1;
        riskCount++;
      }
    }
    const tMax = Math.max(percentile(fill.vertArrival, 0.98), 1e-12);
    paintFillField(ctx, fill.vertArrival, tMax, riskCount ? risk : undefined);

    const stats = result.stats;
    paintGateMarkers(ctx, `flowgate:${result.hash}`,
                     stats.gate?.position ?? null, gate);

    const skin = stats.fill ?? {};
    const legend: LegendEntry[] = [
      { color: GATE, label: 'gate (click to move, then recompute)' },
      { color: rampColor(0), label: 'fills early' },
      { color: rampColor(1), label: `fills late (~${tMax.toFixed(2)} s)` },
      { color: ISO_COLOR, label: 'isolines: equal fill time' },
      ...(riskCount
        ? [{ color: FREEZE, label: 'freeze-off / short-shot risk' }] : []),
      { color: COL.inaccess, label: 'unreached / below grid resolution' },
    ];
    return {
      legend,
      stats: `${(stats.reached_volume_fraction * 100).toFixed(1)}% of the`
        + ` interior reached · freeze-off on `
        + `${(stats.freeze_off.surface_fraction * 100).toFixed(1)}% of the`
        + ` judgeable surface\nskin ${skin.skin_coef} mm/√s over `
        + `${skin.fill_time} s fill, ${skin.iterations} passes, `
        + `${skin.neighborhood}-neighborhood · gate snapped `
        + `${stats.gate.snap_distance_mm.toFixed(2)} mm`
        + voxelSpecNote(stats),
    };
  },
};

const VOXEL_SCALARS = ['distance', 'arrival', 'frozen'] as const;

const voxelFieldMode: ViewMode = {
  id: 'voxelField',
  label: 'Voxel fields (debug)',
  async paint(ctx): Promise<PaintInfo> {
    const results = flowVoxelResults(ctx.manifest);
    const result = pickResult(results, ctx.params.flowResult);
    if (!result) {
      throw new Error('no flow_voxels result yet — run the analysis below');
    }
    const grid = await loadVoxelGrid(ctx, result);
    const scalar = (VOXEL_SCALARS as readonly string[]).includes(
      ctx.params.voxelScalar) ? ctx.params.voxelScalar : 'distance';

    // fill-derived fields need a flow_fill result on this exact grid
    let fill: FlowFill | null = null;
    let fillStats: Record<string, any> | null = null;
    if (scalar !== 'distance') {
      const fills = flowFillResults(ctx.manifest)
        .filter((r) => r.stats.voxels_hash === result.hash);
      const fillResult = pickResult(fills, ctx.params.fillResult);
      if (!fillResult) {
        throw new Error('no flow_fill result on this voxel grid — place a '
          + 'gate in the flow fill view and compute');
      }
      fill = await loadFill(ctx, fillResult);
      fillStats = fillResult.stats;
    }

    ctx.setGraph(`voxels:${grid.key}`, voxelPositions(grid),
                 new Uint32Array(0),
                 new Float32Array(grid.index.length).fill(grid.h / 2));
    ctx.setMeshOpacity(ctx.params.voxelSurface ? 0.35 : 0.15);

    let legend: LegendEntry[];
    if (scalar === 'distance') {
      const dMax = Math.max(percentile(grid.dist, 0.98), 1e-12);
      ctx.paintGraph((n) => rampColor(1 - grid.dist[n] / dMax));
      legend = [
        { color: rampColor(1), label: 'near the mold wall (thin)' },
        { color: rampColor(0), label: `mid-channel (≥ ${dMax.toFixed(2)} mm)` },
      ];
    } else if (scalar === 'arrival') {
      const arrival = fill!.arrival;
      const aMax = Math.max(percentile(arrival, 0.98), 1e-12);
      ctx.paintGraph((n) => (isFinite(arrival[n])
        ? rampColor(Math.min(arrival[n] / aMax, 1)) : COL.inaccess));
      legend = [
        { color: rampColor(0), label: 'fills early' },
        { color: rampColor(1), label: `fills late (~${aMax.toFixed(2)} s)` },
        { color: COL.inaccess, label: 'never reached' },
      ];
    } else {
      const frozen = fill!.frozen;
      const passes = Math.max(fillStats!.fill?.iterations ?? 3, 2);
      ctx.paintGraph((n) => {
        const code = frozen[n];
        if (code === FROZEN_OK) return COL.ok;
        if (code === 0) return COL.inaccess;
        return rampColor(1 - (code - 2) / Math.max(passes - 2, 1));
      });
      legend = [
        { color: COL.ok, label: 'filled' },
        { color: rampColor(1), label: 'frozen early (skin / hesitation)' },
        { color: rampColor(0), label: 'frozen in a late pass' },
        { color: COL.inaccess, label: 'never reached' },
      ];
    }

    if (ctx.params.voxelSurface) {
      // matching per-vertex projection painted on the (dimmed) surface
      if (scalar === 'distance') {
        const { vertHalf } = await loadVertVoxel(ctx, result);
        const dMax = Math.max(percentile(vertHalf, 0.98), 1e-12);
        ctx.paintCorners((f, k) => {
          const v = vertHalf[ctx.faces[3 * f + k]];
          return isNaN(v) ? COL.inaccess : rampColor(1 - v / dMax);
        });
      } else if (scalar === 'arrival') {
        const aMax = Math.max(percentile(fill!.vertArrival, 0.98), 1e-12);
        ctx.paintCorners((f, k) => {
          const v = fill!.vertArrival[ctx.faces[3 * f + k]];
          return isNaN(v) ? COL.inaccess : rampColor(Math.min(v / aMax, 1));
        });
      } else {
        ctx.paintFaces((f) => {
          const code = fill!.vertFrozen[ctx.faces[3 * f]];
          if (code === FROZEN_OK) return COL.ok;
          if (code === FROZEN_UNJUDGED) return fade(COL.ok);
          return FREEZE;
        });
      }
    } else {
      ctx.paintFaces(() => COL.ok);
    }

    const stats = result.stats;
    return {
      legend,
      stats: `${stats.interior_voxels} interior voxels · `
        + `${stats.grid.dims.join('×')} grid at `
        + `${stats.grid.voxel.toFixed(3)} mm · median half-thickness `
        + `${stats.median_half_thickness.toFixed(2)} mm`
        + (stats.sign_check !== 'ok'
          ? '\nsign check suspect — the mesh may not be watertight' : '')
        + voxelSpecNote(stats),
    };
  },
};

const coolingMode: ViewMode = {
  id: 'cooling',
  label: 'Cooling time',
  async paint(ctx): Promise<PaintInfo> {
    const results = flowVoxelResults(ctx.manifest);
    const result = pickResult(results, ctx.params.flowResult);
    if (!result) {
      throw new Error('no flow_voxels result yet — run the analysis below');
    }
    const { vertHalf } = await loadVertVoxel(ctx, result);
    const coef = parseFloat(ctx.params.coolCoef) || 1.0;
    const field = new Float32Array(vertHalf.length);
    for (let v = 0; v < field.length; v++) {
      field[v] = coef * vertHalf[v] * vertHalf[v];
    }
    const tMax = Math.max(percentile(field, 0.98), 1e-12);
    paintFillField(ctx, field, tMax);
    return {
      legend: [
        { color: rampColor(0), label: 'cools fast (thin)' },
        { color: rampColor(1), label: `cools slow (~${tMax.toFixed(1)} s)` },
        { color: ISO_COLOR, label: 'isolines: equal cooling time' },
        { color: COL.inaccess, label: 'below grid resolution' },
      ],
      stats: `cooling ∝ half-thickness² (coefficient ${coef} s/mm²) — `
        + `slow-cooling thick spots drive cycle time and sink marks`
        + voxelSpecNote(result.stats),
    };
  },
};

const PIN_OVER: RGB = [1, 0.15, 0.15];

/** Publish the latest simulation summary for the Controls pin list.
 * paintKey includes viewerParams, so the JSON-equality guard is what keeps
 * this from looping the repaint. */
function publishEjSim(sim: EjectorSimResponse | null) {
  const summary = sim && {
    pins: sim.pins.map((p) => ({
      force_n: p.force_n, pressure_mpa: p.pressure_mpa,
      utilization: p.utilization, over_limit: p.over_limit,
    })),
    max_deflection_mm: sim.stats.max_deflection_mm,
    total_sticking_n: sim.stats.total_sticking_n,
  };
  const current = useStore.getState().viewerParams.injection_molding?.ejSim;
  if (JSON.stringify(current ?? null) !== JSON.stringify(summary)) {
    useStore.getState().setViewerParam('injection_molding', 'ejSim', summary);
  }
}

const ejectorMode: ViewMode = {
  id: 'ejector',
  label: 'Ejector pins',
  async paint(ctx): Promise<PaintInfo> {
    const results = stickingResults(ctx);
    const result = pickResult(results, ctx.params.stickResult);
    if (!result) {
      throw new Error('no ejection_sticking result yet — run the analysis below');
    }
    const data = await loadSticking(ctx, result);
    const sk = data.skeleton;
    const pins: Pin[] = ctx.params.pins ?? [];
    const confidence = (result.stats.confidence === 'full' ? ''
      : '\nno mold orientation result — pull assumed +Z, all steep faces grip')
      + meshSpecNote(result.stats);

    if (ctx.params.ejShowDraft) {
      publishEjSim(null);
      const grip = result.stats.grip_deg ?? 15;
      ctx.paintFaces((f) => rampColor(1 - Math.min(data.draftDeg[f], 45) / 45));
      return {
        legend: [
          { color: rampColor(1), label: `no draft — grips below ${grip}°` },
          { color: rampColor(0), label: 'draft ≥ 45° / perpendicular' },
        ],
        stats: `draft angle vs pull axis [${result.stats.pull
          .map((c: number) => c.toFixed(0)).join(', ')}]${confidence}`,
      };
    }

    if (!pins.length) {
      publishEjSim(null);
      const vals = faceValues(ctx, data.vertForce, null);
      const vMax = Math.max(percentile(data.vertForce, 0.98), 1e-9);
      ctx.paintFaces((f) => (vals[f] > 0 ? rampColor(Math.min(vals[f] / vMax, 1)) : COL.ok));
      return {
        legend: [
          { color: rampColor(1), label: 'sticks hard (release force)' },
          { color: COL.ok, label: 'no grip' },
        ],
        stats: `total sticking force ${result.stats.totals.sticking_force_n.toFixed(0)} N — `
          + `click the part to place an ejector pin${confidence}`,
      };
    }

    const sim = await simulateCached(ctx.manifest.part.id, {
      result_hash: result.hash,
      pins,
      E: parseFloat(ctx.params.ejE) || 2000,
      allowable_pressure: parseFloat(ctx.params.ejAllow) || 80,
    });
    publishEjSim(sim);

    // deflection per node -> per vertex -> per face (fill-flow pattern)
    const deflection = sim.deflection;
    const finite = new Float32Array(
      deflection.filter((w): w is number => w != null));
    // p95 scale: sliver-chain outliers must not wash out the bulk field
    const wMax = Math.max(percentile(finite, 0.95), 1e-12);
    const vertexW = new Float32Array(ctx.manifest.mesh!.counts.verts).fill(NaN);
    for (let v = 0; v < vertexW.length; v++) {
      const node = sk.vertNode[v];
      const w = node !== SENTINEL ? deflection[node] : null;
      if (w != null) vertexW[v] = w;
    }
    const vals = faceValues(ctx, vertexW, null);
    ctx.paintFaces((f) => (isNaN(vals[f]) ? COL.inaccess : rampColor(vals[f] / wMax)));

    // pins as marker-only graph dots (visible through the mesh)
    const markerScale = Math.max(2 * percentile(sk.radii, 0.98), 1);
    const positions = new Float32Array(pins.flatMap((p) => p.point));
    const radii = new Float32Array(pins.map(
      (p) => Math.max(p.diameter, markerScale)));
    ctx.setGraph(`ejector:${result.hash}:${JSON.stringify(pins)}`,
                 positions, new Uint32Array(0), radii);
    ctx.paintGraph((m) => (sim.pins[m]?.over_limit
      ? PIN_OVER : rampColor(Math.min(sim.pins[m]?.utilization ?? 0, 1))));

    const worst = Math.max(...sim.pins.map((p) => p.utilization));
    const unsupported = sim.stats.unsupported
      .reduce((sum, u) => sum + u.load_n, 0);
    // bulk (p95) deflection is the headline: thin sliver chains in the
    // skeleton can blow up the raw max far beyond anything physical
    const bulk = sim.stats.p95_deflection_mm;
    const max = sim.stats.max_deflection_mm;
    const sliver = max > 5 * Math.max(bulk, 1e-9)
      ? ' (local thin slivers flex much further — model unreliable there)'
      : '';
    return {
      legend: [
        { color: rampColor(0), label: 'pin lightly loaded / deflects least' },
        { color: rampColor(1), label: 'pin near limit / deflects most' },
        { color: PIN_OVER, label: 'pin over allowable pressure' },
        { color: COL.inaccess, label: 'unsupported / no skeleton node' },
      ],
      stats: `sticking ${sim.stats.total_sticking_n.toFixed(0)} N · `
        + `bulk deflection ${bulk.toFixed(3)} mm (indicative)${sliver} · `
        + `worst pin ${(100 * worst).toFixed(0)}% of allowable`
        + (unsupported > 1e-6
          ? `\n${unsupported.toFixed(0)} N of sticking load is on regions no pin supports`
          : '')
        + `\nclick to add a pin · click a pin to remove it${confidence}`,
    };
  },
};

/** Per-vertex mask of readings explainable by nearby sharp geometry —
    the false-low corner/edge artifacts of the tangent-at-vertex probe.
    Mirrors pipeline.edge_excluded: inside the stored [band_lo, band_hi]
    window, or flagged `suspect` (penetrating center / crease wobble).
    Null when the result predates the band arrays. */
async function edgeExclusion(
  ctx: ViewCtx, analysis: string, member: string,
): Promise<Uint8Array | null> {
  const results = resultsFor(ctx.manifest, analysis);
  const result = results[results.length - 1];
  if (!result) return null;
  const desc = resolveField(ctx, result, member);
  const loDesc = resolveField(ctx, result, 'band_lo');
  const hiDesc = resolveField(ctx, result, 'band_hi');
  const suspectDesc = resolveField(ctx, result, 'suspect');
  if (!desc || !loDesc || !hiDesc || !suspectDesc) return null;
  const [values, lo, hi, suspect] = await Promise.all([
    ctx.getField(desc) as Promise<Float32Array>,
    ctx.getField(loDesc) as Promise<Float32Array>,
    ctx.getField(hiDesc) as Promise<Float32Array>,
    ctx.getField(suspectDesc) as Promise<Uint8Array>,
  ]);
  const out = new Uint8Array(values.length);
  for (let i = 0; i < values.length; i++) {
    if (suspect[i] || (values[i] >= lo[i] && values[i] <= hi[i])) out[i] = 1;
  }
  return out;
}

const thicknessMode = heatmapMode(
  'thickness', 'Wall thickness heatmap',
  (ctx) => scalarField(ctx, 'thickness', 'thickness'),
  {
    flagDirection: 'below',
    thresholdParam: 'minThickness',
    minParam: 'thicknessMin',
    scaleParam: 'thicknessScale',
    bandLoParam: 'thicknessBandLo',
    bandHiParam: 'thicknessBandHi',
    okLabel: 'thick — ok',
    exclusion: (ctx) => edgeExclusion(ctx, 'thickness', 'thickness'),
    maskParam: 'maskExplained',
  });

const gapsMode = heatmapMode(
  'gaps', 'Wall gaps / clearance heatmap',
  (ctx) => scalarField(ctx, 'gaps', 'gap'),
  {
    flagDirection: 'below',
    thresholdParam: 'minGap',
    minParam: 'gapMin',
    scaleParam: 'gapScale',
    bandLoParam: 'gapBandLo',
    bandHiParam: 'gapBandHi',
    okLabel: 'clearance ok (incl. no opposing wall in range)',
    exclusion: (ctx) => edgeExclusion(ctx, 'gaps', 'gap'),
    maskParam: 'maskExplained',
  });

const rayThicknessMode = heatmapMode(
  'rayThickness', 'Ray wall thickness heatmap',
  (ctx) => scalarField(ctx, 'ray_thickness', 'ray_thickness'),
  {
    flagDirection: 'below',
    thresholdParam: 'minRayThickness',
    minParam: 'rayThicknessMin',
    scaleParam: 'rayThicknessScale',
    bandLoParam: 'rayThicknessBandLo',
    bandHiParam: 'rayThicknessBandHi',
    okLabel: 'thick — ok',
  });

const rayGapMode = heatmapMode(
  'rayGap', 'Ray wall gap / clearance heatmap',
  (ctx) => scalarField(ctx, 'ray_gap', 'ray_gap'),
  {
    flagDirection: 'below',
    thresholdParam: 'minRayGap',
    minParam: 'rayGapMin',
    scaleParam: 'rayGapScale',
    bandLoParam: 'rayGapBandLo',
    bandHiParam: 'rayGapBandHi',
    okLabel: 'clearance ok (incl. no opposing wall in range)',
  });

// pocket depth/width ratio along the pull direction — the slenderness of
// the mold-steel core each pocket needs (thin steel above ~2-3×)
const slendernessMode = heatmapMode(
  'slenderness', 'Steel slenderness heatmap',
  (ctx) => scalarField(ctx, 'slenderness', 'slenderness'),
  {
    flagDirection: 'above',
    thresholdParam: 'maxSlenderness',
    scaleParam: 'slendernessScale',
    units: '×',
    okLabel: 'stocky steel — ok',
  });

// distance to supporting thick material over local thickness scale — the
// direction-free stiffness proxy (bending compliance ~ ratio³); long thin
// bridges and large unsupported panels read high
const thinSpanMode = heatmapMode(
  'thinSpan', 'Thin span / stiffness heatmap',
  (ctx) => scalarField(ctx, 'thin_span', 'span_ratio'),
  {
    flagDirection: 'above',
    thresholdParam: 'maxSpanRatio',
    minParam: 'spanMin',
    scaleParam: 'spanScale',
    bandLoParam: 'spanBandLo',
    bandHiParam: 'spanBandHi',
    units: '×',
    okLabel: 'well supported — ok',
  });

// separation angle per ball (requires the "Store contact angles" analysis
// param): wall ~180°, N-degree corner ~N, edge-collapsed ~0, saturated NaN
const thicknessAngleMode = heatmapMode(
  'thicknessAngle', 'Thickness contact angle',
  (ctx) => scalarField(ctx, 'thickness', 'contact_angle'),
  {
    flagDirection: 'below',
    thresholdParam: 'minAngle',
    minParam: 'angleMin',
    scaleParam: 'angleScale',
    bandLoParam: 'angleBandLo',
    bandHiParam: 'angleBandHi',
    units: '°',
    okLabel: 'wall-like (→ 180°)',
    maskedLabel: 'no opposing contact / saturated',
  });

const gapAngleMode = heatmapMode(
  'gapAngle', 'Gap contact angle',
  (ctx) => scalarField(ctx, 'gaps', 'contact_angle'),
  {
    flagDirection: 'below',
    thresholdParam: 'minAngle',
    minParam: 'angleMin',
    scaleParam: 'angleScale',
    bandLoParam: 'angleBandLo',
    bandHiParam: 'angleBandHi',
    units: '°',
    okLabel: 'wall-like (→ 180°)',
    maskedLabel: 'no opposing contact / saturated',
  });

async function inspect(face: number, ctx: ViewCtx): Promise<string[]> {
  const lines: string[] = [];
  const at3 = (field: Float32Array) =>
    [0, 1, 2].map((k) => field[ctx.faces[3 * face + k]].toFixed(2)).join(' / ');

  try {
    const data = await loadAssignment(ctx);
    const labels: string[] = data.desc.params.labels;
    const b = data.brepIds[face];
    const bits: string[] = [];
    for (let f = 0; f < labels.length; f++) {
      if ((data.membership[face] >>> f) & 1) bits.push(labels[f]);
    }
    const label = faceLabel(b, data.brepDesc);
    lines.push(`brep face: ${label}${label.includes('.') ? ' (split piece)' : ''}`);
    lines.push(`reachable by: ${bits.join(', ') || 'nothing (internal)'}`);
    if (b >= data.valid.length) {
      lines.push('assigned: pending — result predates this cut');
    } else {
      const validNames: string[] = [];
      for (let f = 0; f < labels.length; f++) {
        if ((data.valid[b] >>> f) & 1) validNames.push(labels[f]);
      }
      const cat = data.current[b];
      const catName = cat === INTERNAL_FEATURE
        ? `internal undercut ${data.region[face] || ''}`
        : cat === CONFLICT_FEATURE ? 'conflict / needs split' : labels[cat];
      const overridden = cat !== data.defaults[b] ? ' (override)' : '';
      lines.push(`face valid for: ${validNames.join(', ') || '—'}`);
      lines.push(`assigned: ${catName}${overridden}`);
    }
  } catch {
    // assignment data unavailable — skip those lines
  }

  const thickness = scalarField(ctx, 'thickness', 'thickness');
  if (thickness) {
    lines.push(`wall thickness: ${at3(await ctx.getField(thickness) as Float32Array)} mm`);
  }
  const gap = scalarField(ctx, 'gaps', 'gap');
  if (gap) {
    lines.push(`wall gap: ${at3(await ctx.getField(gap) as Float32Array)} mm`);
  }
  return lines;
}

const EMPTY: Record<string, any> = {};

function NumberParam({ label, value, placeholder, onChange }: {
  label: string; value: any; placeholder?: string; onChange: (v: string) => void;
}) {
  return (
    <div>
      <label>{label}</label>
      <input
        type="number" step="0.1" value={value} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

/** Form-string to number with a fallback (0 is a valid value, so no ||). */
function num(value: any, fallback: number): number {
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : fallback;
}

function InjectionControls() {
  const manifest = useStore((s) => s.manifest);
  const modeId = useStore((s) => s.modeId);
  const params = useStore((s) => s.viewerParams.injection_molding) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  const set = (name: string, value: any) => setParam('injection_molding', name, value);

  const results = manifest ? resultsFor(manifest, 'mold_orientation') : [];
  const result = pickResult(results, params.result);
  const options: any[] = result?.stats.options ?? [];
  const fieldOptions = options.slice(0, 3);
  const hasBrep = !!manifest?.fields.some((f) => f.id === 'brep_edges');

  const skelResults = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'wall_skeleton');

  const sprueResultList = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'sprue_proposals'
      && r.stats.schema === 2);
  const sprueResult = pickResult(sprueResultList, params.sprueResult);
  const proposals: Proposal[] = sprueResult?.stats.proposals ?? [];

  const stickingList = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding'
      && r.analysis === 'ejection_sticking' && r.stats.schema === 2);
  const pins: Pin[] = params.pins ?? [];
  const ejSim = params.ejSim ?? null;

  const partId = useStore((s) => s.partId);
  const jobs = useStore((s) => s.jobs);
  const busy = jobs.some((j) => j.part_id === partId
    && (j.status === 'queued' || j.status === 'running'));
  const flowList = manifest ? flowVoxelResults(manifest) : [];
  const fillList = manifest ? flowFillResults(manifest) : [];

  function submitFlow(analysis: 'flow_voxels' | 'flow_fill') {
    if (!partId) return;
    const voxel = Number.isFinite(parseFloat(params.flowVoxel))
      ? parseFloat(params.flowVoxel) : null;
    const jobParams: Record<string, any> = analysis === 'flow_voxels'
      ? { voxel }
      : {
        voxel,
        gate: params.gate,
        delta0: num(params.flowDelta0, 0),
        skin_coef: num(params.flowSkinCoef, 0.12),
        fill_time: num(params.flowFillTime, 2),
        iterations: Math.max(1, Math.round(num(params.flowIterations, 3))),
        neighborhood: String(params.flowNeighborhood ?? '26'),
      };
    runAnalysisJob(partId, 'injection_molding', analysis, jobParams)
      .then(() => set(analysis === 'flow_voxels' ? 'flowResult' : 'fillResult', -1))
      .catch((err) => useStore.getState().set({
        error: err instanceof Error ? err.message : String(err),
      }));
  }

  return (
    <>
      {modeId === 'sprue' && (
        <>
          <label>Result (parameter set)</label>
          <select
            value={params.sprueResult ?? -1}
            onChange={(e) => {
              set('sprueResult', parseInt(e.target.value));
              set('proposal', null);
            }}
          >
            {sprueResultList.length > 0 && <option value={-1}>latest</option>}
            {sprueResultList.map((r, i) => (
              <option key={r.hash} value={i}>
                {`${r.stats.proposals?.length ?? 0} proposals · ${r.hash}`}
              </option>
            ))}
            {!sprueResultList.length && <option value={-1}>no results yet</option>}
          </select>

          <div className="proposal-list">
            {proposals.map((p) => (
              <button
                key={p.rank}
                className={params.proposal === p.rank ? 'selected' : ''}
                onClick={() => set('proposal', params.proposal === p.rank ? null : p.rank)}
              >
                {`#${p.rank} · ${p.score.toFixed(2)}`}
                {p.gate_style !== 'unknown' && ` · ${p.gate_style === 'edge' ? 'edge gate' : 'hot tip'}`}
                {p.side !== 'unknown' && ` · side ${p.side}`}
              </button>
            ))}
          </div>

          {params.proposal != null && proposals[params.proposal] && (
            <>
              <div className="hint">
                {proposals[params.proposal].reasons.pros.map((r) => `+ ${r}`).join(' · ')}
                {proposals[params.proposal].reasons.cons.length > 0 && (
                  ` · ${proposals[params.proposal].reasons.cons.map((r) => `− ${r}`).join(' · ')}`)}
              </div>
              <button
                onClick={() => {
                  set('gate', proposals[params.proposal].point);
                  useStore.getState().set({ modeId: 'skeleton' });
                }}
              >
                open in fill-flow mode
              </button>
              <button onClick={() => set('proposal', null)}>clear selection</button>
            </>
          )}

          <div className="row">
            <label className="check">
              <input
                type="checkbox" checked={params.showCandidates === true}
                onChange={(e) => set('showCandidates', e.target.checked)}
              />
              all candidates (score heatmap)
            </label>
            <label className="check">
              <input
                type="checkbox" checked={params.showWeld !== false}
                onChange={(e) => set('showWeld', e.target.checked)}
              />
              weld indicator
            </label>
          </div>

          <div className="hint">
            click a marker on the part (or a proposal above) to inspect its fill
          </div>
        </>
      )}

      {modeId === 'ejector' && (
        <>
          <label>Result (parameter set)</label>
          <select
            value={params.stickResult ?? -1}
            onChange={(e) => set('stickResult', parseInt(e.target.value))}
          >
            {stickingList.length > 0 && <option value={-1}>latest</option>}
            {stickingList.map((r, i) => (
              <option key={r.hash} value={i}>
                {`${(r.stats.totals?.sticking_force_n ?? 0).toFixed(0)} N sticking · ${r.hash}`}
              </option>
            ))}
            {!stickingList.length && <option value={-1}>no results yet</option>}
          </select>

          <div className="row">
            <div>
              <label>Pin diameter (mm)</label>
              <select
                value={params.pinDiameter ?? 3}
                onChange={(e) => set('pinDiameter', parseFloat(e.target.value))}
              >
                {[2, 3, 4, 6, 8].map((d) => (
                  <option key={d} value={d}>{`Ø${d}`}</option>
                ))}
              </select>
            </div>
            <label className="check">
              <input
                type="checkbox" checked={params.ejShowDraft === true}
                onChange={(e) => set('ejShowDraft', e.target.checked)}
              />
              draft-angle view
            </label>
          </div>

          <div className="row">
            <NumberParam
              label="E modulus (MPa)" value={params.ejE ?? 2000}
              onChange={(v) => set('ejE', v)}
            />
            <NumberParam
              label="Allowable pin pressure (MPa)" value={params.ejAllow ?? 80}
              onChange={(v) => set('ejAllow', v)}
            />
          </div>

          {pins.length > 0 && (
            <div className="proposal-list">
              {pins.map((p, i) => (
                <button
                  key={i}
                  className={ejSim?.pins?.[i]?.over_limit ? 'over' : ''}
                  onClick={() => set('pins', pins.filter((_, j) => j !== i))}
                  title="remove this pin"
                >
                  {`#${i} · Ø${p.diameter}`}
                  {ejSim?.pins?.[i] && (
                    ` · ${ejSim.pins[i].force_n.toFixed(1)} N`
                    + ` · ${ejSim.pins[i].pressure_mpa.toFixed(1)} MPa`
                    + ` (${(100 * ejSim.pins[i].utilization).toFixed(0)}%)`
                  )}
                  {' ✕'}
                </button>
              ))}
            </div>
          )}
          {pins.length > 0 && (
            <button onClick={() => set('pins', [])}>clear pins</button>
          )}

          <div className="hint">
            click the part to add a pin at the chosen diameter ·
            click a pin (marker or list) to remove it
          </div>
        </>
      )}

      {modeId === 'skeleton' && (
        <>
          <label>Result (parameter set)</label>
          <select
            value={params.skelResult ?? -1}
            onChange={(e) => set('skelResult', parseInt(e.target.value))}
          >
            {skelResults.length > 0 && <option value={-1}>latest</option>}
            {skelResults.map((r, i) => (
              <option key={r.hash} value={i}>
                {`max r ${r.params.max_radius ?? '?'} mm · ${r.hash}`}
              </option>
            ))}
            {!skelResults.length && <option value={-1}>no results yet</option>}
          </select>

          <label>Skeleton graph</label>
          <select value={params.graph ?? 'cluster'} onChange={(e) => set('graph', e.target.value)}>
            <option value="cluster">clustered (medial skeleton)</option>
            <option value="raw">raw (one node per vertex)</option>
          </select>

          <div className="hint">
            click the part to place the injection gate; click again to move it
          </div>
          {params.gate && (
            <button onClick={() => set('gate', null)}>clear gate</button>
          )}
        </>
      )}

      {modeId === 'flowFill' && (
        <>
          <label>Fill result</label>
          <select
            value={params.fillResult ?? -1}
            onChange={(e) => set('fillResult', parseInt(e.target.value))}
          >
            {fillList.length > 0 && <option value={-1}>latest</option>}
            {fillList.map((r, i) => (
              <option key={r.hash} value={i}>
                {`gate (${(r.stats.gate?.point ?? [])
                  .map((c: number) => c.toFixed(0)).join(', ')})`
                  + ` · skin ${r.stats.fill?.skin_coef} · ${r.hash}`}
              </option>
            ))}
            {!fillList.length && <option value={-1}>no results yet</option>}
          </select>

          <div className="row">
            <NumberParam
              label="Voxel size (mm)" value={params.flowVoxel ?? ''}
              placeholder="auto" onChange={(v) => set('flowVoxel', v)}
            />
            <NumberParam
              label="Fill time (s)" value={params.flowFillTime ?? 2}
              onChange={(v) => set('flowFillTime', v)}
            />
          </div>
          <div className="row">
            <NumberParam
              label="Skin growth (mm/√s)" value={params.flowSkinCoef ?? 0.12}
              onChange={(v) => set('flowSkinCoef', v)}
            />
            <NumberParam
              label="Initial skin (mm)" value={params.flowDelta0 ?? 0}
              onChange={(v) => set('flowDelta0', v)}
            />
          </div>
          <div className="row">
            <div>
              <label>Neighborhood</label>
              <select
                value={params.flowNeighborhood ?? '26'}
                onChange={(e) => set('flowNeighborhood', e.target.value)}
              >
                <option value="26">26 (isotropic)</option>
                <option value="6">6 (fast)</option>
              </select>
            </div>
            <NumberParam
              label="Skin passes" value={params.flowIterations ?? 3}
              onChange={(v) => set('flowIterations', v)}
            />
          </div>

          <button
            className="run" disabled={!params.gate || busy || !partId}
            onClick={() => submitFlow('flow_fill')}
          >
            {busy ? 'computing…' : 'Compute fill'}
          </button>
          {params.gate && (
            <button onClick={() => set('gate', null)}>clear gate</button>
          )}
          <div className="hint">
            click the part to place or move the gate, then compute — each
            parameter set is cached and selectable above
          </div>
        </>
      )}

      {modeId === 'voxelField' && (
        <>
          <label>Voxel result</label>
          <select
            value={params.flowResult ?? -1}
            onChange={(e) => set('flowResult', parseInt(e.target.value))}
          >
            {flowList.length > 0 && <option value={-1}>latest</option>}
            {flowList.map((r, i) => (
              <option key={r.hash} value={i}>
                {`${r.stats.grid?.voxel?.toFixed(2)} mm · `
                  + `${r.stats.interior_voxels} voxels · ${r.hash}`}
              </option>
            ))}
            {!flowList.length && <option value={-1}>no results yet</option>}
          </select>

          <label>Field</label>
          <select
            value={params.voxelScalar ?? 'distance'}
            onChange={(e) => set('voxelScalar', e.target.value)}
          >
            <option value="distance">wall distance (SDF)</option>
            <option value="arrival">fill arrival (needs a fill result)</option>
            <option value="frozen">frozen state (needs a fill result)</option>
          </select>

          <label className="check">
            <input
              type="checkbox" checked={params.voxelSurface === true}
              onChange={(e) => set('voxelSurface', e.target.checked)}
            />
            project onto the surface
          </label>

          <div className="row">
            <NumberParam
              label="Voxel size (mm)" value={params.flowVoxel ?? ''}
              placeholder="auto" onChange={(v) => set('flowVoxel', v)}
            />
          </div>
          <button
            className="run" disabled={busy || !partId}
            onClick={() => submitFlow('flow_voxels')}
          >
            {busy ? 'computing…' : 'Compute voxels'}
          </button>
        </>
      )}

      {modeId === 'cooling' && (
        <>
          <NumberParam
            label="Cooling coefficient (s/mm²)" value={params.coolCoef ?? 1}
            onChange={(v) => set('coolCoef', v)}
          />
          <div className="hint">
            cooling time ∝ half-thickness² from the flow voxelization —
            run "Flow voxels (SDF)" below if the view is empty
          </div>
        </>
      )}

      {modeId === 'assignment' && (
        <>
          <label>Result (parameter set)</label>
          <select value={params.result ?? -1} onChange={(e) => set('result', parseInt(e.target.value))}>
            {results.length > 0 && <option value={-1}>latest</option>}
            {results.map((r, i) => (
              <option key={r.hash} value={i}>
                {`max slides ${r.params.max_slides ?? '?'} · ${r.hash}`}
              </option>
            ))}
            {!results.length && <option value={-1}>no results yet</option>}
          </select>

          <label>Orientation option</label>
          <select value={params.option ?? 0} onChange={(e) => set('option', parseInt(e.target.value))}>
            {fieldOptions.map((o, i) => (
              <option key={i} value={i}>
                {`±d${o.pair[0]} · ${o.slides.length} slide(s) · ${o.feasible ? 'feasible' : 'infeasible'}`}
              </option>
            ))}
            {!fieldOptions.length && <option value={0}>—</option>}
          </select>

          <div className="row">
            <label className="check">
              <input
                type="checkbox" checked={params.showLines !== false}
                onChange={(e) => set('showLines', e.target.checked)}
              />
              parting lines
            </label>
            <label className="check">
              <input
                type="checkbox" checked={params.showArrows !== false}
                onChange={(e) => set('showArrows', e.target.checked)}
              />
              direction arrows
            </label>
          </div>

          <div className="hint">
            click a face to cycle it between its valid sides/slides ·
            faded stripes = other valid features
          </div>

          <button
            disabled={!hasBrep || !results.length}
            onClick={() => void runCtxAction(async (ctx) => {
              const data = await loadAssignment(ctx);
              const { summary, changed } = await optimizeParting(ctx, {
                valid: data.valid, defaults: data.defaults, current: data.current,
                option: data.option, overridesKey: data.overridesKey,
                overridesUrl: data.result.overrides_url,
              });
              useStore.getState().set({ pick: summary });
              return changed;
            })}
          >
            optimize parting lines
          </button>

          <SplitControls host={moldSplitHost} />

          {options.length > 0 && (
            <div className="hint">
              ranked: {options.map((o, i) =>
                `#${i} ±d${o.pair[0]} ${o.feasible ? '✓' : '✗'} ${(o.coverage * 100).toFixed(0)}%`).join(' · ')}
            </div>
          )}
        </>
      )}

      {modeId === 'thickness' && (
        <>
          <div className="row">
            <NumberParam
              label="Min thickness (mm)" value={params.minThickness ?? 1.0}
              onChange={(v) => set('minThickness', v)}
            />
            <NumberParam
              label="Heatmap max (mm)" value={params.thicknessScale ?? ''}
              placeholder="auto" onChange={(v) => set('thicknessScale', v)}
            />
          </div>
          <label className="check">
            <input
              type="checkbox" checked={params.maskExplained !== false}
              onChange={(e) => set('maskExplained', e.target.checked)}
            />
            show edge-explained readings as ok
          </label>
        </>
      )}

      {modeId === 'gaps' && (
        <>
          <div className="row">
            <NumberParam
              label="Min gap (mm)" value={params.minGap ?? 0.5}
              onChange={(v) => set('minGap', v)}
            />
            <NumberParam
              label="Heatmap max (mm)" value={params.gapScale ?? ''}
              placeholder="auto" onChange={(v) => set('gapScale', v)}
            />
          </div>
          <label className="check">
            <input
              type="checkbox" checked={params.maskExplained !== false}
              onChange={(e) => set('maskExplained', e.target.checked)}
            />
            show edge-explained readings as ok
          </label>
        </>
      )}

      {modeId === 'rayThickness' && (
        <div className="row">
          <NumberParam
            label="Min thickness (mm)" value={params.minRayThickness ?? 1.0}
            onChange={(v) => set('minRayThickness', v)}
          />
          <NumberParam
            label="Heatmap max (mm)" value={params.rayThicknessScale ?? ''}
            placeholder="auto" onChange={(v) => set('rayThicknessScale', v)}
          />
        </div>
      )}

      {modeId === 'rayGap' && (
        <div className="row">
          <NumberParam
            label="Min gap (mm)" value={params.minRayGap ?? 0.5}
            onChange={(v) => set('minRayGap', v)}
          />
          <NumberParam
            label="Heatmap max (mm)" value={params.rayGapScale ?? ''}
            placeholder="auto" onChange={(v) => set('rayGapScale', v)}
          />
        </div>
      )}

      {modeId === 'slenderness' && (
        <div className="row">
          <NumberParam
            label="Max depth/width (×)" value={params.maxSlenderness ?? 2.0}
            onChange={(v) => set('maxSlenderness', v)}
          />
          <NumberParam
            label="Heatmap max (×)" value={params.slendernessScale ?? ''}
            placeholder="auto" onChange={(v) => set('slendernessScale', v)}
          />
        </div>
      )}

      {modeId === 'thinSpan' && (
        <div className="row">
          <NumberParam
            label="Max span/thickness (×)" value={params.maxSpanRatio ?? 5.0}
            onChange={(v) => set('maxSpanRatio', v)}
          />
          <NumberParam
            label="Heatmap max (×)" value={params.spanScale ?? ''}
            placeholder="auto" onChange={(v) => set('spanScale', v)}
          />
        </div>
      )}

      {(modeId === 'thicknessAngle' || modeId === 'gapAngle') && (
        <div className="row">
          <NumberParam
            label="Min angle (°)" value={params.minAngle ?? 60}
            onChange={(v) => set('minAngle', v)}
          />
          <NumberParam
            label="Heatmap max (°)" value={params.angleScale ?? 180}
            onChange={(v) => set('angleScale', v)}
          />
        </div>
      )}
    </>
  );
}

export const injectionPlugin: ProcessPlugin = {
  processId: 'injection_molding',
  label: 'Injection molding',
  modes: [assignmentMode, sprueMode, flowFillMode, coolingMode, ejectorMode,
          thicknessMode, gapsMode, rayThicknessMode, rayGapMode,
          slendernessMode, thinSpanMode,
          thicknessAngleMode, gapAngleMode,
          skeletonMode, voxelFieldMode, brepFacesMode, faceAttrsMode, pmiMode,
          highlightsMode],
  defaults: () => ({
    result: -1, option: 0,
    showLines: true, showArrows: true,
    splitMode: false, splitFace: null, splitStart: null, showCuts: true,
    minThickness: 1.0, thicknessScale: '',
    minGap: 0.5, gapScale: '', maskExplained: true,
    minRayThickness: 1.0, rayThicknessScale: '',
    minRayGap: 0.5, rayGapScale: '',
    maxSlenderness: 2.0, slendernessScale: '',
    maxSpanRatio: 5.0, spanScale: '',
    minAngle: 60, angleScale: 180,
    skelResult: -1, graph: 'cluster', gate: null,
    sprueResult: -1, proposal: null, showCandidates: false, showWeld: true,
    stickResult: -1, pins: [], pinDiameter: 3, ejE: 2000, ejAllow: 80,
    ejShowDraft: false, ejSim: null,
    flowResult: -1, fillResult: -1, voxelScalar: 'distance',
    voxelSurface: false, flowVoxel: '', flowDelta0: '0',
    flowSkinCoef: '0.12', flowFillTime: '2', flowIterations: '3',
    flowNeighborhood: '26', coolCoef: '1',
  }),
  Controls: InjectionControls,
  inspect,
  onPick(face, point, ctx) {
    const { modeId, setViewerParam } = useStore.getState();
    const splitParams = useStore.getState().viewerParams.injection_molding ?? {};
    if (modeId === 'assignment' && splitParams.splitMode) {
      return handleSplitPick(moldSplitHost, face, point, ctx);
    }
    if (modeId === 'skeleton' || modeId === 'flowFill') {
      setViewerParam('injection_molding', 'gate', point);
      return true;
    }
    if (modeId === 'ejector') {
      // click near an existing pin removes it, anywhere else adds one
      const params = useStore.getState().viewerParams.injection_molding ?? {};
      const pins: Pin[] = params.pins ?? [];
      let min = Infinity;
      let max = -Infinity;
      for (let i = 0; i < ctx.verts.length; i++) {
        if (ctx.verts[i] < min) min = ctx.verts[i];
        if (ctx.verts[i] > max) max = ctx.verts[i];
      }
      const snap = 0.05 * (max - min) * Math.sqrt(3);
      const hit = pins.findIndex((p) => Math.hypot(
        p.point[0] - point[0], p.point[1] - point[1],
        p.point[2] - point[2]) < snap);
      const next = hit >= 0
        ? pins.filter((_, i) => i !== hit)
        : [...pins, { point, diameter: params.pinDiameter ?? 3 }];
      setViewerParam('injection_molding', 'pins', next);
      return true;
    }
    if (modeId === 'sprue' && pickableProposals) {
      // snap the click to the nearest proposal marker
      const { points, snap } = pickableProposals;
      let best = -1;
      let bestDist = snap * snap;
      for (let p = 0; p < points.length / 3; p++) {
        const dx = points[3 * p] - point[0];
        const dy = points[3 * p + 1] - point[1];
        const dz = points[3 * p + 2] - point[2];
        const d = dx * dx + dy * dy + dz * dz;
        if (d < bestDist) {
          bestDist = d;
          best = p;
        }
      }
      if (best < 0) return false;
      setViewerParam('injection_molding', 'proposal', best);
      // keep the fill-flow mode's gate in sync with the picked proposal
      setViewerParam('injection_molding', 'gate',
                     [points[3 * best], points[3 * best + 1], points[3 * best + 2]]);
      return true;
    }
    return false;
  },
};
