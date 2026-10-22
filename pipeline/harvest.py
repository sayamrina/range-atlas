"""Harvest occurrence records from GBIF and OBIS and write them to the lakehouse as Darwin Core Parquet.

GBIF's search API returns records in a fixed order, so the first pages favour whichever dataset happens to
come first, and pages deep into a result set are very slow. Instead we ask GBIF how many records each country
holds and give every country a share of the sample that grows with the square root of its count. Well-recorded
countries still contribute most, but they no longer drown out the rest of Europe. Within a country we read
pages at evenly spaced offsets among the first few thousand records. OBIS is sampled per five-year period.
"""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
import requests

from . import config, lake

log = logging.getLogger(__name__)
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "range-atlas/1.0 (portfolio project; https://github.com/sayamrina/range-atlas)"

GBIF = "https://api.gbif.org/v1/occurrence/search"
OBIS = "https://api.obis.org/v3/occurrence"
PAGE = 300


def get_json(url: str, params: dict, tries: int = 5) -> dict:
    for attempt in range(tries):
        r = SESSION.get(url, params=params, timeout=60)
        if r.status_code in (429, 500, 502, 503, 504):
            wait = int(r.headers.get("Retry-After", 2 ** attempt))
            log.warning("%s returned %s, waiting %ss", url, r.status_code, wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"{url} kept failing after {tries} tries")


