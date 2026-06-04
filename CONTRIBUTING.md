# Contributing

This repository is an open-source DevSecOps reference pipeline for AWS Lambda
container workloads. Contributions are welcome when they improve correctness,
security, reproducibility, documentation, or maintainability.

## Useful Contribution Areas

- CI/CD hardening for GitHub Actions and AWS OIDC.
- Terraform security improvements and least-privilege IAM refinements.
- SAST, SCA, IaC, container scanning, and release workflow automation.
- Safer Lambda input validation, observability, and cost controls.
- Documentation for adapting the pipeline to real AWS accounts.

## Development Notes

1. Do not commit cloud credentials, API tokens, Terraform state, or `.tfvars`
   files.
2. Keep security scanner failures visible. Avoid suppressions unless the risk
   is documented and intentionally accepted.
3. Prefer small pull requests with clear rationale and validation notes.
4. Run the relevant local checks where possible before opening a pull request.

## Security

Please do not disclose vulnerabilities publicly before they are reviewed.
Use the process described in `SECURITY.md`.
