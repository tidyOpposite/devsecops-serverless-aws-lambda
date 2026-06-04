output "dlq_arn" {
  description = "ARN of the Lambda dead-letter queue."
  value       = aws_sqs_queue.lambda_dlq.arn
}

output "dlq_url" {
  description = "URL of the Lambda dead-letter queue."
  value       = aws_sqs_queue.lambda_dlq.id
}

output "function_arn" {
  description = "ARN of the Lambda function."
  value       = aws_lambda_function.gif_generator_lambda.arn
}

output "function_name" {
  description = "Name of the Lambda function."
  value       = aws_lambda_function.gif_generator_lambda.function_name
}

output "image_uri" {
  description = "Image URI currently configured for Lambda."
  value       = aws_lambda_function.gif_generator_lambda.image_uri
}

output "invoke_arn" {
  description = "Invoke ARN used by API Gateway integration."
  value       = aws_lambda_function.gif_generator_lambda.invoke_arn
}

output "role_arn" {
  description = "ARN of the Lambda execution role."
  value       = aws_iam_role.lambda_exec_role.arn
}
