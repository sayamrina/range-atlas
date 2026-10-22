"""The S3 lakehouse and the AWS Batch runner, tested against moto (a local AWS mock), not a real account."""
import json

import boto3
import pandas as pd
import pytest
from moto import mock_aws
from moto.server import ThreadedMotoServer

from api import runner
from pipeline import config, features, harvest, lake

from .conftest import record


@pytest.fixture
def s3_lake(monkeypatch):
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": "eu-central-1",
                 "AWS_ENDPOINT_URL": endpoint}.items():
        monkeypatch.setenv(k, v)
    boto3.client("s3", endpoint_url=endpoint).create_bucket(
        Bucket="atlas", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"})
    import s3fs
    s3fs.S3FileSystem.clear_instance_cache()
    monkeypatch.setattr(config, "LAKE", "s3://atlas/lake")
    yield endpoint
    server.stop()


def test_lakehouse_on_s3_reads_with_duckdb_and_dask(s3_lake, grid, monkeypatch):
    rows = [record(i, "Tetrix subulata", 8 + i * 0.01, 50 + i * 0.01) for i in range(50)]
    lake.write_partition(harvest.normalise(pd.DataFrame(rows)), "clean", "gbif", "tetrix-subulata")
    n = lake.connect().execute(f"SELECT count(*) FROM read_parquet('{lake.glob('clean')}', hive_partitioning = true)").fetchone()[0]
    assert n == 50
    monkeypatch.setattr(features, "MODELLED", {"tetrix-subulata"})
    out = features.run(scheduler="sync")
    assert out["records_with_climate"] == 50


@mock_aws(config={"batch": {"use_docker": False}})
def test_aws_batch_runner_submits_and_tracks_a_job(monkeypatch):
    iam = boto3.client("iam", region_name="eu-central-1")
    role = iam.create_role(RoleName="batch", AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": []}))
    ec2 = boto3.client("ec2", region_name="eu-central-1")
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.0.0/24")["Subnet"]["SubnetId"]
    sg = ec2.create_security_group(GroupName="batch", Description="batch", VpcId=vpc)["GroupId"]
    batch = boto3.client("batch", region_name="eu-central-1")
    env = batch.create_compute_environment(
        computeEnvironmentName="atlas", type="MANAGED", serviceRole=role["Role"]["Arn"],
        computeResources={"type": "FARGATE", "maxvCpus": 4, "subnets": [subnet], "securityGroupIds": [sg]})
    batch.create_job_queue(jobQueueName="atlas", priority=1, state="ENABLED",
                           computeEnvironmentOrder=[{"order": 1, "computeEnvironment": env["computeEnvironmentArn"]}])
    batch.register_job_definition(jobDefinitionName="atlas-sdm", type="container",
                                  containerProperties={"image": "range-atlas-worker", "vcpus": 2, "memory": 4096,
                                                       "command": ["python", "-m", "pipeline.worker"]})
    monkeypatch.setenv("RANGE_ATLAS_BATCH_QUEUE", "atlas")
    monkeypatch.setenv("RANGE_ATLAS_BATCH_JOB_DEFINITION", "atlas-sdm")

    stored = {}

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            stored["sql"], stored["params"] = sql, params
            return self

        def fetchone(self):
            return {"id": 1, "species": "tetrix-subulata", "status": stored["params"][0]}

    monkeypatch.setattr(runner.db, "connect", lambda: FakeConn())
    r = runner.AwsBatchRunner(client=batch)
    r.submit(1, "tetrix-subulata")
    job_id = stored["params"][0]
    job = batch.describe_jobs(jobs=[job_id])["jobs"][0]
    assert job["jobName"] == "range-atlas-run-1"
    assert job["container"]["command"] == ["python", "-m", "pipeline.worker", "tetrix-subulata"]

    refreshed = r.refresh({"id": 1, "species": "tetrix-subulata", "status": "queued", "external_id": job_id})
    assert refreshed["status"] in runner.BATCH_STATES.values()
    assert r.refresh({"id": 1, "status": "succeeded", "external_id": job_id})["status"] == "succeeded"
