# Troubleshooting Guide

## GitHub OIDC

### `Not authorized to perform sts:AssumeRoleWithWebIdentity`

Check the IAM role trust policy:

* `token.actions.githubusercontent.com:aud` must equal `sts.amazonaws.com`.
* For deploys, `sub` should include `repo:<owner>/<repo>:ref:refs/heads/main`.
* For PR plans, use a separate lower-privilege role and allow the relevant
  pull request subject pattern only if you accept that risk.
* Confirm the workflow has `permissions: id-token: write`.

### PR plan cannot access Terraform backend

The plan role needs S3 access to the state bucket and DynamoDB access to the
lock table. It also needs read permissions for resources Terraform refreshes.

## Terraform Backend

### `S3 bucket does not exist`

Run the bootstrap stack first:

```bash
cd terraform/bootstrap
terraform init
terraform apply -var="state_bucket_name=<globally-unique-state-bucket>"
```

Then copy the output into `terraform/backend.tf`.

### `Error acquiring the state lock`

Another Terraform run may be active. Check GitHub Actions concurrency and the
DynamoDB item in the lock table. Only use `terraform force-unlock` after
confirming no apply is running.

### Wrong environment is planned

Run:

```bash
terraform workspace show
terraform workspace select dev
```

The root module rejects unsupported workspaces. Valid values are `dev`,
`staging`, and `prod`.

## ECR And Docker

### `ImageTagAlreadyExistsException`

ECR is intentionally immutable. Do not push `latest`. The workflow tags images
as `sha-<commit>`.

### Lambda image architecture mismatch

The Dockerfile pins `linux/amd64` and Terraform sets Lambda `x86_64`. If you
change Lambda to ARM64, replace the FFmpeg download and base image digest too.

### Docker build cannot pull `public.ecr.aws/lambda/python`

Check outbound network access from the runner and Docker Hub/ECR Public rate
or availability issues. The base image is pinned by digest, so update the
digest intentionally when moving to a newer Lambda runtime.

## Lambda Deploy And Rollback

### First deploy fails because Lambda image does not exist

The workflow solves this by applying only KMS/ECR first, building and pushing
the image, then applying the full workload with `lambda_image_uri`. If running
locally, follow the same sequence.

### Rollback did not run

Rollback requires an existing previous Lambda image URI. On the first ever
deployment there is nothing to roll back to.

### Health check returns 500

Check CloudWatch Logs for the Lambda function and verify:

* `OUTPUT_BUCKET_NAME` is set.
* Lambda execution role can use the KMS key.
* The output bucket exists in the selected workspace environment.
* The Docker image contains `ffmpeg` and `ffprobe`.

## S3 Frontend

### Frontend returns 403

The frontend bucket is intentionally public for S3 website hosting. Confirm:

* Bucket public access block allows public bucket policies for this bucket.
* The bucket policy allows `s3:GetObject` on `bucket/*`.
* Objects were synced to the bucket after a successful backend deploy.
* You are using the website endpoint, not the REST API endpoint.

### Browser cannot reach API

Check that `frontend/script.js` in the deployed artifact has the API URL
substituted. The workflow writes to `dist/frontend/script.js` before S3 sync,
not to the repository source file.

## Scanners

### Snyk steps are skipped

Set `SNYK_TOKEN` in repository secrets. Without it, Bandit, Terraform validate,
Trivy, and ZAP still run.

### Trivy flags the public frontend bucket

The S3 website bucket is intentionally public for the demo. The Terraform code
contains scoped `trivy:ignore` comments for that managed exception. Do not copy
that exception to private data buckets.

### OWASP ZAP fails deploy validation

Review the generated `zap-baseline-prod` artifact. Warnings are reported but
do not fail the job by default. Fail-level findings should be triaged before
rerunning the deployment.
