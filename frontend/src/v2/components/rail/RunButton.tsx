import { Play, RotateCw } from 'lucide-react';
import { Button } from '../../../catalyst/button';
import type { ExecutionState } from '../../checks/status';

/**
 * Slot 5. The one primary verb.
 *
 * Four rails wrote the same busy / current / else ternary, and two of them
 * passed a raw Lucide component without `data-slot="icon"` — which is what
 * Catalyst keys its 16 px sizing and icon tint off, so those buttons rendered a
 * differently-sized icon. Fixed here once.
 *
 * The label follows EXECUTION, not the verdict: a check that has run and failed
 * still offers "Re-run", never "Run".
 */
export function RailRunButton({
  execution, busy, onRun, disabled, runLabel = 'Run',
  rerunLabel = 'Re-run', busyLabel = 'Running…',
}: {
  execution: ExecutionState;
  busy: boolean;
  onRun: () => void;
  disabled?: boolean;
  runLabel?: string;
  rerunLabel?: string;
  busyLabel?: string;
}) {
  const ran = execution === 'current' || execution === 'stale';
  return (
    <Button onClick={onRun} disabled={disabled || busy} className="w-full">
      {busy ? (
        <>
          <RotateCw data-slot="icon" className="animate-spin" />
          {busyLabel}
        </>
      ) : ran ? (
        <><RotateCw data-slot="icon" /> {rerunLabel}</>
      ) : (
        <><Play data-slot="icon" /> {runLabel}</>
      )}
    </Button>
  );
}
