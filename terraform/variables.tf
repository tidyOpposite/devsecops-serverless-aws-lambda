variable "project_name" {
  description = "Назва проекту (використовується для іменування ресурсів)."
  type        = string
  default     = "devsecops-pipeline"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.project_name))
    error_message = "project_name must be 3-32 chars and contain only lowercase letters, numbers, and hyphens."
  }
}

variable "aws_region" {
  description = "Регіон AWS для розгортання ресурсів."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Fallback environment used when Terraform runs in the default workspace."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "environment_config" {
  description = "Per-environment runtime and API settings. Terraform workspaces select the active environment."
  type = map(object({
    lambda_memory_size         = number
    lambda_timeout             = number
    log_retention_days         = number
    api_throttling_burst_limit = number
    api_throttling_rate_limit  = number
    cors_allowed_origins       = list(string)
  }))

  default = {
    dev = {
      lambda_memory_size         = 1024
      lambda_timeout             = 120
      log_retention_days         = 30
      api_throttling_burst_limit = 25
      api_throttling_rate_limit  = 50
      cors_allowed_origins       = ["*"]
    }
    staging = {
      lambda_memory_size         = 1536
      lambda_timeout             = 180
      log_retention_days         = 90
      api_throttling_burst_limit = 50
      api_throttling_rate_limit  = 100
      cors_allowed_origins       = ["*"]
    }
    prod = {
      lambda_memory_size         = 2048
      lambda_timeout             = 240
      log_retention_days         = 365
      api_throttling_burst_limit = 100
      api_throttling_rate_limit  = 200
      cors_allowed_origins       = ["*"]
    }
  }

  validation {
    condition     = alltrue([for environment in ["dev", "staging", "prod"] : contains(keys(var.environment_config), environment)])
    error_message = "environment_config must define dev, staging, and prod."
  }
}

variable "lambda_memory_size" {
  description = "Legacy fallback for Lambda memory size. Prefer environment_config."
  type        = number
  default     = 1024
}

variable "lambda_timeout" {
  description = "Legacy fallback for Lambda timeout. Prefer environment_config."
  type        = number
  default     = 120
}

variable "lambda_image_uri" {
  description = "Full immutable image URI for the Lambda workload."
  type        = string
  default     = ""

  validation {
    condition = var.lambda_image_uri == "" || (
      can(regex("^\\d{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/.+(?::[^:@]+|@sha256:[A-Fa-f0-9]{64})$", var.lambda_image_uri))
      && !can(regex(":(latest|bootstrap)$", lower(var.lambda_image_uri)))
    )
    error_message = "lambda_image_uri must be empty for validation-only runs or an immutable ECR image URI; latest/bootstrap tags are not allowed."
  }
}

variable "terraform_admin_role_name" {
  description = "Optional IAM role name allowed to administer the workload KMS key in addition to account root delegation."
  type        = string
  default     = ""
}
