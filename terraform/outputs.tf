# Визначення вихідних значень (наприклад, URL API).

output "api_gateway_invoke_url" {
  description = "URL для виклику API Gateway"
  value       = aws_apigatewayv2_stage.default_stage.invoke_url
}

output "ecr_repository_url" {
  description = "URL ECR репозиторію для Lambda образу"
  value       = aws_ecr_repository.lambda_repo.repository_url
}

output "output_s3_bucket_name" {
  description = "Назва S3 бакету для згенерованих GIF"
  value       = aws_s3_bucket.output_bucket.bucket
}

output "frontend_s3_bucket_name" {
  description = "Назва S3 бакету для статичного фронтенду"
  value       = aws_s3_bucket.frontend_bucket.bucket
}

output "frontend_s3_website_endpoint" {
  description = "URL ендпоінту статичного веб-сайту на S3"
  value       = aws_s3_bucket_website_configuration.frontend_website.website_endpoint
}

output "log_s3_bucket_name" {
  description = "Назва S3 бакету для логів доступу"
  value       = aws_s3_bucket.log_bucket.bucket
}

output "lambda_dlq_sqs_url" {
  description = "URL SQS черги для Lambda DLQ"
  value       = aws_sqs_queue.lambda_dlq.id
}

output "kms_key_arn" {
  description = "ARN створеного KMS ключа"
  value       = aws_kms_key.encryption_key.arn
}