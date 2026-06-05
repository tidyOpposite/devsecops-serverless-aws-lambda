locals {
  function_name = "${var.name_prefix}-lambda"
  image_uri     = var.lambda_image_uri != "" ? var.lambda_image_uri : "${var.ecr_repository_url}:bootstrap"
}

resource "aws_sqs_queue" "lambda_dlq" {
  name                              = "${var.name_prefix}-lambda-dlq"
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
  tags                              = var.tags
}

data "aws_iam_policy_document" "lambda_assume_role_policy" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_execution_policy" {
  statement {
    sid    = "AllowCloudWatchLogWrites"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda_log_group.arn}:*"]
  }

  statement {
    sid       = "AllowS3OutputAccess"
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${var.output_bucket_arn}/*"]
  }

  statement {
    sid       = "AllowDLQPublish"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.lambda_dlq.arn]
  }

  statement {
    sid    = "AllowXRay"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "AllowKMSUsageForLambda"
    effect = "Allow"
    actions = [
      "kms:GenerateDataKey",
      "kms:Decrypt",
      "kms:Encrypt",
      "kms:ReEncrypt*",
      "kms:DescribeKey",
    ]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role" "lambda_exec_role" {
  name               = "${var.name_prefix}-lambda-exec-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role_policy.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "lambda_execution_policy" {
  name   = "${var.name_prefix}-lambda-execution-policy"
  role   = aws_iam_role.lambda_exec_role.id
  policy = data.aws_iam_policy_document.lambda_execution_policy.json
}

resource "aws_cloudwatch_log_group" "lambda_log_group" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

resource "aws_lambda_function" "workload" {
  function_name = local.function_name
  role          = aws_iam_role.lambda_exec_role.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  architectures = ["x86_64"]
  memory_size   = var.lambda_memory_size
  timeout       = var.lambda_timeout

  dead_letter_config {
    target_arn = aws_sqs_queue.lambda_dlq.arn
  }

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      AWS_REGION         = var.aws_region
      ENVIRONMENT        = var.environment
      OUTPUT_BUCKET_NAME = var.output_bucket_name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda_log_group,
    aws_iam_role_policy.lambda_execution_policy,
  ]

  tags = var.tags
}
