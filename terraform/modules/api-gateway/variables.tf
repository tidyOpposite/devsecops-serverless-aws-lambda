variable "api_throttling_burst_limit" {
  description = "API Gateway burst throttle limit."
  type        = number
}

variable "api_throttling_rate_limit" {
  description = "API Gateway steady-state throttle limit."
  type        = number
}

variable "cors_allowed_origins" {
  description = "Allowed CORS origins."
  type        = list(string)

  validation {
    condition     = length(var.cors_allowed_origins) > 0 && alltrue([for origin in var.cors_allowed_origins : length(trimspace(origin)) > 0])
    error_message = "cors_allowed_origins must contain at least one non-empty origin."
  }
}

variable "kms_key_arn" {
  description = "KMS key used for API Gateway access logs."
  type        = string
}

variable "lambda_invoke_arn" {
  description = "Lambda invoke ARN for API Gateway integration."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days."
  type        = number
}

variable "name_prefix" {
  description = "Environment-aware resource name prefix."
  type        = string
}

variable "tags" {
  description = "Common tags applied to module resources."
  type        = map(string)
}
