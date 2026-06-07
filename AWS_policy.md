# AWS IAM Policy Guidance For The CLI-Managed Pipeline

DevSecOps Pipeline Kit uses the CLI as the setup and diagnostics surface, while
GitHub Actions uses OIDC to exchange a GitHub-issued identity token for
short-lived AWS credentials. Do not store long-lived AWS access keys in GitHub
secrets.

Use the CLI to render and validate repository setup:

```bash
devsecops github-setup --write
devsecops gh-setup --apply \
  --deploy-role-arn arn:aws:iam::<ACCOUNT_ID>:role/<DEPLOY_ROLE> \
  --plan-role-arn arn:aws:iam::<ACCOUNT_ID>:role/<PLAN_ROLE>
devsecops gh-doctor
devsecops branch-doctor
```

Review IAM trust and permission policies manually before applying them. The CLI
helps configure GitHub variables/secrets and diagnose repository state; it does
not create AWS IAM roles for you.

## Recommended Roles

| Role | Used by | Purpose |
| --- | --- | --- |
| `AWS_PLAN_ROLE_TO_ASSUME_ARN` | Pull request and manual plan workflows | Read Terraform state, acquire state lock, refresh resources, and produce plans. |
| `AWS_ROLE_TO_ASSUME_ARN` | Manual production deploy workflow from `main` | Apply Terraform, read configured images for optional scanning, and perform rollback. |

Use separate roles. The workflow requires `AWS_PLAN_ROLE_TO_ASSUME_ARN` for
Terraform plans and does not fall back to the deploy role. The plan role should
not be able to mutate workload resources beyond Terraform backend locking.

## OIDC Provider

Create an IAM OIDC identity provider:

* Provider URL: `https://token.actions.githubusercontent.com`
* Audience: `sts.amazonaws.com`

## Deploy Trust Policy Example

Replace placeholders with your account, owner, and repository names.

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
          "token.actions.githubusercontent.com:sub": "repo:<GITHUB_OWNER>/<REPOSITORY>:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

For PR plans, create a separate trust policy scoped to the pull request subject
patterns you are willing to trust. The bundled workflow skips AWS-backed
Terraform plans for pull requests from forks; keep that guard unless you have a
separate review and sandbox strategy for untrusted contributions.

After creating the roles, run:

```bash
devsecops compose
devsecops gh-setup --apply \
  --deploy-role-arn arn:aws:iam::<ACCOUNT_ID>:role/<DEPLOY_ROLE> \
  --plan-role-arn arn:aws:iam::<ACCOUNT_ID>:role/<PLAN_ROLE>
devsecops gh-doctor
```

`AWS_PLAN_ROLE_TO_ASSUME_ARN` is required even if older local config or presets
have `use_separate_aws_plan_role=false`. That config value is retained for
readiness posture display, but deploy-role fallback is disabled.

## Backend Access

Both plan and deploy roles need access to the Terraform backend:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TerraformStateBucket",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::<TERRAFORM_STATE_BUCKET_NAME>",
        "arn:aws:s3:::<TERRAFORM_STATE_BUCKET_NAME>/*"
      ]
    },
    {
      "Sid": "TerraformDynamoDBLocking",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:DeleteItem",
        "dynamodb:DescribeTable"
      ],
      "Resource": "arn:aws:dynamodb:<AWS_REGION>:<ACCOUNT_ID>:table/<TERRAFORM_LOCK_TABLE_NAME>"
    }
  ]
}
```

## Deploy Permissions Baseline

Terraform creates and updates IAM, KMS, S3, ECR, Lambda, API Gateway,
CloudWatch Logs, SQS, and related policies. A practical deploy-role baseline is
shown below. Tighten resources by ARN once your account, project name, and
environment names are stable.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ECRAuth",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "ECRManageAndRead",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:CreateRepository",
        "ecr:DeleteRepository",
        "ecr:DescribeImages",
        "ecr:DescribeRepositories",
        "ecr:GetDownloadUrlForLayer",
        "ecr:GetLifecyclePolicy",
        "ecr:PutImageScanningConfiguration",
        "ecr:PutImageTagMutability",
        "ecr:PutLifecyclePolicy",
        "ecr:TagResource",
        "ecr:UntagResource"
      ],
      "Resource": "arn:aws:ecr:<AWS_REGION>:<ACCOUNT_ID>:repository/<PROJECT_NAME>-*-lambda-repo"
    },
    {
      "Sid": "ManageWorkloadWithTerraform",
      "Effect": "Allow",
      "Action": [
        "apigateway:*",
        "cloudwatch:*",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:DeleteRolePolicy",
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:PassRole",
        "iam:PutRolePolicy",
        "iam:TagRole",
        "iam:UntagRole",
        "kms:CancelKeyDeletion",
        "kms:CreateAlias",
        "kms:CreateKey",
        "kms:DeleteAlias",
        "kms:DescribeKey",
        "kms:EnableKeyRotation",
        "kms:GetKeyPolicy",
        "kms:GetKeyRotationStatus",
        "kms:ListAliases",
        "kms:ListResourceTags",
        "kms:PutKeyPolicy",
        "kms:ScheduleKeyDeletion",
        "kms:TagResource",
        "kms:UntagResource",
        "lambda:AddPermission",
        "lambda:CreateFunction",
        "lambda:DeleteFunction",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:RemovePermission",
        "lambda:TagResource",
        "lambda:UntagResource",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "logs:*",
        "s3:*",
        "sqs:*",
        "xray:PutTelemetryRecords",
        "xray:PutTraceSegments"
      ],
      "Resource": "*"
    }
  ]
}
```

## Plan Role Guidance

The plan role needs backend access and enough read permissions for Terraform
refresh. Start with read-only permissions for the services above, then add
specific `kms:DescribeKey`, `iam:GetRole`, `iam:GetRolePolicy`,
`lambda:GetFunctionConfiguration`, `ecr:DescribeRepositories`, and S3 listing
permissions as Terraform reports missing access.

## KMS Admin Role Name

The CLI exposes the Terraform variable `terraform_admin_role_name` through local
config:

```bash
devsecops set terraform_admin_role_name <existing-role-name> --render
```

The variable is optional and defaults to an empty string. If you set it, the
workload KMS key policy grants explicit key administration to that IAM role name
in addition to account root delegation. Only set it to a role that already
exists; otherwise AWS KMS can reject the key policy because of an invalid
principal.
