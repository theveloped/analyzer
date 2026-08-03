import clsx from 'clsx';
import { useEffect, useMemo, useState } from 'react';
import { Select } from '../../catalyst/select';
import {
  initialValues, parseValues, type ParamValues,
} from '../../params/codec';
import { useStore } from '../../state/store';
import { runAnalysisJob } from '../../viewer/jobs';
import {
  Rail, RailError, RailHeader, RailRunButton, RailSection,
} from '../components/rail';
import { ParamsForm } from '../components/rail/ParamsForm';
import { hintCls } from '../components/styles';
import { useV2 } from '../store';
import { useBusy } from './run';

/**
 * Run any analysis in the catalog with its declared params.
 *
 * A GLOBAL capability, not a per-lens one: it materializes whatever a lens or
 * a check needs, whichever surface you happen to be on. The v1 panel it
 * replaces was hosted inside every lens rail, so the same escape hatch was
 * rendered forty-one times — and was the last v2 consumer of the v1 markup.
 *
 * `target="compute"` is what makes the form emit a job payload rather than
 * viewer params: blank means "use the backend's declared default", so the
 * codec omits it rather than sending an empty string.
 */
export function ComputeRail() {
  const partId = useStore((s) => s.partId);
  const jobs = useStore((s) => s.jobs);
  const catalog = useStore((s) => s.catalog);
  const close = useV2((s) => s.setComputeRailOpen);
  const busy = useBusy();

  const choices = useMemo(() => catalog.flatMap((process) =>
    process.analyses.map((analysis) => ({
      key: `${process.id}/${analysis.id}`, process, analysis,
    }))), [catalog]);

  const [choiceKey, setChoiceKey] = useState('');
  const [values, setValues] = useState<ParamValues>({});
  const [error, setError] = useState<string | null>(null);
  const choice = choices.find((c) => c.key === choiceKey) ?? choices[0] ?? null;

  useEffect(() => {
    if (choice) setValues(initialValues(choice.analysis));
    setError(null);
  }, [choice?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    if (!partId || !choice) return;
    setError(null);
    try {
      await runAnalysisJob(partId, choice.process.id, choice.analysis.id,
        parseValues(choice.analysis, values));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const partJobs = jobs.filter((j) => j.part_id === partId).slice(0, 6);

  return (
    <Rail>
      <RailHeader
        title="Compute"
        blurb="Run any analysis directly — the escape hatch when a lens needs
          something that is not cached yet."
        onClose={() => close(false)}
        closeTitle="Close compute"
      />

      <RailSection title="Analysis">
        <Select
          value={choice?.key ?? ''}
          aria-label="Analysis"
          onChange={(e) => setChoiceKey(e.target.value)}
        >
          {choices.map((c) => (
            <option key={c.key} value={c.key}>
              {`${c.process.label} — ${c.analysis.label}`}
            </option>
          ))}
        </Select>
        {choice && (
          <p className={clsx('mt-1', hintCls)}>{choice.analysis.description}</p>
        )}
      </RailSection>

      {choice && choice.analysis.params.length > 0 && (
        <RailSection title="Parameters">
          <ParamsForm
            specs={choice.analysis.params}
            values={values}
            target="compute"
            onChange={(name, value) =>
              setValues((v) => ({ ...v, [name]: value }))}
          />
        </RailSection>
      )}

      <RailRunButton
        execution="not_run"
        busy={busy}
        onRun={() => void run()}
        disabled={!partId}
        runLabel="Run"
      />
      <RailError>{error}</RailError>

      {partJobs.length > 0 && (
        <RailSection title="Jobs">
          <div className="flex flex-col gap-1">
            {partJobs.map((j) => (
              <div key={j.id} className="rounded-lg bg-zinc-950/2.5 p-2 dark:bg-white/5">
                <div className="flex items-baseline justify-between gap-2 text-[11px]/5">
                  <span className="min-w-0 truncate font-mono text-zinc-700 dark:text-zinc-300">
                    {`#${j.id} ${j.process}/${j.analysis}`}
                  </span>
                  <span className={clsx('shrink-0 tabular-nums',
                    j.status === 'error'
                      ? 'text-red-600 dark:text-red-500'
                      : 'text-zinc-500 dark:text-zinc-400')}
                  >
                    {j.status === 'running'
                      ? `${Math.round(j.progress * 100)} %` : j.status}
                  </span>
                </div>
                {j.status === 'running' && j.message && (
                  <p className={hintCls}>{j.message}</p>
                )}
                <RailError>{j.error}</RailError>
              </div>
            ))}
          </div>
        </RailSection>
      )}
    </Rail>
  );
}
