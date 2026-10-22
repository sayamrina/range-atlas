"""PostgreSQL/PostGIS access: connection, schema and loading the lakehouse into the metadata layer."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from pipeline import config, lake

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://range:range@localhost:5432/range_atlas")
SCHEMA = Path(__file__).with_name("schema.sql")


@contextmanager
def connect():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def init_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA.read_text())


def load_from_lake() -> dict:
    """Replace species and occurrences with the clean layer of the lake (runs are kept)."""
    init_schema()
    occ = lake.connect().sql(f"""
        SELECT occurrenceID, species, basisOfRecord, year, countryCode, coordinateUncertaintyInMeters,
               datasetKey, license, decimalLongitude, decimalLatitude
        FROM read_parquet('{lake.glob('clean')}', hive_partitioning = true, union_by_name = true)
        WHERE species <> 'orthoptera-background'
    """).fetchall()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM occurrences")
        for i, sp in enumerate(config.SPECIES):
            cur.execute("""
                INSERT INTO species (slug, idx, name, common, family, source, source_key, modelled)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET idx = EXCLUDED.idx, name = EXCLUDED.name, common = EXCLUDED.common,
                    family = EXCLUDED.family, source = EXCLUDED.source, source_key = EXCLUDED.source_key,
                    modelled = EXCLUDED.modelled
            """, (sp.slug, i, sp.name, sp.common, sp.family, sp.source, sp.key, sp.model))
        cur.execute("CREATE TEMP TABLE staging (id text, species text, basis text, year int, country text, "
                    "unc double precision, dataset text, license text, lon double precision, lat double precision)")
        with cur.copy("COPY staging FROM STDIN") as copy:
            for row in occ:
                copy.write_row(row)
        cur.execute("""
            INSERT INTO occurrences (occurrence_id, species, basis, year, country, uncertainty_m, dataset_key, license, geom)
            SELECT id, species, basis, year, country, unc, dataset, license, ST_SetSRID(ST_MakePoint(lon, lat), 4326)
            FROM staging
        """)
    return {"occurrences": len(occ), "species": len(config.SPECIES)}


if __name__ == "__main__":
    print(load_from_lake())
