import { describe, expect, it } from 'vitest';
import { PROCESS_PLUGINS } from '../registry';
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
