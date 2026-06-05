output "log_bucket_name" {
  description = "Name of the S3 bucket that stores access logs."
  value       = aws_s3_bucket.log_bucket.bucket
}

output "output_bucket_arn" {
  description = "ARN of the private workload data bucket."
  value       = aws_s3_bucket.output_bucket.arn
}

output "output_bucket_name" {
  description = "Name of the private workload data bucket."
  value       = aws_s3_bucket.output_bucket.bucket
}
