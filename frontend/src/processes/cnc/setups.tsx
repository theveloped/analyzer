// CNC setup-combination assignment: membership-based per-BREP-face setup
// selection (striped multi-valid faces, click-to-cycle), setup boundary
// lines on BREP edges and per-setup direction arrows — the CNC analog of
// the injection molding mold-assignment view over the same overrides
// machinery.

import { putOverrides } from '../../api/client';
import type { FieldDescriptor, Manifest, ResultEntry } from '../../api/types';
import {
  COL, fade, nextSetBit, nthSetBit, popcount, regionColor,
} from '../../colorizers/core';
import type {
  LegendEntry, PaintInfo, RGB, ViewCtx, ViewMode,
} from '../../registry/types';
import { useStore } from '../../state/store';
import {
  drawSplitOverlays, edgeDescriptors, effectiveDescriptor,
  type SplitHost,
} from '../../splits/splits';
import { SplitControls } from '../../splits/SplitControls';

const CONFLICT_FEATURE = 254;
const INTERNAL_FEATURE = 255;
const SETUPS_SCHEMA = 3;

export function setupsResults(manifest: Manifest): ResultEntry[] {
  return manifest.results.filter(
    (r) => r.process === 'cnc' && r.analysis === 'setups'
      && r.stats.schema === SETUPS_SCHEMA);
}

function resolveField(ctx: ViewCtx, result: ResultEntry, member: string) {
  const fieldId = result.fields.find((f) => f.endsWith(`.${member}`));
  return fieldId ? ctx.manifest.fields.find((f) => f.id === fieldId) ?? null : null;
}

/** The stats entry behind field option k of a setups result. */
export function fieldOption(result: ResultEntry, k: number) {
  const index = result.stats.field_options?.[k];
  return index === undefined ? null : result.stats.options?.[index] ?? null;
}

interface SetupsData {
  result: ResultEntry;
  option: number; // field option index (membership_<option>)
  desc: FieldDescriptor; // membership field (labels/colors in params)
  brepDesc: FieldDescriptor; // effective face ids (subfaces or brep_faces)
  membership: Uint32Array;
  region: Uint32Array;
  valid: Uint32Array;
  defaults: Uint8Array;
  brepIds: Uint32Array;
  current: Uint8Array; // per-brep-face selected setup (override-aware)
  overridesKey: string;
}

export async function loadSetups(ctx: ViewCtx): Promise<SetupsData> {
  const results = setupsResults(ctx.manifest);
  if (!results.length) {
    const stale = ctx.manifest.results.some(
      (r) => r.process === 'cnc' && r.analysis === 'setups');
    throw new Error(stale
      ? 'stored result has an old schema — re-run setup combinations'
      : 'no setups result yet — run the analysis below');
  }
  const result = results[ctx.params.setupsResult ?? 0] ?? results[results.length - 1];
  const option = ctx.params.setupsOption ?? 0;

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
    throw new Error('assignment fields missing — re-run setup combinations');
  }

  const [membership, region, valid, defaults, brepIds] = await Promise.all([
    ctx.getField(desc) as Promise<Uint32Array>,
    ctx.getField(regionDesc) as Promise<Uint32Array>,
    ctx.getField(validDesc) as Promise<Uint32Array>,
    ctx.getField(defaultsDesc) as Promise<Uint8Array>,
    ctx.getField(brepDesc) as Promise<Uint32Array>,
  ]);

  const overridesKey = `cnc.setups.${result.hash}`;
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

/** Split-interaction wiring for the setups assignment view. */
export const cncSplitHost: SplitHost = {
  processId: 'cnc',
  modeId: 'setups',
  currentResult: (manifest, params) => {
    const results = setupsResults(manifest);
    return results[params.setupsResult ?? 0] ?? results[results.length - 1];
  },
  analysisOf: (result) => (result.stats.verdict ? 'setup_verdict' : 'setups'),
  resultParam: 'setupsResult',
};

/** 'A · 2 setups (flip) · feasible 100%' — selector / stats label. */
export function optionLabel(opt: any): string {
  if (!opt) return '—';
  return `${opt.machine} · ${opt.setups.length} setup(s)`
    + `${opt.flip ? ' (flip)' : ''}`
    + `${opt.verdict ? ' · tool verdict' : ''}`
    + ` · ${opt.feasible ? 'feasible' : `${(opt.coverage * 100).toFixed(1)}%`}`;
}

/** Result-selector label: search vs verdict entries, stale warning. */
export function resultLabel(r: ResultEntry): string {
  const base = r.stats.verdict
    ? `${optionLabel(r.stats.options?.[0])} · ${r.params.tools?.length ?? '?'} tools`
    : `tilt ${r.params.tilt ?? '?'}° · max ${r.params.max_setups ?? '?'} setups`;
  return `${r.stale ? '⚠ stale · ' : ''}${base} · ${r.hash}`;
}

