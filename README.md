# Secure Serverless DevSecOps Pipeline for AWS Lambda

Open-source reference implementation of a security-focused CI/CD pipeline for
containerized AWS Lambda workloads. The repository combines GitHub Actions,
Terraform, Docker, AWS OIDC authentication, SAST, SCA, IaC scanning, and
container scanning in one reproducible example.

## Introduction

This document provides detailed instructions for setting up and running the
DevSecOps pipeline for the "GIF Preview Generator" project. The main element of
the project is the CI/CD pipeline built with GitHub Actions, which automates
build, security scanning, and deployment of the infrastructure and application
to AWS. The demo application, an AWS Lambda function that generates GIF
previews, serves as a realistic workload for this pipeline.

## What This Repository Contains

*   A GitHub Actions workflow with secure AWS authentication through OIDC.
*   Terraform configuration for AWS Lambda, API Gateway, S3, ECR, KMS, SQS DLQ,
    CloudWatch Logs, and IAM.
*   Multi-layer security checks: Bandit for Python SAST, Snyk for dependencies
    and containers, and Trivy for IaC scanning.
*   A Dockerfile for an AWS Lambda container image with FFmpeg.
*   A demo frontend and Lambda function for generating GIF previews from video.
*   Documentation covering AWS IAM policies, the security model, contribution
    process, and the MIT license.

## Prerequisites

Before setup, make sure you have the following:

1.  **AWS account**: An active Amazon Web Services account with permissions to
    create the required resources: IAM, S3, ECR, Lambda, API Gateway, KMS, SQS,
    and CloudWatch.
2.  **GitHub account**: Required for hosting the code and using GitHub Actions.
3.  **Snyk account (recommended)**: Used for scanning dependencies and Docker
    images for vulnerabilities. If you do not have a Snyk account, the Snyk
    steps in the pipeline can be skipped or adapted.
