import { useEffect, useState } from 'react';
import { postPlanImpact } from '../../api/client';
import type { Plan } from '../../api/types';
import { Button } from '../../catalyst/button';
import {
  Dialog, DialogActions, DialogBody, DialogDescription, DialogTitle,
} from '../../catalyst/dialog';
import { useStore } from '../../state/store';
import { describeCheck } from '../checks/catalog';
import { StatusBadge } from '../components/status';
import { applyPlanEdit, usePlanSection } from './hooks';

/** One staged plan edit awaiting confirmation. */
export interface PendingEdit {
  title: string;
  /** Patch in the impact endpoint's shape (decisions merge, lists replace). */
  patch: Partial<Plan>;
}

const OUTCOME_BADGE = {
  unchanged: 'good', revalidates: 'good', recomputes: 'warning',
  error: 'critical',
} as const;

const OUTCOME_HINT: Record<string, string> = {
  unchanged: 'same result — nothing to do',
  revalidates: 'an earlier result already covers this — free',
  recomputes: 'needs a re-run',
  error: 'check configuration breaks',
};

/**
 * Impact preview before applying a plan edit: the server re-keys every check
 * under the patched plan (pure hash arithmetic, no jobs) and reports what
 * would recompute — so changing a decision never silently un-finishes work.
 */
export function ImpactModal({ edit, onClose }: {
  edit: PendingEdit; onClose: () => void;
}) {
  const partId = useStore((s) => s.partId);
  const section = usePlanSection();
  const [rows, setRows] = useState<
    { id: string; label: string; outcome: string }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    if (!partId) return;
    postPlanImpact(partId, edit.patch as Record<string, any>)
      .then((report) => setRows(Object.entries(report).map(([id, r]) => ({
        id,
        label: (() => {
          const plan = section?.plan;
          const check = (edit.patch.checks ?? plan?.checks)
            ?.find((c) => c.id === id);
          return (check && plan && describeCheck(check, plan)?.label) || id;
        })(),
        outcome: r.outcome,
      }))))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [partId, edit, section]);

  const apply = () => {
    setApplying(true);
    void applyPlanEdit(edit.patch)
      .then(onClose)
      .finally(() => setApplying(false));
  };

  return (
    <Dialog open onClose={onClose} size="md">
      <DialogTitle>{edit.title}</DialogTitle>
      <DialogDescription>
        Impact on the plan's checks before anything changes:
      </DialogDescription>
      <DialogBody>
        {error ? (
          <p className="text-sm/6 text-red-600 dark:text-red-500">⚠ {error}</p>
        ) : !rows ? (
          <p className="text-sm/6 text-zinc-500">Computing impact…</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {rows.map((row) => (
              <div key={row.id} className="flex items-center justify-between gap-2">
                <span className="text-sm/6 text-zinc-950 dark:text-white">{row.label}</span>
                <StatusBadge
                  status={OUTCOME_BADGE[row.outcome as keyof typeof OUTCOME_BADGE] ?? 'neutral'}
                >
                  {row.outcome}
                </StatusBadge>
              </div>
            ))}
            <p className="mt-1 text-xs/5 text-zinc-500 dark:text-zinc-400">
              {rows.some((r) => r.outcome === 'recomputes')
                ? OUTCOME_HINT.recomputes + ' for the flagged checks — results '
                  + 'land under new hashes; nothing is overwritten.'
                : 'No recomputation needed — the study already covers this.'}
            </p>
          </div>
        )}
      </DialogBody>
      <DialogActions>
        <Button plain onClick={onClose}>Cancel</Button>
        <Button onClick={apply} disabled={!rows || applying}>
          {applying ? 'Applying…' : 'Apply'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
