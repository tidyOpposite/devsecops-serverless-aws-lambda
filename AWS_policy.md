# Політика AWS IAM для CI/CD Пайплайну GitHub Actions

Цей документ описує конфігурацію AWS IAM, необхідну для надання дозволів робочому процесу (workflow) GitHub Actions на розгортання інфраструктури та додатків проекту GIF Generator.

## 1. Огляд

Для безпечної взаємодії між GitHub Actions та AWS використовується механізм OpenID Connect (OIDC). Це дозволяє уникнути зберігання довготривалих статичних ключів доступу AWS (Access Key ID та Secret Access Key) у секретах GitHub. Замість цього, робочий процес GitHub Actions отримує тимчасові облікові дані AWS шляхом "обміну" OIDC токена на спеціально створену IAM роль в AWS.

Основні компоненти:

1.  **IAM OIDC Identity Provider:** Налаштований в AWS для довіри до `token.actions.githubusercontent.com`.
2.  **IAM Role for GitHub Actions:** Спеціальна IAM роль, яку може "прийняти" (assume) робочий процес GitHub Actions.
    *   **Trust Policy (Політика довіри):** Визначає, хто може прийняти цю роль. Вона обмежує доступ лише до запитів від OIDC провайдера GitHub для конкретного репозиторію та гілки.
    *   **Permissions Policy (Політика дозволів):** Визначає, які дії (AWS API calls) може виконувати той, хто прийняв цю роль. Дотримується принципу найменших привілеїв.

## 2. Налаштування OIDC Провайдера в AWS IAM

Перед створенням ролі необхідно налаштувати OIDC провайдера в консолі AWS IAM:

*   **Provider URL:** `https://token.actions.githubusercontent.com`
*   **Audience:** `sts.amazonaws.com`

Детальні інструкції доступні в [документації AWS](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html) та [документації GitHub Actions](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services).

## 3. IAM Роль для GitHub Actions

Необхідно створити IAM роль з наступними політиками.

### 3.1. Політика Довіри (Trust Policy)

Ця політика дозволяє GitHub Actions OIDC провайдеру приймати цю роль, але **лише** для робочих процесів, що запускаються з вашого конкретного репозиторію та, рекомендовано, лише для основної гілки (наприклад, `main`).

