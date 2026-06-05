# Generated Artifacts

DevSecOps Pipeline Kit separates source configuration from generated helper
artifacts. This keeps the CLI product predictable: users edit or create local
configuration, then the CLI renders deterministic files for Terraform, GitHub,
and operator checklists.

## Ownership Rules

| File | Owner | Commit? | How to change it |
| --- | --- | --- | --- |
| `.devsecops-pipeline.toml` | CLI-managed local source config | No | Use `devsecops config new`, `devsecops config reset`, `devsecops set`, `devsecops preset apply`, `devsecops compose`, or a careful manual edit followed by `devsecops config validate`. |
| `terraform/generated.auto.tfvars` | CLI-owned generated artifact | No | Update `.devsecops-pipeline.toml`, then run `devsecops render`. |
| `dist/devsecops/backend.tf` | CLI-owned generated template | No | Update backend settings, then run `devsecops render`. Copy or adapt into `terraform/backend.tf` only after review. |
| `dist/devsecops/github-variables.env` | CLI-owned generated helper | No | Update `.devsecops-pipeline.toml`, then run `devsecops render`. |
| `dist/devsecops/github-setup.sh` | CLI-owned generated helper | No | Update `.devsecops-pipeline.toml`, then run `devsecops render` or `devsecops github-setup --write`. |
| `dist/devsecops/setup-checklist.md` | CLI-owned generated checklist | No | Update `.devsecops-pipeline.toml`, then run `devsecops render`. |
| `dist/devsecops/readiness-report.md` | CLI-owned generated report | No | Run `devsecops report` after changing config or environment state. |
| `.devsecops/snapshots/` | CLI-owned local recovery data | No | Created automatically before CLI-owned files are overwritten. Inspect with `devsecops snapshots`. |

## CLI-Owned Header

Rendered files include a header that identifies them as CLI-owned. The header
means:

* Do not edit the generated file directly for durable changes.
* Change `.devsecops-pipeline.toml` or the relevant external system instead.
* Regenerate the artifact with the command named in the header.
* Review generated files before copying, running, or applying anything that can
  mutate GitHub or AWS.

## Source Config Versus Generated Output

`.devsecops-pipeline.toml` is local source configuration. It includes
`schema_version = 1` so future versions can migrate deliberately. It should
contain project settings, environment settings, feature flags, and backend
names. It must not contain AWS credentials, GitHub tokens, Snyk tokens, private
keys, or other secrets.

Generated artifacts are outputs of that source config. They may contain
non-secret values such as project names, regions, repository variable values,
and placeholder commands for secrets. They are ignored by Git because they are
environment-specific and can be regenerated.

## Backend Template Exception

The repository contains `terraform/backend.tf` as the Terraform backend file
used by the root module. The CLI also renders `dist/devsecops/backend.tf` as a
reviewable template. Treat the rendered file as a helper, not as an automatic
replacement for the tracked Terraform backend file.

When you are ready to switch backend values, review the rendered template and
copy or adapt the backend block intentionally.

## Rollback Boundary

`devsecops rollback` restores only local CLI-owned files from snapshots. It
does not roll back a deployed Lambda function in AWS.

Cloud deployment rollback is handled by the GitHub Actions production workflow
when a deployment or enabled validation step fails. Keep these two rollback
paths separate:

* Local rollback: restore CLI-managed config and generated files.
* Cloud rollback: restore the previous Lambda image during a failed production
  deployment workflow.
