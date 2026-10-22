"""Shared settings for the Range Atlas pipeline: study area, species, predictors and paths."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# LAKE can point to a local folder or an s3:// prefix; every reader and writer goes through fsspec.
LAKE = os.environ.get("RANGE_ATLAS_LAKE", str(ROOT / "data" / "lake"))
CACHE = Path(os.environ.get("RANGE_ATLAS_CACHE", ROOT / "data" / "cache"))
SITE_DATA = Path(os.environ.get("RANGE_ATLAS_SITE_DATA", ROOT / "web" / "public" / "data"))

# Europe, west of the Urals: lon_min, lat_min, lon_max, lat_max
BBOX = (-11.0, 34.0, 40.0, 72.0)
YEARS = (2000, 2026)
MAX_UNCERTAINTY_M = 10_000

# How many GBIF records to sample per species and for the target-group background.
RECORDS_PER_SPECIES = int(os.environ.get("RANGE_ATLAS_RECORDS", 3000))
BACKGROUND_RECORDS = int(os.environ.get("RANGE_ATLAS_BACKGROUND", 9000))


@dataclass(frozen=True)
class Species:
    key: int          # GBIF taxon key (or AphiaID for OBIS)
    name: str
    common: str
    family: str
    source: str = "gbif"
    model: bool = True  # marine species are shown on the map but not modelled (no marine predictors)

    @property
    def slug(self) -> str:
        return self.name.lower().replace(" ", "-")


SPECIES = [
    Species(1692898, "Tettigonia viridissima", "Great green bush-cricket", "Tettigoniidae"),
    Species(4402879, "Roeseliana roeselii", "Roesel's bush-cricket", "Tettigoniidae"),
    Species(1690685, "Decticus verrucivorus", "Wart-biter", "Tettigoniidae"),
    Species(1686243, "Phaneroptera falcata", "Sickle-bearing bush-cricket", "Tettigoniidae"),
    Species(1708251, "Chorthippus biguttulus", "Bow-winged grasshopper", "Acrididae"),
    Species(1700310, "Oedipoda caerulescens", "Blue-winged grasshopper", "Acrididae"),
    Species(1711091, "Stethophyma grossum", "Large marsh grasshopper", "Acrididae"),
    Species(1703254, "Calliptamus italicus", "Italian locust", "Acrididae"),
    Species(1716462, "Gryllus campestris", "Field cricket", "Gryllidae"),
    Species(1681124, "Tetrix subulata", "Slender groundhopper", "Tetrigidae"),
    # Marine, from OBIS (key = WoRMS AphiaID)
    Species(107381, "Carcinus maenas", "European green crab", "Carcinidae", source="obis", model=False),
    Species(123776, "Asterias rubens", "Common starfish", "Asteriidae", source="obis", model=False),
]
ORTHOPTERA_KEY = 1458  # GBIF order key, used for the target-group background

# WorldClim 2.1 bioclimatic variables at 10 arc-minutes (Fick & Hijmans 2017)
WORLDCLIM_URL = "https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_10m_bio.zip"
PREDICTORS = {
    "bio1": "Annual mean temperature (°C)",
    "bio5": "Max temperature of the warmest month (°C)",
    "bio6": "Min temperature of the coldest month (°C)",
    "bio12": "Annual precipitation (mm)",
    "bio15": "Precipitation seasonality (CV, %)",
}

# Darwin Core columns kept in the lakehouse, in this order
DWC_COLUMNS = [
    "occurrenceID", "source", "sourceRecordID", "datasetKey", "datasetName", "license",
    "scientificName", "acceptedName", "taxonKey", "family", "basisOfRecord", "occurrenceStatus",
    "eventDate", "year", "decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters",
    "countryCode", "issues", "role",
]
