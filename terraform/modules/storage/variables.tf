variable "account_id" {
  description = "Current AWS account ID."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key used for private buckets."
  type        = string
}

variable "name_prefix" {
  description = "Environment-aware resource name prefix."
  type        = string
}

variable "tags" {
  description = "Common tags applied to module resources."
  type        = map(string)
}
