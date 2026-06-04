# Створення HTTP API Gateway для виклику Lambda функції.

resource "aws_cloudwatch_log_group" "api_gw_log_group" {
  name              = "/aws/apigateway/${var.project_name}-http-api"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.encryption_key.arn
  tags              = { Project = var.project_name, ManagedBy = "Terraform" }
}

resource "aws_apigatewayv2_api" "http_api" {
  name          = "${var.project_name}-http-api"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization", "X-Amz-Date", "X-Api-Key", "X-Amz-Security-Token"]
    max_age       = 300
  }
  tags = { Project = var.project_name, ManagedBy = "Terraform" }
}

resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.gif_generator_lambda.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "default_route" {
  api_id             = aws_apigatewayv2_api.http_api.id
  route_key          = "$default"
  target             = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_stage" "default_stage" {
  api_id      = aws_apigatewayv2_api.http_api.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 100
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw_log_group.arn
    format          = jsonencode({ requestId = "$context.requestId", sourceIp = "$context.identity.sourceIp", requestTime = "$context.requestTime", protocol = "$context.protocol", httpMethod = "$context.httpMethod", resourcePath = "$context.resourcePath", status = "$context.status", responseLength = "$context.responseLength", integrationErrorMessage = "$context.integrationErrorMessage" })
  }

  tags       = { Project = var.project_name, ManagedBy = "Terraform" }
  depends_on = [aws_cloudwatch_log_group.api_gw_log_group]
}