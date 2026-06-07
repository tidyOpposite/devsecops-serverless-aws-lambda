# Changelog

All notable changes to this project are documented here. The project uses
semantic versioning.

## Unreleased

No unreleased changes.

## v0.6.1 - 2026-06-07

### Added

* README development-status callout and repository badges that make the early
  alpha stage, active development cadence, and evolving security controls clear
  to new visitors.

### Security

* GitHub Actions now use narrower job-level permissions and checkout steps no
  longer persist repository credentials.
* Terraform plan workflows require `AWS_PLAN_ROLE_TO_ASSUME_ARN` and no longer
  fall back to the deployment role.
* Pull requests from forked repositories no longer run the AWS-backed Terraform
  plan job.
* Terraform now rejects mutable Lambda image tags and prevents planning or
  applying the Lambda workload without an explicit immutable image URI.
* Workload and access-log S3 buckets now deny non-TLS requests with bucket
  policies.

## v0.6.0 - 2026-06-07

### Added

* Stronger GitHub Actions status output with failed step summaries, strict
  failure mode, next actions, and linked runbooks.
* `devsecops health` for validating the deployed `/health` endpoint outside
  GitHub Actions.
* `devsecops aws outputs` for read-only inspection of deployed Lambda, API
  Gateway, and CloudWatch output values.
* Milestone 5 runbooks for failed Terraform plan, failed apply, failed
  validation, missing images, and failed deployment rollback.

### Changed

* `devsecops readiness --strict` now supports CI-friendly non-zero exits when
  readiness is below 100%.
* Local snapshot restore output now explicitly distinguishes local CLI-owned
  file recovery from cloud Lambda deployment rollback.

## v0.5.0 - 2026-06-06

### Added

* First successful pipeline guide with exact commands and expected output.
* Bring-your-own Lambda image documentation and a separate workload template
  contract that keeps workload source outside this repository.
* `devsecops preflight` for local Lambda image URI shape, immutability, and
  region checks.
* `devsecops dry-run` for a no-write, no-AWS-credentials first-success
  preview.
* `devsecops render --dry-run` for previewing generated artifact changes
  without writing files.
* Troubleshooting links in readiness next actions.

## v0.4.1 - 2026-06-06

### Fixed

* Avoided the `RuntimeWarning` emitted by `python -m devsecops_cli.main` by
  making package-level `main` loading lazy.
* Added tests that keep CLI, package, and root/CLI package versions aligned.

### Changed

* CI jobs now have explicit timeouts to avoid stalled package or Terraform
  checks holding runners indefinitely.
* CI now builds root package artifacts before the install smoke test, so
  packaging failures are caught before release publication.
* Release packaging now installs `setuptools` and `wheel` explicitly before
  building artifacts.
* Roadmap implementation statuses now point to released versions instead of
  stale `Unreleased` wording.

## v0.4.0 - 2026-06-06

### Added

* Golden tests for generated Terraform tfvars, GitHub variable files, and
  GitHub setup scripts.
* End-to-end CLI test that runs `config new`, `config validate`, `render`, and
  `report` in a temporary repository.
* Root package install smoke test that verifies the `devsecops` console
  command.
* Mocked failure tests for missing `gh`, missing AWS credentials, unavailable
  Terraform, and snapshot rollback safety for CLI-owned versus user-owned
  files.
* CI workflow for CLI tests plus generated Terraform `fmt`, `init`, and
  `validate` checks.

### Changed

* Generated `terraform/generated.auto.tfvars` now uses Terraform fmt-compatible
  attribute alignment.

## v0.3.0 - 2026-06-06

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
* Top-level CLI help now focuses on grouped primary commands while preserving
  legacy flat aliases.
* Main menu navigation now clears the terminal when entering sections and when
  returning to the menu, and menu items are grouped into sections with up to
  three actions per row.

### Added

* Product roadmap, command inventory, and generated-artifact ownership
  documentation for the Milestone 0 product contract.
* Schema-versioned clean configuration workflow with `devsecops config new`,
  `show`, `validate`, `diff`, `reset`, and `schema`.
* Config schema export, canonical config diffing, and a migration scaffold for
  future config schema versions.
* Grouped CLI UX for `doctor`, `github`, `terraform`, and `snapshot`
  workflows, plus `config set` as the primary config edit command.
* JSON and compact output modes for readiness and doctor workflows used by
  automation.
* Stable CLI exit-code constants for validation, missing tools, auth failures,
  unexpected runtime errors, and interrupts.
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
