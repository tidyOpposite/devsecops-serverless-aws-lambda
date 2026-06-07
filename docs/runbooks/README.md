# Operational Runbooks

Use these runbooks after `devsecops github status`, `devsecops doctor actions`,
`devsecops aws outputs`, or `devsecops health` points to a failing operational
path.

| Failure | Runbook |
| --- | --- |
| Terraform plan failed | [Failed Terraform plan](failed-terraform-plan.md) |
| Terraform apply failed | [Failed Terraform apply](failed-terraform-apply.md) |
| Validation failed | [Failed validation](failed-validation.md) |
| Lambda image missing or unusable | [Missing Lambda image](missing-image.md) |
| Deployment rollback failed | [Failed deployment rollback](failed-rollback.md) |

All commands are read-only unless a command explicitly includes `--apply`,
`--yes`, or a GitHub/AWS mutating command shown for manual recovery.
