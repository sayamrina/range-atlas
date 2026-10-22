"""Write everything the static web app needs into web/public/data."""
from __future__ import annotations

import json
import logging
import shutil

import numpy as np
import requests

from . import climate, config, lake, tiles


log = logging.getLogger(__name__)


def licence(raw) -> str:
    """Normalise the many spellings of Creative Commons licences used by publishers."""
    v = str(raw or "").lower()
    if not v or v == "nan":
        return "Not stated"
    if "zero" in v or "cc0" in v:
        return "CC0"
    if "by" in v:
        return "CC BY-NC" if "nc" in v.split("by", 1)[1] else "CC BY"
    return str(raw)


def dataset_titles(keys: list[tuple[str, str]]) -> dict[str, str]:
    """Records often lack datasetName; look the titles up once per (source, key) and keep them in the cache."""
    path = config.CACHE / "dataset_titles.json"
    known = json.loads(path.read_text()) if path.exists() else {}
    for source, key in keys:
        if key in known:
            continue
        try:
            if source == "gbif":
                r = requests.get(f"https://api.gbif.org/v1/dataset/{key}", timeout=20)
                known[key] = r.json().get("title", "") if r.ok else ""
            else:
                r = requests.get(f"https://api.obis.org/v3/dataset/{key}", timeout=20)
                results = r.json().get("results", []) if r.ok else []
                known[key] = results[0].get("title", "") if results else ""
        except (requests.RequestException, ValueError) as exc:
            log.warning("dataset %s: %s", key, exc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(known, indent=0))
    return known


def dataset_table(occ) -> list[dict]:
    occ = occ.assign(licence=occ.license.map(licence))
    rows = []
    for (source, key), part in occ.groupby(["source", "datasetKey"], dropna=False):
        names = part.datasetName.dropna()
        rows.append({"source": source, "datasetKey": "" if key is None or str(key) == "nan" else str(key),
                     "datasetName": names.iloc[0] if len(names) else "",
                     "licences": sorted(part.licence.unique()), "records": int(len(part))})
    missing = [(r["source"], r["datasetKey"]) for r in rows if not r["datasetName"] and r["datasetKey"]]
    titles = dataset_titles(missing)
    for r in rows:
        r["datasetName"] = r["datasetName"] or titles.get(r["datasetKey"], "")
    return sorted(rows, key=lambda r: -r["records"])


def dump(obj, rel: str) -> None:
    path = config.SITE_DATA / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), default=str))


def run() -> dict:
    out = config.SITE_DATA
    for sub in ("tiles", "overlays", "models"):
        shutil.rmtree(out / sub, ignore_errors=True)
    grid = climate.load()
    con = lake.connect()
    quality = {q["species"]: q for q in lake.read_json("clean/_quality.json")}
    thinned = dict(con.sql(f"SELECT species, count(*) FROM read_parquet('{lake.glob('features')}', hive_partitioning = true) GROUP BY ALL").fetchall())

    occ = con.sql(f"""
        SELECT species, source, occurrenceID, scientificName, basisOfRecord, year, eventDate, countryCode,
               round(decimalLatitude, 5) AS lat, round(decimalLongitude, 5) AS lon, coordinateUncertaintyInMeters,
               datasetKey, datasetName, license
        FROM read_parquet('{lake.glob('clean')}', hive_partitioning = true, union_by_name = true)
        WHERE species <> 'orthoptera-background'
    """).df()
    (out / "lake").mkdir(parents=True, exist_ok=True)
    occ.to_parquet(out / "lake" / "occurrences.parquet", index=False, compression="zstd")

    index = {sp.slug: i for i, sp in enumerate(config.SPECIES)}
    pts = occ.assign(species=occ.species.map(index), basis=occ.basisOfRecord.fillna(""))
    n_tiles = tiles.write_tiles(pts[["lon", "lat", "species", "year", "basis"]], out / "tiles", bbox=config.BBOX)

    species = []
    for i, sp in enumerate(config.SPECIES):
        entry = {"index": i, "slug": sp.slug, "name": sp.name, "common": sp.common, "family": sp.family,
                 "source": sp.source, "key": sp.key, "modelled": sp.model,
                 "harvested": quality.get(sp.slug, {}).get("harvested", 0),
                 "clean": quality.get(sp.slug, {}).get("passed", 0), "cells": int(thinned.get(sp.slug, 0)),
                 "link": (f"https://www.gbif.org/species/{sp.key}" if sp.source == "gbif"
                          else f"https://obis.org/taxon/{sp.key}")}
        if sp.model:
            result = lake.read_json(f"models/species={sp.slug}/result.json")
            entry["metrics"] = result["metrics"]
            dump(result, f"models/{sp.slug}.json")
            with lake.fs_for(lake.uri("models")).open(lake.uri("models", f"species={sp.slug}", "suitability.npz"), "rb") as f:
                maps = dict(np.load(f))
            for name, arr in maps.items():
                tiles.write_overlay(arr, grid, out / "overlays" / f"{sp.slug}-{name}.png", 0.0, 1.0, fade=True)
        species.append(entry)
    dump(species, "species.json")

    predictors = []
    for p, label in config.PREDICTORS.items():
        arr = grid.data[p]
        lo, hi = float(np.nanpercentile(arr, 2)), float(np.nanpercentile(arr, 98))
        tiles.write_overlay(arr, grid, out / "overlays" / f"{p}.png", lo, hi, stops=tiles.SLATE)
        predictors.append({"id": p, "label": label, "min": round(lo, 1), "max": round(hi, 1)})
    dump({"bounds": grid.bounds, "predictors": predictors, "bbox": config.BBOX, "years": config.YEARS,
          "ramps": {"suitability": tiles.HEATHER, "predictor": tiles.SLATE}}, "meta.json")

    dump(list(quality.values()), "quality.json")
    datasets = dataset_table(occ)
    dump(datasets, "datasets.json")
    licences = occ.license.map(licence).value_counts()
    dump({k: int(v) for k, v in licences.items()}, "licences.json")
    return {"tiles": n_tiles, "occurrences_published": int(len(occ)), "datasets": int(len(datasets))}
