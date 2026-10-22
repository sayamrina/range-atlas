"""Attach predictor values to the clean records with Dask, one partition per species, then thin them to one
record per species and grid cell. Thinning stops a few heavily recorded places from dominating the model."""
from __future__ import annotations

import dask.dataframe as dd
import numpy as np
import pandas as pd

from . import climate, config, lake

MODELLED = {sp.slug for sp in config.SPECIES if sp.model} | {"orthoptera-background"}


def add_predictors(part: pd.DataFrame, grid: climate.Grid) -> pd.DataFrame:
    """Runs on each Dask partition: look up the grid cell and the predictor values of every record."""
    part = part.copy()
    row, col = grid.cell(part["decimalLongitude"].to_numpy(), part["decimalLatitude"].to_numpy())
    part["cell"] = np.where(row >= 0, row * grid.shape[1] + col, -1).astype("int64")
    for name, values in grid.sample(part["decimalLongitude"].to_numpy(), part["decimalLatitude"].to_numpy()).items():
        part[name] = values
    return part


def run(scheduler: str = "threads") -> dict:
    grid = climate.load()
    ddf = dd.read_parquet(lake.uri("clean"), columns=["occurrenceID", "decimalLatitude", "decimalLongitude", "year", "species"])
    ddf = ddf[ddf["species"].astype(str).isin(MODELLED)]
    meta = ddf._meta.assign(cell=pd.Series(dtype="int64"), **{p: pd.Series(dtype="float32") for p in config.PREDICTORS})
    enriched = ddf.map_partitions(add_predictors, grid, meta=meta)
    df = enriched.compute(scheduler=scheduler)
    df["species"] = df["species"].astype(str)

    on_land = df[(df["cell"] >= 0) & df[list(config.PREDICTORS)].notna().all(axis=1)]
    thinned = on_land.sort_values("occurrenceID").drop_duplicates(["species", "cell"])
    for species, part in thinned.groupby("species"):
        lake.write_parquet(part.drop(columns=["source", "species"], errors="ignore"),
                           lake.uri("features", f"species={species}", "part-0.parquet"))
    return {"records_with_climate": int(len(on_land)), "cells_after_thinning": int(len(thinned)),
            "dropped_no_climate": int(len(df) - len(on_land))}