def spread_offsets(total: int, wanted: int, page: int, cap: int) -> list[int]:
    """Page offsets spread evenly over min(total, cap) records, enough pages to reach `wanted`."""
    reachable = min(total, cap)
    pages = max(1, min(-(-wanted // page), -(-reachable // page)))
    if pages == 1:
        return [0]
    step = max(page, (reachable - page) // (pages - 1))
    return sorted({min(i * step, max(0, reachable - page)) for i in range(pages)})


def gbif_params(taxon_key: int) -> dict:
    lon0, lat0, lon1, lat1 = config.BBOX
    return {
        "taxonKey": taxon_key, "hasCoordinate": "true", "hasGeospatialIssue": "false",
        "occurrenceStatus": "PRESENT", "decimalLatitude": f"{lat0},{lat1}", "decimalLongitude": f"{lon0},{lon1}",
        "year": f"{config.YEARS[0]},{config.YEARS[1]}", "coordinateUncertaintyInMeters": f"0,{config.MAX_UNCERTAINTY_M}",
    }


def gbif_row(r: dict, role: str) -> dict:
    return {
        "occurrenceID": f"gbif:{r['key']}", "source": "gbif", "sourceRecordID": str(r["key"]),
        "datasetKey": r.get("datasetKey"), "datasetName": r.get("datasetName"), "license": r.get("license"),
        "scientificName": r.get("scientificName"), "acceptedName": r.get("species") or r.get("acceptedScientificName"),
        "taxonKey": r.get("speciesKey") or r.get("taxonKey"), "family": r.get("family"),
        "basisOfRecord": r.get("basisOfRecord"), "occurrenceStatus": r.get("occurrenceStatus"),
        "eventDate": r.get("eventDate"), "year": r.get("year"),
        "decimalLatitude": r.get("decimalLatitude"), "decimalLongitude": r.get("decimalLongitude"),
        "coordinateUncertaintyInMeters": r.get("coordinateUncertaintyInMeters"),
        "countryCode": r.get("countryCode"), "issues": ";".join(r.get("issues") or []), "role": role,
    }


def country_quotas(counts: dict[str, int], wanted: int) -> dict[str, int]:
    """Split `wanted` over countries in proportion to sqrt(count), never more than a country holds."""
    weights = {c: n ** 0.5 for c, n in counts.items() if n > 0}
    total = sum(weights.values()) or 1
    return {c: min(counts[c], max(1, round(wanted * w / total))) for c, w in weights.items()}


def harvest_gbif(taxon_key: int, wanted: int, role: str = "presence") -> pd.DataFrame:
    params = gbif_params(taxon_key)
    head = get_json(GBIF, {**params, "limit": 0, "facet": "COUNTRY", "facetLimit": 80})
    counts = {c["name"]: c["count"] for c in head["facets"][0]["counts"]} if head["facets"] else {}
    rows: list[dict] = []
    for country, quota in country_quotas(counts, wanted).items():
        got = 0
        for offset in spread_offsets(counts[country], quota, PAGE, cap=3000):
            limit = min(PAGE, quota - got)
            page = get_json(GBIF, {**params, "country": country, "limit": limit, "offset": offset})
            rows += [gbif_row(r, role) for r in page["results"]]
            got += len(page["results"])
            time.sleep(0.1)
            if got >= quota:
                break
    log.info("GBIF %s: %s of %s records from %s countries", taxon_key, len(rows), head["count"], len(counts))
    return pd.DataFrame(rows, columns=config.DWC_COLUMNS)


def obis_row(r: dict) -> dict:
    return {
        "occurrenceID": f"obis:{r['id']}", "source": "obis", "sourceRecordID": r["id"],
        "datasetKey": r.get("dataset_id"), "datasetName": r.get("datasetName"), "license": r.get("license"),
        "scientificName": r.get("scientificName"), "acceptedName": r.get("species") or r.get("scientificName"),
        "taxonKey": r.get("aphiaID"), "family": r.get("family"), "basisOfRecord": r.get("basisOfRecord"),
        "occurrenceStatus": r.get("occurrenceStatus"), "eventDate": r.get("eventDate"), "year": r.get("date_year"),
        "decimalLatitude": r.get("decimalLatitude"), "decimalLongitude": r.get("decimalLongitude"),
        "coordinateUncertaintyInMeters": r.get("coordinateUncertaintyInMeters"),
        "countryCode": r.get("countryCode"), "issues": ";".join(r.get("flags") or []), "role": "presence",
    }


def harvest_obis(aphia_id: int, wanted: int) -> pd.DataFrame:
    lon0, lat0, lon1, lat1 = config.BBOX
    params = {
        "taxonid": aphia_id, "startdate": f"{config.YEARS[0]}-01-01", "absence": "false",
        "geometry": f"POLYGON(({lon0} {lat0},{lon1} {lat0},{lon1} {lat1},{lon0} {lat1},{lon0} {lat0}))",
    }
    # OBIS pages with a cursor rather than offsets, so we spread the sample over five-year periods instead
    periods = [(y, min(y + 4, config.YEARS[1])) for y in range(config.YEARS[0], config.YEARS[1] + 1, 5)]
    rows: list[dict] = []
    total = 0
    for start, end in periods:
        page = get_json(OBIS, {**params, "startdate": f"{start}-01-01", "enddate": f"{end}-12-31",
                               "size": min(5000, -(-wanted // len(periods)))})
        rows += [obis_row(r) for r in page["results"]]
        total += page["total"]
        time.sleep(0.2)
    log.info("OBIS %s: %s of %s records", aphia_id, len(rows), total)
    return pd.DataFrame(rows, columns=config.DWC_COLUMNS)


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    for col in ("year", "taxonKey"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in set(config.DWC_COLUMNS) - {"decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters", "year", "taxonKey"}:
        df[col] = df[col].astype("string")
    return df.drop_duplicates("occurrenceID")


def run() -> dict:
    """Harvest every species plus the background sample; returns counts for the run log."""
    counts: dict[str, int] = {}
    for sp in config.SPECIES:
        if sp.source == "gbif":
            df = harvest_gbif(sp.key, config.RECORDS_PER_SPECIES)
        else:
            df = harvest_obis(sp.key, config.RECORDS_PER_SPECIES)
        lake.write_partition(normalise(df), "raw", sp.source, sp.slug)
        counts[sp.slug] = len(df)
    bg = harvest_gbif(config.ORTHOPTERA_KEY, config.BACKGROUND_RECORDS, role="background")
    lake.write_partition(normalise(bg), "raw", "gbif", "orthoptera-background")
    counts["orthoptera-background"] = len(bg)
    lake.write_json({"harvested": date.today().isoformat(), "counts": counts}, "raw/_harvest.json")
    return {"records_harvested": sum(counts.values()), "species": len(config.SPECIES),
            "background_records": counts["orthoptera-background"]}
