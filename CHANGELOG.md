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
* GitHub Actions no longer run on direct pushes to `main`; pull requests still
  validate automatically, and production deploys are manual workflow dispatch
  runs.
* Documentation now positions the terminal CLI as the primary product surface
  and Terraform/GitHub Actions as the execution layer behind it.
* CLI package descriptions and top-level help now describe the same
  CLI-product first-run path as the README.
* Recommended first-run workflow now uses `devsecops config new`,
  `config validate`, and `config diff` before rendering.

### Added

* Product roadmap, command inventory, and generated-artifact ownership
  documentation for the Milestone 0 product contract.
* Schema-versioned clean configuration workflow with `devsecops config new`,
  `show`, `validate`, `diff`, `reset`, and `schema`.
* Config schema export, canonical config diffing, and a migration scaffold for
  future config schema versions.
* Dependency-free terminal CLI with main menu, setup wizard, readiness report,
  render command, Terraform plan wrapper, backend bootstrap helper, and control
  explanations.
* CLI dashboard, environment table, controls matrix, config validation,
  architecture view, non-interactive `set` command, and unit tests.
* Dashboard watch mode, compact/full terminal layouts, category readiness
  scoring, and visible main-menu readiness gap fixes.
* Optional `devsecops tui` command with Rich rendering when optional UI
  dependencies are installed, and compact dashboard fallback otherwise.
* CLI readiness report export, GitHub setup script generation, environment
  presets, and optional Terraform workspace creation for plans.
* Policy preset profiles now support `preset list`, `preset show <name>`, and
  `preset apply <name>`, with new `enterprise` and `student-demo` profiles.
* Pipeline composer command for choosing Snyk, DAST, health checks, strict
  CORS, production approval environment, and separate AWS plan role controls,
  then regenerating config, helper artifacts, and readiness report.
* Root Python packaging for `pipx install .`, package-based CLI modules, and
  GitHub release wheel/sdist artifacts.
* GitHub CLI integration with `gh-doctor`, `gh-status`, and safe
  `github-setup --apply` support for repository variables and provided
  secrets.
* AWS Doctor CLI command for identity, backend, ECR, Lambda execution role,
  Lambda, API Gateway, CloudWatch log group, and configured ECR image
  diagnostics, with strict mode for automation.
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
