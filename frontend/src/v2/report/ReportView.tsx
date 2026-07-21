import clsx from 'clsx';
import { ArrowLeft, FileText } from 'lucide-react';
import { useEffect, useState } from 'react';
import { fetchReport } from '../../api/client';
import type { Report } from '../../api/types';
import { dispositionOf } from '../checks/evaluators';
import { StatusBadge, type StatusKind } from '../components/status';

const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';

const VERDICT_BADGE: Record<string, StatusKind> = {
  pass: 'good', review: 'warning', fail: 'serious', na: 'neutral',
  unknown: 'neutral',
};

const DISPOSITION_BADGE: Record<string, StatusKind> = {
  open: 'warning', accepted: 'good', customer_approval: 'warning',
  resolved: 'good',
};

/**
 * Read-only render of a published report bundle — what a customer sees.
 * Everything comes from the immutable bundle: verdicts, findings, the
 * dispositions AS THEY WERE at publish time, and the captured views.
 * Nothing here recomputes or reads live plan state.
 */
export function ReportView({ partId, rid, onBack }: {
  partId: string; rid: string; onBack: () => void;
}) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReport(null);
    setError(null);
    fetchReport(partId, rid)
      .then(setReport)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [partId, rid]);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-8">
        <button type="button" onClick={onBack}
          className="mb-4 flex items-center gap-1.5 text-sm/6 text-zinc-500 transition hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white">
          <ArrowLeft className="size-4" /> Back to the workspace
        </button>

        {error ? (
          <p className="text-sm/6 text-red-600 dark:text-red-500">⚠ {error}</p>
        ) : !report ? (
          <p className={hintCls}>Loading report…</p>
        ) : (
          <>
            <div className="mb-6 border-b border-zinc-950/5 pb-4 dark:border-white/10">
              <div className="flex items-center gap-2">
                <FileText className="size-5 text-blue-600 dark:text-blue-400" />
                <h1 className="text-lg/7 font-semibold text-zinc-950 dark:text-white">
                  {report.title}
                </h1>
              </div>
              <p className={clsx('mt-1', hintCls)}>
                {report.part} · plan revision {report.plan_revision} · published{' '}
                {new Date(report.published_at).toLocaleString()} · {report.rid}
              </p>
            </div>

            <div className="flex flex-col gap-6">
              {report.checks.map((check) => (
                <div key={check.id}
                  className="rounded-xl border border-zinc-950/5 p-4 dark:border-white/10">
                  <div className="flex items-center gap-2">
                    <h2 className="flex-1 text-sm/6 font-semibold text-zinc-950 dark:text-white">
                      {check.label}
                    </h2>
                    <StatusBadge status={VERDICT_BADGE[check.verdict] ?? 'neutral'}>
                      {check.verdict === 'na' ? 'data' : check.verdict}
                    </StatusBadge>
                  </div>

                  {check.shot && (
                    <img
                      src={`/api/parts/${encodeURIComponent(partId)}/reports/${encodeURIComponent(rid)}/shots/${check.shot}`}
                      alt={`${check.label} view`}
                      className="mt-3 w-full rounded-lg ring-1 ring-zinc-950/10 dark:ring-white/10"
                    />
                  )}

                  <div className="mt-3">
                    {check.findings.length === 0 ? (
                      <p className={hintCls}>No findings — within policy.</p>
                    ) : check.findings.map((finding) => {
                      const state = dispositionOf(
                        { id: finding.id } as any, report.dispositions);
                      const why = report.dispositions[finding.id]?.why;
                      return (
                        <div key={finding.id}
                          className="mt-1.5 rounded-lg bg-zinc-950/2.5 p-2.5 dark:bg-white/5">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs/5 font-medium text-zinc-950 dark:text-white">
                              {finding.label ?? finding.id}
                            </span>
                            <StatusBadge
                              status={DISPOSITION_BADGE[state] ?? 'neutral'}>
                              {state.replace('_', ' ')}
                            </StatusBadge>
                          </div>
                          {finding.detail && <p className={hintCls}>{finding.detail}</p>}
                          {why && (
                            <p className={clsx(hintCls, 'mt-0.5 italic')}>
                              “{why}” — {report.dispositions[finding.id]?.by}
                            </p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
