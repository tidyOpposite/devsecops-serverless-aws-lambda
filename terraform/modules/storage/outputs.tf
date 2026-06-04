output "frontend_bucket_name" {
  description = "Name of the public static frontend bucket."
  value       = aws_s3_bucket.frontend_bucket.bucket
}

output "frontend_website_endpoint" {
  description = "S3 website endpoint for the static frontend."
  value       = aws_s3_bucket_website_configuration.frontend_website.website_endpoint
}

output "frontend_website_url" {
  description = "HTTP URL for the S3 static website."
  value       = "http://${aws_s3_bucket_website_configuration.frontend_website.website_endpoint}"
}

output "log_bucket_name" {
  description = "Name of the S3 bucket that stores access logs."
  value       = aws_s3_bucket.log_bucket.bucket
}

output "output_bucket_arn" {
  description = "ARN of the private output GIF bucket."
  value       = aws_s3_bucket.output_bucket.arn
}

output "output_bucket_name" {
  description = "Name of the private output GIF bucket."
  value       = aws_s3_bucket.output_bucket.bucket
}
