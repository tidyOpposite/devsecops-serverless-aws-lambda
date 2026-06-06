# Troubleshooting Guide

Start with the CLI. It is the primary troubleshooting surface for this project;
Terraform, GitHub Actions, AWS, and scanner commands are the execution layer
behind the CLI.

## Start Here

Run:

```bash
devsecops dashboard --mode compact
devsecops dashboard --watch --interval 10
devsecops tui
devsecops readiness
devsecops doctor
devsecops doctor --deep
```

Use `[i] details` from the main menu or `devsecops readiness` to see only the
checks that block 100% readiness and the concrete fix for each one. Use
`devsecops doctor` when you need the full local check list. The dashboard
groups readiness into Local, Terraform, GitHub, AWS, Security, and Deployment
scores.

`devsecops tui` uses optional Rich/Textual dependencies. Without them, it falls
back to the compact dashboard. Install the extra with `pipx install ".[tui]"`
or `python3 -m pip install -e ".[tui]"`.

Generate a shareable report:

```bash
devsecops report --deep
```

The report is written to `dist/devsecops/readiness-report.md`.

## CLI Installation And Navigation

### `devsecops` command is not found

Install the root package with `pipx`:

```bash
pipx install .
devsecops menu
```

For development, install it in editable mode:

```bash
python3 -m pip install -e .
devsecops menu
```

Without installing, run the package module from the repository:

```bash
PYTHONPATH=cli python3 -m devsecops_cli menu
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

### Local config is missing

Create a clean local source config:

```bash
devsecops config new --preset balanced
devsecops config validate
```

For a no-write preview first:

```bash
devsecops dry-run --preset balanced
```

### Project files are missing

Readiness expects the tracked Terraform module and GitHub workflow files to be
present:

```text
terraform/main.tf
terraform/modules/lambda/main.tf
.github/workflows/deploy.yml
```

Restore those files from the repository before running `devsecops render`,
Terraform plans, or production workflow dispatch.

### Config validation fails

Run config validation and fix the reported key:

```bash
devsecops config validate
devsecops config show --format toml
devsecops config set <key> <value>
```

Then rerun:

```bash
devsecops config validate
devsecops render --dry-run
```

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

### Lambda image URI is missing or invalid

Set an immutable Lambda container image:

```bash
devsecops preflight --image-uri 123456789012.dkr.ecr.us-east-1.amazonaws.com/app:sha-a1b2c3
devsecops set lambda_image_uri 123456789012.dkr.ecr.us-east-1.amazonaws.com/app:sha-a1b2c3 --render
devsecops readiness
```

The production workflow rejects `latest` and `bootstrap` because rollback and
auditability depend on stable image identity.

If preflight reports a region mismatch, publish or select an image in the same
region as `aws_region`, then rerun:

```bash
devsecops preflight --image-uri <immutable-ecr-image-uri>
```

See [Bring your own Lambda image](bring-your-own-image.md) for the image
contract.

### Update one setting without rerunning the wizard

Use `devsecops set` with a dotted config key:

```bash
devsecops set backend.bucket my-state-bucket --render
devsecops set enable_dast true
devsecops set environments.prod.lambda_memory_size 2048
devsecops validate-config
```

### Rebuild config and generated outputs from controls

Run the composer when policy toggles drift across local config and GitHub
helpers:

```bash
devsecops compose
```

It updates `.devsecops-pipeline.toml`, renders Terraform/GitHub helper
artifacts, and rewrites `dist/devsecops/readiness-report.md`.

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

### GitHub repository variables or secrets are missing

Generate and review setup commands:

```bash
devsecops github setup --write
```

Apply repository variables and provided secrets:

```bash
devsecops github setup --apply \
  --deploy-role-arn arn:aws:iam::<account-id>:role/<deploy-role> \
  --plan-role-arn arn:aws:iam::<account-id>:role/<plan-role>
```

Then verify:

```bash
devsecops doctor github --strict
```

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

## AWS Diagnostics

### Check AWS account and deployed resources

Run:

```bash
devsecops aws-doctor --environment prod
```

AWS Doctor checks:

* AWS CLI installation and `sts get-caller-identity`;
* Terraform backend S3 bucket;
* DynamoDB lock table;
* ECR repository for the selected environment;
* Lambda execution IAM role;
* Lambda function;
* API Gateway HTTP API;
* Lambda CloudWatch log group;
* configured `lambda_image_uri` existence when it points to ECR.

Before the first deploy, ECR, Lambda execution role, Lambda, API Gateway, and
log group checks can return `WARN not deployed yet`. That is expected. Fix
identity/backend warnings first, then run the manual deploy flow.

Use `--strict` when AWS Doctor runs in automation and any scored warning should
fail the command:

```bash
devsecops aws-doctor --environment prod --strict
```

### AWS Doctor cannot inspect resources

Install and configure AWS CLI:

```bash
aws sts get-caller-identity
devsecops aws-doctor --environment prod
```

If identity works but resource checks fail, confirm `aws_region`,
`backend.region`, and the selected `--environment` match the account where the
pipeline was deployed.

## Terraform Diagnostics

### Terraform CLI is not found

Install Terraform and confirm it is on `PATH`:

```bash
terraform version
devsecops doctor local --format compact
```

### Terraform validation fails

Run the same local validation command used by the CLI and CI:

```bash
terraform -chdir=terraform init -backend=false -input=false -no-color
terraform -chdir=terraform validate -no-color
```

Fix the reported Terraform file, then rerun:

```bash
devsecops doctor local --deep --format compact
```

## Git Diagnostics

### Git or branch readiness fails

Production deploy workflow dispatch must run from `main`:

```bash
git branch --show-current
git switch main
devsecops readiness
```

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
