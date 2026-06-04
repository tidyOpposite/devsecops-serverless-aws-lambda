# Налаштування AWS провайдера та Terraform backend.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.48"
    }
  }
}

provider "aws" {
  region = var.aws_region
  # Припускаємо, що автентифікація обробляється через змінні середовища,
  # профіль AWS або IAM роль (наприклад, у CI/CD).
}