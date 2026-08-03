// The one string<->JSON codec for declared params.
//
// A form edits strings; the backend wants typed JSON, and the viewer wants
// whatever its readers already parse. Both sides of that translation live
// here so the v1 compute panel and the v2 rails cannot disagree about what
// "2, 4" or a blank field means.
//
// Blank is THREE different things depending on where a value is going, which
// is why `parseValues` omits rather than guessing:
//   - omitted from a job payload  -> the backend's declared default applies
//   - '' in viewerParams          -> "auto" (a heatmap keeps the data max)
//   - null in a compute payload   -> an explicit "no override"
// Collapsing them is the bug this file exists to prevent.

import type { AnalysisInfo, ParamSpec } from '../api/types';

export type ParamValues = Record<string, any>;

export function initialValues(analysis: AnalysisInfo): ParamValues {
  const values: ParamValues = {};
  for (const spec of analysis.params) values[spec.name] = formatDefault(spec);
  return values;
}

/** A spec's default as the string a form field shows. */
export function formatDefault(spec: ParamSpec): string | boolean {
  if (spec.type === 'bool') return !!spec.default;
  if (spec.default == null) return '';
  if (spec.type === 'int_list' || spec.type === 'number_list') {
    return (spec.default as any[]).join(', ');
  }
  if (spec.type === 'vector_list') {
    // one vector per line, "x y z" — the shape the directions picker edits
    return (spec.default as any[])
      .map((v) => (Array.isArray(v) ? v.join(' ') : String(v)))
      .join('\n');
  }
  if (spec.type === 'group_list') {
    // face-id groups, one group per line
    return (spec.default as any[])
      .map((g) => (Array.isArray(g) ? g.join(', ') : String(g)))
      .join('\n');
  }
  if (spec.type === 'tip_list') {
    return (spec.default as any[])
      .map((t) => (typeof t === 'string' ? t : `${t.diameter}:${t.corner_radius}`))
      .join(', ');
  }
  if (spec.type === 'tool_list') {
    return (spec.default as any[])
      .map((t) => (typeof t === 'string' ? t
        : [t.diameter, t.corner_radius ?? 0, t.stickout ?? '', t.holder_radius ?? '']
          .join(':').replace(/:+$/, '')))
      .join(', ');
  }
  return String(spec.default);
}

/** One field's string back to the JSON the backend expects, or `undefined`
 * when it is blank and the declared default should apply. */
export function parseValue(spec: ParamSpec, raw: unknown): any {
  if (spec.type === 'bool') return !!raw;
  const text = String(raw ?? '').trim();
  if (!text) return undefined;
  switch (spec.type) {
    case 'int': return parseInt(text);
    case 'number': return parseFloat(text);
    case 'int_list':
      return text.split(/[\s,]+/).filter(Boolean).map((x) => parseInt(x));
    case 'number_list':
      return text.split(/[\s,]+/).filter(Boolean).map((x) => parseFloat(x));
    case 'vector_list':
      // one vector per line; "x y z" or "x,y,z"
      return text.split(/\n+/).map((line) => line.trim()).filter(Boolean)
        .map((line) => line.split(/[\s,]+/).filter(Boolean).map(Number));
    case 'group_list':
      return text.split(/\n+/).map((line) => line.trim()).filter(Boolean)
        .map((line) => line.split(/[\s,]+/).filter(Boolean).map(Number));
    case 'tip_list':
    case 'tool_list':
      // "D:rc" / "D:rc:stickout:holder" strings, parsed by the backend
      return text.split(/[\s,]+/).filter(Boolean);
    default: return text;
  }
}

/** Parse a whole form's state back into the JSON the backend expects. */
export function parseValues(analysis: AnalysisInfo, values: ParamValues): ParamValues {
  const out: ParamValues = {};
  for (const spec of analysis.params) {
    const parsed = parseValue(spec, values[spec.name]);
    if (parsed !== undefined) out[spec.name] = parsed;
  }
  return out;
}
