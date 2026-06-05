# Changelog

All notable changes to this project are documented here. The project uses
semantic versioning.

## Unreleased

### Removed

* Bundled sample workload source, local image build files, and browser client.
* Sample-workload documentation for generated media, public web hosting, and
  bundled runtime binaries.

### Changed

* Production deploy now consumes a prebuilt immutable image from
  `LAMBDA_IMAGE_URI`.
* HTTP smoke testing and OWASP ZAP DAST are opt-in repository-variable gates.
* Default project naming now uses `devsecops-pipeline`.
* Documentation now positions the terminal CLI as the primary product surface
  and Terraform/GitHub Actions as the execution layer behind it.

### Added

* Dependency-free terminal CLI with main menu, setup wizard, readiness report,
  render command, Terraform plan wrapper, backend bootstrap helper, and control
  explanations.
* CLI dashboard, environment table, controls matrix, config validation,
  architecture view, non-interactive `set` command, and unit tests.
* CLI readiness report export, GitHub setup script generation, environment
  presets, and optional Terraform workspace creation for plans.
* GitHub CLI integration with `gh-doctor`, `gh-status`, and safe
  `github-setup --apply` support for repository variables and provided
  secrets.
* Branch protection and GitHub Actions diagnostics with `branch-doctor` and
  `actions-status`, including required check validation and failed job
  summaries.
* CLI snapshot and rollback workflow with newest-first restore points, change
  inspection, dry-run preview, confirmation prompt, and safety snapshot before
  restore.
* Menu input sections now support `b`, `back`, `0`, or `cancel` to return
  without saving pending configuration changes.
* Readiness indicator now exposes an `[i] details` shortcut and
  `devsecops readiness` command with concrete fix actions for checks blocking
  100% readiness.

## v0.1.0 - 2026-06-04

### Added

* Multi-environment Terraform support for `dev`, `staging`, and `prod`
  through workspaces.
* Modular Terraform structure for KMS, storage, ECR, Lambda, and API Gateway.
* S3 remote state backend with DynamoDB locking plus a bootstrap stack.
* GitHub Actions PR Terraform plan with PR comments and downloadable plan
  artifacts.
* Production apply/deploy path on merge to `main`.
* Immutable Lambda image deployment.
* Automatic Lambda rollback to the previous image on failed deployment
  validation.
* Optional OWASP ZAP DAST baseline scan after production deployment.
* Reference documentation for architecture, security model, scanner rationale,
  cost estimation, and troubleshooting.

### Changed

* Removed mutable `latest` image publishing from the deployment flow.
* Environment-specific names now include the active workspace.
