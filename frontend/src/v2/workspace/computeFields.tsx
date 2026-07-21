import clsx from 'clsx';
import { Input } from '../../catalyst/input';
import { Switch } from '../../catalyst/switch';
import type { ComputeField } from '../analyses';
import { useV2 } from '../store';

const labelCls = 'text-sm/6 font-medium text-zinc-950 dark:text-white';
const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';

export function BoolRow({ label, hint, checked, onChange }: {
  label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div className={labelCls}>{label}</div>
        {hint && <p className={hintCls}>{hint}</p>}
      </div>
      <Switch checked={checked} onChange={onChange} aria-label={label} />
    </div>
  );
}

/** One compute-time knob bound to the v2 store's per-analysis payload
 * (keyed by an arbitrary id — the catalog analysis id or a lens key). */
export function ComputeInput({ computeId, field }: {
  computeId: string; field: ComputeField;
}) {
  const value = useV2((s) => s.compute[computeId]?.[field.key] ?? field.default);
  const setCompute = useV2((s) => s.setCompute);
  if (field.type === 'bool') {
    return (
      <BoolRow
        label={field.label}
        hint={field.hint}
        checked={value === true}
        onChange={(v) => setCompute(computeId, field.key, v)}
      />
    );
  }
  return (
    <div>
      <label className={labelCls}>{field.label}{field.unit ? ` (${field.unit})` : ''}</label>
      <div className="mt-2">
        <Input
          type="number"
          step="0.1"
          placeholder={field.placeholder}
          value={value == null ? '' : String(value)}
          onChange={(e) => {
            const raw = e.target.value;
            setCompute(computeId, field.key, raw === '' ? null : Number(raw));
          }}
        />
      </div>
      {field.hint && <p className={clsx('mt-1', hintCls)}>{field.hint}</p>}
    </div>
  );
}
