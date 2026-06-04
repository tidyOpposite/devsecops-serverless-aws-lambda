# Створення IAM ролі та політик для Lambda функції.

data "aws_iam_policy_document" "lambda_assume_role_policy" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# Надання дозволу на запис логів у CloudWatch Logs
data "aws_iam_policy_document" "lambda_execution_policy" {
  statement {
    sid    = "AllowCloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }

  # Надання дозволу на доступ до S3 бакету
  statement {
    sid    = "AllowS3Access"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
    ]
    resources = [
      aws_s3_bucket.output_bucket.arn,
      "${aws_s3_bucket.output_bucket.arn}/*",
    ]
  }

  # Надання дозволу на відправку повідомлень до SQS DLQ
  statement {
    sid    = "AllowDLQPublish"
    effect = "Allow"
    actions = [
      "sqs:SendMessage"
    ]
    resources = [aws_sqs_queue.lambda_dlq.arn]
  }

  # Надання дозволу на надсилання даних трасування до AWS X-Ray
  statement {
    sid = "AllowXRay"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords"
    ]
    resources = ["*"]
    effect    = "Allow"
  }

  # Надання дозволу на використання KMS ключа для шифрування та дешифрування
  statement {
    sid    = "AllowKMSUsageForLambda"
    effect = "Allow"
    actions = [
      # Потрібні для SQS
      "kms:GenerateDataKey",
      "kms:Decrypt",
      # Потрібні для S3 PutObject/GetObject з SSE-KMS
      "kms:Encrypt",
      "kms:ReEncrypt*",
      "kms:DescribeKey"
    ]
    resources = [aws_kms_key.encryption_key.arn]
  }
}

# Надання дозволу сервісу Lambda приймати цю роль
resource "aws_iam_role" "lambda_exec_role" {
  name               = "${var.project_name}-lambda-exec-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role_policy.json
  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}

# Політика IAM ролі Lambda
resource "aws_iam_role_policy" "lambda_execution_policy" {
  name   = "${var.project_name}-lambda-execution-policy"
  role   = aws_iam_role.lambda_exec_role.id
  policy = data.aws_iam_policy_document.lambda_execution_policy.json
}