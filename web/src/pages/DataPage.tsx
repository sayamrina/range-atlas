import { useEffect, useMemo, useState } from "react";
import { fmt, load, type Dataset, type Quality, type Species } from "../data";

const CHECKS: Record<string, string> = {
  duplicate: "Duplicate: same species, place and day as another record",
  outside_study_area: "Outside the study area",
  zero_or_equal_coordinates: "Coordinates at 0,0 or latitude equal to longitude",
  flagged_by_source: "Flagged by GBIF or OBIS (e.g. coordinates do not match the country)",
  imprecise_coordinates: "Coordinates uncertain by more than 10 km",
  outside_time_window: "Recorded before 2000 or with no year",
  recorded_absence: "Recorded as absent",
  fossil_or_material: "Fossil or literature citation",
  missing_coordinates: "No coordinates",
};

export default function DataPage() {
  const [quality, setQuality] = useState<Quality[]>([]);
  const [species, setSpecies] = useState<Species[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [licences, setLicences] = useState<Record<string, number>>({});
  useEffect(() => {
    load<Quality[]>("quality.json").then(setQuality);
    load<Species[]>("species.json").then(setSpecies);
    load<Dataset[]>("datasets.json").then(setDatasets);
    load<Record<string, number>>("licences.json").then(setLicences);
  }, []);

  const name = (slug: string) => species.find((s) => s.slug === slug);
  const totals = useMemo(() => {
    const t: Record<string, number> = {};
    for (const q of quality) for (const [k, v] of Object.entries(q.failed)) t[k] = (t[k] ?? 0) + v;
    return Object.entries(t).sort((a, b) => b[1] - a[1]);
  }, [quality]);
  const repaired = quality.reduce((n, q) => n + (q.repaired?.occurrenceStatus ?? 0), 0);
  const harvested = quality.reduce((n, q) => n + q.harvested, 0);
  const passed = quality.reduce((n, q) => n + q.passed, 0);
  const byLicence = Object.entries(licences).sort((a, b) => b[1] - a[1]);
  // species first, alphabetically; the background sample last
  const rows = [...quality].sort((a, b) =>
    (a.species === "orthoptera-background" ? 1 : 0) - (b.species === "orthoptera-background" ? 1 : 0) || a.species.localeCompare(b.species));

  return (
    <article className="page">
      <header className="page-head">
        <h1>Data quality</h1>
        <p>Before anything is mapped or modelled, every record goes through the same checks. Of {fmt.format(harvested)} harvested
          records, {fmt.format(passed)} passed. The background sample of other grasshoppers and crickets is checked the same way.</p>
      </header>

      <h2>Why records were set aside</h2>
      <table className="table">
        <thead><tr><th scope="col">Check</th><th scope="col" className="num">Records</th></tr></thead>
        <tbody>
          {totals.map(([k, v]) => <tr key={k}><td>{CHECKS[k] ?? k}</td><td className="num">{fmt.format(v)}</td></tr>)}
        </tbody>
      </table>
      {repaired > 0 && (
        <p className="aside">{fmt.format(repaired)} OBIS records carried a taxon identifier where the occurrence status belongs.
          Both APIs were asked for presences only, so these were kept as presences and counted here instead of being dropped.</p>
      )}

      <h2>By species</h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th scope="col">Species</th><th scope="col">Source</th><th scope="col" className="num">Harvested</th>
              <th scope="col" className="num">Passed</th><th scope="col">Main reason for removal</th></tr>
          </thead>
          <tbody>
            {rows.map((q) => {
              const top = Object.entries(q.failed).sort((a, b) => b[1] - a[1])[0];
              const sp = name(q.species);
              return (
                <tr key={q.species}>
                  <td>{sp ? <em>{sp.name}</em> : "Background: other Orthoptera"}</td>
                  <td>{q.source.toUpperCase()}</td>
                  <td className="num">{fmt.format(q.harvested)}</td>
                  <td className="num">{fmt.format(q.passed)}</td>
                  <td>{top ? `${(CHECKS[top[0]] ?? top[0]).split(":")[0]} (${fmt.format(top[1])})` : "none"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h2>Where the records come from</h2>
      <p>The published records come from {fmt.format(datasets.length)} datasets. Each keeps its own licence; most of the data is
        free to reuse with attribution.</p>
      <ul className="licences">
        {byLicence.map(([l, n]) => <li key={l}><b>{l}</b> {fmt.format(n)} records</li>)}
      </ul>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th scope="col">Dataset</th><th scope="col">Source</th><th scope="col">Licences</th><th scope="col" className="num">Records</th></tr></thead>
          <tbody>
            {datasets.slice(0, 25).map((d, i) => (
              <tr key={`${d.source}-${d.datasetKey}-${i}`}>
                <td>{d.datasetKey
                  ? <a href={d.source === "gbif" ? `https://www.gbif.org/dataset/${d.datasetKey}` : `https://obis.org/dataset/${d.datasetKey}`}
                       target="_blank" rel="noopener">{d.datasetName || d.datasetKey}</a>
                  : d.datasetName || "Unknown dataset"}</td>
                <td>{d.source.toUpperCase()}</td>
                <td>{d.licences.join(", ")}</td>
                <td className="num">{fmt.format(d.records)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="aside">The 25 largest datasets are listed; the full list is in the data you can query on the next page.</p>
    </article>
  );
}
