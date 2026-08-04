// The v1 compute panel's auto-generated form. The ParamSpec -> value codec it
// used to own now lives in `src/params/codec.ts`, shared with the v2 rails so
// the two cannot disagree about what a blank field means.
//
// v2's renderer is `v2/components/rail/ParamsForm.tsx`; this file stays until
// the v1 viewer retires.

import type { AnalysisInfo, ParamSpec } from '../api/types';
import {
  formatDefault, initialValues, parseValues, type ParamValues,
} from '../params/codec';

export type { ParamValues };
export { initialValues, parseValues };

export function ParamForm({ analysis, values, onChange }: {
  analysis: AnalysisInfo;
  values: ParamValues;
  onChange: (name: string, value: any) => void;
}) {
  return (
    <>
      {analysis.params.map((spec) => (
        <ParamRow key={spec.name} spec={spec}
          value={values[spec.name] ?? formatDefault(spec)}
          onChange={(v) => onChange(spec.name, v)} />
      ))}
    </>
  );
}

function ParamRow({ spec, value, onChange }: {
  spec: ParamSpec; value: any; onChange: (v: any) => void;
}) {
  const label = spec.label ?? spec.name;
  if (spec.type === 'bool') {
    return (
      <label className="check">
        <input type="checkbox" checked={!!value}
          onChange={(e) => onChange(e.target.checked)} />
        {label}
      </label>
    );
  }
  if (spec.type === 'select') {
    return (
      <div className="row">
        <label>{label}</label>
        <select value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
          {(spec.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    );
  }
  const numeric = spec.type === 'int' || spec.type === 'number';
  return (
    <div className="row">
      <label>{label}{spec.unit ? ` (${spec.unit})` : ''}</label>
      <input
        type={numeric ? 'number' : 'text'}
        value={String(value ?? '')}
        min={spec.min}
        max={spec.max}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