export const setupsMode: ViewMode = {
  id: 'setups',
  label: 'Setup assignment (3-axis / 3+2)',
  async paint(ctx): Promise<PaintInfo> {
    const data = await loadSetups(ctx);
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
      // ids past the arrays = new sub-faces the result predates — paint
      // them via the conflict path until the auto re-run lands
      const cat = b < current.length ? current[b] : CONFLICT_FEATURE;
      if (cat === INTERNAL_FEATURE) {
        internalCount++;
        return regionColor(region[f]);
      }
      if (cat === CONFLICT_FEATURE) {
        conflictCount++;
        // spatially truthful: each triangle shows which setup partially
        // covers it (faded); uncovered triangles get the conflict color
        const m = membership[f];
        return m ? fade(colors[nthSetBit(m, 0)]) : conflictColor;
      }
      counts[cat]++;
      const v = valid[b];
      const n = popcount(v);
      if (n <= 1) return colors[cat];
      // striped multi-valid face: selected setup strong, others faded
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
    const regionDesc = resolveField(ctx, data.result, `internal_region_${data.option}`);
    const regionCounts: number[] = regionDesc?.params.region_counts ?? [];
    const SHOWN_REGIONS = 8;
    regionCounts.slice(0, SHOWN_REGIONS).forEach((count: number, i: number) => {
      legend.push({ color: regionColor(i + 1), label: `unmachinable region ${i + 1} (${count})` });
    });
    if (regionCounts.length > SHOWN_REGIONS) {
      const rest = regionCounts.slice(SHOWN_REGIONS).reduce((a, b) => a + b, 0);
      legend.push({
        color: regionColor(SHOWN_REGIONS + 1),
        label: `… ${regionCounts.length - SHOWN_REGIONS} more regions (${rest} faces)`,
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
    const splitLines = await drawSplitOverlays(ctx, cncSplitHost, brepIds);

    const opt = fieldOption(data.result, data.option);
    if (ctx.params.showArrows !== false && opt) {
      ctx.setArrows(opt.arrows.map((arrow: any) => ({
        direction: arrow.direction,
        color: colors[arrow.index] ?? COL.ok,
      })));
    }

    let stats = '';
    if (opt) {
      const setups = opt.setups.map((s: any, j: number) =>
        `${labels[j]}: d${s.direction} +${s.marginal.toFixed(0)} mm²`).join(' · ');
      stats = `${optionLabel(opt)} · unmachinable ${opt.counts.internal.toFixed(0)} mm²\n${setups}`;
      if (opt.verdict) {
        stats += `\ntool verdict: coverage ${(opt.coverage * 100).toFixed(1)}%`
          + ` (visibility ${(opt.verdict.base_coverage * 100).toFixed(1)}%)`
          + ` · lost to tooling ${opt.verdict.lost.toFixed(0)} mm²`;
      }
    }
    if (data.result.stale) {
      stats += '\n⚠ cuts or directions changed since this result — re-run the analysis';
    }
    stats += '\nstriped = machinable in several setups — click a face to cycle';
    if (splitLines.length) stats += `\n${splitLines.join('\n')}`;
    return { legend, stats };
  },

  async onPick(face, ctx): Promise<boolean> {
    let data: SetupsData;
    try {
      data = await loadSetups(ctx);
    } catch {
      return false;
    }
    const b = data.brepIds[face];
    if (b >= data.valid.length) return false; // sub-face newer than result
    const v = data.valid[b];
    if (popcount(v) < 2) return false; // solid / conflict / unmachinable

    const next = nextSetBit(v, data.current[b]);
    const { setOverride } = useStore.getState();
    setOverride(data.overridesKey, data.option, b,
                next === data.defaults[b] ? null : next);

    const payload = useStore.getState().overrides[data.overridesKey] ?? {};
    if (data.result.overrides_url) {
      putOverrides(data.result.overrides_url, payload).catch((err) =>
        useStore.getState().set({ error: String(err) }));
    }
    return true;
  },
};

const EMPTY: Record<string, any> = {};

/** Setups-mode section of the CNC controls (result/option/toggles). */
export function SetupsControls() {
  const manifest = useStore((s) => s.manifest);
  const params = useStore((s) => s.viewerParams.cnc) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  const set = (name: string, value: any) => setParam('cnc', name, value);

  const results = manifest ? setupsResults(manifest) : [];
  const result = results[params.setupsResult ?? 0] ?? results[results.length - 1];
  const options: any[] = result?.stats.options ?? [];
  const fieldOptions: number[] = result?.stats.field_options ?? [];

  return (
    <>
      <label>Result (parameter set)</label>
      <select
        value={params.setupsResult ?? 0}
        onChange={(e) => { set('setupsResult', parseInt(e.target.value)); set('setupsOption', 0); }}
      >
        {results.map((r, i) => (
          <option key={r.hash} value={i}>{resultLabel(r)}</option>
        ))}
        {!results.length && <option value={0}>no results yet</option>}
      </select>

      <label>Setup plan</label>
      <select
        value={params.setupsOption ?? 0}
        onChange={(e) => set('setupsOption', parseInt(e.target.value))}
      >
        {fieldOptions.map((index, k) => (
          <option key={k} value={k}>{optionLabel(options[index])}</option>
        ))}
        {!fieldOptions.length && <option value={0}>—</option>}
      </select>

      <div className="row">
        <label className="check">
          <input
            type="checkbox" checked={params.showLines !== false}
            onChange={(e) => set('showLines', e.target.checked)}
          />
          setup boundaries
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
        click a face to cycle it between the setups that can machine it ·
        faded stripes = other valid setups
      </div>

      <SplitControls host={cncSplitHost} />

      {options.length > 0 && (
        <div className="hint">
          ranked: {options.slice(0, 6).map((o, i) =>
            `#${i} ${o.machine}×${o.setups.length} ${o.feasible ? '✓' : '✗'}`).join(' · ')}
        </div>
      )}
    </>
  );
}
