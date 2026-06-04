# Створення Lambda функції з використанням контейнерного образу.

resource "aws_lambda_function" "gif_generator_lambda" {
  function_name = "${var.project_name}-lambda"
  role          = aws_iam_role.lambda_exec_role.arn
  package_type  = "Image"
  # Використовуємо URI образу з CI/CD або :latest для початкового створення.
  image_uri   = var.lambda_image_uri != "" ? var.lambda_image_uri : "${aws_ecr_repository.lambda_repo.repository_url}:latest"
  memory_size = var.lambda_memory_size
  timeout     = var.lambda_timeout
  dead_letter_config { target_arn = aws_sqs_queue.lambda_dlq.arn }
  tracing_config { mode = "Active" }

  environment {
    variables = {
      OUTPUT_BUCKET_NAME = aws_s3_bucket.output_bucket.bucket
    }
  }
  depends_on = [aws_cloudwatch_log_group.lambda_log_group]
  tags       = { Project = var.project_name, ManagedBy = "Terraform" }
}

resource "aws_cloudwatch_log_group" "lambda_log_group" {
  name              = "/aws/lambda/${var.project_name}-lambda"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.encryption_key.arn
  tags              = { Project = var.project_name, ManagedBy = "Terraform" }
}

resource "aws_lambda_permission" "api_gw_permission" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gif_generator_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  # Дозвіл на виклик з будь-якого методу та шляху нашого API Gateway
  source_arn = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}