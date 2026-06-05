# AWS Cost Estimation

This is a practical order-of-magnitude estimate for the AWS execution layer
managed by the CLI. Use the CLI to choose environment presets and render the
configuration, then verify production estimates with the official AWS pricing
pages and the AWS Pricing Calculator:

* AWS Lambda pricing: https://aws.amazon.com/lambda/pricing/
* API Gateway pricing: https://aws.amazon.com/api-gateway/pricing/
* S3 pricing: https://aws.amazon.com/s3/pricing/
* ECR pricing: https://aws.amazon.com/ecr/pricing/
* DynamoDB pricing: https://aws.amazon.com/dynamodb/pricing/on-demand/
* KMS pricing: https://aws.amazon.com/kms/pricing/
* CloudWatch pricing: https://aws.amazon.com/cloudwatch/pricing/

## Assumptions

Inspect and tune the active CLI configuration first:

```bash
devsecops envs
devsecops preset minimal --render
devsecops preset balanced --render
devsecops preset strict --render
devsecops set environments.prod.lambda_memory_size 2048 --render
devsecops set environments.prod.log_retention_days 90 --render
```

The table below estimates typical infrastructure after applying the rendered
Terraform configuration.

| Dimension | Low usage | Moderate usage |
| --- | ---: | ---: |
| API requests / month | 10,000 | 1,000,000 |
| Average Lambda duration | 500 ms | 1 second |
| Lambda memory | 1 GB | 2 GB |
| Workload data storage | 5 GB | 100 GB |
| ECR image storage | 3 GB | 10 GB |
| CloudWatch log ingestion | 1 GB | 25 GB |

## Monthly Estimate

| Service | Low usage | Moderate usage | Notes |
| --- | ---: | ---: | --- |
| Lambda requests | ~$0.00 | ~$0.20 | First 1M requests may be covered by free tier. |
| Lambda duration | ~$0.08 | ~$33.33 | Before free tier; actual cost depends heavily on workload duration and memory. |
| API Gateway HTTP API | ~$0.01 | ~$1.00 | HTTP API request pricing is lower than REST API for many simple use cases. |
| S3 storage | ~$0.12 | ~$2.30 | Private workload data and access logs; request charges vary by workload. |
| ECR storage | ~$0.30 | ~$1.00 | Lifecycle policy keeps the latest 30 SHA-tagged images. |
| DynamoDB lock table | ~$0.00 | ~$0.01 | Terraform locking traffic is tiny with on-demand billing. |
| KMS key | ~$1.00 | ~$1.00+ | One customer-managed key per environment; request usage may add small charges. |
| CloudWatch Logs | ~$0.53 | ~$13.25 | Ingestion dominates; retention varies by environment. |
| SQS DLQ | ~$0.00 | ~$0.00 | Only failed async events; API Gateway sync errors do not enter DLQ. |
| Estimated total | ~$2.04/month | ~$52.09/month | Excludes data transfer out, taxes, NAT, custom domains, WAF, and CloudFront. |

## Cost Drivers

* Lambda duration and configured memory are the largest workload-specific
  variables.
* CloudWatch Logs can become expensive if the workload logs large payloads or
  high-volume debug output.
* S3 workload data grows indefinitely unless lifecycle expiration is added.
* KMS has a predictable per-key monthly floor. Three environments mean three
  workload KMS keys, plus any keys you add for the backend.
* Optional DAST does not add AWS infrastructure cost, but it generates API
  calls during deployment.

## Cost Controls

Use CLI presets and targeted config edits before rendering:

```bash
devsecops preset minimal --render
devsecops set environments.dev.log_retention_days 7 --render
devsecops set environments.staging.log_retention_days 30 --render
devsecops set environments.prod.api_throttling_rate_limit 100 --render
```

Additional controls:

* Tune Lambda memory with real duration metrics; higher memory can reduce
  duration enough to lower total cost.
* Add S3 lifecycle rules for workload data that does not need long retention.
* Keep ECR lifecycle policy enabled for immutable image tags.
* Keep CloudWatch retention low in `dev` and `staging`.
* Consider ARM64 Lambda only after verifying the workload image and native
  dependencies support it.
