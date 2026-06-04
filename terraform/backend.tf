terraform {
  backend "s3" {
    bucket  = "replace-with-your-terraform-state-bucket"
    key     = "global/s3/terraform.tfstate" # Шлях до файлу стану всередині бакету
    region  = "us-east-1"
    encrypt = true # Вмикаємо шифрування файлу стану
  }
}
