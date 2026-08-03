import type { FC } from 'react';
import type { ParamSpec } from '../../../api/types';
import { Input } from '../../../catalyst/input';
import { Select } from '../../../catalyst/select';
import { formatDefault } from '../../../params/codec';
import { RailBool, RailField } from './fields';

/**
 * Slot 3, generated. A settings section rendered from the same `ParamSpec`
 * shape the backend already publishes for every analysis — so a lens declares
 * WHICH knobs it reads (`ViewMode.params`) and gets the form for free, rather
 * than a per-process panel hand-written once and shown to every mode.
 *
 * Nine of the eleven ParamTypes render from the spec alone. The two list types
 * that need a real editor (`vector_list`, `group_list` — axes and face groups)
 * and anything whose options come from the manifest rather than the spec (the
 * CNC direction and tool-tip selects) arrive as `overrides`: the mode still
 * DECLARES the param, so the rail knows to show it and in what order, and the
 * plugin supplies the widget.
 */

export interface ParamWidgetProps {
  spec: ParamSpec;
  value: unknown;
  /** Every value in the same bag — a widget whose options depend on a sibling
   * (tool tip on the selected direction) needs more than its own. */
  values: Record<string, unknown>;
  onChange: (value: unknown) => void;
}

export type ParamWidget = FC<ParamWidgetProps>;

/**
 * Where the edited value is going, declared rather than inferred.
 *
 * `viewer` writes into `viewerParams`, whose readers are `parseFloat`-based
 * and where `''` means "auto" — and where at least one reader (`holder`) calls
 * `.trim()` and would throw on a number. So a viewer form keeps strings.
 * `compute` is a job payload: typed JSON, blank omitted so the backend's
 * declared default applies.
 */
export type ParamTarget = 'viewer' | 'compute';

export function ParamsForm({
  specs, values, onChange, overrides, target = 'viewer',
}: {
  specs: ParamSpec[];
  values: Record<string, unknown>;
  onChange: (name: string, value: unknown) => void;
  overrides?: Record<string, ParamWidget>;
  target?: ParamTarget;
}) {
  if (!specs.length) return null;
  return (
    <div className="flex flex-col gap-3">
      {specs.map((spec) => {
        const Override = overrides?.[spec.name];
        const value = values[spec.name] ?? formatDefault(spec);
        if (Override) {
          return (
            <RailField key={spec.name} label={spec.label ?? spec.name} unit={spec.unit}>
              <Override spec={spec} value={value} values={values}
                onChange={(v) => onChange(spec.name, v)} />
            </RailField>
          );
        }
        return (
          <ParamRow key={spec.name} spec={spec} value={value} target={target}
            onChange={(v) => onChange(spec.name, v)} />
        );
      })}
    </div>
  );
}

function ParamRow({ spec, value, target, onChange }: {
  spec: ParamSpec; value: unknown; target: ParamTarget;
  onChange: (v: unknown) => void;
}) {
  const label = spec.label ?? spec.name;

  if (spec.type === 'bool') {
    return (
      <RailBool label={label} hint={spec.unit} checked={!!value}
        onChange={onChange} />
    );
  }

  if (spec.type === 'select') {
    return (
      <RailField label={label} unit={spec.unit}>
        <Select value={String(value ?? '')} aria-label={label}
          onChange={(e) => onChange(e.target.value)}>
          {(spec.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
        </Select>
      </RailField>
    );
  }

  const numeric = spec.type === 'int' || spec.type === 'number';
  const multiline = spec.type === 'vector_list' || spec.type === 'group_list';
  return (
    <RailField
      label={label}
      unit={spec.unit}
      hint={multiline ? 'One per line.' : undefined}
    >
      <Input
        type={numeric ? 'number' : 'text'}
        // a blank numeric field is "auto" for a viewer param and "use the
        // declared default" for a compute one; both read as a placeholder
        placeholder={spec.default == null
          ? (target === 'viewer' ? 'auto' : 'default') : undefined}
        step={spec.type === 'int' ? 1 : 'any'}
        min={spec.min}
        max={spec.max}
        value={String(value ?? '')}
        aria-label={label}
        // the raw string either way: a viewer param is READ with parseFloat
        // and '' means auto, and a compute payload is parsed once at submit
        // by params/codec so a half-typed "1.5e" never reaches a job
        onChange={(e) => onChange(e.target.value)}
      />
    </RailField>
  );
}
