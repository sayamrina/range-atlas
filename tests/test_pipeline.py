import json

import mapbox_vector_tile
import numpy as np
import pandas as pd

from pipeline import clean, config, features, harvest, lake, sdm, tiles

from .conftest import record


def fast_models() -> dict:
    return {"glm": sdm.make_pipeline(sdm.StandardScaler(), sdm.FunctionTransformer(sdm.add_squares),
                                     sdm.LogisticRegression(max_iter=2000)),
            "rf": sdm.RandomForestClassifier(n_estimators=40, min_samples_leaf=5, random_state=0)}


def test_spread_offsets_cover_the_reachable_range():
    offsets = harvest.spread_offsets(total=10_000, wanted=1200, page=300, cap=3000)
    assert offsets[0] == 0 and offsets[-1] == 2700 and len(offsets) == 4
    assert harvest.spread_offsets(total=50, wanted=1000, page=300, cap=3000) == [0]


def test_country_quotas_damp_the_best_recorded_countries():
    q = harvest.country_quotas({"GB": 40_000, "PL": 400, "AL": 3}, wanted=1000)
    assert q["GB"] > q["PL"] > q["AL"] >= 1
    assert q["GB"] < 0.95 * 1000  # proportional to counts GB would get 99 %
    assert q["AL"] == 3  # never more than a country holds


def test_gbif_and_obis_records_map_to_the_same_darwin_core_columns():
    g = harvest.gbif_row({"key": 7, "species": "Tetrix subulata", "decimalLatitude": 50, "decimalLongitude": 8,
                          "issues": ["COORDINATE_ROUNDED"], "year": 2020}, "presence")
    o = harvest.obis_row({"id": "abc", "scientificName": "Carcinus maenas", "decimalLatitude": 54,
                          "decimalLongitude": 10, "flags": ["no_depth"], "date_year": 2019})
    assert set(g) == set(o) == set(config.DWC_COLUMNS)
    assert g["occurrenceID"] == "gbif:7" and g["issues"] == "COORDINATE_ROUNDED"
    assert o["occurrenceID"] == "obis:abc" and o["year"] == 2019


def test_quality_checks_flag_bad_records_and_repair_status(lake_dir):
    rows = [
        record(1, "x", 8.0, 50.0),
        record(2, "x", 8.0, 50.0),                                    # same place and day as 1
        record(3, "x", 0.0, 0.0),                                     # zero coordinates (and outside the area)
        record(4, "x", 8.5, 50.5, coordinateUncertaintyInMeters=50_000.0),
        record(5, "x", 9.0, 51.0, issues="COUNTRY_COORDINATE_MISMATCH"),
        record(6, "x", 9.5, 51.5, year=1990),
        record(7, "x", 10.0, 52.0, occurrenceStatus="absent"),
        record(8, "x", 10.5, 52.5, occurrenceStatus="urn:lsid:marinespecies.org:taxname:107381"),
    ]
    rows[1]["eventDate"] = rows[0]["eventDate"]
    lake.write_partition(harvest.normalise(pd.DataFrame(rows)), "raw", "gbif", "x")
    out = clean.run()
    report = lake.read_json("clean/_quality.json")[0]
    assert out == {"records_raw": 8, "records_clean": 2}
    assert report["failed"] == {"duplicate": 1, "outside_study_area": 1, "imprecise_coordinates": 1,
                                "flagged_by_source": 1, "outside_time_window": 1, "recorded_absence": 1}
    assert report["repaired"] == {"occurrenceStatus": 1}


def test_features_thin_to_one_record_per_cell(raw_lake, grid):
    clean.run()
    out = features.run(scheduler="sync")
    df = lake.connect().sql(f"SELECT * FROM read_parquet('{lake.glob('features')}', hive_partitioning = true)").df()
    assert out["cells_after_thinning"] == len(df)
    assert not df.duplicated(["species", "cell"]).any()
    assert df[list(config.PREDICTORS)].notna().all().all()


def test_spatial_folds_keep_blocks_together():
    rng = np.random.default_rng(0)
    lon, lat = rng.uniform(-10, 40, 2000), rng.uniform(35, 70, 2000)
    folds = sdm.spatial_folds(lon, lat)
    blocks = pd.DataFrame({"b": np.floor(lon / 5) * 1000 + np.floor(lat / 5), "f": folds})
    assert (blocks.groupby("b").f.nunique() == 1).all()
    assert set(folds) == set(range(sdm.FOLDS))


def test_models_learn_a_climate_signal(raw_lake, grid, monkeypatch):
    monkeypatch.setattr(sdm, "models", fast_models)
    clean.run()
    features.run(scheduler="sync")
    result = sdm.run_species("calliptamus-italicus")
    assert result["metrics"]["glm"]["auc"] > 0.75
    assert result["importance"]["glm"]["bio1"] == max(result["importance"]["glm"].values())
    with lake.fs_for(lake.uri("models")).open(lake.uri("models", "species=calliptamus-italicus", "suitability.npz"), "rb") as f:
        maps = np.load(f)
        south, north = np.nanmean(maps["ensemble"][-5:]), np.nanmean(maps["ensemble"][:8])
    assert south > north


def test_tile_coordinates_and_round_trip(tmp_path):
    tx, ty, px, py = tiles.tile_coords(np.array([8.68]), np.array([50.11]), 6)
    assert (tx[0], ty[0]) == (33, 21)
    pts = pd.DataFrame({"lon": [8.68, 8.69, 2.35], "lat": [50.11, 50.12, 48.85], "species": [1, 1, 2],
                        "year": [2020, 2021, 2019], "basis": ["HUMAN_OBSERVATION"] * 3})
    n = tiles.write_tiles(pts, tmp_path)
    assert n >= tiles.MAX_ZOOM + 1
    z0 = mapbox_vector_tile.decode((tmp_path / "0" / "0" / "0.pbf").read_bytes())["occurrences"]["features"]
    assert sum(f["properties"]["n"] for f in z0) == 3   # merged at low zoom, but no record lost


def test_overlay_rows_are_spaced_in_web_mercator(grid):
    arr = np.repeat(np.arange(grid.shape[0], dtype="float32")[:, None], grid.shape[1], axis=1)
    merc = tiles.to_mercator_rows(arr, grid, height=200)
    assert merc[0, 0] == 0 and merc[-1, 0] == grid.shape[0] - 1
    steps = np.diff(merc[:, 0])
    assert (steps >= 0).all()
    # Mercator stretches the north, so northern grid rows repeat more often than southern ones
    assert (merc[:, 0] < grid.shape[0] / 2).sum() > (merc[:, 0] >= grid.shape[0] / 2).sum()


def test_export_writes_what_the_app_reads(raw_lake, grid, monkeypatch):
    sp = [s for s in config.SPECIES if s.slug == "calliptamus-italicus"]
    monkeypatch.setattr(config, "SPECIES", sp)
    monkeypatch.setattr(features, "MODELLED", {"calliptamus-italicus", "orthoptera-background"})
    monkeypatch.setattr(sdm, "models", fast_models)
    clean.run()
    features.run(scheduler="sync")
    sdm.run()
    from pipeline import export
    out = export.run()
    site = config.SITE_DATA
    species = json.loads((site / "species.json").read_text())
    assert species[0]["slug"] == "calliptamus-italicus" and "metrics" in species[0]
    assert (site / "overlays" / "calliptamus-italicus-ensemble.png").exists()
    assert (site / "lake" / "occurrences.parquet").exists()
    assert out["occurrences_published"] == species[0]["clean"]
