// Auto-generated form from the backend's declared parameter specs. Plugins
// can override individual widgets later via ProcessPlugin.paramWidgets; the
// declared types below cover everything the current analyses need.

import type { AnalysisInfo, ParamSpec } from '../api/types';

export type ParamValues = Record<string, any>;

export function initialValues(analysis: AnalysisInfo): ParamValues {
  const values: ParamValues = {};
  for (const spec of analysis.params) values[spec.name] = formatDefault(spec);
  return values;
}

function formatDefault(spec: ParamSpec): string | boolean {
  if (spec.type === 'bool') return !!spec.default;
  if (spec.default == null) return '';
  if (spec.type === 'int_list' || spec.type === 'number_list') {
    return (spec.default as any[]).join(', ');
  }
  if (spec.type === 'tip_list') {
    return (spec.default as any[])
      .map((t) => (typeof t === 'string' ? t : `${t.diameter}:${t.corner_radius}`))
      .join(', ');
  }
  return String(spec.default);
}

/** Parse the form's string state back into the JSON the backend expects. */
export function parseValues(analysis: AnalysisInfo, values: ParamValues): ParamValues {
  const out: ParamValues = {};
  for (const spec of analysis.params) {
    const raw = values[spec.name];
    if (spec.type === 'bool') { out[spec.name] = !!raw; continue; }
    const text = String(raw ?? '').trim();
    if (!text) continue; // let the backend default apply
    switch (spec.type) {
      case 'int': out[spec.name] = parseInt(text); break;
      case 'number': out[spec.name] = parseFloat(text); break;
      case 'int_list':
        out[spec.name] = text.split(/[\s,]+/).filter(Boolean).map((x) => parseInt(x));
        break;
      case 'number_list':
        out[spec.name] = text.split(/[\s,]+/).filter(Boolean).map((x) => parseFloat(x));
        break;
      case 'tip_list':
        out[spec.name] = text.split(/[\s,]+/).filter(Boolean); // "D:rc" strings
        break;
      default: out[spec.name] = text;
    }
  }
  return out;
}

export function ParamForm({ analysis, values, onChange }: {
  analysis: AnalysisInfo;
  values: ParamValues;
  onChange: (name: string, value: any) => void;
}) {
  return (
    <>
      {analysis.params.map((spec) => {
        const label = spec.label ?? spec.name;
        if (spec.type === 'bool') {
          return (
            <label key={spec.name} className="check">
              <input
                type="checkbox" checked={!!values[spec.name]}
                onChange={(e) => onChange(spec.name, e.target.checked)}
              />
              {label}
            </label>
          );
        }
        if (spec.type === 'select') {
          return (
            <div key={spec.name}>
              <label>{label}</label>
              <select
                value={values[spec.name] ?? ''}
                onChange={(e) => onChange(spec.name, e.target.value)}
              >
                {(spec.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
          );
        }
        const numeric = spec.type === 'int' || spec.type === 'number';
        return (
          <div key={spec.name}>
            <label>{label}{spec.unit ? ` (${spec.unit})` : ''}</label>
            <input
              type={numeric ? 'number' : 'text'}
              min={spec.min} max={spec.max}
              step={spec.type === 'int' ? 1 : 'any'}
              value={values[spec.name] ?? ''}
              placeholder={spec.type.endsWith('list') ? 'comma separated' : undefined}
              onChange={(e) => onChange(spec.name, e.target.value)}
            />
          </div>
        );
      })}
    </>
  );
}
