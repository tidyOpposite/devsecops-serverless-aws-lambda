variable "aws_region" {
  description = "AWS region exposed to the Lambda runtime."
  type        = string
}

variable "ecr_repository_url" {
  description = "ECR repository URL used when lambda_image_uri is not provided."
  type        = string
}

variable "environment" {
  description = "Deployment environment."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key used by Lambda-adjacent resources."
  type        = string
}

variable "lambda_image_uri" {
  description = "Immutable ECR image URI for the Lambda function."
  type        = string
}

variable "lambda_memory_size" {
  description = "Lambda memory size in MB."
  type        = number
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds."
  type        = number
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days."
  type        = number
}

variable "name_prefix" {
  description = "Environment-aware resource name prefix."
  type        = string
}

variable "output_bucket_arn" {
  description = "ARN of the private workload data bucket."
  type        = string
}

variable "output_bucket_name" {
  description = "Name of the private workload data bucket."
  type        = string
}

variable "tags" {
  description = "Common tags applied to module resources."
  type        = map(string)
}
