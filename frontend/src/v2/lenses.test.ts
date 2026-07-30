import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
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

/**
 * A route template names the lens each of its checks opens. The backend cannot
 * check that — the lens registry only exists here — so a template naming a lens
 * that was renamed or removed would silently open nothing. Read as text: a YAML
 * parser is not a dependency worth adding for one key.
 *
 * The Python half is `test_vocab.py`, which checks the same templates' analysis
 * ids, operation kinds and stats rules against the registry and `plans.py`.
 */
describe('route templates', () => {
  const dir = fileURLToPath(new URL('../../../catalogue/routes/', import.meta.url));
  const files = readdirSync(dir).filter((n) => n.endsWith('.yaml'));

  it('has templates to check', () => {
    expect(files.length, `no route templates in ${dir}`).toBeGreaterThan(0);
  });

  it.each(files)('%s names only lenses that exist', (file) => {
    const text = readFileSync(dir + file, 'utf-8');
    const named = [...text.matchAll(/^\s*lens:\s*(\S+)/gm)].map((m) => m[1]);
    const keys = new Set(LENSES.map((lens) => lens.key));
    expect(named.length, 'no lens: keys found — has the syntax changed?')
      .toBeGreaterThan(0);
    expect(named.filter((key) => !keys.has(key)), 'unknown lens keys')
      .toEqual([]);
  });
});
