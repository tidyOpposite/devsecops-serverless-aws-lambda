# Changelog

All notable changes to this project are documented here. The project uses
semantic versioning.

## v0.1.0 - 2026-06-04

### Added

* Multi-environment Terraform support for `dev`, `staging`, and `prod`
  through workspaces.
* Modular Terraform structure for KMS, storage, ECR, Lambda, and API Gateway.
* S3 remote state backend with DynamoDB locking plus a bootstrap stack.
* GitHub Actions PR Terraform plan with PR comments and downloadable plan
  artifacts.
* Production apply/deploy path on merge to `main`.
* Immutable SHA-tag Lambda image deployment.
* Automatic Lambda rollback to the previous image on failed deployment
  validation.
* OWASP ZAP DAST baseline scan after production deployment.
* Lambda `/health` endpoint for smoke tests and DAST readiness.
* Hardened Dockerfile with digest-pinned base image, multi-stage build, and
  explicit non-root runtime user.
* Reference documentation for architecture, security model, scanner rationale,
  cost estimation, and troubleshooting.

### Changed

* Removed mutable `latest` image publishing from the deployment flow.
* Environment-specific names now include the active workspace.
