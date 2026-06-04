output "key_arn" {
  description = "ARN of the customer-managed KMS key."
  value       = aws_kms_key.encryption_key.arn
}

output "key_id" {
  description = "ID of the customer-managed KMS key."
  value       = aws_kms_key.encryption_key.key_id
}

output "alias_name" {
  description = "Friendly KMS alias name."
  value       = aws_kms_alias.encryption_key.name
}
