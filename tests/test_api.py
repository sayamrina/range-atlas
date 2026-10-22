"""API tests against a real PostGIS database. Set TEST_DATABASE_URL (CI starts a postgis/postgis service)."""
import os
import time

import mapbox_vector_tile
import psycopg
import pytest
from fastapi.testclient import TestClient

from pipeline import clean, config

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def client(raw_lake, grid, monkeypatch):
    from api import app as app_module
    from api import db, runner

    monkeypatch.setattr(db, "DATABASE_URL", URL)
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS runs, occurrences, species CASCADE")
    clean.run()
    db.load_from_lake()

    def fake_run(slug):
        time.sleep(0.2)
        return {"species": slug, "presence_cells": 80, "background_cells": 400, "seconds": 0.2,
                "metrics": {"glm": {"auc": 0.8, "tss": 0.5, "threshold": 0.4}}, "importance": {"glm": {"bio1": 0.2}}}

    monkeypatch.setattr(runner.sdm, "run_species", fake_run)
    monkeypatch.setattr(app_module, "runner", runner.LocalRunner())
    app_module.tile_cache.clear()
    return TestClient(app_module.app)


def test_health_and_species(client):
    assert client.get("/api/health").json()["occurrences"] == 400
    species = client.get("/api/species").json()
    assert len(species) == len(config.SPECIES)
    ci = next(s for s in species if s["slug"] == "calliptamus-italicus")
    assert ci["occurrences"] == 400 and ci["modelled"]


def test_occurrences_as_geojson_with_bbox(client):
    all_ = client.get("/api/species/calliptamus-italicus/occurrences?limit=5000").json()["features"]
    box = client.get("/api/species/calliptamus-italicus/occurrences?bbox=0,40,10,45&limit=5000").json()["features"]
    assert len(all_) == 400 and 0 < len(box) < 400
    assert all(0 <= f["geometry"]["coordinates"][0] <= 10 for f in box)
    assert client.get("/api/species/calliptamus-italicus/occurrences?bbox=1,2,3").status_code == 422
    assert client.get("/api/species/nope/occurrences").status_code == 404


def test_vector_tiles_from_postgis_are_cached(client):
    r = client.get("/tiles/0/0/0.pbf?species=calliptamus-italicus")
    assert r.status_code == 200 and r.headers["cache-control"].startswith("public")
    feats = mapbox_vector_tile.decode(r.content)["occurrences"]["features"]
    assert len(feats) == 400 and set(feats[0]["properties"]) == {"s", "n", "y", "b"}
    again = client.get("/tiles/0/0/0.pbf?species=calliptamus-italicus", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304
    assert client.get("/tiles/1/5/0.pbf").status_code == 404


def test_model_run_lifecycle(client):
    assert client.post("/api/runs", json={"species": "carcinus-maenas"}).status_code == 422
    assert client.post("/api/runs", json={"species": "nope"}).status_code == 404
    run = client.post("/api/runs", json={"species": "calliptamus-italicus"})
    assert run.status_code == 202 and run.json()["status"] == "queued"
    assert client.post("/api/runs", json={"species": "calliptamus-italicus"}).status_code == 409
    for _ in range(50):
        state = client.get(f"/api/runs/{run.json()['id']}").json()
        if state["status"] == "succeeded":
            break
        time.sleep(0.1)
    assert state["status"] == "succeeded" and state["result"]["metrics"]["glm"]["auc"] == 0.8
    assert client.get("/api/runs?species=calliptamus-italicus").json()[0]["id"] == state["id"]
