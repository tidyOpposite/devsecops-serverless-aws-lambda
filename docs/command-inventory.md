# Command Inventory

This document records the current `devsecops` command surface and the product
status of each command. It is a product contract: users should know which
commands are the preferred workflow, which commands are compatibility aliases,
and which commands may still change.

## Status Legend

| Status | Meaning |
| --- | --- |
| Stable | Preferred command for the supported CLI workflow. Flags and behavior should remain compatible within normal semver expectations. |
| Experimental | Useful command, but command shape, output, or grouping may change before `v1.0`. |
| Alias | Compatibility or shorthand command. Prefer the target command for new scripts and docs. |
| Support | User-facing inspection or explanation command. It is safe to use, but output formatting may change as the CLI improves. |

## Recommended First Run

The README quick start and `devsecops --help` use the same first-run path:

```bash
devsecops config new --preset balanced
devsecops config validate
devsecops config diff
devsecops render
devsecops readiness
devsecops report
```

Use `devsecops menu` when you prefer the interactive path.

## Commands

| Command | Status | Scope | Notes |
| --- | --- | --- | --- |
| `devsecops menu` | Stable | Interactive CLI | Opens the main terminal menu. Default command when no subcommand is passed. |
| `devsecops init` | Alias | Configuration | Legacy interactive entry point for creating or updating `.devsecops-pipeline.toml`. Prefer `devsecops config new` for clean non-interactive config generation. |
| `devsecops readiness` | Stable | Diagnostics | Shows scored readiness gaps and concrete next actions. |
| `devsecops render` | Stable | Generation | Writes CLI-owned Terraform and GitHub helper artifacts. |
| `devsecops report` | Stable | Reporting | Writes a CLI-owned Markdown readiness report. |
| `devsecops dashboard` | Stable | Diagnostics | Prints a one-screen readiness dashboard. |
| `devsecops doctor` | Stable | Diagnostics | Checks local readiness. `--deep` adds external Terraform/AWS checks and may vary by installed tools. |
| `devsecops validate-config` | Alias | Configuration | Compatibility command for `devsecops config validate`. |
| `devsecops set` | Stable | Configuration | Sets one local config key. Use `--render` to regenerate artifacts after the update. |
| `devsecops config` | Alias | Configuration | Compatibility shorthand for `devsecops config show`. |
| `devsecops config show` | Stable | Configuration | Prints the current local source config as TOML or normalized JSON. |
| `devsecops config new` | Stable | Configuration | Creates a clean schema-versioned config from a preset. Refuses to overwrite unless `--force` is passed. |
| `devsecops config validate` | Stable | Configuration | Validates the local source config before Terraform or GitHub commands run. |
| `devsecops config diff` | Stable | Configuration | Shows canonical TOML drift or compares the current config against a preset. |
| `devsecops config reset` | Stable | Configuration | Resets local source config to a clean preset after taking a snapshot. |
| `devsecops config schema` | Stable | Configuration | Prints the config schema contract as JSON or Markdown. |
| `devsecops preset list` | Stable | Configuration | Lists policy presets. |
| `devsecops preset show <name>` | Stable | Configuration | Prints a preset summary. |
| `devsecops preset apply <name>` | Stable | Configuration | Applies a preset while preserving user-specific identity values. |
| `devsecops preset <name>` | Alias | Configuration | Backward-compatible shorthand for `devsecops preset apply <name>`. |
| `devsecops compose` | Experimental | Configuration | Interactive control picker that writes config, artifacts, and a report in one pass. |
| `devsecops snapshots` | Stable | Recovery | Lists or inspects local snapshots of CLI-owned files. |
| `devsecops rollback` | Stable | Recovery | Restores local CLI-owned files from a snapshot. This is not the cloud Lambda deployment rollback. |
| `devsecops github-setup` | Stable | GitHub | Prints, writes, or applies GitHub variable/secret setup commands through `gh`. |
| `devsecops gh-setup` | Alias | GitHub | Alias for `devsecops github-setup`. |
| `devsecops gh-doctor` | Stable | GitHub | Checks GitHub CLI, repository variables, and repository secrets. |
| `devsecops actions-status` | Stable | GitHub | Shows recent GitHub Actions runs and failed job summaries. |
| `devsecops gh-status` | Alias | GitHub | Compatibility name for `devsecops actions-status`. |
| `devsecops branch-doctor` | Stable | GitHub | Checks branch protection and required checks. |
| `devsecops aws-doctor` | Experimental | AWS | Checks AWS identity, backend, and deployed resources. Output may evolve as AWS diagnostics mature. |
| `devsecops plan <env>` | Experimental | Terraform | Convenience wrapper for Terraform plan against an environment workspace. |
| `devsecops bootstrap` | Experimental | Terraform | Plans or applies the Terraform backend bootstrap stack. Mutates AWS only with `--apply`. |
| `devsecops envs` | Support | Inspection | Prints environment settings. |
| `devsecops controls` | Support | Inspection | Prints the security controls matrix. |
| `devsecops architecture` | Support | Inspection | Prints the architecture tree. |
| `devsecops explain [topic]` | Support | Inspection | Explains a pipeline security control. |
| `devsecops tui` | Experimental | UI | Optional Rich/Textual UI bridge. Requires installing optional dependencies. |

## Product Boundary

The CLI owns local configuration and generated helper artifacts. Terraform,
GitHub Actions, AWS resources, and scanner tools are still transparent
execution layers that operators can inspect directly.

Commands that mutate local files create snapshots where appropriate. Commands
that mutate GitHub or AWS require explicit flags such as `--apply` and should
be reviewed before use in production.
