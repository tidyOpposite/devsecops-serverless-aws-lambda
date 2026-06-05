variable "aws_region" {
  description = "AWS region for the Terraform state backend."
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for Terraform remote state."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "state_bucket_name must be a valid globally unique S3 bucket name."
  }
}

variable "lock_table_name" {
  description = "DynamoDB table name used by the Terraform S3 backend for state locking."
  type        = string
  default     = "devsecops-pipeline-terraform-locks"
}

variable "tags" {
  description = "Tags applied to backend resources."
  type        = map(string)
  default = {
    Project   = "devsecops-pipeline"
    ManagedBy = "Terraform"
    Purpose   = "terraform-state"
  }
}
