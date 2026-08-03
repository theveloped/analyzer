import { describe, expect, it } from 'vitest';
import { PROCESS_PLUGINS } from '../registry';
import { ANALYSES } from './analyses';
import { FIELD_LENSES } from './fieldLenses';
import { CURATION, LENSES } from './lenses';

/**
 * The lens registry is derived, so its failure mode is silence: a curation
 * key that matches no mode, or a plugin the builder never visits, produces
 * no error and no lens. These assertions are the enforcement.
 */
describe('lens registry', () => {
  it('curates only keys that resolve to a real mode', () => {
    const modeKeys = new Set(
      Object.values(PROCESS_PLUGINS).flatMap(
        (plugin) => plugin.modes.map((mode) => `${plugin.processId}:${mode.id}`)),
    );
    const orphans = Object.keys(CURATION).filter((key) => !modeKeys.has(key));
    expect(orphans, 'curation keys matching no ViewMode').toEqual([]);
  });

  it('builds a lens for every registered plugin', () => {
    // a plugin missing from the display order must still contribute lenses;
    // it previously produced zero, silently
    const covered = new Set(LENSES.map((lens) => lens.processId));
    const expected = Object.values(PROCESS_PLUGINS)
      // the directions plugin's single mode is deliberately hidden
      .filter((plugin) => plugin.modes.some((mode) => mode.id !== 'directions'))
      .map((plugin) => plugin.processId);
    for (const processId of expected) expect(covered).toContain(processId);
  });

  it('gives every lens a unique key', () => {
    const keys = LENSES.map((lens) => lens.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

/**
 * Lens keys named in CODE. The backend cannot check these — the lens registry
 * only exists here — so a key left behind by a rename would silently open
 * nothing. Route templates used to be the other namer of lens keys; with them
 * gone, the field lenses and the check catalog are what remain.
 */
describe('lens keys named in code', () => {
  const keys = new Set(LENSES.map((lens) => lens.key));

  it('every field lens points at a real lens', () => {
    const named = Object.values(FIELD_LENSES).map((def) => def.lensKey);
    expect(named.length, 'no field lenses found').toBeGreaterThan(0);
    expect(named.filter((key) => !keys.has(key)), 'unknown lens keys')
      .toEqual([]);
  });

  it('every catalog analysis has a lens of its own id', () => {
    const named = ANALYSES.map((a) => `${a.process}:${a.id}`);
    expect(named.length, 'no catalog analyses found').toBeGreaterThan(0);
    expect(named.filter((key) => !keys.has(key)), 'unknown lens keys')
      .toEqual([]);
  });
});

/**
 * `ViewMode.params` is the declaration the rail generates its settings from.
 * Nothing else can check it: the names are viewer-param keys, which exist only
 * on the frontend, and a typo produces a field that edits a key no paint reads
 * — silently, forever. These are that check.
 */
describe('declared mode params', () => {
  const modes = Object.values(PROCESS_PLUGINS).flatMap((plugin) =>
    plugin.modes.map((mode) => ({ plugin, mode })));

  it('has modes declaring params at all', () => {
    const declaring = modes.filter(({ mode }) => mode.params?.length);
    expect(declaring.length, 'no ViewMode declares params').toBeGreaterThan(0);
  });

  it('seeds every param that declares a real default', () => {
    // A null default means "auto": absent from the bag is exactly how a
    // heatmap says "span the data range", so those must NOT be seeded. A
    // param with a real default is different — leave it out of `defaults()`
    // and the generated field renders blank while the paint uses the value,
    // so the panel disagrees with the picture.
    const orphans: string[] = [];
    for (const { plugin, mode } of modes) {
      const defaults = plugin.defaults({} as never) ?? {};
      for (const spec of mode.params ?? []) {
        if (spec.default != null && !(spec.name in defaults)) {
          orphans.push(`${plugin.processId}:${mode.id} -> ${spec.name}`);
        }
      }
    }
    expect(orphans, 'params with a real default the plugin never seeds')
      .toEqual([]);
  });

  it('does not declare one name two ways within a process', () => {
    // viewerParams is ONE bag per process, so two modes sharing a name share
    // the value — deliberate for maskExplained, a bug if the types disagree
    const clashes: string[] = [];
    for (const plugin of Object.values(PROCESS_PLUGINS)) {
      const seen = new Map<string, string>();
      for (const mode of plugin.modes) {
        for (const spec of mode.params ?? []) {
          const shape = `${spec.type}:${JSON.stringify(spec.default)}`;
          const prev = seen.get(spec.name);
          if (prev && prev !== shape) {
            clashes.push(`${plugin.processId}:${spec.name} ${prev} vs ${shape}`);
          }
          seen.set(spec.name, shape);
        }
      }
    }
    expect(clashes, 'same param name declared two ways').toEqual([]);
  });

  it('agrees with the field lenses that name the same params', () => {
    // FIELD_LENSES hand-copies threshold/min/scale/band/mask names off the
    // mode. Until it reads them, this is what keeps the two tables honest.
    const mismatches: string[] = [];
    for (const [key, def] of Object.entries(FIELD_LENSES)) {
      const [processId, modeId] = key.split(':');
      const mode = PROCESS_PLUGINS[processId]?.modes.find((m) => m.id === modeId);
      const names = new Set((mode?.params ?? []).map((s) => s.name));
      if (!names.size) continue; // not a heatmapMode — nothing to compare
      for (const name of [def.thresholdParam, def.minParam, def.scaleParam]) {
        if (name && !names.has(name)) mismatches.push(`${key}: ${name}`);
      }
    }
    expect(mismatches, 'field lens names a param its mode does not declare')
      .toEqual([]);
  });
});
