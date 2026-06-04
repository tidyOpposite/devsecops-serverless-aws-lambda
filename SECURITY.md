# Security Policy

This repository is a reference implementation for a secure serverless
DevSecOps pipeline. The detailed threat model and control mapping live in
[docs/security-model.md](docs/security-model.md).

## Supported Versions

| Version | Supported |
| --- | --- |
| `v0.1.x` | Yes |

## Reporting A Vulnerability

Please do not open public issues for exploitable vulnerabilities. Report them
privately to the repository maintainer with:

* affected component and version or commit SHA;
* reproduction steps;
* expected impact;
* suggested fix, if known.

The maintainer should acknowledge receipt within 5 business days and publish a
fix or mitigation plan after triage.

## Security Baseline

The current baseline includes:

* GitHub Actions OIDC instead of long-lived AWS keys.
* Terraform S3 backend with DynamoDB state locking.
* Environment isolation through Terraform workspaces.
* Modular Terraform with KMS-encrypted private storage, ECR, Lambda, API
  Gateway, CloudWatch Logs, and SQS DLQ.
* Bandit SAST, Snyk SCA/container scanning, Trivy IaC scanning, and OWASP ZAP
  DAST.
* Immutable ECR image tags and automatic Lambda rollback on failed deployment
  validation.
* Digest-pinned AWS Lambda base image and explicit non-root container runtime.

## Known Security Exceptions

* The static frontend bucket is public because the demo uses native S3 website
  hosting. Production variants should prefer CloudFront Origin Access Control.
* The API is unauthenticated to keep the reference focused on pipeline
  controls. Add an authorizer before handling sensitive workloads.
* FFmpeg is downloaded as a static amd64 binary. Review upstream releases and
  container scan results regularly.