**Приклад Політики Довіри:**

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
            },
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Condition": {
                "StringEquals": {
                    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                },
                "StringLike": {
                    "token.actions.githubusercontent.com:sub": "repo:<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>:ref:refs/heads/main"
                    // Або для будь-якої гілки: "repo:<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>:*"
                    // Або для конкретного workflow: "repo:<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>:workflow:<WORKFLOW_NAME>"
                }
            }
        }
    ]
}
```

**Зауваження:**

*   Замініть `<ACCOUNT_ID>` на ваш ID облікового запису AWS.
*   Замініть `<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>` на ваш шлях до репозиторію GitHub.
*   Рекомендується обмежити політику конкретною гілкою (`ref:refs/heads/main`) або навіть конкретним файлом workflow (`workflow:<WORKFLOW_NAME>`), щоб мінімізувати ризики.

### 3.2. Політика Дозволів (Permissions Policy)

Ця політика надає необхідні дозволи для виконання завдань CI/CD пайплайну: розгортання інфраструктури за допомогою Terraform та публікації Docker-образу в ECR. Дозволи мають бути максимально обмеженими (принцип найменших привілеїв).

**Необхідні дозволи (приклад):**

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "TerraformStateAndLocking",
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:ListBucket", // Потрібно для terraform init
                "s3:DeleteObject" // Потрібно для видалення стану при destroy
            ],
            "Resource": [
                "arn:aws:s3:::<TERRAFORM_STATE_BUCKET_NAME>/*",
                "arn:aws:s3:::<TERRAFORM_STATE_BUCKET_NAME>" // Для ListBucket
            ]
        },
        {
            "Sid": "TerraformDynamoDBLocking",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:DeleteItem",
                "dynamodb:DescribeTable" // Потрібно для перевірки існування таблиці
            ],
            "Resource": "arn:aws:dynamodb:<AWS_REGION>:<ACCOUNT_ID>:table/<TERRAFORM_LOCK_TABLE_NAME>"
        },
        {
            "Sid": "ECRAuthAndPush",
            "Effect": "Allow",
            "Action": [
                "ecr:GetAuthorizationToken",
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage",
                "ecr:DescribeRepositories" // Дозволяє перевіряти існування репозиторію
                // "ecr:CreateRepository" // Розкоментуйте, якщо репозиторій не створюється через Terraform
            ],
            "Resource": "*" // ecr:GetAuthorizationToken потребує "*"
            // Можна обмежити інші дії ECR конкретним ARN репозиторію:
            // "Resource": "arn:aws:ecr:<AWS_REGION>:<ACCOUNT_ID>:repository/<ECR_REPOSITORY_NAME>"
        },
        {
            "Sid": "ManageAWSServicesWithTerraform",
            "Effect": "Allow",
            "Action": [
                // Дозволи для керування ресурсами, визначеними в Terraform
                // S3
                "s3:CreateBucket",
                "s3:DeleteBucket",
                "s3:PutBucketPolicy",
                "s3:GetBucketPolicy",
                "s3:DeleteBucketPolicy",
                "s3:PutBucketPublicAccessBlock",
                "s3:GetBucketPublicAccessBlock",
                "s3:PutBucketVersioning",
                "s3:GetBucketVersioning",
                "s3:PutLifecycleConfiguration",
                "s3:GetLifecycleConfiguration",
                "s3:PutBucketLogging",
                "s3:GetBucketLogging",
                "s3:ListBucketVersions", // Може бути потрібним для destroy
                // Lambda
                "lambda:CreateFunction",
                "lambda:DeleteFunction",
                "lambda:GetFunction",
                "lambda:GetFunctionConfiguration",
                "lambda:UpdateFunctionCode",
                "lambda:UpdateFunctionConfiguration",
                "lambda:AddPermission", // Для API Gateway trigger
                "lambda:RemovePermission",
                "lambda:TagResource",
                "lambda:UntagResource",
                // IAM (для створення/керування ролями Lambda, політиками)
                "iam:CreateRole",
                "iam:DeleteRole",
                "iam:GetRole",
                "iam:AttachRolePolicy",
                "iam:DetachRolePolicy",
                "iam:PutRolePolicy",
                "iam:GetRolePolicy",
                "iam:DeleteRolePolicy",
                "iam:CreatePolicy",
                "iam:DeletePolicy",
                "iam:GetPolicy",
                "iam:PassRole", // Дуже важливо для передачі ролі сервісам (Lambda, etc.)
                // API Gateway
                "apigateway:*", // Можна деталізувати, але для Terraform часто простіше дати всі
                // SQS
                "sqs:CreateQueue",
                "sqs:DeleteQueue",
                "sqs:GetQueueAttributes",
                "sqs:SetQueueAttributes",
                "sqs:GetQueueUrl",
                "sqs:TagQueue",
                "sqs:UntagQueue",
                // KMS
                "kms:CreateKey",
                "kms:DescribeKey",
                "kms:EnableKeyRotation",
                "kms:GetKeyPolicy",
                "kms:PutKeyPolicy",
                "kms:GetKeyRotationStatus",
                "kms:ScheduleKeyDeletion",
                "kms:CancelKeyDeletion",
                "kms:TagResource",
                "kms:UntagResource",
                // CloudWatch Logs
                "logs:CreateLogGroup",
                "logs:DeleteLogGroup",
                "logs:DescribeLogGroups",
                "logs:PutRetentionPolicy",
                "logs:TagLogGroup", // Залежить від версії провайдера Terraform
                // ECR (якщо керується Terraform)
                "ecr:CreateRepository",
                "ecr:DeleteRepository",
                "ecr:SetRepositoryPolicy",
                "ecr:GetRepositoryPolicy",
                "ecr:DeleteRepositoryPolicy",
                "ecr:PutImageTagMutability",
                "ecr:PutImageScanningConfiguration",
                // X-Ray
                "xray:PutEncryptionConfig" // Якщо налаштовується через Terraform
                // Інші сервіси, якими керує Terraform...
            ],
            "Resource": "*" // Багато дій Terraform потребують широких дозволів на створення/пошук ресурсів
            // **РЕКОМЕНДАЦІЯ:** По можливості, звужуйте Resource ARN для Delete*, Get*, Update* дій
        }
        // {
        //     "Sid": "AllowRunSpecificChecks", // Опціональний блок для додаткових перевірок в пайплайні
        //     "Effect": "Allow",
        //     "Action": [
        //         "lambda:GetFunctionConfiguration",
        //         "s3:ListBucket"
        //         // ... інші дії лише для читання
        //     ],
        //     "Resource": "*" // Або конкретні ARN
        // }
    ]
}
```

