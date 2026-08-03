import type { FC } from 'react';
import type { ResultEntry } from '../../api/types';
import { Select } from '../../catalyst/select';
import { useStore } from '../../state/store';

/**
 * Injection params a `ParamSpec` cannot describe on its own.
 *
 * Four of these lenses pick WHICH stored result to paint — skeleton, mold
 * assignment, flow fill and the voxel debug view. The option list is the
 * manifest's results for that analysis, so it changes whenever a job lands and
 * cannot be a static `options` array. That is the case `paramWidgets` exists
 * for, and it is the same widget four times over with a different results
 * getter — hence a factory rather than four components.
 *
 * The stored value is an INDEX into that list, with -1 meaning "the latest",
 * which is what the paints already read via `pickResult`.
 */
export function resultPicker(
  process: string, analysis: string,
  describe: (r: ResultEntry) => string,
): FC<{ value: unknown; onChange: (v: unknown) => void }> {
  return function ResultPicker({ value, onChange }) {
    const manifest = useStore((s) => s.manifest);
    const results = (manifest?.results ?? []).filter(
      (r) => r.process === process && r.analysis === analysis);
    return (
      <Select
        value={String(value ?? -1)}
        aria-label="Result"
        onChange={(e) => onChange(parseInt(e.target.value))}
      >
        {results.length > 0 && <option value={-1}>latest</option>}
        {results.map((r, i) => (
          <option key={r.hash} value={i}>{describe(r)}</option>
        ))}
        {!results.length && <option value={-1}>no results yet</option>}
      </Select>
    );
  };
}

export const INJECTION_PARAM_WIDGETS = {
  skelResult: resultPicker(
    'injection_molding', 'wall_skeleton',
    (r) => `max r ${r.params.max_radius ?? '?'} mm · ${r.hash}`,
  ) as any,
};
