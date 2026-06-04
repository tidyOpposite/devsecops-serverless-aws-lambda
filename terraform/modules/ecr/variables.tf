variable "kms_key_arn" {
  description = "KMS key used for ECR encryption."
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
