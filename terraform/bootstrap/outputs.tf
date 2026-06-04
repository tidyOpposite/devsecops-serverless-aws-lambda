output "backend_config" {
  description = "Values to copy into terraform/backend.tf."
  value = {
    bucket         = aws_s3_bucket.terraform_state.bucket
    dynamodb_table = aws_dynamodb_table.terraform_locks.name
    region         = var.aws_region
  }
}

output "dynamodb_lock_table_name" {
  description = "DynamoDB lock table name."
  value       = aws_dynamodb_table.terraform_locks.name
}

output "state_bucket_name" {
  description = "Terraform state bucket name."
  value       = aws_s3_bucket.terraform_state.bucket
}
