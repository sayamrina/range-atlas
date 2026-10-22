"""Run the whole pipeline and record each step in the run log shown in the app.

    python -m pipeline.run_all            # harvest, clean, features, models, export
    python -m pipeline.run_all --no-harvest  # reuse the raw layer already in the lake
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone

import requests

from . import clean, config, export, features, harvest, sdm

log = logging.getLogger("pipeline")
PREVIOUS_RUNS = os.environ.get("RANGE_ATLAS_PREVIOUS_RUNS", "https://sayamrina.github.io/range-atlas/data/runs.json")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def previous_runs() -> list[dict]:
    """Keep the history across rebuilds: the live site is the only place it is stored."""
    try:
        r = requests.get(PREVIOUS_RUNS, timeout=20)
        return r.json() if r.ok else []
    except (requests.RequestException, ValueError):
        return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-harvest", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    steps = [("harvest", harvest.run), ("quality checks", clean.run), ("predictors (Dask)", features.run),
             ("species distribution models", sdm.run), ("export", export.run)]
    if args.no_harvest:
        steps = steps[1:]
    run = {"id": now(), "started": now(), "trigger": os.environ.get("GITHUB_EVENT_NAME", "local"),
           "commit": os.environ.get("GITHUB_SHA", "")[:7], "status": "running", "steps": []}
    for name, fn in steps:
        t = time.time()
        step = {"name": name, "started": now()}
        try:
            result = fn()
            step.update(status="succeeded", output=result)
        except Exception as exc:  # the log should show which step failed and why
            step.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            log.error(traceback.format_exc())
        step["seconds"] = round(time.time() - t, 1)
        run["steps"].append(step)
        log.info("%s: %s in %ss", name, step["status"], step["seconds"])
        if step["status"] == "failed":
            break
    run["finished"] = now()
    run["status"] = "failed" if any(s["status"] == "failed" for s in run["steps"]) else "succeeded"

    history = [r for r in previous_runs() if r.get("id") != run["id"]][-29:] + [run]
    config.SITE_DATA.mkdir(parents=True, exist_ok=True)
    (config.SITE_DATA / "runs.json").write_text(json.dumps(history, indent=1, default=str))
    if run["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
