# AWS Cost Estimation

This is a practical order-of-magnitude estimate for `us-east-1`. Always verify
with the official AWS pricing pages and the AWS Pricing Calculator before
production use:

* AWS Lambda pricing: https://aws.amazon.com/lambda/pricing/
* API Gateway pricing: https://aws.amazon.com/api-gateway/pricing/
* S3 pricing: https://aws.amazon.com/s3/pricing/
* ECR pricing: https://aws.amazon.com/ecr/pricing/
* DynamoDB pricing: https://aws.amazon.com/dynamodb/pricing/on-demand/
* KMS pricing: https://aws.amazon.com/kms/pricing/
* CloudWatch pricing: https://aws.amazon.com/cloudwatch/pricing/

## Assumptions

| Dimension | Low demo | Moderate demo |
| --- | ---: | ---: |
| API requests / month | 10,000 | 1,000,000 |
| Successful GIF generations | 1,000 | 100,000 |
| Average Lambda duration | 5 seconds | 5 seconds |
| Lambda memory | 2 GB | 2 GB |
| Generated GIF storage | 5 GB | 250 GB |
| ECR image storage | 3 GB | 10 GB |
| CloudWatch log ingestion | 1 GB | 25 GB |

## Monthly Estimate

| Service | Low demo | Moderate demo | Notes |
| --- | ---: | ---: | --- |
| Lambda requests | ~$0.00 | ~$0.20 | First 1M requests may be covered by free tier. |
| Lambda duration | ~$1.67 | ~$166.67 | 2 GB x 5s = 10 GB-s per request, before free tier. |
| API Gateway HTTP API | ~$0.01 | ~$1.00 | HTTP API request pricing is much lower than REST API for this use case. |
| S3 storage | ~$0.12 | ~$5.75 | Output, frontend, and logs; request charges are usually small at demo scale. |
| ECR storage | ~$0.30 | ~$1.00 | Lifecycle policy keeps the latest 30 SHA-tagged images. |
| DynamoDB lock table | ~$0.00 | ~$0.01 | Terraform locking traffic is tiny with on-demand billing. |
| KMS key | ~$1.00 | ~$1.00+ | One customer-managed key per environment; request usage may add small charges. |
| CloudWatch Logs | ~$0.53 | ~$13.25 | Ingestion dominates; retention varies by environment. |
| SQS DLQ | ~$0.00 | ~$0.00 | Only failed async events; API Gateway sync errors do not enter DLQ. |
| Estimated total | ~$3.63/month | ~$188.88/month | Excludes data transfer out, taxes, NAT, custom domains, WAF, and CloudFront. |

## Cost Drivers

* Lambda duration is the largest variable cost because video processing is CPU
  and memory intensive.
* CloudWatch Logs can become expensive if FFmpeg output is too verbose.
* S3 output storage grows indefinitely unless lifecycle expiration is added.
* KMS has a predictable per-key monthly floor. Three environments mean three
  workload KMS keys, plus any keys you add for the backend.
* DAST itself does not add AWS infrastructure cost, but it generates API calls
  during deployment.

## Cost Controls

* Tune Lambda memory with real duration metrics; higher memory can reduce
  duration enough to lower total cost.
* Add S3 lifecycle rules for generated GIF expiration if long retention is not
  required.
* Keep ECR lifecycle policy enabled for immutable SHA tags.
* Keep CloudWatch retention low in `dev` and `staging`.
* Consider ARM64 Lambda only after replacing the amd64 FFmpeg binary path.
