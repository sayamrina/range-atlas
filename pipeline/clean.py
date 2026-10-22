"""Quality checks on the raw layer, written in DuckDB SQL. Each record gets the name of the first check it
fails (or NULL); the clean layer keeps only records that pass, and the counts go into the quality report."""
from __future__ import annotations

from . import config, lake

# Records with these GBIF/OBIS interpretation issues are dropped.
BAD_ISSUES = [
    "ZERO_COORDINATE", "COORDINATE_INVALID", "COORDINATE_OUT_OF_RANGE", "PRESUMED_SWAPPED_COORDINATE",
    "PRESUMED_NEGATED_LATITUDE", "PRESUMED_NEGATED_LONGITUDE", "COUNTRY_COORDINATE_MISMATCH",
    "TAXON_MATCH_HIGHERRANK", "TAXON_MATCH_NONE",
]

CHECKS = [
    ("missing_coordinates", "decimalLatitude IS NULL OR decimalLongitude IS NULL"),
    ("outside_study_area", "NOT (decimalLongitude BETWEEN {lon0} AND {lon1} AND decimalLatitude BETWEEN {lat0} AND {lat1})"),
    ("zero_or_equal_coordinates", "(decimalLatitude = 0 AND decimalLongitude = 0) OR decimalLatitude = decimalLongitude"),
    ("flagged_by_source", "len(list_intersect(string_split(coalesce(issues, ''), ';'), {bad})) > 0"),
    ("imprecise_coordinates", "coordinateUncertaintyInMeters > {max_unc}"),
    ("outside_time_window", "year IS NULL OR year NOT BETWEEN {y0} AND {y1}"),
    ("recorded_absence", "lower(trim(occurrenceStatus)) = 'absent'"),
    ("fossil_or_material", "basisOfRecord IN ('FOSSIL_SPECIMEN', 'MATERIAL_CITATION')"),
]


def flag_sql() -> str:
    lon0, lat0, lon1, lat1 = config.BBOX
    params = dict(lon0=lon0, lon1=lon1, lat0=lat0, lat1=lat1, max_unc=config.MAX_UNCERTAINTY_M,
                  y0=config.YEARS[0], y1=config.YEARS[1], bad="[" + ", ".join(f"'{i}'" for i in BAD_ISSUES) + "]")
    cases = "\n".join(f"WHEN {cond.format(**params)} THEN '{name}'" for name, cond in CHECKS)
    return f"CASE {cases}\nELSE NULL END"


def run() -> dict:
    con = lake.connect()
    con.sql(f"""
        CREATE OR REPLACE TABLE flagged AS
        SELECT *, {flag_sql()} AS failed_check
        FROM read_parquet('{lake.glob('raw')}', hive_partitioning = true, union_by_name = true)
    """)
    # Some publishers put other values (e.g. a WoRMS LSID) in occurrenceStatus. Both APIs were asked for
    # presences only, so such values are repaired to 'present' and counted rather than dropped.
    repaired = dict(con.sql("""
        SELECT species, count(*) FROM flagged
        WHERE occurrenceStatus IS NOT NULL AND lower(trim(occurrenceStatus)) NOT IN ('present', 'absent')
        GROUP BY ALL
    """).fetchall())
    con.sql("""
        UPDATE flagged SET occurrenceStatus = 'PRESENT'
        WHERE occurrenceStatus IS NULL OR lower(trim(occurrenceStatus)) <> 'absent'
    """)
    # Same species, same place (to ~10 m) and same day counts once.
    con.sql("""
        CREATE OR REPLACE TABLE flagged AS
        SELECT * REPLACE (
            CASE WHEN failed_check IS NULL AND row_number() OVER (
                PARTITION BY species, round(decimalLatitude, 4), round(decimalLongitude, 4), eventDate
                ORDER BY occurrenceID) > 1 THEN 'duplicate' ELSE failed_check END AS failed_check)
        FROM flagged
    """)
    counts = con.sql("""
        SELECT species, source, coalesce(failed_check, 'passed') AS outcome, count(*) AS n
        FROM flagged GROUP BY ALL ORDER BY species
    """).fetchall()
    clean = con.sql("SELECT * EXCLUDE (failed_check) FROM flagged WHERE failed_check IS NULL").df()
    for (source, species), part in clean.groupby(["source", "species"]):
        lake.write_partition(part.drop(columns=["species"]), "clean", source, species)

    report: dict[str, dict] = {}
    for species, source, outcome, n in counts:
        r = report.setdefault(species, {"species": species, "source": source, "harvested": 0, "passed": 0, "failed": {},
                                        "repaired": {"occurrenceStatus": int(repaired.get(species, 0))}})
        r["harvested"] += n
        if outcome == "passed":
            r["passed"] = n
        else:
            r["failed"][outcome] = n
    lake.write_json(list(report.values()), "clean/_quality.json")
    return {"records_raw": sum(r["harvested"] for r in report.values()), "records_clean": int(len(clean))}
