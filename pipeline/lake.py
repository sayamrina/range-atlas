"""Small helpers around the lakehouse: hive-partitioned Parquet on a local path or on S3, read with DuckDB.

Layers:
  raw/source=<gbif|obis>/species=<slug>/part-0.parquet      Darwin Core records as harvested
  clean/source=.../species=.../part-0.parquet               records that passed the quality checks
  features/species=.../part-0.parquet                       thinned records with predictor values
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse

import duckdb
import fsspec
import pandas as pd

from . import config


def uri(*parts: str) -> str:
    return "/".join([config.LAKE.rstrip("/"), *parts])


def fs_for(path: str) -> fsspec.AbstractFileSystem:
    return fsspec.core.url_to_fs(path)[0]


def write_parquet(df: pd.DataFrame, path: str) -> None:
    fs = fs_for(path)
    fs.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
    with fs.open(path, "wb") as f:
        df.to_parquet(f, index=False, compression="zstd")


def write_partition(df: pd.DataFrame, layer: str, source: str, species: str) -> str:
    # partition values live in the path, so they are dropped from the file itself
    path = uri(layer, f"source={source}", f"species={species}", "part-0.parquet")
    write_parquet(df.drop(columns=["source"], errors="ignore"), path)
    return path


def write_json(obj, rel: str) -> None:
    path = uri(rel)
    fs = fs_for(path)
    fs.makedirs(path.rsplit("/", 1)[0], exist_ok=True)
    with fs.open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)


def read_json(rel: str):
    with fsspec.open(uri(rel), "r") as f:
        return json.load(f)


def connect() -> duckdb.DuckDBPyConnection:
    """DuckDB connection that can read the lake wherever it lives. On S3 it uses DuckDB's own httpfs reader
    with the usual AWS settings; AWS_ENDPOINT_URL points it at an S3-compatible store (MinIO, moto)."""
    con = duckdb.connect()
    if config.LAKE.startswith("s3://"):
        con.execute("INSTALL httpfs; LOAD httpfs;")
        opts = [f"REGION '{os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION', 'eu-central-1')}'"]
        if os.environ.get("AWS_ACCESS_KEY_ID"):
            opts += [f"KEY_ID '{os.environ['AWS_ACCESS_KEY_ID']}'", f"SECRET '{os.environ['AWS_SECRET_ACCESS_KEY']}'"]
        else:
            opts.append("PROVIDER credential_chain")
        if endpoint := os.environ.get("AWS_ENDPOINT_URL"):
            url = urlparse(endpoint)
            opts += [f"ENDPOINT '{url.netloc}'", "URL_STYLE 'path'", f"USE_SSL {str(url.scheme == 'https').lower()}"]
        con.execute(f"CREATE SECRET lake (TYPE s3, {', '.join(opts)})")
    return con


def glob(layer: str) -> str:
    return uri(layer, "**", "*.parquet")
