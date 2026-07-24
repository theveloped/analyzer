import type { PmiData, PmiDimension, PmiTolerance } from '../../api/types';
import { PMI_ANNO_COL, PMI_DIM_COL } from '../../colorizers/core';
import type { RGB } from '../../registry/types';
import { datumColorRGB } from './datumColors';
import type { PmiGroups } from './pmiGroups';

/** One floating control-frame/dimension callout. Each `anchorGroups` entry gets
 * its own leader to the shared label — so a dimension across disjoint faces
 * draws a leader to each while the value floats once. */
export interface PmiCalloutData {
  kind: 'tolerance' | 'dimension';
  entity: PmiTolerance | PmiDimension;
  anchorGroups: number[][];
}

export interface PmiView {
  colorMap: Array<[number, RGB]>;
  callouts: PmiCalloutData[];
  legend: Array<{ color: RGB; label: string }>;
}

/** What the rail has selected: a scope chip, or one isolated entity. */
export type PmiSelection =
  | { kind: 'scope'; scope: 'all' | 'pattern' | 'nodatum' | `datum:${string}` }
  | { kind: 'tolerance'; id: number }
  | { kind: 'pattern'; key: string }
  | { kind: 'dimension'; id: number };

/**
 * Turn a rail selection into what the viewer shows: which faces are painted
 * (per-datum colours, toleranced amber, dimensions blue), which frames float a
 * callout, and the legend. Rules match the panel:
 *  - a datum scope colours only that datum + the target faces of every frame
 *    referencing it, and floats all those frames;
 *  - one control frame floats alone, colouring its faces plus its datums (each
 *    in its own colour);
 *  - one dimension floats alone with a leader to each of its faces;
 *  - the "all" overview colours everything but floats nothing (too dense).
 */
export function buildPmiView(
  pmi: PmiData, groups: PmiGroups, sel: PmiSelection, showDims: boolean,
): PmiView {
  const map = new Map<number, RGB>();
  const callouts: PmiCalloutData[] = [];
  const datumsShown = new Set<string>();
  let hasAnno = false;
  let hasDim = false;

  const datumFaces = (letter: string): number[] => {
    const out: number[] = [];
    for (const d of pmi.datums) if (d.name === letter) out.push(...d.face_ids);
    return out;
  };
  const paintDatum = (letter: string) => {
    const faces = datumFaces(letter);
    if (!faces.length) return;
    const col = datumColorRGB(letter);
    for (const f of faces) map.set(f, col); // datum colour wins over amber
    datumsShown.add(letter);
  };
  const paintTolerance = (t: PmiTolerance) => {
    for (const f of t.face_ids) if (!map.has(f)) map.set(f, PMI_ANNO_COL);
    if (t.face_ids.length) hasAnno = true;
  };
  const floatTolerance = (t: PmiTolerance, groupsOfFaces: number[][]) => {
    callouts.push({ kind: 'tolerance', entity: t, anchorGroups: groupsOfFaces.filter((g) => g.length) });
  };
  const paintDimension = (d: PmiDimension): number[] => {
    const faces = [...d.face_ids, ...(d.secondary_face_ids ?? [])];
    for (const f of faces) if (!map.has(f)) map.set(f, PMI_DIM_COL);
    if (faces.length) hasDim = true;
    return faces;
  };

  if (sel.kind === 'tolerance') {
    const t = pmi.tolerances.find((x) => x.id === sel.id);
    if (t) { paintTolerance(t); floatTolerance(t, [t.face_ids]); t.datum_names.forEach(paintDatum); }
  } else if (sel.kind === 'pattern') {
    const p = groups.patterns.find((x) => x.key === sel.key);
    if (p) {
      p.tolerances.forEach(paintTolerance);
      floatTolerance(p.sample, p.tolerances.map((t) => t.face_ids)); // a leader per instance
      p.sample.datum_names.forEach(paintDatum);
    }
  } else if (sel.kind === 'dimension') {
    const d = pmi.dimensions.find((x) => x.id === sel.id);
    if (d) {
      const faces = paintDimension(d);
      callouts.push({ kind: 'dimension', entity: d, anchorGroups: faces.map((f) => [f]) });
    }
  } else if (sel.scope === 'all') {
    pmi.tolerances.forEach(paintTolerance);
    for (const d of pmi.datums) if (d.name) paintDatum(d.name);
  } else if (sel.scope === 'pattern') {
    for (const p of groups.patterns) {
      p.tolerances.forEach(paintTolerance);
      floatTolerance(p.sample, p.tolerances.map((t) => t.face_ids));
      p.sample.datum_names.forEach(paintDatum);
    }
  } else if (sel.scope === 'nodatum') {
    for (const t of groups.noDatum) { paintTolerance(t); floatTolerance(t, [t.face_ids]); }
  } else {
    const letter = sel.scope.slice('datum:'.length);
    paintDatum(letter); // only this datum
    for (const t of pmi.tolerances) {
      if (!t.datum_names.includes(letter)) continue;
      paintTolerance(t);
      floatTolerance(t, [t.face_ids]);
    }
  }

  // the dimensions layer toggle adds blue faces to any scope overview (no float)
  if (showDims && sel.kind === 'scope') for (const d of pmi.dimensions) paintDimension(d);

  const legend: Array<{ color: RGB; label: string }> = [];
  if (hasAnno) legend.push({ color: PMI_ANNO_COL, label: 'toleranced faces' });
  if (hasDim) legend.push({ color: PMI_DIM_COL, label: 'dimensions' });
  for (const letter of [...datumsShown].sort()) {
    legend.push({ color: datumColorRGB(letter), label: `datum ${letter}` });
  }

  return { colorMap: [...map.entries()], callouts, legend };
}
