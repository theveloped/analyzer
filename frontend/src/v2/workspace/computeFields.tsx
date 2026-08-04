import clsx from 'clsx';
import { Input } from '../../catalyst/input';
import type { ComputeField } from '../analyses';
import { useV2 } from '../store';
import { RailBool } from '../components/rail';
import { hintCls, labelCls } from '../components/styles';


/** One compute-time knob bound to the v2 store's per-analysis payload
 * (keyed by an arbitrary id — the catalog analysis id or a lens key). */
export function ComputeInput({ computeId, field }: {
  computeId: string; field: ComputeField;
}) {
  const value = useV2((s) => s.compute[computeId]?.[field.key] ?? field.default);
  const setCompute = useV2((s) => s.setCompute);
  if (field.type === 'bool') {
    return (
      <RailBool
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
