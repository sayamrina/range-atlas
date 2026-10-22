# Range Atlas

Where have European grasshoppers and crickets been recorded since 2000, and where does the climate suit them?
Range Atlas answers this from open biodiversity data: it harvests records from **GBIF** and **OBIS**, checks
them, attaches **WorldClim** climate values, fits two species distribution models per species and publishes
an interactive map. A scheduled workflow rebuilds everything every week.

**Live app:** https://sayamrina.github.io/range-atlas/

| Map | What you see |
|---|---|
| Records | Clean occurrence records as vector tiles; click a point for its year and basis of record |
| Climate suitability | GLM, Random Forest or their average, as a map overlay |
| Model card | Spatially cross-validated AUC and TSS, predictor importance, response curves |
| Model runs | Every pipeline run, step by step, with counts and durations |
| Data quality | Why records were removed, per check and per species; datasets and licences |
| Query the data | DuckDB running in the browser on the published Parquet file |

## How it works

```
GBIF API ─┐                       DuckDB SQL            Dask                 scikit-learn
OBIS API ─┴─► raw (Darwin Core) ──► clean ───────────► features ──────────► models ──► export
             Parquet lakehouse      quality checks     + WorldClim values   GLM + RF   tiles, overlays,
             (local or S3)          duplicates         thinned per cell     spatial CV  Parquet, JSON
                                                                                         │
                                         PostGIS ◄── api.db (load) ◄────────────────────┤
                                         FastAPI: species, GeoJSON, MVT tiles, runs       ▼
                                                                                    static web app
```

1. **Harvest** (`pipeline/harvest.py`). GBIF is sampled country by country with a quota that grows with the
   square root of each country's record count, so well-recorded countries do not drown out the rest of Europe
   (and deep result pages, which are slow, are never needed). OBIS is sampled per five-year period. Both are
   mapped to the same Darwin Core columns and written as Parquet, partitioned by source and species.
2. **Check** (`pipeline/clean.py`). DuckDB SQL flags missing, impossible or imprecise coordinates (> 10 km),
   dates outside 2000–2026, recorded absences, fossils, records flagged by the source, and duplicates (same
   species, place and day). Non-vocabulary `occurrenceStatus` values (some OBIS publishers put a WoRMS LSID there)
   are repaired and counted, not dropped.
3. **Attach climate** (`pipeline/features.py`). Dask maps over the clean partitions and samples five WorldClim 2.1
   bioclimatic variables at 10 arc-minutes, then thins to one record per species and grid cell.
4. **Model** (`pipeline/sdm.py`). A logistic GLM with quadratic terms and a Random Forest compare presences with a
   **target-group background** (cells where any Orthoptera were recorded), which shares the observers' bias.
   Evaluation uses **spatial block cross-validation** (5° blocks, five folds). Permutation importance and response
   curves are saved with each model.
5. **Export** (`pipeline/export.py`, `pipeline/tiles.py`). Static Mapbox Vector Tiles (z0–6), Web Mercator PNG
   overlays, a Parquet file for in-browser SQL, and JSON for the app.

## Services

- **API** (`api/`): FastAPI on PostgreSQL/PostGIS. `GET /api/species`, `GET /api/species/{slug}/occurrences?bbox=`
  (GeoJSON), `GET /tiles/{z}/{x}/{y}.pbf` (`ST_AsMVT`, in-memory LRU cache, ETag and `304`), `POST /api/runs` and
  `GET /api/runs/{id}` to start and follow a model run, `GET /api/species/{slug}/suitability.png`.
- **Run backends** (`api/runner.py`): `local` runs the model in a worker thread; `aws-batch` submits a container job
  that runs `python -m pipeline.worker <species>` against the lakehouse on S3 and is tracked with `DescribeJobs`.
  See `infra/aws/`. **The AWS path is tested against moto (a local AWS mock), not on a real AWS account.**
- **Lakehouse on S3:** set `RANGE_ATLAS_LAKE=s3://bucket/prefix`. Writes go through fsspec; DuckDB reads with its
  `httpfs` extension; `AWS_ENDPOINT_URL` points both at an S3-compatible store.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pipeline.run_all            # about 15 minutes: harvest, checks, models, export
cd web && npm ci && npm run dev                 # http://localhost:5173

# API with PostGIS (DATABASE_URL defaults to postgresql://range:range@localhost:5432/range_atlas)
.venv/bin/python -m api.db                      # load the clean layer into PostGIS
.venv/bin/uvicorn api.app:app --reload          # http://localhost:8000/docs
VITE_API_URL=http://localhost:8000 npm run dev  # the app then uses the API for tiles and model runs
```

`docker-compose.yml` starts PostGIS, an S3-compatible store and the API; the CI builds the image and validates
the compose file, but the stack has not been run with Docker outside CI.

## Tests

`pytest` runs 16 tests: sampling, Darwin Core mapping, each quality check, Dask feature extraction, spatial folds,
that the models recover a known climate signal from synthetic data, tile coordinates and round trips, overlay
projection, the export, the API against a real PostGIS (`TEST_DATABASE_URL`), the S3 lakehouse and the AWS Batch
runner against moto. CI runs them on every push with a PostGIS service container.

## Limits

- About 3,000 recent records per species are sampled, not the full GBIF or OBIS holdings.
- Five climate variables at about 18 km say nothing about land use, soil or vegetation structure. The maps show
  where the climate fits, not where a species lives today.
- The best cross-validated AUC per species is between about 0.56 and 0.78. That is modest, and expected with a
  target-group background and spatial blocks; widespread species are hard to separate from the background by
  climate alone.
- Marine species are mapped but not modelled: the predictors describe land.

## Data and credits

- GBIF.org occurrence search API; OBIS occurrence API. Records keep the licence set by their publishers
  (CC0, CC BY, CC BY-NC); the Data quality page lists datasets and licences.
- Fick, S. E. and Hijmans, R. J. (2017). WorldClim 2: new 1-km spatial resolution climate surfaces for global land
  areas. *International Journal of Climatology* 37(12), 4302–4315.
- Base map: OpenFreeMap, © OpenMapTiles, data © OpenStreetMap contributors.

This app was built by Amrina, with AI as a supporting tool for coding. It is an independent project, not
affiliated with GBIF, OBIS or any research institution.
