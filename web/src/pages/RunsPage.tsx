import { useCallback, useEffect, useState } from "react";
import { API, api, fmtDate, load, MODEL_LABEL, type ApiRun, type PipelineRun, type Species } from "../data";

const STEP_NOTES: Record<string, string> = {
  harvest: "Records requested from the GBIF and OBIS APIs",
  "quality checks": "Coordinates, dates, duplicates and source flags checked in DuckDB",
  "predictors (Dask)": "Climate values attached to every record, one Dask partition per species",
  "species distribution models": "GLM and Random Forest fitted and cross-validated for each species",
  export: "Vector tiles, map overlays and data files written for this site",
};

// {"records_passed": 39510, "median_auc_rf": 0.68} -> "39,510 records passed, median AUC RF 0.68"
function outputText(o?: Record<string, unknown>) {
  if (!o) return "";
  return Object.entries(o)
    .filter(([, v]) => typeof v === "number")
    .map(([k, v]) => {
      const n = (v as number).toLocaleString("en-GB");
      const auc = k.match(/^median_auc_(\w+)$/);
      return auc ? `median AUC ${auc[1] === "rf" ? "Random Forest" : auc[1].toUpperCase()} ${n}` : `${n} ${k.replace(/_/g, " ")}`;
    })
    .join(", ");
}

function PipelineHistory() {
  const [runs, setRuns] = useState<PipelineRun[] | null>(null);
  useEffect(() => { load<PipelineRun[]>("runs.json").then((r) => setRuns([...r].reverse())).catch(() => setRuns([])); }, []);
  if (!runs) return <p className="loading">Loading the run log…</p>;
  if (!runs.length) return <p>No pipeline run has been recorded yet.</p>;
  return (
    <ol className="runs">
      {runs.map((run, i) => {
        const total = run.steps.reduce((t, s) => t + s.seconds, 0);
        return (
          <li key={run.id} className={`run ${run.status}`}>
            <details open={i === 0}>
              <summary>
                <span className={`status ${run.status}`}>{run.status === "succeeded" ? "Succeeded" : run.status === "failed" ? "Failed" : "Running"}</span>
                <span className="when">{fmtDate(run.started)}</span>
                <span className="how">{run.trigger === "schedule" ? "Weekly schedule" : run.trigger === "push" ? "New code pushed" : run.trigger === "workflow_dispatch" ? "Started by hand" : "Local run"}
                  {run.commit && <>, commit <code>{run.commit}</code></>}</span>
                <span className="took">{Math.round(total / 60)} min</span>
              </summary>
              <ol className="steps">
                {run.steps.map((s) => (
                  <li key={s.name} className={s.status}>
                    <div className="step-head">
                      <b>{s.name[0].toUpperCase() + s.name.slice(1)}</b>
                      <span>{s.seconds < 60 ? `${s.seconds} s` : `${(s.seconds / 60).toFixed(1)} min`}</span>
                    </div>
                    <p>{STEP_NOTES[s.name]}{outputText(s.output) && `: ${outputText(s.output)}`}</p>
                    {s.error && <p className="error">{s.error}</p>}
                    <span className="bar" style={{ width: `${Math.max(1, (s.seconds / total) * 100)}%` }} aria-hidden="true" />
                  </li>
                ))}
              </ol>
            </details>
          </li>
        );
      })}
    </ol>
  );
}

function LiveRuns() {
  const [species, setSpecies] = useState<Species[]>([]);
  const [runs, setRuns] = useState<ApiRun[]>([]);
  const [chosen, setChosen] = useState("tetrix-subulata");
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(() => api<ApiRun[]>("/api/runs").then(setRuns).catch((e) => setMessage(e.message)), []);
  useEffect(() => { load<Species[]>("species.json").then((s) => setSpecies(s.filter((x) => x.modelled))); refresh(); }, [refresh]);
  useEffect(() => {
    if (!runs.some((r) => r.status === "queued" || r.status === "running")) return;
    const t = window.setInterval(refresh, 2000);
    return () => window.clearInterval(t);
  }, [runs, refresh]);

  const start = async () => {
    setMessage(null);
    try {
      const run = await api<ApiRun>("/api/runs", { method: "POST", body: JSON.stringify({ species: chosen }) });
      setMessage(`Run ${run.id} queued.`);
      refresh();
    } catch (e) {
      setMessage((e as Error).message);
    }
  };

  return (
    <section className="live">
      <h2>Start a model run</h2>
      <p>Connected to the API at <code>{API}</code>. A run refits both models for one species with the current data.</p>
      <div className="start">
        <select value={chosen} onChange={(e) => setChosen(e.target.value)} aria-label="Species">
          {species.map((s) => <option key={s.slug} value={s.slug}>{s.name}</option>)}
        </select>
        <button type="button" className="primary" onClick={start}>Start run</button>
      </div>
      {message && <p className="notice" role="status">{message}</p>}
      <table className="table">
        <thead><tr><th>Run</th><th>Species</th><th>Status</th><th>Started</th><th>Result</th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td><em>{species.find((s) => s.slug === r.species)?.name ?? r.species}</em></td>
              <td><span className={`status ${r.status}`}>{r.status[0].toUpperCase() + r.status.slice(1)}</span></td>
              <td>{r.started ? fmtDate(r.started) : "waiting"}</td>
              <td>{r.result ? (["glm", "rf"] as const).map((m) => `${MODEL_LABEL[m]} AUC ${r.result!.metrics[m].auc.toFixed(2)}`).join(", ") : r.error ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function RunsPage() {
  return (
    <article className="page">
      <header className="page-head">
        <h1>Model runs</h1>
        <p>Every week a scheduled workflow harvests fresh records, checks them, refits all models and rebuilds this site.
          Each run is logged here step by step, with what it produced and how long it took.</p>
      </header>
      {API ? <LiveRuns /> : (
        <p className="aside">On this public copy runs start only on the schedule. With the API running (see the About page), you can start a run
          for one species from this page and follow it until it finishes.</p>
      )}
      <h2>Pipeline history</h2>
      <PipelineHistory />
    </article>
  );
}
