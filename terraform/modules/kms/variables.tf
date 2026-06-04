variable "account_id" {
  description = "Current AWS account ID."
  type        = string
}

variable "aws_region" {
  description = "AWS region for regional service principals."
  type        = string
}

variable "environment" {
  description = "Deployment environment."
  type        = string
}

variable "name_prefix" {
  description = "Environment-aware resource name prefix."
  type        = string
}

variable "project_name" {
  description = "Project name."
  type        = string
}

variable "terraform_admin_role_name" {
  description = "IAM role name allowed to administer the KMS key."
  type        = string
}

variable "tags" {
  description = "Common tags applied to module resources."
  type        = map(string)
}
