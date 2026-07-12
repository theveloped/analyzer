// Injection molding plugin: membership-based mold assignment (striped
// multi-valid BREP faces, click-to-cycle, parting line on BREP edges),
// rolling-sphere wall thickness and gaps/clearance heatmaps, and the
// wall-thickness skeleton graph with interactive fill flow.

import { putOverrides } from '../../api/client';
import type { FieldDescriptor, Manifest, ResultEntry } from '../../api/types';
import {
  brepFacesMode, COL, faceValues, heatmapMode, highlightsMode, percentile,
  rampColor, regionColor,
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

const CONFLICT_FEATURE = 254;
const INTERNAL_FEATURE = 255;

const ARROW_COLORS: Record<string, RGB> = {
  main_a: [0.44, 0.64, 0.86], // side A
  main_b: [0.62, 0.8, 0.58], // side B
};

function fade(color: RGB): RGB {
  return [color[0] * 0.45 + 0.55, color[1] * 0.45 + 0.55, color[2] * 0.45 + 0.55];
}

function popcount(x: number): number {
  let n = 0;
  while (x) { n += x & 1; x >>>= 1; }
  return n;
}

function nthSetBit(x: number, n: number): number {
  for (let bit = 0; bit < 32; bit++) {
    if ((x >>> bit) & 1) {
      if (n === 0) return bit;
      n--;
    }
  }
  return 0;
}

function nextSetBit(x: number, after: number): number {
  for (let bit = after + 1; bit < 32; bit++) if ((x >>> bit) & 1) return bit;
  for (let bit = 0; bit <= after; bit++) if ((x >>> bit) & 1) return bit;
  return after;
}

function resultsFor(manifest: Manifest, analysis: string) {
  return manifest.results.filter(
    (r) => r.process === 'injection_molding' && r.analysis === analysis
      && (analysis !== 'mold_orientation' || r.stats.schema === 2));
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

interface AssignmentData {
  result: ResultEntry;
  option: number;
  desc: FieldDescriptor; // membership field (labels/colors in params)
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
  const result = results[ctx.params.result ?? 0] ?? results[results.length - 1];
  const option = ctx.params.option ?? 0;

  const brepDesc = ctx.manifest.fields.find((f) => f.id === 'brep_faces');
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
    result, option, desc, membership, region, valid, defaults, brepIds,
    current, overridesKey,
  };
}

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

    ctx.paintFaces((f) => {
      const b = brepIds[f];
      const cat = current[b];
      if (cat === INTERNAL_FEATURE) {
        internalCount++;
        return regionColor(region[f]);
      }
      if (cat === CONFLICT_FEATURE) {
        conflictCount++;
        // spatially truthful: each triangle shows which feature partially
        // reaches it (faded); unreachable triangles get the conflict color
        const m = membership[f];
        return m ? fade(colors[nthSetBit(m, 0)]) : conflictColor;
      }
      counts[cat]++;
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
      .map((label, i) => ({ color: colors[i], label: `${label} (${counts[i]})` }))
      .filter((_, i) => counts[i] > 0);
    if (conflictCount) {
      legend.push({ color: conflictColor, label: `conflict / needs split (${conflictCount})` });
    }
    const regionCounts: number[] = desc.params.region_counts
      ?? (resolveField(ctx, data.result, `internal_region_${data.option}`)?.params.region_counts ?? []);
    regionCounts.forEach((count: number, i: number) => {
      legend.push({ color: regionColor(i + 1), label: `internal undercut ${i + 1} (${count})` });
    });

    if (ctx.params.showLines !== false) {
      const edgesDesc = ctx.manifest.fields.find((f) => f.id === 'brep_edges');
      const pairsDesc = ctx.manifest.fields.find((f) => f.id === 'brep_edge_pairs');
      if (edgesDesc && pairsDesc) {
        const edges = await ctx.getField(edgesDesc) as Float32Array;
        const pairs = await ctx.getField(pairsDesc) as Uint32Array;
        const kept: number[] = [];
        for (let e = 0; e < pairs.length / 2; e++) {
          const a = current[pairs[2 * e]];
          const b = current[pairs[2 * e + 1]];
          if (a !== b && a < CONFLICT_FEATURE && b < CONFLICT_FEATURE) {
            for (let i = 0; i < 6; i++) kept.push(edges[6 * e + i]);
          }
        }
        ctx.setLines(new Float32Array(kept));
      }
    }

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
    stats += '\nstriped = multiple valid features — click a face to cycle';
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

function pickSkeletonResult(ctx: ViewCtx) {
  const results = skeletonResults(ctx);
  const result = results[ctx.params.skelResult ?? 0] ?? results[0];
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
    ctx.setGraph(sk.key, sk.nodes, sk.edges, sk.radii);
    ctx.setMeshOpacity(0.35);

    const gate = ctx.params.gate as [number, number, number] | null;
    const graphLabel = which === 'raw'
      ? `raw graph ${nodeCount} nodes` : `clustered graph ${nodeCount} nodes`;

    if (!gate) {
      // no gate yet: color the skeleton by local wall radius (thin = hot)
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

    const source = nearestNode(sk.nodes, gate);
    const dist = dijkstra(buildAdjacency(sk.key, sk), source);
    const tMax = Math.max(percentile(dist, 0.98), 1e-12);

    ctx.paintGraph((n) => {
      if (n === source) return GATE;
      return isFinite(dist[n]) ? rampColor(dist[n] / tMax) : COL.inaccess;
    });

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
    const vals = faceValues(ctx, vertexFill, null);
    ctx.paintFaces((f) => (isNaN(vals[f]) ? COL.inaccess : rampColor(vals[f] / tMax)));

    return {
      legend: [
        { color: GATE, label: 'gate (click to move)' },
        { color: rampColor(0), label: 'fills early' },
        { color: rampColor(1), label: 'fills late' },
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
    const result = results[ctx.params.sprueResult ?? 0] ?? results[0];
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
    const vals = faceValues(ctx, vertexFill, null);
    ctx.paintFaces((f) => (isNaN(vals[f]) ? COL.inaccess : rampColor(vals[f] / tMax)));

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
        ...(ctx.params.showWeld !== false
          ? [{ color: WELD, label: 'weld-line indicator (fronts meet)' }] : []),
        { color: COL.inaccess, label: 'unreached / no skeleton node' },
      ],
      stats: `${reasons}${confidence}`,
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
    const result = results[ctx.params.stickResult ?? 0] ?? results[0];
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

const thicknessMode = heatmapMode(
  'thickness', 'Wall thickness heatmap',
  (ctx) => scalarField(ctx, 'thickness', 'thickness'),
  {
    flagDirection: 'below',
    thresholdParam: 'minThickness',
    scaleParam: 'thicknessScale',
    okLabel: 'thick — ok',
  });

const gapsMode = heatmapMode(
  'gaps', 'Wall gaps / clearance heatmap',
  (ctx) => scalarField(ctx, 'gaps', 'gap'),
  {
    flagDirection: 'below',
    thresholdParam: 'minGap',
    scaleParam: 'gapScale',
    okLabel: 'clearance ok (incl. no opposing wall in range)',
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
    lines.push(`brep face: ${b}`);
    lines.push(`reachable by: ${bits.join(', ') || 'nothing (internal)'}`);
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

function InjectionControls() {
  const manifest = useStore((s) => s.manifest);
  const modeId = useStore((s) => s.modeId);
  const params = useStore((s) => s.viewerParams.injection_molding) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  const set = (name: string, value: any) => setParam('injection_molding', name, value);

  const results = manifest ? resultsFor(manifest, 'mold_orientation') : [];
  const result = results[params.result ?? 0] ?? results[results.length - 1];
  const options: any[] = result?.stats.options ?? [];
  const fieldOptions = options.slice(0, 3);

  const skelResults = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'wall_skeleton');

  const sprueResultList = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'sprue_proposals'
      && r.stats.schema === 2);
  const sprueResult = sprueResultList[params.sprueResult ?? 0] ?? sprueResultList[0];
  const proposals: Proposal[] = sprueResult?.stats.proposals ?? [];

  const stickingList = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding'
      && r.analysis === 'ejection_sticking' && r.stats.schema === 2);
  const pins: Pin[] = params.pins ?? [];
  const ejSim = params.ejSim ?? null;

  return (
    <>
      {modeId === 'sprue' && (
        <>
          <label>Result (parameter set)</label>
          <select
            value={params.sprueResult ?? 0}
            onChange={(e) => {
              set('sprueResult', parseInt(e.target.value));
              set('proposal', null);
            }}
          >
            {sprueResultList.map((r, i) => (
              <option key={r.hash} value={i}>
                {`${r.stats.proposals?.length ?? 0} proposals · ${r.hash}`}
              </option>
            ))}
            {!sprueResultList.length && <option value={0}>no results yet</option>}
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
            value={params.stickResult ?? 0}
            onChange={(e) => set('stickResult', parseInt(e.target.value))}
          >
            {stickingList.map((r, i) => (
              <option key={r.hash} value={i}>
                {`${(r.stats.totals?.sticking_force_n ?? 0).toFixed(0)} N sticking · ${r.hash}`}
              </option>
            ))}
            {!stickingList.length && <option value={0}>no results yet</option>}
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
            value={params.skelResult ?? 0}
            onChange={(e) => set('skelResult', parseInt(e.target.value))}
          >
            {skelResults.map((r, i) => (
              <option key={r.hash} value={i}>
                {`max r ${r.params.max_radius ?? '?'} mm · ${r.hash}`}
              </option>
            ))}
            {!skelResults.length && <option value={0}>no results yet</option>}
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

      {modeId === 'assignment' && (
        <>
          <label>Result (parameter set)</label>
          <select value={params.result ?? 0} onChange={(e) => set('result', parseInt(e.target.value))}>
            {results.map((r, i) => (
              <option key={r.hash} value={i}>
                {`max slides ${r.params.max_slides ?? '?'} · ${r.hash}`}
              </option>
            ))}
            {!results.length && <option value={0}>no results yet</option>}
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

          {options.length > 0 && (
            <div className="hint">
              ranked: {options.map((o, i) =>
                `#${i} ±d${o.pair[0]} ${o.feasible ? '✓' : '✗'} ${(o.coverage * 100).toFixed(0)}%`).join(' · ')}
            </div>
          )}
        </>
      )}

      {modeId === 'thickness' && (
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
      )}

      {modeId === 'gaps' && (
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
      )}
    </>
  );
}

export const injectionPlugin: ProcessPlugin = {
  processId: 'injection_molding',
  label: 'Injection molding',
  modes: [assignmentMode, sprueMode, ejectorMode, thicknessMode, gapsMode,
          skeletonMode, brepFacesMode, highlightsMode],
  defaults: () => ({
    result: 0, option: 0,
    showLines: true, showArrows: true,
    minThickness: 1.0, thicknessScale: '',
    minGap: 0.5, gapScale: '',
    skelResult: 0, graph: 'cluster', gate: null,
    sprueResult: 0, proposal: null, showCandidates: false, showWeld: true,
    stickResult: 0, pins: [], pinDiameter: 3, ejE: 2000, ejAllow: 80,
    ejShowDraft: false, ejSim: null,
  }),
  Controls: InjectionControls,
  inspect,
  onPick(face, point, ctx) {
    const { modeId, setViewerParam } = useStore.getState();
    if (modeId === 'skeleton') {
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
