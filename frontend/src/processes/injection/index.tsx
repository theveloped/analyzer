// Injection molding plugin: membership-based mold assignment (striped
// multi-valid BREP faces, click-to-cycle, parting line on BREP edges),
// rolling-sphere wall thickness and gaps/clearance heatmaps, and the
// wall-thickness skeleton graph with interactive fill flow.

import { putOverrides } from '../../api/client';
import type { FieldDescriptor, Manifest, ResultEntry } from '../../api/types';
import {
  brepFacesMode, COL, faceValues, fade, heatmapMode, highlightsMode,
  nextSetBit, nthSetBit, percentile, popcount, rampColor, regionColor,
} from '../../colorizers/core';
import type {
  LegendEntry, PaintInfo, ProcessPlugin, RGB, ViewCtx, ViewMode,
} from '../../registry/types';
import { useStore } from '../../state/store';
import {
  buildAdjacency, dijkstra, loadSkeleton, nearestNode, SENTINEL,
  skeletonResults,
} from './skeleton';

const CONFLICT_FEATURE = 254;
const INTERNAL_FEATURE = 255;

const ARROW_COLORS: Record<string, RGB> = {
  main_a: [0.44, 0.64, 0.86], // side A
  main_b: [0.62, 0.8, 0.58], // side B
};

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
          + `click the part to place an injection gate`,
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
        + `fill time = Σ length / r⁴ along the skeleton (relative units)`,
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

  return (
    <>
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
  modes: [assignmentMode, thicknessMode, gapsMode, skeletonMode,
          brepFacesMode, highlightsMode],
  defaults: () => ({
    result: 0, option: 0,
    showLines: true, showArrows: true,
    minThickness: 1.0, thicknessScale: '',
    minGap: 0.5, gapScale: '',
    skelResult: 0, graph: 'cluster', gate: null,
  }),
  Controls: InjectionControls,
  inspect,
  onPick(face, point) {
    if (useStore.getState().modeId !== 'skeleton') return false;
    useStore.getState().setViewerParam('injection_molding', 'gate', point);
    return true;
  },
};
