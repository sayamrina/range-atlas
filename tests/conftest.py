"""Fixtures: a small synthetic climate grid and lakehouse, so the tests run offline and in seconds."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline import climate, config, harvest, lake


def synthetic_grid() -> climate.Grid:
    """A 1° grid over the study area. bio1 falls from 20 °C in the south to -2 °C in the north."""
    lon0, lat0, lon1, lat1 = config.BBOX
    rows, cols = int(lat1 - lat0), int(lon1 - lon0)
    lat = lat1 - (np.arange(rows) + 0.5)
    base = np.repeat((20 - (lat - lat0) * 22 / (lat1 - lat0))[:, None], cols, axis=1).astype("float32")
    rng = np.random.default_rng(1)
    data = {
        "bio1": base,
        "bio5": base + 10,
        "bio6": base - 10,
        "bio12": (600 + rng.normal(0, 80, base.shape)).astype("float32"),
        "bio15": (30 + rng.normal(0, 5, base.shape)).astype("float32"),
    }
    for arr in data.values():
        arr[:3, :3] = np.nan  # a patch of sea in the north-west corner
    return climate.Grid(data, lon0, lat1, 1.0)


def record(i: int, species: str, lon: float, lat: float, **extra) -> dict:
    row = {c: None for c in config.DWC_COLUMNS}
    row.update(occurrenceID=f"gbif:{species}-{i}", source="gbif", sourceRecordID=str(i), scientificName=species,
               acceptedName=species, basisOfRecord="HUMAN_OBSERVATION", occurrenceStatus="PRESENT",
               eventDate=f"2015-06-{i % 28 + 1:02d}", year=2015, decimalLatitude=lat, decimalLongitude=lon,
               coordinateUncertaintyInMeters=100.0, countryCode="DE", issues="", role="presence",
               datasetKey="d1", datasetName="Test dataset", license="CC0_1_0", family="Acrididae", taxonKey=1)
    row.update(extra)
    return row


@pytest.fixture
def grid(monkeypatch):
    g = synthetic_grid()
    monkeypatch.setattr(climate, "load", lambda: g)
    return g


@pytest.fixture
def lake_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LAKE", str(tmp_path / "lake"))
    monkeypatch.setattr(config, "SITE_DATA", tmp_path / "site")
    return tmp_path


@pytest.fixture
def raw_lake(lake_dir):
    """Raw layer: one warm-loving species (south only), the target-group background everywhere."""
    rng = np.random.default_rng(0)
    warm = [record(i, "Calliptamus italicus", rng.uniform(-8, 38), rng.uniform(36, 48)) for i in range(400)]
    bg = [record(i, "Orthoptera", rng.uniform(-8, 38), rng.uniform(36, 70), role="background") for i in range(1200)]
    lake.write_partition(harvest.normalise(pd.DataFrame(warm)), "raw", "gbif", "calliptamus-italicus")
    lake.write_partition(harvest.normalise(pd.DataFrame(bg)), "raw", "gbif", "orthoptera-background")
    return lake_dir
