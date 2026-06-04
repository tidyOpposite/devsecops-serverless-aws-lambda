# Визначення змінних Terraform.

variable "project_name" {
  description = "Назва проекту (використовується для іменування ресурсів)."
  type        = string
  default     = "gif-generator"
}

variable "aws_region" {
  description = "Регіон AWS для розгортання ресурсів."
  type        = string
  default     = "us-east-1"
}

variable "lambda_memory_size" {
  description = "Обсяг пам'яті для Lambda функції (MB)."
  type        = number
  default     = 1024
}

variable "lambda_timeout" {
  description = "Тайм-аут Lambda функції (секунди)."
  type        = number
  default     = 120
}

variable "lambda_image_uri" {
  description = "Повний URI Docker-образу Lambda в ECR (передається з CI/CD)."
  type        = string
  default     = "" # За замовчуванням порожньо, Terraform використає :latest
}