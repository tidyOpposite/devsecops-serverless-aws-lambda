# Roadmap

DevSecOps Pipeline Kit is currently an alpha-stage CLI-first product. The
core direction is promising, but the project should be treated as a developing
product rather than a finished platform. This roadmap turns the current
Terraform/GitHub/AWS reference kit into a polished `devsecops` CLI experience.

## Product Direction

The CLI is the product. Terraform modules, GitHub Actions workflows, AWS
resources, scanners, and generated files are the execution layer behind it.

The product promise:

* Create a clean, reviewable local pipeline configuration.
* Validate whether the pipeline is ready for a real deployment.
* Render deterministic Terraform and GitHub helper artifacts.
* Diagnose local, GitHub, AWS, security, and deployment gaps.
* Help operators recover safely from CLI-managed configuration changes.

## Current Assessment

Strengths:

* Installable dependency-free CLI with `pipx install .`.
* Local setup wizard, presets, composer, dashboard, readiness checks, reports,
  snapshots, and rollback for CLI-managed files.
* GitHub and AWS diagnostics through `gh-doctor`, `branch-doctor`,
  `actions-status`, and `aws-doctor`.
* Security-aware deployment model with immutable Lambda image input, GitHub
  OIDC, Terraform validation, IaC scanning, optional Snyk, optional HTTP
  validation, optional OWASP ZAP baseline, and production rollback.
* Clear repository decision to avoid bundling sample application code and to
  consume a prebuilt `LAMBDA_IMAGE_URI`.

Gaps that make the project feel raw:

* The CLI has many useful commands, but the product workflow is still spread
  across top-level commands and aliases.
* Configuration lifecycle commands exist partially, but there is no explicit
  clean-config workflow with schema versioning, diffing, reset, or migration.
* Tests cover important helpers, but there is limited end-to-end verification
  of install, command output, generated artifacts, and Terraform/GitHub files.
* Generated file ownership needs a stronger contract so users know what is
  safe to edit and what should be regenerated.
* Documentation explains the pieces, but the first-success path should be more
  direct and measurable.
* The product has not yet proven a full happy path in a documented real AWS
  account walkthrough.

## Roadmap Themes

1. Productize the configuration lifecycle.
2. Consolidate and simplify the CLI user experience.
3. Make validation and generated artifacts trustworthy.
4. Prove the first successful deployment path.
5. Harden operational workflows for real users.
6. Improve release engineering and install confidence.
7. Keep security controls visible, testable, and explainable.

## Milestone 0: Stabilize The Product Contract

Target: immediate cleanup before the next feature release.

Goals:

* Define the product as a CLI that creates, validates, renders, and diagnoses a
  secure AWS Lambda delivery pipeline.
* Keep Terraform, GitHub Actions, AWS, and scanners as transparent execution
  layers rather than hidden magic.
* Make the supported user journey obvious from the README and `--help`.

Deliverables:

* Roadmap document.
* README link to the roadmap.
* Short product-positioning section in README if the existing introduction
  becomes too broad.
* Command inventory that marks each command as stable, experimental, alias, or
  internal-support.
* Documentation for generated artifact ownership.

Acceptance criteria:

* A new user can understand the product boundary in under five minutes.
* The README quick start and `devsecops --help` describe the same path.
* Generated files are clearly labeled as CLI-owned.

## Milestone 1: Clean Configuration Workflow

Target release: `v0.3.0`.

Problem: `init`, `set`, `preset`, `compose`, `validate-config`, and `render`
are useful, but the clean configuration lifecycle is implicit.

Planned command shape:

```bash
devsecops config new --preset balanced
devsecops config show --format toml
devsecops config validate
devsecops config diff
devsecops config reset --preset minimal
devsecops config schema
devsecops render
```

Deliverables:

* Add a `schema_version` field to `.devsecops-pipeline.toml`.
* Add deterministic clean-config generation with no secrets and no generated
  output mixed into source configuration.
* Add `config new`, `config reset`, `config diff`, and `config schema`.
* Keep existing commands working while steering users toward the grouped
  `config` workflow.
* Add idempotency tests for clean config generation and rendering.
* Add a migration scaffold for future config schema versions.

Acceptance criteria:

* A clean config can be generated non-interactively.
* Running config generation and render twice produces no unexpected diff.
* Validation catches bad values before Terraform or GitHub commands run.
* No AWS secrets, GitHub secrets, tokens, or credentials are written to the
  local config.

## Milestone 2: CLI UX Consolidation

Target release: `v0.4.0`.

Problem: the command surface is powerful but too wide for a first-time user.

Planned improvements:

* Organize commands into clear groups:
  * `config`: create, show, set, validate, diff, reset, schema.
  * `render`: generate Terraform and GitHub helper artifacts.
  * `doctor`: local, GitHub, AWS, branch, actions, and deep diagnostics.
  * `terraform`: plan and bootstrap helpers.
  * `github`: setup and status helpers.
  * `snapshot`: list, inspect, and restore CLI-managed files.
* Keep top-level compatibility aliases where useful, but mark legacy aliases in
  help output.
* Standardize output modes: human, compact, and JSON where automation needs it.
* Define stable exit codes for validation failure, missing external tools,
  authentication failure, and unexpected runtime errors.
* Improve `devsecops menu` so it follows the same command groups.

Acceptance criteria:

* `devsecops --help` is short enough to scan.
* Every command has an example in help or documentation.
* JSON output exists for readiness and doctor workflows used by automation.
* Golden tests protect important help text and command output.