4.  **Terraform CLI (local, for review and validation)**: Terraform 1.0.0 or
    later for local validation and understanding the IaC configuration. You can
    download it from the [official Terraform website](https://www.terraform.io/downloads.html).
5.  **Docker (local, for review and testing)**: Required for local Docker image
    builds and tests. You can download it from the [official Docker website](https://www.docker.com/products/docker-desktop).
6.  **AWS CLI (local, for verification)**: Installed and configured AWS Command
    Line Interface for interacting with AWS. Installation instructions are
    available [here](https://aws.amazon.com/cli/).

## AWS Environment Setup

The following AWS setup is required for the pipeline to work correctly.

### 1. Create an S3 Bucket for Terraform State

Terraform uses a state file to track created resources. It is recommended to
store this state file in a secure remote location, such as an S3 bucket.

1.  **Create an S3 bucket manually through the AWS Management Console, AWS CLI,
    or Terraform outside this project.**
    *   **Bucket name**: Must be globally unique. Save this name.
    *   **Region**: Choose the region where you plan to deploy the main project
        resources.
    *   **Versioning**: Enable versioning to protect against accidental deletion
        or corruption of the state file.
    *   **Encryption**: It is recommended to enable default encryption, for
        example AES-256.
    *   **Block public access**: Make sure all public access block settings are
        enabled for this bucket.

2.  **Update the Terraform backend configuration**:
    In `terraform/backend.tf`, replace the `bucket` value with the name of the
    S3 bucket you created. If you selected a different region for the state
    bucket than the region used for the rest of the deployment (`us-east-1` by
    default), update the `region` value in the `backend "s3"` block as well.

    ```terraform
    # terraform/backend.tf
    terraform {
      backend "s3" {
        bucket         = "your-unique-s3-bucket-name-for-terraform-state" # REPLACE THIS VALUE
        key            = "global/s3/terraform.tfstate"
        region         = "us-east-1" # Region of your S3 state bucket
        encrypt        = true
      }
    }
    ```

    **Note**: Although `terraform/backend.tf` already exists in this project,
    the S3 bucket for storing Terraform state is usually a prerequisite that is
    created once before the first `terraform init`. Terraform cannot create the
    bucket for its own state during the first initialization when the backend is
    already configured to use S3. For production use, it is also recommended to
    add DynamoDB state locking or another mechanism to control parallel
    Terraform runs.

### 2. Configure the IAM OpenID Connect (OIDC) Provider

This allows GitHub Actions to authenticate securely to your AWS account without
storing long-lived access keys.

1.  Go to **IAM** in the AWS Management Console.
2.  In the navigation panel, choose **Identity providers**.
3.  Click **Add provider**.
4.  Select **OpenID Connect** as the provider type.
5.  For **Provider URL**, enter: `https://token.actions.githubusercontent.com`
6.  For **Audience**, enter: `sts.amazonaws.com`
7.  Click **Get thumbprint** to retrieve the certificate thumbprint.
8.  Click **Add provider**.

Detailed instructions are available in the [AWS documentation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html).

### 3. Create an IAM Role for GitHub Actions

This role defines which permissions the GitHub Actions pipeline will have in
AWS.

1.  Go to **IAM** in the AWS Management Console.
2.  In the navigation panel, choose **Roles**.
3.  Click **Create role**.
4.  For **Trusted entity type**, choose **Web identity**.
5.  In the **Web identity** section:
    *   Choose the **Identity provider** you created in the previous step, the
        one associated with `token.actions.githubusercontent.com`.
    *   For **Audience**, choose `sts.amazonaws.com`, usually the only available
        option.
    *   Optional but recommended for better security: in **GitHub organization
        or repository (optional)**, specify:
        *   **Organization**: Your GitHub username, for example `MyGitHubUser`.
        *   **Repository name**: Your repository name, for example
            `diploma_project-main`.
        *   **Branch (optional but recommended)**: Specify the main branch, for
            example `main`. This limits role assumption to workflows running
            from that branch. You can also specify `*` for any branch if needed.
6.  Click **Next**.
7.  **Add permissions policies**:
    At this step, grant the role the permissions needed for Terraform
    operations and interactions with ECR, S3, Lambda, and other services. It is
    recommended to create a custom least-privilege policy. A detailed example is
    provided in `AWS_policy.md`, section 3.2, "Permissions Policy".
    *   Click **Create policy**. A new window or tab opens.
    *   Open the **JSON** tab.
    *   Copy the example policy from `AWS_policy.md` section 3.2 and carefully
        replace all placeholders (`<ACCOUNT_ID>`, `<AWS_REGION>`,
        `<TERRAFORM_STATE_BUCKET_NAME>`, `<ECR_REPOSITORY_NAME>`, and others)
        with your actual values. `<ECR_REPOSITORY_NAME>` corresponds to the
        `ECR_REPOSITORY` variable in `.github/workflows/deploy.yml`; by default
        it is `<PROJECT_NAME>-lambda-repo`. `<TERRAFORM_STATE_BUCKET_NAME>` is
        the bucket name created in step 1.
    *   Click **Next: Tags** and optionally add tags.
    *   Click **Next: Review**.
    *   Set **Name**, for example `GitHubActionsGIFGeneratorPolicy`.
    *   Add a description if needed.
    *   Click **Create policy**.
    *   Return to the role creation tab, refresh the policy list, find the
        newly created policy by name, and select it.
8.  Click **Next**.
9.  **Configure the role**:
    *   Set **Role name**, for example `GitHubActionsGIFGeneratorRole`. Save
        this name and the role **ARN**; they are needed for GitHub Secrets.
    *   Add a description if needed.
    *   Verify **Trusted entities**: it should include the ARN of your OIDC
        provider and the repository or branch conditions if you configured them.
    *   Verify **Permissions policies**: your policy should be attached.
10. Click **Create role**.

## GitHub Repository and Actions Setup

### 1. Fork or Clone the Repository

If you are using this project as a template, fork the repository to your GitHub
account or clone it.

### 2. Configure GitHub Secrets

GitHub Secrets are used to store sensitive values required by the pipeline.

Go to your GitHub repository, then **Settings** > **Secrets and variables** >
**Actions**. Create the following repository secrets with **New repository
secret**:

1.  **`AWS_ROLE_TO_ASSUME_ARN`**:
    *   **Value**: The full ARN of the IAM role created for GitHub Actions, for
        example `arn:aws:iam::123456789012:role/GitHubActionsGIFGeneratorRole`.

2.  **`AWS_REGION`**:
    *   **Value**: The AWS region where resources will be deployed, for example
        `us-east-1`. This should match the region used for the IAM role and
        Terraform resources.

3.  **`SNYK_TOKEN` (optional but recommended)**:
    *   **Value**: Your Snyk API token. You can find it in your Snyk account
        settings. If you do not plan to use Snyk, the Snyk scanning steps in
        `.github/workflows/deploy.yml` can be commented out or removed, and
        this secret is not required.

### 3. Configure GitHub Variables If Needed

GitHub Variables store non-sensitive configuration. In this project, the
`PROJECT_NAME` variable, which defaults to `gif-generator`, is used in
`.github/workflows/deploy.yml` to construct the ECR repository name
(`${{ vars.PROJECT_NAME || 'gif-generator' }}-lambda-repo`) and is passed to
Terraform to name AWS resources.

If you want to change the project name used for resource names:

1.  Go to your GitHub repository, then **Settings** > **Secrets and variables** >
    **Actions**.
2.  Open the **Variables** tab.
3.  Click **New repository variable**.
4.  **Name**: `PROJECT_NAME`
5.  **Value**: Your desired project name, for example `my-gif-app`.

If you do not create this variable, the default value `gif-generator` is used.
Make sure this project name, whether default or custom, is consistent with the
placeholders you replaced in the IAM permissions policy, such as the ECR
repository ARN.

Also, if you changed `project_name` in `terraform/variables.tf` from
`gif-generator` to something else, make sure the GitHub Variable `PROJECT_NAME`
matches that value, or update the IAM policy placeholders accordingly.

## Running and Monitoring the Pipeline

### 1. Starting a Run

The DevSecOps pipeline is defined in `.github/workflows/deploy.yml` and is
configured for manual execution only. It does not run automatically on every
commit.

To start it manually:

1.  Go to your repository on GitHub.
2.  Open the **Actions** tab.
3.  Select **Deploy GIF Generator to AWS**.
4.  Click **Run workflow**.
5.  Select the `main` branch and confirm the run.

For the first infrastructure deployment, complete all prerequisite AWS and
GitHub setup steps first, including the Terraform state bucket, OIDC role,
GitHub Secrets, and any required updates to `terraform/backend.tf`.

### 2. Monitoring Progress

You can monitor pipeline execution in real time:

1.  Go to your repository on GitHub.
2.  Open the **Actions** tab.
3.  In **All workflows**, select the workflow named **Deploy GIF Generator to
    AWS**.
4.  Click a specific workflow run to inspect its steps and logs.

### 3. Expected Results and Artifacts

After a successful manual workflow run on `main`:

*   **AWS infrastructure is deployed**: All resources defined in Terraform, such
    as S3 buckets, ECR repository, KMS key, Lambda function, API Gateway, and
    SQS queue, are created or updated in your AWS account.
*   **Docker image in ECR**: The Lambda Docker image is built and pushed to the
    Amazon ECR repository with the `latest` tag and the commit SHA tag.
*   **Lambda function is updated**: The Lambda function is configured to use the
    newly pushed Docker image.
*   **Frontend is deployed**: Static frontend files from `frontend/` are synced
    to the corresponding S3 bucket, and `frontend/script.js` is updated with
    the current API Gateway endpoint.
*   **Terraform outputs**: Terraform outputs, such as the API Gateway URL and
    S3 bucket names, can be viewed in the workflow logs or locally with
    `terraform output` after backend configuration and `terraform init`.

The frontend URL corresponds to the S3 website endpoint available in the
Terraform output `frontend_s3_website_endpoint`.

## Project Structure

Short description of the main directories and files:

*   `/.github/workflows/deploy.yml`: Defines the DevSecOps CI/CD pipeline using
    GitHub Actions.
*   `/terraform/`: Contains all infrastructure as code using Terraform.
    *   `main.tf` or files such as `provider.tf`, `variables.tf`, `outputs.tf`,
        `s3.tf`, `lambda.tf`, and others: Terraform configuration files for
        different AWS resources.
    *   `backend.tf`: S3 backend configuration for storing Terraform state.
*   `/lambda_function/`: Contains the Python code for the demo Lambda function.
    *   `lambda_function.py`: Main Lambda function file.
    *   `requirements.txt`: Python dependencies for the Lambda function.
*   `/frontend/`: Contains files for the simple web interface.
    *   `index.html`: Main HTML page.
    *   `script.js`: JavaScript for backend interaction.
    *   `style.css`: CSS styles.
*   `Dockerfile`: Instructions for building the Lambda Docker image, including
    `ffmpeg`.
*   `AWS_policy.md`: Detailed description and example IAM policy for the GitHub
    Actions role.
*   `SECURITY.md`: Project security policy.
*   `.gitignore`: Files ignored by Git.
*   `.dockerignore`: Files ignored during Docker image builds.
*   `README.md`: This setup and usage guide.

## Demo Application: GIF Preview Generator

The project includes a demo web application that allows users to upload video
files. The backend Lambda function then generates a short GIF preview. The
frontend, deployed to S3, communicates with the Lambda function through API
Gateway.

**Testing the deployed Lambda function after a successful pipeline run:**

Because the main focus is the DevSecOps pipeline rather than the application
itself, the simplest way to test the deployed Lambda function is to use a tool
such as Postman, `curl`, or any other HTTP client.

1.  **Get the API Gateway URL**: After a successful pipeline run, the API
    Gateway URL is shown as a Terraform output named `api_gateway_invoke_url`.
    It can also be found in the AWS API Gateway console.
2.  **Prepare a video file**: Choose a small video file up to 100 MB in MP4,
    MOV, AVI, MKV, WebM, or Ogg format.
3.  **Send a POST request**:
    *   **Method**: `POST`
    *   **URL**: Your API Gateway URL.
    *   **Headers**:
        *   `Content-Type`: The MIME type of your video file, for example
            `video/mp4`.
    *   **Body**: The video file content encoded as **Base64**.

    **Example with `curl` on Linux/macOS:**
    Assume you have `test_video.mp4` and an API Gateway URL such as
    `https://abcdef123.execute-api.us-east-1.amazonaws.com/`.

    ```bash
    # 1. Encode the video as Base64. The result is written to video.b64.
    base64 < test_video.mp4 | tr -d '\n' > video.b64

    # 2. Send the request, replacing the URL and Content-Type as needed.
    curl -X POST \
      --header "Content-Type: video/mp4" \
      --data-binary "@video.b64" \
      YOUR_API_GATEWAY_URL_HERE
    ```

    The response should be JSON containing a `download_url` for the generated
    GIF file stored in S3.

## DevSecOps Tools Used

The pipeline integrates the following tools for security:

1.  **Bandit**: A static application security testing (SAST) tool for finding
    common security issues in the Python Lambda code.
2.  **Snyk**:
    *   **SCA (Software Composition Analysis)**: Scans
        `lambda_function/requirements.txt` for vulnerabilities in Python
        dependencies.
    *   **Container Scan**: Scans the built Docker image for vulnerabilities in
        the operating system and system libraries.
3.  **Trivy**:
    *   **IaC Scan**: Scans Terraform configuration files (`.tf`) for security
        misconfigurations and potential infrastructure vulnerabilities.

These tools help detect security issues early in development and deployment.

## Important Pipeline Security Aspects

*   **OIDC Authentication**: Using OpenID Connect for GitHub Actions
    authentication to AWS is much safer than storing static access keys.
*   **Least Privilege**: The IAM role for GitHub Actions should be configured
    with the minimum permissions required for its tasks. Review and update this
    policy regularly. The example in `AWS_policy.md` is a useful baseline, but
    it can be restricted further.
*   **Branch Protection**: Configure protection rules for the `main` branch in
    your GitHub repository, for example requiring pull request review and status
    checks before merging.
*   **Secret Management**: Store all secrets, such as `SNYK_TOKEN` and
    `AWS_ROLE_TO_ASSUME_ARN`, in GitHub Secrets and never commit them directly
    to the repository.
*   **Security Scanning**: Do not skip the security scanning steps. Review their
    results and fix detected vulnerabilities.

This guide should help you successfully configure and run the DevSecOps
pipeline.

---

# Українська версія

## Вступ

Цей документ надає детальну інструкцію з налаштування та запуску DevSecOps-пайплайну для проєкту "Генератор GIF Прев'ю". Головним елементом проєкту є саме CI/CD пайплайн, побудований з використанням GitHub Actions, який автоматизує процеси збірки, сканування безпеки та розгортання інфраструктури і додатку в AWS. Демонстраційний додаток (Lambda-функція для генерації GIF) слугує прикладом робочого навантаження для цього пайплайну.

## Що містить репозиторій

*   GitHub Actions workflow з безпечною AWS-автентифікацією через OIDC.
*   Terraform-конфігурацію для AWS Lambda, API Gateway, S3, ECR, KMS, SQS DLQ, CloudWatch Logs та IAM.
*   Багаторівневі перевірки безпеки: Bandit для Python SAST, Snyk для залежностей і контейнерів, Trivy для IaC.
*   Dockerfile для Lambda container image з FFmpeg.
*   Демонстраційний frontend і Lambda-функцію для генерації GIF-прев'ю з відео.
*   Документацію з AWS IAM політиками, security model, contribution process та MIT license.

## Передумови

Перед початком налаштування переконайтеся, що у вас є наступне:

1.  **Обліковий запис AWS**: Активний акаунт Amazon Web Services з правами на створення необхідних ресурсів (IAM, S3, ECR, Lambda, API Gateway, KMS, SQS, CloudWatch).
2.  **Обліковий запис GitHub**: Для розміщення коду та використання GitHub Actions.
3.  **Обліковий запис Snyk (рекомендовано)**: Для сканування залежностей та Docker-образу на вразливості. Якщо у вас немає облікового запису Snyk, відповідні кроки в пайплайні можна буде пропустити або адаптувати.
4.  **Terraform CLI (локально, для розуміння)**: Встановлений Terraform версії 1.0.0 або вище для можливості локальної перевірки та розуміння IaC конфігурацій. Завантажити можна з [офіційного сайту Terraform](https://www.terraform.io/downloads.html).
5.  **Docker (локально, для розуміння)**: Встановлений Docker для можливості локальної збірки та тестування Docker-образу. Завантажити можна з [офіційного сайту Docker](https://www.docker.com/products/docker-desktop).
6.  **AWS CLI (локально, для перевірки)**: Встановлений та налаштований AWS Command Line Interface для взаємодії з AWS. Інструкції з встановлення доступні [тут](https://aws.amazon.com/cli/).

## Налаштування Середовища AWS

Для коректної роботи пайплайну необхідно виконати наступні налаштування у вашому AWS акаунті.

### 1. Створення S3 Бакету для Зберігання Стану Terraform

Terraform використовує файл стану для відстеження створених ресурсів. Цей файл стану рекомендується зберігати у віддаленому та безпечному місці, наприклад, в S3 бакеті.

1.  **Створіть S3 бакет вручну через AWS Management Console, AWS CLI або Terraform (поза цим проєктом).**
    *   **Назва бакету**: Має бути глобально унікальною. Занотуйте цю назву.
    *   **Регіон**: Оберіть регіон, в якому ви плануєте розгортати основні ресурси проєкту.
    *   **Версіонування**: Увімкніть версіонування для бакету, щоб захиститися від випадкового видалення або пошкодження файлу стану.
    *   **Шифрування**: Рекомендовано увімкнути шифрування за замовчуванням (наприклад, AES-256).
    *   **Блокування публічного доступу**: Переконайтеся, що всі налаштування блокування публічного доступу увімкнені для цього бакету.

2.  **Оновіть конфігурацію Terraform Backend**:
    У файлі `terraform/backend.tf` замініть значення `bucket` на назву створеного вами S3 бакету. Якщо ви обрали інший регіон для бакету, ніж той, що буде використовуватись для розгортання інших ресурсів (`us-east-1` за замовчуванням), оновіть також параметр `region` у блоці `backend "s3"`.

    ```terraform
    # terraform/backend.tf
    terraform {
      backend "s3" {
        bucket         = "ваша-унікальна-назва-s3-бакету-для-стану" # ЗАМІНІТЬ ЦЕ ЗНАЧЕННЯ
        key            = "global/s3/terraform.tfstate"
        region         = "us-east-1" # Регіон вашого S3 бакету для стану
        encrypt        = true
      }
    }
    ```

    **Примітка**: Хоча файл `terraform/backend.tf` вже існує в проєкті, створення самого S3 бакету для зберігання стану зазвичай є попереднім кроком, який виконується один раз. Terraform не може створити бакет для власного стану під час першого `terraform init`, якщо backend вже налаштований на використання S3. Для production-використання також рекомендується додати DynamoDB state locking або інший контроль паралельних запусків Terraform.

### 2. Налаштування IAM OpenID Connect (OIDC) Провайдера

Це дозволить GitHub Actions безпечно автентифікуватися у вашому AWS акаунті без необхідності зберігання довготривалих ключів доступу.

1.  Перейдіть до сервісу **IAM** в AWS Management Console.
2.  У навігаційній панелі оберіть **Identity providers**.
3.  Натисніть **Add provider**.
4.  Оберіть тип провайдера **OpenID Connect**.
5.  Для **Provider URL** вкажіть: `https://token.actions.githubusercontent.com`
6.  Для **Audience** вкажіть: `sts.amazonaws.com`
7.  Натисніть **Get thumbprint**, щоб отримати відбиток сертифікату.
8.  Натисніть **Add provider**.

Детальні інструкції доступні в [документації AWS](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html).

### 3. Створення IAM Ролі для GitHub Actions

Ця роль визначатиме, які дозволи матиме ваш GitHub Actions пайплайн у AWS.

1.  Перейдіть до сервісу **IAM** в AWS Management Console.
2.  У навігаційній панелі оберіть **Roles**.
3.  Натисніть **Create role**.
4.  Для **Trusted entity type** оберіть **Web identity**.
5.  У секції **Web identity**:
    *   Оберіть **Identity provider**, який ви створили на попередньому кроці (той, що пов'язаний з `token.actions.githubusercontent.com`).
    *   Для **Audience** оберіть `sts.amazonaws.com` (зазвичай це єдиний доступний варіант).
    *   (Опціонально, але рекомендовано для підвищення безпеки) У секції **GitHub organization or repository (optional)** вкажіть:
        *   **Organization**: Ваш логін GitHub (наприклад, `MyGitHubUser`).
        *   **Repository name**: Назва вашого репозиторію (наприклад, `diploma_project-main`).
        *   **Branch (optional but recommended)**: Вкажіть основну гілку, наприклад, `main`. Це обмежить можливість прийняття ролі лише для workflow, запущених з цієї гілки. Ви можете також вказати `*` для будь-якої гілки, якщо це необхідно.
6.  Натисніть **Next**.
7.  **Додавання політик дозволів (Permissions policies)**:
    На цьому кроці вам потрібно надати ролі дозволи, необхідні для виконання операцій Terraform, взаємодії з ECR, S3, Lambda та іншими сервісами. Рекомендовано створити власну політику, дотримуючись принципу найменших привілеїв. Детальний приклад політики наведено у файлі `AWS_policy.md` цього проєкту (секція 3.2 "Політика Дозволів").
    *   Натисніть **Create policy**. Відкриється нове вікно/вкладка.
    *   Перейдіть на вкладку **JSON**.
    *   Скопіюйте вміст прикладу політики з `AWS_policy.md` (секція 3.2), **уважно замінивши всі плейсхолдери** (`<ACCOUNT_ID>`, `<AWS_REGION>`, `<TERRAFORM_STATE_BUCKET_NAME>`, `<ECR_REPOSITORY_NAME>` тощо) на ваші актуальні значення. `<ECR_REPOSITORY_NAME>` буде відповідати значенню змінної `ECR_REPOSITORY` у файлі `.github/workflows/deploy.yml` (за замовчуванням це `<PROJECT_NAME>-lambda-repo`). `<TERRAFORM_STATE_BUCKET_NAME>` - це назва бакету, яку ви створили на кроці 1.
    *   Натисніть **Next: Tags** (опціонально, додайте теги за потреби).
    *   Натисніть **Next: Review**.
    *   Вкажіть **Name** для політики (наприклад, `GitHubActionsGIFGeneratorPolicy`).
    *   Надайте опис (опціонально).
    *   Натисніть **Create policy**.
    *   Поверніться на вкладку створення ролі. Оновіть список політик (кнопка-іконка оновлення) та знайдіть щойно створену політику за її назвою. Виберіть її (поставте галочку).
8.  Натисніть **Next**.
9.  **Налаштування ролі**:
    *   Вкажіть **Role name** (наприклад, `GitHubActionsGIFGeneratorRole`). Занотуйте цю назву, а також **ARN** цієї ролі – вони знадобляться для налаштування GitHub Secrets.
    *   Надайте опис (опціонально).
    *   Перевірте **Trusted entities** – там має бути вказаний ARN вашого OIDC провайдера та умови (conditions) на репозиторій/гілку, якщо ви їх вказували.
    *   Перевірте **Permissions policies** – там має бути прикріплена ваша політика.
10. Натисніть **Create role**.

## Налаштування GitHub Репозиторію та Actions

### 1. Форк/Клонування Репозиторію

Якщо ви працюєте з цим проєктом як з шаблоном, створіть форк репозиторію на свій GitHub акаунт, або клонуйте його.

### 2. Налаштування GitHub Secrets

GitHub Secrets використовуються для безпечного зберігання чутливої інформації, яка потрібна для роботи пайплайну.

Перейдіть до вашого репозиторію на GitHub, потім **Settings** > **Secrets and variables** > **Actions**. Створіть наступні секрети (кнопка **New repository secret**):

1.  **`AWS_ROLE_TO_ASSUME_ARN`**:
    *   **Значення**: Повний ARN IAM ролі, яку ви створили на попередньому кроці для GitHub Actions (наприклад, `arn:aws:iam::123456789012:role/GitHubActionsGIFGeneratorRole`).

2.  **`AWS_REGION`**:
    *   **Значення**: Регіон AWS, в якому ви плануєте розгортати ресурси (наприклад, `us-east-1`). Це має бути той самий регіон, який ви вказували при створенні IAM ролі та для якого налаштовані ресурси в Terraform.

3.  **`SNYK_TOKEN` (опціонально, але рекомендовано)**:
    *   **Значення**: Ваш токен доступу Snyk API. Його можна знайти в налаштуваннях вашого облікового запису Snyk. Якщо ви не плануєте використовувати Snyk, кроки сканування Snyk у файлі `.github/workflows/deploy.yml` можна закоментувати або видалити, і цей секрет не потрібен.

### 3. Налаштування GitHub Variables (якщо необхідно)

GitHub Variables використовуються для зберігання нечутливої конфігураційної інформації. У цьому проєкті змінна `PROJECT_NAME` (за замовчуванням `gif-generator`) використовується у файлі `.github/workflows/deploy.yml` для формування імені ECR репозиторію (`${{ vars.PROJECT_NAME || 'gif-generator' }}-lambda-repo`) та передається в Terraform для іменування інших ресурсів.

Якщо ви хочете змінити назву проєкту, яка буде використовуватись для іменування ресурсів:

1.  Перейдіть до вашого репозиторію на GitHub, потім **Settings** > **Secrets and variables** > **Actions**.
2.  Перейдіть на вкладку **Variables**.
3.  Натисніть **New repository variable**.
4.  **Name**: `PROJECT_NAME`
5.  **Value**: Ваша бажана назва проєкту (наприклад, `my-gif-app`).

Якщо ви не створите цю змінну, буде використано значення за замовчуванням `gif-generator`. Переконайтеся, що назва проєкту, яку ви тут вкажете (або значення за замовчуванням), узгоджується з плейсхолдерами, які ви замінювали в IAM політиці дозволів (наприклад, для ARN ECR репозиторію).

Також, якщо ви змінили `project_name` у файлі `terraform/variables.tf` з `gif-generator` на щось інше, переконайтеся, що GitHub Variable `PROJECT_NAME` відповідає цьому значенню, або що плейсхолдери в IAM політиці оновлені відповідно.

## Запуск та Моніторинг Пайплайну

### 1. Ініціація Запуску

DevSecOps-пайплайн, визначений у файлі `.github/workflows/deploy.yml`, налаштований тільки на ручний запуск. Він не запускається автоматично при кожному коміті.

Щоб запустити пайплайн вручну:

1.  Перейдіть до вашого репозиторію на GitHub.
2.  Відкрийте вкладку **Actions**.
3.  Виберіть **Deploy GIF Generator to AWS**.
4.  Натисніть **Run workflow**.
5.  Виберіть гілку `main` та підтвердьте запуск.

Для першого запуску та розгортання інфраструктури, перед запуском workflow виконайте всі попередні кроки налаштування AWS та GitHub: створення S3 bucket для Terraform state, налаштування OIDC ролі, GitHub Secrets та оновлення `terraform/backend.tf`.

### 2. Спостереження за Прогресом

Ви можете спостерігати за виконанням пайплайну в реальному часі:

1.  Перейдіть до вашого репозиторію на GitHub.
2.  Відкрийте вкладку **Actions**.
3.  У списку "All workflows" виберіть пайплайн (назва за замовчуванням: **Deploy GIF Generator to AWS**).
4.  Натисніть на конкретний запуск пайплайну, щоб переглянути його кроки та лог-файли для кожного кроку.

### 3. Очікувані Результати та Артефакти

Після успішного ручного запуску пайплайну на гілці `main`:

*   **Інфраструктура AWS розгорнута**: Всі ресурси, визначені у файлах Terraform (S3 бакети, ECR репозиторій, KMS ключ, Lambda функція, API Gateway, SQS черга тощо), будуть створені або оновлені у вашому AWS акаунті.
*   **Docker-образ в ECR**: Зібраний Docker-образ для Lambda-функції буде завантажено до створеного Amazon ECR репозиторію з тегами `latest` та хешем коміту.
*   **Lambda-функція оновлена**: Lambda-функція буде налаштована на використання щойно завантаженого Docker-образу.
*   **Фронтенд розгорнуто**: Статичні файли фронтенду (з `frontend/`) будуть завантажені до відповідного S3 бакету, а файл `frontend/script.js` буде оновлено актуальним URL-ендпоінтом API Gateway.
*   **Вихідні дані Terraform**: Ви можете перевірити вихідні дані Terraform (такі як URL API Gateway або назви S3 бакетів) у логах кроку "Get Terraform Outputs for Frontend" або виконавши `terraform output` локально (після налаштування backend та `terraform init`).

URL для доступу до фронтенд-додатку буде відповідати ендпоінту S3 веб-сайту, який можна знайти у вихідних даних Terraform (`frontend_s3_website_endpoint`).

## Структура Проєкту

Короткий опис основних директорій та файлів:

*   `/.github/workflows/deploy.yml`: Визначає DevSecOps CI/CD пайплайн за допомогою GitHub Actions.
*   `/terraform/`: Містить всю інфраструктуру як код (IaC) за допомогою Terraform.
    *   `main.tf` (або `provider.tf`, `variables.tf`, `outputs.tf`, `s3.tf`, `lambda.tf` тощо): Файли конфігурації Terraform для різних ресурсів AWS.
    *   `backend.tf`: Конфігурація S3 backend для зберігання стану Terraform.
*   `/lambda_function/`: Містить код Python для демонстраційної Lambda-функції.
    *   `lambda_function.py`: Основний файл Lambda-функції.
    *   `requirements.txt`: Залежності Python для Lambda-функції.
*   `/frontend/`: Містить файли для простого веб-інтерфейсу.
    *   `index.html`: Головна HTML-сторінка.
    *   `script.js`: JavaScript для взаємодії з бекендом.
    *   `style.css`: CSS-стилі.
*   `Dockerfile`: Інструкції для збірки Docker-образу Lambda-функції, включаючи `ffmpeg`.
*   `AWS_policy.md`: Детальний опис та приклад IAM політики для ролі GitHub Actions.
*   `SECURITY.md`: Політика безпеки проєкту.
*   `.gitignore`: Файли, які ігноруються системою Git.
*   `.dockerignore`: Файли, які ігноруються при збірці Docker-образу.
*   `README.md`: Цей файл – інструкції з налаштування та запуску.

## Демонстраційний Додаток (Генератор GIF Прев'ю)

Проєкт включає демонстраційний веб-додаток, який дозволяє користувачам завантажувати відеофайли, після чого Lambda-функція на бекенді генерує коротке GIF-прев'ю. Фронтенд (розгортається на S3) взаємодіє з Lambda-функцією через API Gateway.

**Перевірка розгорнутої Lambda-функції (після успішного пайплайну):**

Оскільки основний фокус проєкту на DevSecOps-пайплайні, а не на самому додатку, найпростіший спосіб перевірити роботу розгорнутої Lambda-функції (якщо це необхідно) – це використати інструмент типу Postman, `curl` або будь-який інший HTTP-клієнт.

1.  **Отримайте URL API Gateway**: Після успішного виконання пайплайну, URL API Gateway буде виведено як частина вихідних даних Terraform (output `api_gateway_invoke_url`). Його також можна знайти в консолі AWS API Gateway.
2.  **Підготуйте відеофайл**: Оберіть невеликий відеофайл (до 100MB, у форматі MP4, MOV, AVI, MKV, WebM або Ogg).
3.  **Надішліть POST-запит**:
    *   **Метод**: `POST`
    *   **URL**: URL вашого API Gateway.
    *   **Headers**:
        *   `Content-Type`: MIME-тип вашого відеофайлу (наприклад, `video/mp4`).
    *   **Body**: Вміст вашого відеофайлу, закодований у форматі **Base64**.

    **Приклад використання `curl` (для Linux/macOS):**
    Припустимо, у вас є файл `test_video.mp4` та URL API Gateway `https://abcdef123.execute-api.us-east-1.amazonaws.com/`.

    ```bash
    # 1. Закодуйте відео в Base64 (результат буде у файлі video.b64)
    base64 < test_video.mp4 | tr -d '\n' > video.b64

    # 2. Надішліть запит, підставивши ваш URL та Content-Type
    curl -X POST \
      --header "Content-Type: video/mp4" \
      --data-binary "@video.b64" \
      YOUR_API_GATEWAY_URL_HERE
    ```

    У відповідь ви маєте отримати JSON з посиланням (`download_url`) на згенерований GIF-файл, збережений в S3.

## Інструменти DevSecOps, що Використовуються

Пайплайн інтегрує наступні інструменти для забезпечення безпеки:

1.  **Bandit**: Інструмент статичного аналізу коду (SAST) для пошуку поширених вразливостей безпеки в Python-коді Lambda-функції.
2.  **Snyk**:
    *   **SCA (Software Composition Analysis)**: Сканує файл `lambda_function/requirements.txt` для виявлення вразливостей у залежностях Python.
    *   **Container Scan**: Сканує зібраний Docker-образ на наявність вразливостей в операційній системі та системних бібліотеках.
3.  **Trivy**:
    *   **IaC Scan**: Сканує конфігураційні файли Terraform (`.tf`) на предмет неправильних налаштувань безпеки та потенційних вразливостей в інфраструктурі.

Ці інструменти допомагають виявляти проблеми безпеки на ранніх етапах розробки та розгортання.

## Важливі Аспекти Безпеки Пайплайну

*   **OIDC Автентифікація**: Використання OpenID Connect для автентифікації GitHub Actions в AWS є значно безпечнішим, ніж зберігання статичних ключів доступу.
*   **Принцип Найменших Привілеїв**: IAM роль для GitHub Actions має бути налаштована з мінімально необхідними дозволами для виконання її завдань. Регулярно переглядайте та оновлюйте цю політику. Приклад у `AWS_policy.md` надає гарну основу, але його можна ще більше обмежити.
*   **Захист Гілок (Branch Protection)**: Налаштуйте правила захисту для гілки `main` у вашому GitHub репозиторії (наприклад, вимагати перевірку pull request перед злиттям, вимагати проходження статусних перевірок CI).
*   **Управління Секретами**: Надійно зберігайте всі секрети (такі як `SNYK_TOKEN`, `AWS_ROLE_TO_ASSUME_ARN`) в GitHub Secrets і ніколи не комітьте їх безпосередньо в код.
*   **Сканування Безпеки**: Не пропускайте кроки сканування безпеки. Аналізуйте їхні результати та виправляйте виявлені вразливості.

Сподіваємось, ця інструкція допоможе вам успішно налаштувати та запустити DevSecOps-пайплайн!
