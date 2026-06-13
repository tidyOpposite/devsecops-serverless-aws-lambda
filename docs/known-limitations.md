# Known Limitations

This register separates accepted product limitations from blockers that must
be closed before `v1.0.0` can be called stable.

## Accepted Limitations

These are intentional boundaries for the first stable CLI release. They should
stay explicit in docs and release notes, but they do not block `v1.0.0`.

| Limitation | Why accepted | User action |
| --- | --- | --- |
| Workload source and image build logic are outside this repository. | The product is the pipeline CLI, not a sample application framework. | Build and publish an immutable Lambda-compatible image in the workload repository. |
| Terraform, GitHub Actions, AWS, and scanners remain visible execution layers. | Operators need reviewable infrastructure and workflow behavior. | Inspect Terraform plans, workflow logs, and generated artifacts before mutating AWS or GitHub. |
| Native Windows is not a release target. | The supported Windows path is WSL2 Ubuntu, which keeps shell, path, Terraform, and CLI behavior consistent. | Use WSL2 Ubuntu for Windows workstations. |
| PyPI publishing is deferred. | GitHub Release wheels plus `SHA256SUMS` are the documented package path. | Install pinned or latest wheels from GitHub Releases. |
| The generated API is unauthenticated by default. | The kit focuses on delivery-pipeline controls rather than application auth design. | Add Cognito, IAM auth, JWT authorizers, API keys, or workload-specific authorization before handling sensitive workloads. |
| DAST is an OWASP ZAP passive baseline scan. | Authenticated flows and business-logic testing are workload-specific. | Add application-specific DAST, API tests, and auth-aware security tests in the workload repository. |
| Snyk container scanning is optional. | Some users do not have Snyk accounts or tokens during early pipeline setup. | Enable `ENABLE_SNYK_SCAN=true` and configure `SNYK_TOKEN` for stricter production candidates. |
| Scanner plugin support is not implemented. | Extensible scanner plugins are a post-1.0 backlog item. | Use the documented Trivy, Snyk, and OWASP ZAP hooks, or add custom workflow steps intentionally. |

## Blockers Before v1.0.0 Stable

These must be closed before tagging `v1.0.0`.

| Blocker | Closure evidence |
| --- | --- |
| Full AWS/GitHub production walkthrough execution evidence is missing. | Completed [Production deployment evidence](production-deployment-evidence.md) bundle attached to the release record. |
| GitHub CLI and AWS CLI compatibility have not been verified in the target repository and AWS account for the release candidate. | `gh --version`, `aws --version`, `devsecops doctor github --strict --format json`, `devsecops doctor aws --environment prod --strict --format json`, and Actions status evidence. |
| WSL2 Ubuntu compatibility does not yet have an attached release-candidate transcript. | WSL2 install, `devsecops --version`, first-success dry-run, completion generation, and unit-test transcript. |

## Review Rule

Every release note from `v0.11.0` onward should state whether these limitations
changed. A limitation that causes install, upgrade, verification, deployment,
or rollback failure in a supported environment must move from accepted
limitation to blocker until fixed or explicitly removed from support.
