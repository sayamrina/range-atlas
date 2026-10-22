# Running model jobs on AWS Batch

The API can hand model runs to AWS Batch instead of running them in its own process:

```
RANGE_ATLAS_RUN_BACKEND=aws-batch
RANGE_ATLAS_BATCH_QUEUE=<job queue>
RANGE_ATLAS_BATCH_JOB_DEFINITION=range-atlas-sdm
RANGE_ATLAS_LAKE=s3://<bucket>/lake
```

1. Build the image from the repository root and push it to ECR.
2. Register `job-definition.json` (fill in account, bucket and roles): `aws batch register-job-definition --cli-input-json file://job-definition.json`.
3. The job role needs read/write access to the lake prefix only.

Each run becomes one Batch job (`range-atlas-run-<id>`) that executes `python -m pipeline.worker <species>`,
writes the model surface and result to the lake on S3, and the API reads the job state with `DescribeJobs`.

**Status:** the submit and status code is tested against moto (`tests/test_aws.py`). It has not been run on a
real AWS account; the live demo uses the static export on GitHub Pages instead.
