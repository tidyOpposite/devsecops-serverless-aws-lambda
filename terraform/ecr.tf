# Створення ECR репозиторію для зберігання Docker-образу Lambda.

resource "aws_ecr_repository" "lambda_repo" {
  name                 = "${var.project_name}-lambda-repo"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.encryption_key.arn
  }

  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}