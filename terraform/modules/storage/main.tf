locals {
  log_bucket_name      = "${var.name_prefix}-access-logs-${var.account_id}"
  output_bucket_name   = "${var.name_prefix}-output-gifs-${var.account_id}"
  frontend_bucket_name = "${var.name_prefix}-frontend-${var.account_id}"
}

resource "aws_s3_bucket" "log_bucket" {
  bucket = local.log_bucket_name
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "log_bucket_versioning" {
  bucket = aws_s3_bucket.log_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "log_bucket_sse" {
  bucket = aws_s3_bucket.log_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = var.kms_key_arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "log_bucket_pab" {
  bucket                  = aws_s3_bucket.log_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "s3_log_bucket_policy_doc" {
  statement {
    sid    = "AllowLogDelivery"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.log_bucket.arn}/*"]

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values = [
        aws_s3_bucket.frontend_bucket.arn,
        aws_s3_bucket.output_bucket.arn,
      ]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_s3_bucket_policy" "s3_log_bucket_policy" {
  bucket = aws_s3_bucket.log_bucket.id
  policy = data.aws_iam_policy_document.s3_log_bucket_policy_doc.json
}

resource "aws_s3_bucket" "output_bucket" {
  bucket = local.output_bucket_name
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "output_bucket_versioning" {
  bucket = aws_s3_bucket.output_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "output_bucket_sse" {
  bucket = aws_s3_bucket.output_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = var.kms_key_arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "output_bucket_pab" {
  bucket                  = aws_s3_bucket.output_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_logging" "output_bucket_logging" {
  bucket        = aws_s3_bucket.output_bucket.id
  target_bucket = aws_s3_bucket.log_bucket.id
  target_prefix = "log/output-bucket/"
}

resource "aws_s3_bucket" "frontend_bucket" {
  bucket = local.frontend_bucket_name
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "frontend_bucket_versioning" {
  bucket = aws_s3_bucket.frontend_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_ownership_controls" "frontend_bucket_ownership" {
  bucket = aws_s3_bucket.frontend_bucket.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

data "aws_iam_policy_document" "frontend_bucket_policy_doc" {
  statement {
    sid    = "PublicReadGetObjectForStaticWebsite"
    effect = "Allow"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend_bucket.arn}/*"]
  }
}

resource "aws_s3_bucket_policy" "frontend_bucket_policy" {
  bucket     = aws_s3_bucket.frontend_bucket.id
  policy     = data.aws_iam_policy_document.frontend_bucket_policy_doc.json
  depends_on = [aws_s3_bucket_public_access_block.frontend_bucket_pab]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend_bucket_sse" {
  bucket = aws_s3_bucket.frontend_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_website_configuration" "frontend_website" {
  bucket = aws_s3_bucket.frontend_bucket.id
  index_document {
    suffix = "index.html"
  }
}

# trivy:ignore:AVD-AWS-0087 Public read is intentional for static S3 website hosting.
# trivy:ignore:AVD-AWS-0093 Public bucket policy is intentional for static S3 website hosting.
resource "aws_s3_bucket_public_access_block" "frontend_bucket_pab" {
  bucket                  = aws_s3_bucket.frontend_bucket.id
  block_public_acls       = true
  block_public_policy     = false
  ignore_public_acls      = true
  restrict_public_buckets = false
}

resource "aws_s3_bucket_logging" "frontend_bucket_logging" {
  bucket        = aws_s3_bucket.frontend_bucket.id
  target_bucket = aws_s3_bucket.log_bucket.id
  target_prefix = "log/frontend-bucket/"
}
