import { useEffect, useState } from "react";
import { fmt, fmtDate, load, type PipelineRun, type Species } from "../data";

export default function AboutPage() {
  const [species, setSpecies] = useState<Species[]>([]);
  const [last, setLast] = useState<PipelineRun | null>(null);
  useEffect(() => {
    load<Species[]>("species.json").then(setSpecies);
    load<PipelineRun[]>("runs.json").then((r) => setLast([...r].reverse().find((x) => x.status === "succeeded") ?? null)).catch(() => undefined);
  }, []);
  const records = species.reduce((n, s) => n + s.clean, 0);
  const aucs = species.filter((s) => s.metrics).map((s) => Math.max(s.metrics!.glm.auc, s.metrics!.rf.auc));
  const aucRange = aucs.length ? `between ${Math.min(...aucs).toFixed(2)} and ${Math.max(...aucs).toFixed(2)}` : "";

  return (
    <article className="page about">
      <header className="page-head">
        <h1>About Range Atlas</h1>
        <p>Range Atlas is my own project. It maps where ten European grasshoppers and crickets, and two marine species, have been
          recorded since 2000, and models where the climate suits them. Everything is built from open data and rebuilt every week.</p>
        <div className="link-row">
          <a className="button primary" href="#/">Open the map</a>
          <a className="button" href="https://github.com/sayamrina/range-atlas">Code on GitHub</a>
          <a className="button" href="https://www.linkedin.com/in/amrina-ali-487025119/" target="_blank" rel="noopener">LinkedIn</a>
          <a className="button" href="https://sayamrina.github.io/" target="_blank" rel="noopener">Portfolio</a>
        </div>
      </header>

      <div className="prose">
        <h2>What it is for</h2>
        <p>Biodiversity databases such as GBIF and OBIS hold billions of species records from museums, surveys and citizen
          scientists. Turning them into a map of where a species could live takes several careful steps: the records have to be
          checked, the bias in where people look has to be handled, and the model has to be tested on places it has not seen.
          Range Atlas runs those steps end to end and shows every intermediate result, so the map can be traced back to the
          records behind it.</p>

        <h2>How it works</h2>
        <ol className="steps">
          <li><b>Harvest.</b> Records are requested from the GBIF and OBIS APIs and stored as Darwin Core in a Parquet
            lakehouse, partitioned by source and species. GBIF is sampled country by country so that the best-recorded
            countries do not drown out the rest of Europe.</li>
          <li><b>Check.</b> DuckDB SQL removes records with missing or impossible coordinates, coordinates uncertain by more than
            10 km, dates before 2000, absences, source flags and duplicates. Every removal is counted on the Data quality page.</li>
          <li><b>Attach climate.</b> Dask reads the clean layer partition by partition and adds five WorldClim variables to every
            record. Records are then thinned to one per species and 10 arc-minute grid cell.</li>
          <li><b>Model.</b> A GLM with quadratic terms and a Random Forest compare each species with a target-group background:
            places where any grasshopper or cricket was recorded. Because those places share the same observer bias, the model
            learns climate rather than where people go looking. Scores come from spatial block cross-validation.</li>
          <li><b>Publish.</b> The same run writes vector tiles of the records, map overlays and a Parquet file you can query in the
            browser. A scheduled GitHub Actions workflow runs everything weekly and deploys this site.</li>
        </ol>

        <h2>The parts behind the static site</h2>
        <p>This public copy is static, but the repository also holds the services a team would run:</p>
        <ul>
          <li>A FastAPI service on PostgreSQL/PostGIS with species, GeoJSON queries by bounding box, vector tiles cut by
            PostGIS (<code>ST_AsMVT</code>) with an in-memory cache and ETags, and model runs you can start and follow.</li>
          <li>Two places a model run can execute: inside the API process, or as a container job on AWS Batch that reads and writes
            the lakehouse on S3. The AWS path is tested against moto, a local AWS mock; it has not been run on a real AWS account.</li>
          <li>Docker and docker compose for the stack, and CI that runs the tests (including PostGIS) on every push.</li>
        </ul>

        <h2>Limits</h2>
        <ul>
          <li>Each species uses a sample of about 3,000 recent records, not every record in GBIF or OBIS.</li>
          <li>Five climate variables at about 18 km resolution say nothing about land use, soil or habitat structure, which matter a
            lot for grasshoppers. The maps show where the climate fits, not where the species lives today.</li>
          <li>The best cross-validated AUC per species lies {aucRange}, which is modest. That is expected with a target-group background and
            spatial blocks: they are honest estimates, not inflated ones, and widespread species are hard to separate from the
            background by climate alone.</li>
          <li>Marine species are mapped from OBIS but not modelled, because the predictors describe land.</li>
        </ul>

        <h2>Sources</h2>
        <ul className="sources">
          <li>GBIF.org, occurrence search API, records published by {species.length ? "the datasets listed on the Data quality page" : "many datasets"}.</li>
          <li>OBIS, Ocean Biodiversity Information System, occurrence API (Intergovernmental Oceanographic Commission of UNESCO).</li>
          <li>Fick, S. E. and Hijmans, R. J. (2017). WorldClim 2: new 1-km spatial resolution climate surfaces for global land areas.
            International Journal of Climatology 37(12), 4302–4315.</li>
          <li>Base map: OpenFreeMap, © OpenMapTiles, data © OpenStreetMap contributors.</li>
        </ul>
        {last && <p className="aside">Last rebuild: {fmtDate(last.finished)}, {fmt.format(records)} records on the map.</p>}

        <h2>Who made it</h2>
        <p>Amrina (M.Sc. Educational Technology, Saarland University; B.Sc. Informatics). The design, data choices and analysis are my
          own; AI was used as a supporting tool for coding. This is an independent project and is not affiliated with GBIF, OBIS or
          any research institution.</p>
      </div>
    </article>
  );
}
