# Scanning Tool Rationale

The stack intentionally uses overlapping scanners because each one sees a
different layer of the system.

## Selected Stack

| Layer | Selected tool | Why |
| --- | --- | --- |
| SAST | Bandit | Focused Python security checks, low setup cost, good signal for Lambda code. |
| SCA | Snyk | Commercial vulnerability intelligence, dependency and container coverage in the same workflow. |
| IaC | Trivy config scan | One scanner covers Terraform misconfiguration and can also be used for container/IaC workflows. |
| Container | Snyk Container plus ECR scan-on-push | CI gate before push plus AWS-native visibility after push. |
| DAST | OWASP ZAP baseline | Mature passive HTTP scanning against the deployed API without requiring a full authenticated crawl. |

## Bandit vs Semgrep

| Criterion | Bandit | Semgrep |
| --- | --- | --- |
| Primary fit | Python-specific security linting. | Multi-language static analysis with custom rules. |
| Setup complexity | Very low: `bandit -r lambda_function`. | Moderate: rule packs, configuration, and triage policy matter. |
| Signal in this repo | High enough because the app is a small Python Lambda. | Useful if the repo grows into multiple services or custom rules. |
| False-positive management | Simple severity filtering. | Better long-term policy controls, but needs maintenance. |

Decision: Bandit is the default because this reference workload is Python-only
and small. Semgrep is a strong roadmap candidate when the project needs
cross-language rules, framework-specific patterns, or custom organization
policies.

## Trivy vs Checkov

| Criterion | Trivy config | Checkov |
| --- | --- | --- |
| Coverage | Terraform, Kubernetes, Dockerfile, secrets, and images in one ecosystem. | Deep IaC policy coverage across Terraform, CloudFormation, Kubernetes, and more. |
| CI ergonomics | Simple GitHub Action, fast config scan, consistent severity filters. | Strong policy framework, but usually more configuration and suppression governance. |
| Fit for this repo | Good default because it already covers IaC and can align with container workflows. | Good alternative if the project needs policy-as-code libraries and compliance mapping. |

Decision: Trivy remains the IaC gate because the repo benefits from one
lightweight scanner for config and container-adjacent checks. Checkov is a good
addition if compliance reporting becomes a project goal.

## OWASP ZAP vs Nuclei

| Criterion | OWASP ZAP baseline | Nuclei |
| --- | --- | --- |
| Primary fit | Passive HTTP security baseline and spidering. | Template-driven detection for known exposures and CVEs. |
| Strength | Mature web app scanner, useful for headers, cookies, and common HTTP issues. | Fast, broad, and easy to extend with community templates. |
| Risk | Can create noisy warnings if used as a hard gate without tuning. | Template quality and target relevance need governance. |

Decision: OWASP ZAP is integrated first because the demo has a simple HTTP API
and no authenticated flows. Nuclei is suitable as a follow-up for exposure and
CVE templates.

## Gate Policy

| Finding source | Gate behavior |
| --- | --- |
| Bandit high confidence/high severity | Fail CI. |
| Snyk high/critical dependency or image vulnerability | Fail when `SNYK_TOKEN` is configured. |
| Trivy high/critical IaC finding | Fail CI. |
| OWASP ZAP fail-level result | Fail deployment validation and trigger Lambda rollback. |
| OWASP ZAP warnings | Report but do not fail by default (`-I`) to avoid noisy rollback loops. |