## Milestone 3: Trustworthy Generation And Tests

Target release: `v0.5.0`.

Problem: a CLI that generates infrastructure files must prove that generation
is deterministic, reviewable, and valid.

Deliverables:

* Golden tests for generated `terraform/generated.auto.tfvars`.
* Golden tests for generated GitHub setup scripts and variable files.
* Terraform formatting and validation checks in CI.
* CLI install smoke test from the root package.
* End-to-end test that runs `config new`, `validate`, `render`, and `report`
  in a temporary repository.
* Mocked tests for missing `gh`, missing AWS credentials, and unavailable
  Terraform.
* Snapshot and rollback tests for overwritten CLI-owned files.

Acceptance criteria:

* Generated artifacts have stable diffs across repeated runs.
* CI fails if generated Terraform is syntactically invalid.
* A package install smoke test proves the `devsecops` console command works.
* Rollback tests prove user-owned files are not restored or overwritten by
  mistake.

## Milestone 4: First Successful Pipeline Path

Target release: `v0.6.0`.

Problem: users need a direct, measurable route from install to one successful
pipeline run.

Deliverables:

* A "first successful pipeline" guide with exact commands and expected
  outputs.
* A documented bring-your-own-image path for `LAMBDA_IMAGE_URI`.
* A separate example workload repository or template, not bundled into this
  repository.
* Preflight checks for Lambda container image shape, immutability, and region
  mismatch.
* A dry-run mode that explains what would be rendered or checked before files
  change.
* Troubleshooting entries linked from readiness and doctor failures.

Acceptance criteria:

* A user can complete a documented dry run without AWS credentials.
* A user with AWS, GitHub, and a valid Lambda image can follow one guide to a
  successful production workflow dispatch.
* The CLI explains the next action whenever readiness is below 100%.

## Milestone 5: Operational Reliability

Target release: `v0.7.0`.

Problem: the project should help users operate the pipeline after initial
setup, not only generate it.

Deliverables:

* Stronger `actions-status` output with failed step summaries and next actions.
* Health validation command that can run outside the GitHub workflow.
* AWS output inspection command for deployed Lambda/API Gateway resources.
* Clear separation between local config rollback and deployed Lambda rollback.
* Runbooks for failed Terraform plan, failed apply, failed validation, missing
  image, and failed rollback.
* Optional strict mode for CI-friendly `doctor` and `readiness` checks.

Acceptance criteria:

* A failed deployment can be diagnosed from CLI output and linked runbooks.
* The user can distinguish local generated-file recovery from cloud deployment
  rollback.
* Operational commands are safe by default and require explicit confirmation
  for mutating actions.

## Milestone 6: Security And Policy Maturity

Target release: `v0.8.0`.

Problem: security controls should be visible as product features, not just
Terraform or workflow details.

Deliverables:

* Control catalog that maps CLI options to Terraform, GitHub, AWS, and scanner
  behavior.
* Policy preset comparison table.
* Strict validation for production-risky values such as wildcard CORS,
  disabled approval gates, mutable image tags, and missing validation.
* Optional machine-readable report for audit evidence.
* Clear least-privilege guidance for deploy and plan roles.
* Security regression tests for generated workflows and Terraform variables.

Acceptance criteria:

* Each preset has a documented security posture.
* `devsecops explain <control>` maps a control to concrete generated behavior.
* Audit output can be attached to a pull request or release record.

## Milestone 7: Distribution And Adoption Readiness

Target release: `v0.9.0`.

Problem: users should be able to install, upgrade, and trust releases without
reading the source tree.

Deliverables:

* Release checklist with version bump, changelog, tests, build, and tag steps.
* Published package path decision.
* Upgrade guide for config schema changes.
* Shell completion for common shells.
* Signed or checksummed release artifacts.
* Compatibility matrix for supported operating systems, Python versions,
  Terraform versions, GitHub CLI versions, and AWS CLI versions.

Acceptance criteria:

* A user can install the latest release from documented commands.
* Upgrade behavior is documented before config schema changes ship.
* Release artifacts can be verified.

## Version 1.0 Criteria

The project should not be called stable until all of these are true:

* CLI command groups and core flags are stable.
* Config schema versioning and migration behavior are implemented.
* Clean config generation, validation, rendering, and readiness are covered by
  end-to-end tests.
* Generated Terraform and GitHub artifacts are deterministic and documented.
* At least one full AWS/GitHub production deployment walkthrough is documented.
* Local rollback cannot overwrite files outside the CLI-owned file set.
* Security controls have documented behavior and regression tests.
* Release and upgrade flows are documented.
* Known limitations are explicit rather than hidden in implementation details.

## Post-1.0 Backlog

These ideas may be useful later, but they should not distract from making the
CLI product stable first:

* Plugin system for additional scanners.
* Additional cloud providers.
* Kubernetes deployment target.
* Web UI.
* Authenticated DAST workflows.
* Organization-wide GitHub policy management.
* Remote state inspection beyond the supported AWS backend.

## Near-Term Priority Order

If the project feels too raw, improve it in this order:

1. Make clean config generation a first-class workflow.
2. Simplify the command surface around grouped commands.
3. Add golden and end-to-end tests for generated files.
4. Write the first-success guide.
5. Prove the guide against a real AWS/GitHub setup.
6. Harden operational diagnostics and rollback documentation.
