# Створення KMS ключа для шифрування.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Політика доступу для KMS ключа
data "aws_iam_policy_document" "kms_key_policy" {
  statement {
    sid    = "EnableRootAndTerraformRolePermissions"
    effect = "Allow"
    principals {
      type = "AWS"
      identifiers = [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root",
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/GitHubActions-Terraform-DeployRole"
      ]
    }
    # Дозволи на повне керування ключем
    actions = [
      "kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*",
      "kms:Update*", "kms:Revoke*", "kms:Disable*", "kms:Get*", "kms:Delete*",
      "kms:TagResource", "kms:UntagResource", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion"
    ]
    resources = ["*"]
  }

  # Дозвіл для сервісу S3
  statement {
    sid    = "AllowS3ServiceUsage"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey"
    ]
    resources = ["*"] # Політика застосовується до цього ключа
  }

  # Дозвіл для сервісу SQS
  statement {
    sid    = "AllowSQSServiceUsage"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["sqs.amazonaws.com"]
    }
    actions = [
      "kms:GenerateDataKey",
      "kms:Decrypt"
    ]
    resources = ["*"]
  }

  # Дозвіл для сервісу CloudWatch Logs
  statement {
    sid    = "AllowCloudWatchLogsServiceUsage"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logs.${data.aws_region.current.name}.amazonaws.com"]
    }
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey"
    ]
    resources = ["*"]
    # Обмеження джерелом запиту лише лог-групами поточного акаунту
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:*"]
    }
  }

  # Дозвіл для сервісу ECR
  statement {
    sid    = "AllowECRServiceUsage"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["ecr.amazonaws.com"]
    }
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey"
    ]
    resources = ["*"]
  }
}

resource "aws_kms_key" "encryption_key" {
  description             = "KMS key for encrypting project resources (${var.project_name})"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.kms_key_policy.json

  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}