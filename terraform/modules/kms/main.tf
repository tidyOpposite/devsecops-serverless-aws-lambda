locals {
  terraform_admin_role_arn = var.terraform_admin_role_name == "" ? null : "arn:aws:iam::${var.account_id}:role/${var.terraform_admin_role_name}"
  key_admin_principals = compact([
    "arn:aws:iam::${var.account_id}:root",
    local.terraform_admin_role_arn,
  ])
}

data "aws_iam_policy_document" "kms_key_policy" {
  statement {
    sid    = "EnableRootAndTerraformRolePermissions"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = local.key_admin_principals
    }
    actions = [
      "kms:Create*",
      "kms:Describe*",
      "kms:Enable*",
      "kms:List*",
      "kms:Put*",
      "kms:Update*",
      "kms:Revoke*",
      "kms:Disable*",
      "kms:Get*",
      "kms:Delete*",
      "kms:TagResource",
      "kms:UntagResource",
      "kms:ScheduleKeyDeletion",
      "kms:CancelKeyDeletion",
    ]
    resources = ["*"]
  }

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
      "kms:DescribeKey",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "AllowSQSServiceUsage"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["sqs.amazonaws.com"]
    }
    actions = [
      "kms:GenerateDataKey",
      "kms:Decrypt",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "AllowCloudWatchLogsServiceUsage"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    resources = ["*"]
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:*"]
    }
  }

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
      "kms:DescribeKey",
    ]
    resources = ["*"]
  }
}

resource "aws_kms_key" "encryption_key" {
  description             = "KMS key for ${var.name_prefix}"
  deletion_window_in_days = 7
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.kms_key_policy.json
  tags                    = var.tags
}

resource "aws_kms_alias" "encryption_key" {
  name          = "alias/${var.name_prefix}-encryption"
  target_key_id = aws_kms_key.encryption_key.key_id
}
