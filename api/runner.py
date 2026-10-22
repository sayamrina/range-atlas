"""Where model runs execute. 'local' runs them in a background thread of the API process; 'aws-batch'
submits a container job to AWS Batch, which runs pipeline.worker against the S3 lakehouse.

The AWS path is exercised in the tests against moto (a local AWS mock); it has not been run on a real
AWS account.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from psycopg.types.json import Jsonb

from pipeline import lake, sdm

from . import db

log = logging.getLogger(__name__)

# AWS Batch job states mapped onto the run states the app knows
BATCH_STATES = {"SUBMITTED": "queued", "PENDING": "queued", "RUNNABLE": "queued", "STARTING": "queued",
                "RUNNING": "running", "SUCCEEDED": "succeeded", "FAILED": "failed"}


def summary(result: dict) -> dict:
    """What we keep in the runs table; the full result with response curves stays in the lake."""
    return {k: result[k] for k in ("presence_cells", "background_cells", "metrics", "importance", "seconds")}


class LocalRunner:
    name = "local"

    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdm")

    def submit(self, run_id: int, species: str) -> None:
        self.pool.submit(self._execute, run_id, species)

    def _execute(self, run_id: int, species: str) -> None:
        with db.connect() as conn:
            conn.execute("UPDATE runs SET status = 'running', started = now() WHERE id = %s", (run_id,))
        try:
            result = sdm.run_species(species)
            with db.connect() as conn:
                conn.execute("UPDATE runs SET status = 'succeeded', finished = now(), result = %s WHERE id = %s",
                             (Jsonb(summary(result)), run_id))
        except Exception as exc:
            log.exception("run %s failed", run_id)
            with db.connect() as conn:
                conn.execute("UPDATE runs SET status = 'failed', finished = now(), error = %s WHERE id = %s",
                             (f"{type(exc).__name__}: {exc}", run_id))

    def refresh(self, run: dict) -> dict:
        return run  # the worker thread updates the row itself


class AwsBatchRunner:
    name = "aws-batch"

    def __init__(self, client=None) -> None:
        import boto3
        self.client = client or boto3.client("batch", region_name=os.environ.get("AWS_REGION", "eu-central-1"))
        self.queue = os.environ["RANGE_ATLAS_BATCH_QUEUE"]
        self.definition = os.environ["RANGE_ATLAS_BATCH_JOB_DEFINITION"]

    def submit(self, run_id: int, species: str) -> None:
        job = self.client.submit_job(
            jobName=f"range-atlas-run-{run_id}", jobQueue=self.queue, jobDefinition=self.definition,
            containerOverrides={
                "command": ["python", "-m", "pipeline.worker", species],
                "environment": [{"name": "RANGE_ATLAS_LAKE", "value": os.environ.get("RANGE_ATLAS_LAKE", "")}],
            },
        )
        with db.connect() as conn:
            conn.execute("UPDATE runs SET external_id = %s WHERE id = %s", (job["jobId"], run_id))

    def refresh(self, run: dict) -> dict:
        """Poll AWS Batch for a run that has not finished yet and store what changed."""
        if run["status"] in ("succeeded", "failed") or not run.get("external_id"):
            return run
        jobs = self.client.describe_jobs(jobs=[run["external_id"]])["jobs"]
        if not jobs:
            return run
        job = jobs[0]
        status = BATCH_STATES.get(job["status"], run["status"])
        result, error = None, None
        if status == "succeeded":
            result = summary(lake.read_json(f"models/species={run['species']}/result.json"))
        elif status == "failed":
            error = job.get("statusReason", "AWS Batch job failed")
        with db.connect() as conn:
            return conn.execute("""
                UPDATE runs SET status = %s,
                    started = CASE WHEN %s IN ('running', 'succeeded', 'failed') THEN coalesce(started, now()) END,
                    finished = CASE WHEN %s IN ('succeeded', 'failed') THEN now() END,
                    result = coalesce(%s, result), error = coalesce(%s, error)
                WHERE id = %s RETURNING *
            """, (status, status, status, Jsonb(result) if result else None, error, run["id"])).fetchone()


def make_runner():
    backend = os.environ.get("RANGE_ATLAS_RUN_BACKEND", "local")
    return AwsBatchRunner() if backend == "aws-batch" else LocalRunner()
