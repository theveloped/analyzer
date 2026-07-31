import type { Finding } from '../checks/evaluators';
import { StatusBadge } from '../components/status';
import { hintCls } from '../components/styles';

const SEVERITY_BADGE = { review: 'warning', fail: 'serious' } as const;

/** One derived finding. Read-only: a finding is derived from (result,
 * policy, scope) and never authored, and there is no disposition layer —
 * acknowledging a known issue comes back with the restricted check viewer. */
export function FindingRow({ finding }: { finding: Finding }) {
  return (
    <div className="rounded-lg border border-zinc-950/5 p-2 dark:border-white/10">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs/5 font-medium text-zinc-950 dark:text-white">
          {finding.label}
        </span>
        <StatusBadge status={SEVERITY_BADGE[finding.severity] ?? 'neutral'}>
          {finding.severity}
        </StatusBadge>
      </div>
      <p className={hintCls}>{finding.detail}</p>
    </div>
  );
}
