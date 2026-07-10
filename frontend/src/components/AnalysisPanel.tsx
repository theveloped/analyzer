// Compute panel: pick any analysis from the backend catalog, fill the
// auto-generated form, run it as a job and watch progress. Finished jobs
// refresh the manifest so new fields appear in the view selectors.

import { useEffect, useMemo, useState } from 'react';
import { fetchJob, submitJob } from '../api/client';
import type { AnalysisInfo, Job } from '../api/types';
import { useStore } from '../state/store';
import { refreshManifest, refreshParts, schedulePaint } from '../viewer/controller';
import { initialValues, ParamForm, parseValues } from './ParamForm';

function useAnalysisChoices() {
  const catalog = useStore((s) => s.catalog);
  return useMemo(() => catalog.flatMap((process) =>
    process.analyses.map((analysis) => ({
      key: `${process.id}/${analysis.id}`,
      process,
      analysis,
    }))), [catalog]);
}

export function AnalysisPanel() {
  const partId = useStore((s) => s.partId);
  const jobs = useStore((s) => s.jobs);
  const choices = useAnalysisChoices();
  const [choiceKey, setChoiceKey] = useState('');
  const [values, setValues] = useState<Record<string, any>>({});
  const [error, setError] = useState<string | null>(null);

  const choice = choices.find((c) => c.key === choiceKey) ?? choices[0] ?? null;

  useEffect(() => {
    if (choice) setValues(initialValues(choice.analysis));
    setError(null);
  }, [choice?.key]);

  async function run() {
    if (!partId || !choice) return;
    setError(null);
    try {
      const job = await submitJob(
        partId, choice.process.id, choice.analysis.id,
        parseValues(choice.analysis, values));
      useStore.getState().set({ jobs: [job, ...useStore.getState().jobs] });
      void watch(job);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function watch(job: Job) {
    let current = job;
    while (current.status === 'queued' || current.status === 'running') {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      current = await fetchJob(job.id);
      useStore.getState().set({
        jobs: useStore.getState().jobs.map((j) => (j.id === current.id ? current : j)),
      });
    }
    if (current.status === 'done') {
      await refreshParts();
      await refreshManifest();
      schedulePaint(true);
    }
  }

  const partJobs = jobs.filter((j) => j.part_id === partId).slice(0, 6);

  return (
    <details className="compute" open>
      <summary>Compute</summary>

      <label>Analysis</label>
      <select value={choice?.key ?? ''} onChange={(e) => setChoiceKey(e.target.value)}>
        {choices.map((c) => (
          <option key={c.key} value={c.key}>
            {`${c.process.label} — ${c.analysis.label}`}
          </option>
        ))}
      </select>
      {choice && <div className="hint">{choice.analysis.description}</div>}

      {choice && (
        <ParamForm
          analysis={choice.analysis as AnalysisInfo}
          values={values}
          onChange={(name, value) => setValues((v) => ({ ...v, [name]: value }))}
        />
      )}

      <button type="button" className="run" disabled={!partId} onClick={() => void run()}>
        Run
      </button>
      {error && <div className="hint error">⚠ {error}</div>}

      {partJobs.length > 0 && (
        <div className="jobs">
          {partJobs.map((j) => (
            <div key={j.id} className={`job ${j.status}`}>
              <span>{`#${j.id} ${j.process}/${j.analysis}`}</span>
              <span>
                {j.status === 'running'
                  ? `${Math.round(j.progress * 100)}% ${j.message}`
                  : j.status}
              </span>
              {j.error && <div className="hint error">{j.error}</div>}
            </div>
          ))}
        </div>
      )}
    </details>
  );
}
