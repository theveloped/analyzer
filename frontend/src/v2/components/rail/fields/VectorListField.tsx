import { Plus, X } from 'lucide-react';
import { useState } from 'react';
import { Button } from '../../../../catalyst/button';
import { Input } from '../../../../catalyst/input';
import { hintCls } from '../../styles';

/**
 * The editor for a `vector_list` param: xyz rows in, a removable list out.
 *
 * This is the default widget for that type rather than a per-lens override,
 * because a comma-separated text box cannot express it — which is why the
 * codec used to fall through to `String(default)` and the v1 compute panel
 * silently submitted a raw string for `prep/directions`' `manual` and
 * `cnc/turning_scan`' `axis_vectors`.
 *
 * Lifted out of the directions rail, which had the only working version.
 */

const num = (v: string) => {
  const n = parseFloat(v);
  return isFinite(n) ? n : NaN;
};

export function VectorListField({ vectors, onChange, addLabel = 'Add axis' }: {
  vectors: number[][];
  onChange: (next: number[][]) => void;
  addLabel?: string;
}) {
  const [x, setX] = useState('0');
  const [y, setY] = useState('0');
  const [z, setZ] = useState('1');

  function add() {
    const v = [num(x), num(y), num(z)];
    // a zero vector is not a direction; silently dropping it beats adding an
    // arrow that points nowhere
    if (v.some((c) => !isFinite(c)) || v.every((c) => c === 0)) return;
    onChange([...vectors, v]);
  }

  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2">
        <Input type="number" step="0.1" aria-label="x" value={x}
          onChange={(e) => setX(e.target.value)} />
        <Input type="number" step="0.1" aria-label="y" value={y}
          onChange={(e) => setY(e.target.value)} />
        <Input type="number" step="0.1" aria-label="z" value={z}
          onChange={(e) => setZ(e.target.value)} />
        <Button outline onClick={add} aria-label={addLabel} title={addLabel}>
          <Plus data-slot="icon" />
        </Button>
      </div>
      {vectors.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {vectors.map((v, i) => (
            <li key={i} className="flex items-center gap-2 text-sm/5 text-zinc-700 dark:text-zinc-300">
              <span className="flex-1 tabular-nums">
                [{v.map((c) => (+c).toFixed(2)).join(', ')}]
              </span>
              <button
                type="button"
                aria-label="remove"
                className="text-zinc-400 hover:text-red-600"
                onClick={() => onChange(vectors.filter((_, j) => j !== i))}
              >
                <X className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {!vectors.length && <p className={`mt-1 ${hintCls}`}>None yet.</p>}
    </div>
  );
}
