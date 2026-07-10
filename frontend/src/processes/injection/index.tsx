// Injection molding plugin: parting-direction coverage over the shared
// accessibility matrix, plus the wall-thickness skeleton views (inscribed
// sphere heatmap and the click-a-gate fill-flow simulation on the medial
// graph).

import {
  COL, faceValues, highlightsMode, paintMask, percentile, rampColor,
} from '../../colorizers/core';
import type {
  PaintInfo, ProcessPlugin, RGB, ViewCtx, ViewMode,
} from '../../registry/types';
import { useStore } from '../../state/store';
import {
  buildAdjacency, dijkstra, loadSkeleton, nearestNode, SENTINEL,
  skeletonResults,
} from './skeleton';

function partingResults(ctx: ViewCtx) {
  return ctx.manifest.results.filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'parting_directions');
}

const coverageMode: ViewMode = {
  id: 'coverage',
  label: 'Parting option coverage',
  async paint(ctx): Promise<PaintInfo> {
    const results = partingResults(ctx);
    const result = results[ctx.params.result ?? 0] ?? results[0];
    if (!result) {
      throw new Error('no parting_directions result yet — run the analysis below');
    }
    const fieldId = result.fields[ctx.params.option ?? 0] ?? result.fields[0];
    const desc = ctx.manifest.fields.find((f) => f.id === fieldId);
    if (!desc) throw new Error('coverage mask missing from the manifest');
    const mask = await ctx.getField(desc);
    const info = paintMask(ctx, mask as Uint8Array, COL.floor, COL.tip,
      'covered by the selected parting set', 'not covered (needs a slide / redesign)');
    const option = result.stats.options?.[ctx.params.option ?? 0];
    if (option) {
      info.stats = `directions [${option.directions.join(', ')}] — `
        + `coverage ${(option.coverage * 100).toFixed(1)}%\n${info.stats}`;
    }
    return info;
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

const thicknessMode: ViewMode = {
  id: 'thickness',
  label: 'Wall thickness heatmap',
  async paint(ctx): Promise<PaintInfo> {
    const result = pickSkeletonResult(ctx);
    const fieldId = result.fields.find((f) => f.endsWith('.thickness'));
    const desc = fieldId && ctx.manifest.fields.find((f) => f.id === fieldId);
    if (!desc) throw new Error('thickness field missing from the manifest');
    const thickness = await ctx.getField(desc) as Float32Array;
    const vals = faceValues(ctx, thickness, null);
    const max = percentile(vals, 0.98);
    ctx.paintFaces((f) => (isNaN(vals[f]) ? COL.inaccess : rampColor(vals[f] / max)));
    return {
      legend: [
        { color: rampColor(0), label: 'thin wall (0 mm)' },
        { color: rampColor(0.5), label: `${(max / 2).toFixed(2)} mm` },
        { color: rampColor(1), label: `thick wall (≥ ${max.toFixed(2)} mm)` },
      ],
      stats: `mean thickness ${result.stats.mean_thickness?.toFixed(2)} mm — `
        + `inscribed-sphere diameter per vertex`,
    };
  },
};

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

const EMPTY: Record<string, any> = {};

function InjectionControls() {
  const manifest = useStore((s) => s.manifest);
  const modeId = useStore((s) => s.modeId);
  const params = useStore((s) => s.viewerParams.injection_molding) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  const set = (name: string, value: any) => setParam('injection_molding', name, value);

  const results = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'parting_directions');
  const result = results[params.result ?? 0] ?? results[0];
  const options: { directions: number[]; coverage: number }[] = result?.stats.options ?? [];

  const skelResults = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'wall_skeleton');
  const skeletonView = modeId === 'skeleton' || modeId === 'thickness';

  if (skeletonView) {
    return (
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

        {modeId === 'skeleton' && (
          <>
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
      </>
    );
  }

  return (
    <>
      <label>Result (parameter set)</label>
      <select value={params.result ?? 0} onChange={(e) => set('result', parseInt(e.target.value))}>
        {results.map((r, i) => (
          <option key={r.hash} value={i}>
            {`slides ${r.params.slides ?? 0} · ${r.hash}`}
          </option>
        ))}
        {!results.length && <option value={0}>no results yet</option>}
      </select>

      <label>Parting option</label>
      <select value={params.option ?? 0} onChange={(e) => set('option', parseInt(e.target.value))}>
        {options.slice(0, result?.fields.length ?? 0).map((o, i) => (
          <option key={i} value={i}>
            {`[${o.directions.join(', ')}] — ${(o.coverage * 100).toFixed(1)}%`}
          </option>
        ))}
        {!options.length && <option value={0}>—</option>}
      </select>

      {options.length > 0 && (
        <div className="hint">
          all ranked options:{' '}
          {options.map((o) => `[${o.directions.join(',')}] ${(o.coverage * 100).toFixed(1)}%`).join(' · ')}
        </div>
      )}
    </>
  );
}

export const injectionPlugin: ProcessPlugin = {
  processId: 'injection_molding',
  label: 'Injection molding',
  modes: [coverageMode, thicknessMode, skeletonMode, highlightsMode],
  defaults: () => ({
    result: 0, option: 0, skelResult: 0, graph: 'cluster', gate: null,
  }),
  Controls: InjectionControls,
  onPick(face, point) {
    if (useStore.getState().modeId !== 'skeleton') return false;
    useStore.getState().setViewerParam('injection_molding', 'gate', point);
    return true;
  },
};
