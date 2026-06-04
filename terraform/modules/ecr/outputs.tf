output "repository_arn" {
  description = "ARN of the Lambda ECR repository."
  value       = aws_ecr_repository.lambda_repo.arn
}

output "repository_name" {
  description = "Name of the Lambda ECR repository."
  value       = aws_ecr_repository.lambda_repo.name
}

output "repository_url" {
  description = "URL of the Lambda ECR repository."
  value       = aws_ecr_repository.lambda_repo.repository_url
}
