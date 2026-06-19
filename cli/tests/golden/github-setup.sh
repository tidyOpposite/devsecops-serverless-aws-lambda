#!/usr/bin/env bash
set -euo pipefail

# CLI-owned generated file. Do not edit directly.
# Update .devsecops-pipeline.toml and rerun `devsecops render`.
# See docs/generated-artifacts.md for ownership rules.
# Review placeholder values before running.

gh variable set PROJECT_NAME --body 'golden-pipeline'
gh variable set LAMBDA_IMAGE_URI --body '123456789012.dkr.ecr.eu-central-1.amazonaws.com/golden-pipeline:sha-abc123'
gh variable set API_AUTHORIZATION_TYPE --body 'AWS_IAM'
gh variable set ENABLE_SNYK_SCAN --body 'true'
gh variable set ENABLE_HTTP_VALIDATION --body 'true'
gh variable set ENABLE_DAST --body 'true'
gh variable set PROD_APPROVAL_ENVIRONMENT --body 'prod'

gh secret set AWS_REGION --body 'eu-central-1'
gh secret set AWS_ROLE_TO_ASSUME_ARN --body "<deploy-role-arn>"
gh secret set AWS_PLAN_ROLE_TO_ASSUME_ARN --body "<plan-role-arn>"
gh secret set SNYK_TOKEN --body "<snyk-token>"
