import * as duckdb from "@duckdb/duckdb-wasm";
import { useEffect, useRef, useState } from "react";
import { dataUrl } from "../data";

const EXAMPLES: { title: string; sql: string }[] = [
  {
    title: "Records per species and source",
    sql: `SELECT species, source, count(*) AS records, min(year) AS first, max(year) AS last
FROM occurrences
GROUP BY ALL
ORDER BY records DESC;`,
  },
  {
    title: "How far north each species reaches",
    sql: `SELECT species, round(quantile_cont(lat, 0.95), 1) AS north_95pct, round(max(lat), 1) AS northernmost
FROM occurrences
GROUP BY species
ORDER BY north_95pct DESC;`,
  },
  {
    title: "Records per country for the Italian locust",
    sql: `SELECT countryCode AS country, count(*) AS records
FROM occurrences
WHERE species = 'calliptamus-italicus'
GROUP BY ALL
ORDER BY records DESC
LIMIT 15;`,
  },
  {
    title: "How records were made",
    sql: `SELECT basisOfRecord, count(*) AS records,
       round(100 * count(*) / sum(count(*)) OVER (), 1) AS percent
FROM occurrences
GROUP BY ALL
ORDER BY records DESC;`,
  },
];

type Result = { columns: string[]; rows: unknown[][]; ms: number };

async function startDuckDB() {
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const workerUrl = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }));
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), new Worker(workerUrl));
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);
  await db.registerFileURL("occurrences.parquet", dataUrl("lake/occurrences.parquet"), duckdb.DuckDBDataProtocol.HTTP, false);
  const conn = await db.connect();
  await conn.query("CREATE VIEW occurrences AS SELECT * FROM read_parquet('occurrences.parquet')");
  return conn;
}

function show(v: unknown) {
  if (v === null || v === undefined) return "";
  if (typeof v === "bigint") return v.toLocaleString("en-GB");
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-GB") : String(Math.round(v * 1000) / 1000);
  return String(v);
}

export default function QueryPage() {
  const conn = useRef<duckdb.AsyncDuckDBConnection | null>(null);
  const [state, setState] = useState<"starting" | "ready" | "running" | "failed">("starting");
  const [sql, setSql] = useState(EXAMPLES[0].sql);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (text = sql) => {
    if (!conn.current) return;
    setState("running");
    setError(null);
    const t = performance.now();
    try {
      const table = await conn.current.query(text);
      const columns = table.schema.fields.map((f) => f.name);
      const rows = table.toArray().slice(0, 500).map((r) => columns.map((c) => (r as Record<string, unknown>)[c]));
      setResult({ columns, rows, ms: Math.round(performance.now() - t) });
    } catch (e) {
      setError((e as Error).message.replace(/^.*?Error: /, ""));
      setResult(null);
    }
    setState("ready");
  };

  useEffect(() => {
    let cancelled = false;
    startDuckDB()
      .then((c) => { if (!cancelled) { conn.current = c; setState("ready"); } })
      .catch((e) => { setState("failed"); setError(String(e)); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { if (state === "ready" && !result && !error) run(); }, [state]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <article className="page">
      <header className="page-head">
        <h1>Query the data</h1>
        <p>The clean records are published as one Parquet file. DuckDB runs inside your browser and reads it directly, so you can
          ask your own questions in SQL without a server. The table is called <code>occurrences</code>.</p>
      </header>
      <div className="examples" role="group" aria-label="Example queries">
        {EXAMPLES.map((ex) => (
          <button type="button" key={ex.title} onClick={() => { setSql(ex.sql); run(ex.sql); }} disabled={state !== "ready"}>
            {ex.title}
          </button>
        ))}
      </div>
      <label className="sql-label" htmlFor="sql">SQL</label>
      <textarea id="sql" className="sql" value={sql} onChange={(e) => setSql(e.target.value)} spellCheck={false}
        onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") run(); }} />
      <div className="run-row">
        <button type="button" className="primary" onClick={() => run()} disabled={state !== "ready"}>
          {state === "starting" ? "Starting DuckDB…" : state === "running" ? "Running…" : "Run query"}
        </button>
        <span className="hint">Ctrl + Enter also runs it.{result && ` ${result.rows.length} rows in ${result.ms} ms.`}</span>
      </div>
      {error && <p className="error-box" role="alert">{state === "failed" ? `DuckDB did not start: ${error}` : error}</p>}
      {result && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr>{result.columns.map((c, j) => {
              const v = result.rows[0]?.[j];
              return <th key={c} scope="col" className={typeof v === "number" || typeof v === "bigint" ? "num" : undefined}>{c}</th>;
            })}</tr></thead>
            <tbody>
              {result.rows.map((r, i) => (
                <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === "number" || typeof v === "bigint" ? "num" : undefined}>{show(v)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="aside">Columns: species, source, occurrenceID, scientificName, basisOfRecord, year, eventDate, countryCode, lat, lon,
        coordinateUncertaintyInMeters, datasetKey, datasetName, license. <a href={dataUrl("lake/occurrences.parquet")} download>Download the Parquet file</a>.</p>
    </article>
  );
}
