# Troubleshooting Guide

Start with the CLI. It is the primary troubleshooting surface for this project;
Terraform, GitHub Actions, AWS, and scanner commands are the execution layer
behind the CLI.

## Start Here

Run:

```bash
devsecops readiness
devsecops doctor
devsecops doctor --deep
```

Use `[i] details` from the main menu or `devsecops readiness` to see only the
checks that block 100% readiness and the concrete fix for each one. Use
`devsecops doctor` when you need the full local check list.

Generate a shareable report:

```bash
devsecops report --deep
```

The report is written to `dist/devsecops/readiness-report.md`.

## CLI Installation And Navigation

### `devsecops` command is not found

Run the script directly:

```bash
python3 cli/devsecops_cli.py menu
```

Or install the local CLI entry point:

```bash
python3 -m pip install -e cli
devsecops menu
```

### I entered a menu section by mistake

Input sections can be cancelled with `b`, `back`, `0`, or `cancel`. In the
configuration wizard, cancellation returns to the main menu without saving.

### I changed local config and want to undo it

Inspect snapshots:

```bash
devsecops snapshots
devsecops snapshots --show 1
```

Preview and apply rollback:

```bash
devsecops rollback --last --dry-run
devsecops rollback --to <number-or-id>
```

Rollback restores only CLI-owned local files such as `.devsecops-pipeline.toml`,
`terraform/generated.auto.tfvars`, and generated files under `dist/devsecops/`.

## CLI Configuration

### Readiness says backend bucket is missing

Set a real backend bucket through the CLI and render generated artifacts:

```bash
devsecops set backend.bucket my-state-bucket --render
devsecops readiness
```

Then plan or apply the bootstrap stack:

```bash
devsecops bootstrap
devsecops bootstrap --apply
```

`devsecops bootstrap` runs a Terraform plan by default. Use
`devsecops bootstrap --apply` only when the target AWS account and backend
names are correct.

### Readiness says Lambda image URI is missing

Set an immutable Lambda container image:

```bash
devsecops set lambda_image_uri 123456789012.dkr.ecr.us-east-1.amazonaws.com/app:sha-a1b2c3 --render
devsecops readiness
```

The production workflow rejects `latest` and `bootstrap` because rollback and
auditability depend on stable image identity.

### Update one setting without rerunning the wizard

Use `devsecops set` with a dotted config key:

```bash
devsecops set backend.bucket my-state-bucket --render
devsecops set enable_dast true
devsecops set environments.prod.lambda_memory_size 2048
devsecops validate-config
```

### Generated files look stale

Render again:

```bash
devsecops render
devsecops readiness
```

Generated files are intentionally ignored by Git. They are local bridge files
between the CLI and Terraform/GitHub setup.

## GitHub Diagnostics

### Generate GitHub setup commands

Run:

```bash
devsecops github-setup --write
```

Review `dist/devsecops/github-setup.sh` before running it. It contains
placeholder values for role ARNs and optional secrets.

### GitHub doctor cannot inspect the repository

Install and authenticate GitHub CLI:

```bash
gh auth login
devsecops gh-doctor
```

`devsecops gh-doctor` checks repository variables and secret names, but it
never prints secret values.

### Branch protection doctor reports missing checks

Run:

```bash
devsecops branch-doctor
```

The expected required checks are:

* `Security and Terraform Validate`
* `Terraform Plan`

Configure them in the GitHub branch protection settings for `main` after the
checks have run at least once.

### Actions status cannot show workflow runs

Run:

```bash
gh auth login
gh run list
devsecops actions-status
```

`actions-status` uses `gh run list` and, for failed runs, `gh run view` to show
failed job names.

## GitHub OIDC

### `Not authorized to perform sts:AssumeRoleWithWebIdentity`

Check the IAM role trust policy:

* `token.actions.githubusercontent.com:aud` must equal `sts.amazonaws.com`.
* For deploys, `sub` should include `repo:<owner>/<repo>:ref:refs/heads/main`.
* For PR plans, use a separate lower-privilege role and allow the relevant
  pull request subject pattern only if you accept that risk.
* Confirm the workflow has `permissions: id-token: write`.

Then re-run:

```bash
devsecops gh-doctor
devsecops branch-doctor
```

### PR plan cannot access Terraform backend

The plan role needs S3 access to the state bucket and DynamoDB access to the
lock table. It also needs read permissions for resources Terraform refreshes.
Use `AWS_policy.md` as the IAM review guide, then re-run the failing workflow.

## Terraform Backend

### `S3 bucket does not exist`

Prefer the CLI bootstrap flow:

```bash
devsecops set backend.bucket <globally-unique-state-bucket> --render
devsecops bootstrap --apply
```

Manual fallback:

```bash
cd terraform/bootstrap
terraform init
terraform apply -var="state_bucket_name=<globally-unique-state-bucket>"
```

Then copy or adapt `dist/devsecops/backend.tf` into `terraform/backend.tf`.

### `Error acquiring the state lock`

Another Terraform run may be active. Check:

```bash
devsecops actions-status
```

Then inspect GitHub Actions concurrency and the DynamoDB item in the lock table.
Only use `terraform force-unlock` after confirming no apply is running.

### Wrong environment is planned

Use the CLI plan wrapper:

```bash
devsecops plan dev --create-workspace
```

Manual fallback:

```bash
terraform -chdir=terraform workspace show
terraform -chdir=terraform workspace select dev
```

The root module rejects unsupported workspaces. Valid values are `dev`,
`staging`, and `prod`.

## Lambda Image Deployment

### Deploy fails because `LAMBDA_IMAGE_URI` is empty

Set the value through the CLI, render, then apply GitHub setup:

```bash
devsecops set lambda_image_uri <immutable-image-uri> --render
devsecops gh-setup --apply \
  --deploy-role-arn <deploy-role-arn> \
  --plan-role-arn <plan-role-arn>
devsecops gh-doctor
```

### Lambda image does not exist

Publish the workload image before the production deploy workflow runs. If you
use the ECR repository created by this Terraform stack, create the repository
first with a plan/apply or publish the image from an upstream workflow after ECR
exists.

### Snyk cannot scan a private ECR image

Confirm the deploy role can call `ecr:GetAuthorizationToken`,
`ecr:BatchGetImage`, `ecr:BatchCheckLayerAvailability`, and
`ecr:GetDownloadUrlForLayer` for the repository. Also confirm the image URI
points to the same account or to a registry the runner can authenticate to.

### Rollback did not run

Pipeline rollback requires an existing previous Lambda image URI. On the first
ever deployment there is nothing to roll back to. This is separate from CLI
snapshot rollback, which only restores local CLI-owned files.

### Health check returns 500

Health validation is optional and runs only when
`ENABLE_HTTP_VALIDATION=true`. If enabled, verify:

* The workload image handles API Gateway HTTP API events.
* `GET /health` returns a successful HTTP response.
* CloudWatch Logs for the Lambda function do not show handler, permission, or
  environment errors.
* The Lambda execution role has the AWS permissions required by the workload.

## Scanners

### Snyk steps are skipped

Set `SNYK_TOKEN` in repository secrets. Without it, Terraform validate and
Trivy still run.

### Trivy flags an IaC finding

Review whether the finding is a real infrastructure risk. If a suppression is
needed, keep it scoped to the specific resource and document the accepted risk
in code or `SECURITY.md`.

### OWASP ZAP fails deploy validation

Review the generated `zap-baseline-prod` artifact. Warnings are reported but do
not fail the job by default. Fail-level findings should be triaged before
rerunning the deployment.
