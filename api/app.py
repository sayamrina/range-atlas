"""Range Atlas REST API: species, occurrences, vector tiles from PostGIS and model runs.

    uvicorn api.app:app --reload
"""
from __future__ import annotations

import hashlib
import io
import os
from collections import OrderedDict
from threading import Lock

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from pipeline import climate, lake, tiles

from . import db
from .runner import make_runner

app = FastAPI(title="Range Atlas API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
                   allow_methods=["GET", "POST"], allow_headers=["*"])
runner = make_runner()


class TileCache:
    """Small in-process LRU for rendered tiles; data only changes when the lake is reloaded."""

    def __init__(self, size: int = 2048) -> None:
        self.size, self.items, self.lock = size, OrderedDict(), Lock()

    def get(self, key):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
        return None

    def put(self, key, value) -> None:
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)

    def clear(self) -> None:
        with self.lock:
            self.items.clear()


tile_cache = TileCache()

TILE_SQL = """
WITH bounds AS (SELECT ST_TileEnvelope(%(z)s, %(x)s, %(y)s) AS env)
SELECT ST_AsMVT(t, 'occurrences', 4096, 'geom') AS tile FROM (
    SELECT s.idx AS s, 1 AS n, coalesce(o.year, 0) AS y, coalesce(o.basis, '') AS b,
           ST_AsMVTGeom(ST_Transform(o.geom, 3857), bounds.env, 4096, 64, true) AS geom
    FROM occurrences o JOIN species s ON s.slug = o.species, bounds
    WHERE o.geom && ST_Transform(bounds.env, 4326)
      AND (%(species)s::text IS NULL OR o.species = %(species)s::text)
) t
"""


@app.get("/api/health")
def health() -> dict:
    with db.connect() as conn:
        n = conn.execute("SELECT count(*) AS n FROM occurrences").fetchone()["n"]
    return {"status": "ok", "occurrences": n, "run_backend": runner.name}


@app.get("/api/species")
def species() -> list[dict]:
    with db.connect() as conn:
        return conn.execute("""
            SELECT s.*, count(o.*) AS occurrences,
                   (SELECT r.id FROM runs r WHERE r.species = s.slug AND r.status = 'succeeded'
                    ORDER BY r.finished DESC LIMIT 1) AS latest_run
            FROM species s LEFT JOIN occurrences o ON o.species = s.slug
            GROUP BY s.slug ORDER BY s.idx
        """).fetchall()


@app.get("/api/species/{slug}/occurrences")
def occurrences(slug: str, bbox: str | None = Query(None, description="lon_min,lat_min,lon_max,lat_max"),
                limit: int = Query(1000, le=10_000)) -> dict:
    """GeoJSON FeatureCollection of one species' clean records, optionally inside a bounding box."""
    box = None
    if bbox:
        try:
            box = [float(v) for v in bbox.split(",")]
            assert len(box) == 4
        except (ValueError, AssertionError):
            raise HTTPException(422, "bbox must be four numbers: lon_min,lat_min,lon_max,lat_max")
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM species WHERE slug = %s", (slug,)).fetchone():
            raise HTTPException(404, f"unknown species {slug}")
        rows = conn.execute("""
            SELECT occurrence_id, basis, year, country, uncertainty_m, dataset_key, license, ST_AsGeoJSON(geom)::json AS geometry
            FROM occurrences
            WHERE species = %(slug)s
              AND (%(box)s::float8[] IS NULL OR geom && ST_MakeEnvelope((%(box)s::float8[])[1], (%(box)s::float8[])[2],
                                                                        (%(box)s::float8[])[3], (%(box)s::float8[])[4], 4326))
            ORDER BY occurrence_id LIMIT %(limit)s
        """, {"slug": slug, "box": box, "limit": limit}).fetchall()
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": r.pop("occurrence_id"), "geometry": r.pop("geometry"), "properties": r} for r in rows]}


@app.get("/tiles/{z}/{x}/{y}.pbf")
def tile(z: int, x: int, y: int, request: Request, species: str | None = None) -> Response:
    if not (0 <= z <= 14 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise HTTPException(404, "tile out of range")
    key = (z, x, y, species)
    cached = tile_cache.get(key)
    if cached is None:
        with db.connect() as conn:
            data = bytes(conn.execute(TILE_SQL, {"z": z, "x": x, "y": y, "species": species}).fetchone()["tile"] or b"")
        cached = (data, hashlib.sha1(data).hexdigest()[:16])
        tile_cache.put(key, cached)
    data, etag = cached
    headers = {"ETag": f'"{etag}"', "Cache-Control": "public, max-age=3600"}
    if request.headers.get("if-none-match") == f'"{etag}"':
        return Response(status_code=304, headers=headers)
    return Response(data, media_type="application/vnd.mapbox-vector-tile", headers=headers)


class RunRequest(BaseModel):
    species: str


@app.get("/api/runs")
def list_runs(species: str | None = None, limit: int = Query(50, le=500)) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT * FROM runs WHERE (%(species)s::text IS NULL OR species = %(species)s::text)
            ORDER BY created DESC LIMIT %(limit)s
        """, {"species": species, "limit": limit}).fetchall()
    return [runner.refresh(r) for r in rows]


@app.post("/api/runs", status_code=202)
def start_run(req: RunRequest) -> dict:
    with db.connect() as conn:
        sp = conn.execute("SELECT modelled FROM species WHERE slug = %s", (req.species,)).fetchone()
        if not sp:
            raise HTTPException(404, f"unknown species {req.species}")
        if not sp["modelled"]:
            raise HTTPException(422, f"{req.species} is a marine species; there are no marine predictors to model it with")
        busy = conn.execute("SELECT id FROM runs WHERE species = %s AND status IN ('queued', 'running')",
                            (req.species,)).fetchone()
        if busy:
            raise HTTPException(409, f"run {busy['id']} for {req.species} has not finished yet")
        run = conn.execute("INSERT INTO runs (species, backend) VALUES (%s, %s) RETURNING *",
                           (req.species, runner.name)).fetchone()
    runner.submit(run["id"], req.species)
    return run


@app.get("/api/runs/{run_id}")
def get_run(run_id: int) -> dict:
    with db.connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone()
    if not run:
        raise HTTPException(404, "no such run")
    return runner.refresh(run)


@app.get("/api/species/{slug}/suitability.png")
def suitability(slug: str, model: str = Query("ensemble", pattern="^(glm|rf|ensemble)$")) -> Response:
    """The latest model surface as a Web Mercator PNG overlay (bounds: /api/meta)."""
    try:
        with lake.fs_for(lake.uri("models")).open(lake.uri("models", f"species={slug}", "suitability.npz"), "rb") as f:
            arr = np.load(f)[model]
    except FileNotFoundError:
        raise HTTPException(404, f"no model output for {slug} yet")
    grid = climate.load()
    buf = io.BytesIO()
    tiles.write_overlay(arr, grid, buf, 0.0, 1.0, fade=True)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-cache"})


@app.get("/api/meta")
def meta() -> dict:
    return {"bounds": climate.load().bounds}