**Важливі Зауваження щодо Дозволів:**

*   Замініть плейсхолдери (`<TERRAFORM_STATE_BUCKET_NAME>`, `<TERRAFORM_LOCK_TABLE_NAME>`, `<AWS_REGION>`, `<ACCOUNT_ID>`, `<ECR_REPOSITORY_NAME>`) на ваші реальні значення.
*   Дозвіл `iam:PassRole` є критично важливим, щоб Terraform міг призначити створену `lambda_exec_role` до Lambda функції.
*   Дозволи для `ManageAWSServicesWithTerraform` надано досить широко (`"Resource": "*"`). Це спрощує конфігурацію, але не є ідеально безпечним. Для максимальної безпеки можна спробувати обмежити `Resource` для кожної дії конкретними ARN, де це можливо, хоча це значно ускладнює підтримку політики при зміні інфраструктури. Розгляньте використання інструментів типу [iamlive](https://github.com/iann0036/iamlive) для генерації більш точних політик на основі реальних викликів Terraform.
*   Дозволи `apigateway:*` надані повністю. Їх можна звузити до конкретних дій (`apigateway:GET`, `apigateway:POST`, `apigateway:PUT`, `apigateway:DELETE` тощо), якщо це необхідно.
*   Дозволи для ECR (`Resource: "*"`) для `ecr:GetAuthorizationToken` є необхідністю. Інші дії ECR можна обмежити конкретним ARN репозиторію, якщо він відомий заздалегідь або керується Terraform.
*   Переконайтеся, що ця роль НЕ МАЄ дозволів на керування самою собою або OIDC провайдером.

## 4. Використання Ролі в GitHub Actions Workflow

У вашому файлі workflow (`.github/workflows/main.yml` або подібному) необхідно додати кроки для конфігурації AWS Credentials за допомогою офіційного action `aws-actions/configure-aws-credentials`.

**Приклад фрагменту Workflow:**

```yaml
name: Deploy to AWS

on:
  push:
    branches:
      - main

permissions:
  id-token: write # Необхідно для OIDC
  contents: read # Необхідно для checkout

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v2 # Або новіша версія
        with:
          role-to-assume: arn:aws:iam::<ACCOUNT_ID>:role/<YOUR_GITHUB_ACTIONS_ROLE_NAME> # ARN вашої створеної ролі
          aws-region: <AWS_REGION> # Ваш регіон AWS

      # Кроки для lint, test, terraform init/plan/apply, docker build/push...
      # Наприклад:
      - name: Setup Terraform
        uses: hashicorp/setup-terraform@v2
        # ...

      - name: Terraform Apply
        run: terraform apply -auto-approve
        working-directory: ./terraform

      - name: Login to Amazon ECR
        id: login-ecr
        uses: aws-actions/amazon-ecr-login@v1

      - name: Build, tag, and push image to Amazon ECR
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          ECR_REPOSITORY: <ECR_REPOSITORY_NAME>
          IMAGE_TAG: ${{ github.sha }}
        run: |
          docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
          docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
        # ...
```