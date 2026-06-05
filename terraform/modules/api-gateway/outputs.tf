output "api_endpoint" {
  description = "Base API endpoint."
  value       = aws_apigatewayv2_api.http_api.api_endpoint
}

output "execution_arn" {
  description = "Execution ARN used for Lambda permission source scoping."
  value       = aws_apigatewayv2_api.http_api.execution_arn
}

output "health_url" {
  description = "Optional health check URL for Lambda workloads that expose /health through API Gateway."
  value       = "${trimsuffix(aws_apigatewayv2_stage.default_stage.invoke_url, "/")}/health"
}

output "invoke_url" {
  description = "Default stage invoke URL."
  value       = aws_apigatewayv2_stage.default_stage.invoke_url
}

output "stage_name" {
  description = "API Gateway stage name."
  value       = aws_apigatewayv2_stage.default_stage.name
}
