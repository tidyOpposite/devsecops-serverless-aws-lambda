# Створення SQS черги для недоставлених повідомлень Lambda (Dead Letter Queue).

resource "aws_sqs_queue" "lambda_dlq" {
  name = "${var.project_name}-lambda-dlq"
  # ВИПРАВЛЕНО: Використовуємо наш KMS ключ (AVD-AWS-0135)
  kms_master_key_id                 = aws_kms_key.encryption_key.arn # Шифрування повідомлень
  kms_data_key_reuse_period_seconds = 300

  tags = {
    Project   = var.project_name
    ManagedBy = "Terraform"
  }
}