import { useState } from 'react';
import { postDisposition } from '../../api/client';
import { Button } from '../../catalyst/button';
import { useStore } from '../../state/store';
import { refreshManifest } from '../../viewer/controller';
import { dispositionOf, type Finding } from '../checks/evaluators';
import { StatusBadge } from '../components/status';
import { usePlanSection } from './hooks';

const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';

const DISPOSITION_BADGE = {
  open: 'neutral', accepted: 'good',
  customer_approval: 'warning', resolved: 'good',
} as const;

export function FindingRow({ finding, partId }: {
  finding: Finding; partId: string;
}) {
  const section = usePlanSection();
  const [note, setNote] = useState('');
  const state = dispositionOf(finding, section?.dispositions);
  const judge = (next: 'accepted' | 'open') => {
    void postDisposition(partId, {
      finding_id: finding.id, state: next, by: 'engineer', why: note,
    }).then(() => refreshManifest())
      .catch((err) => useStore.getState().set({ error: String(err) }));
  };
  return (
    <div className="rounded-lg border border-zinc-950/5 p-2 dark:border-white/10">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs/5 font-medium text-zinc-950 dark:text-white">
          {finding.label}
        </span>
        <StatusBadge status={DISPOSITION_BADGE[state]}>{state.replace('_', ' ')}</StatusBadge>
      </div>
      <p className={hintCls}>{finding.detail}</p>
      <div className="mt-1.5 flex items-center gap-1.5">
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="why…"
          className="w-full rounded-md bg-zinc-950/5 px-2 py-1 text-xs/5 text-zinc-950 outline-none placeholder:text-zinc-400 dark:bg-white/10 dark:text-white"
        />
        {state === 'open' ? (
          <Button plain onClick={() => judge('accepted')}>Accept</Button>
        ) : (
          <Button plain onClick={() => judge('open')}>Reopen</Button>
        )}
      </div>
    </div>
  );
}
