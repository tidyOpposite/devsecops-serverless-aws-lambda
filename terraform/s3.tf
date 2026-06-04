# Створення S3 бакетів для логів, вихідних GIF та фронтенду.

resource "aws_s3_bucket" "log_bucket" {
  bucket = "${var.project_name}-access-logs-${data.aws_caller_identity.current.account_id}"
  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}

resource "aws_s3_bucket_versioning" "log_bucket_versioning" {
  bucket = aws_s3_bucket.log_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "s3_log_bucket_policy_doc" {
  statement {
    sid    = "AllowLogDelivery"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.log_bucket.arn}/*" # Дозволити запис об'єктів у бакет логів
    ]
    # Обмеження джерелом та акаунтом
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values = [
        aws_s3_bucket.frontend_bucket.arn,
        aws_s3_bucket.output_bucket.arn
      ]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_s3_bucket_policy" "s3_log_bucket_policy" {
  bucket = aws_s3_bucket.log_bucket.id
  policy = data.aws_iam_policy_document.s3_log_bucket_policy_doc.json
}

resource "aws_s3_bucket_server_side_encryption_configuration" "log_bucket_sse" {
  bucket = aws_s3_bucket.log_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.encryption_key.arn
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

# --- Output Bucket ---

resource "aws_s3_bucket" "output_bucket" {
  bucket = "${var.project_name}-output-gifs-${data.aws_caller_identity.current.account_id}"
  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
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
      kms_master_key_id = aws_kms_key.encryption_key.arn
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

# --- Frontend Bucket ---

resource "aws_s3_bucket" "frontend_bucket" {
  bucket = "${var.project_name}-frontend-${data.aws_caller_identity.current.account_id}"
  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}

resource "aws_s3_bucket_versioning" "frontend_bucket_versioning" {
  bucket = aws_s3_bucket.frontend_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "frontend_bucket_policy_doc" {
  statement {
    sid    = "PublicReadGetObject"
    effect = "Allow"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.frontend_bucket.arn}/*" # Доступ до об'єктів всередині бакету
    ]
  }
}

resource "aws_s3_bucket_policy" "frontend_bucket_policy" {
  bucket = aws_s3_bucket.frontend_bucket.id
  policy = data.aws_iam_policy_document.frontend_bucket_policy_doc.json
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend_bucket_sse" {
  bucket = aws_s3_bucket.frontend_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.encryption_key.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_website_configuration" "frontend_website" {
  bucket = aws_s3_bucket.frontend_bucket.id
  index_document {
    suffix = "index.html"
  }
}

# trivy:ignore:AVD-AWS-0087:Public access policy is allowed for S3 website hosting.
# trivy:ignore:AVD-AWS-0093:Public access restriction is disabled for S3 website hosting.
resource "aws_s3_bucket_public_access_block" "frontend_bucket_pab" {
  bucket                  = aws_s3_bucket.frontend_bucket.id
  block_public_acls       = true
  block_public_policy     = false # Дозволяємо публічну політику бакета
  ignore_public_acls      = true
  restrict_public_buckets = false # Не обмежуємо публічний доступ через ACL
}

resource "aws_s3_bucket_logging" "frontend_bucket_logging" {
  bucket        = aws_s3_bucket.frontend_bucket.id
  target_bucket = aws_s3_bucket.log_bucket.id
  target_prefix = "log/frontend-bucket/"
}

resource "aws_s3_account_public_access_block" "account_pab" {
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
  depends_on = [
    aws_s3_bucket_public_access_block.frontend_bucket_pab,
    aws_s3_bucket_public_access_block.log_bucket_pab,
    aws_s3_bucket_public_access_block.output_bucket_pab
  ]
}
