#!/usr/bin/env python3
"""DevSecOps Pipeline Kit CLI.

This CLI is intentionally dependency-free so it can run before the project has
any Python environment configured. It uses a local TOML config and generates
ignored Terraform/GitHub helper artifacts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback guard
    tomllib = None  # type: ignore[assignment]


VERSION = "0.1.0"
CONFIG_FILE = ".devsecops-pipeline.toml"
DIST_DIR = Path("dist/devsecops")
GENERATED_TFVARS = Path("terraform/generated.auto.tfvars")
SNAPSHOT_DIR = Path(".devsecops/snapshots")
SNAPSHOT_FILES = [
    Path(CONFIG_FILE),
    GENERATED_TFVARS,
    DIST_DIR / "backend.tf",
    DIST_DIR / "github-variables.env",
    DIST_DIR / "github-setup.sh",
    DIST_DIR / "setup-checklist.md",
    DIST_DIR / "readiness-report.md",
]
SNAPSHOT_FILE_PATHS = {str(path) for path in SNAPSHOT_FILES}
PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,31}$")
AWS_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
PRESETS = {"minimal", "balanced", "strict"}
CONFIG_SET_PATHS = {
    "project_name",
    "aws_region",
    "lambda_image_uri",
    "enable_http_validation",
    "enable_dast",
    "terraform_admin_role_name",
    "backend.bucket",
    "backend.key",
    "backend.region",
    "backend.lock_table",
    "backend.workspace_key_prefix",
}
for _env_name in ["dev", "staging", "prod"]:
    for _setting in [
        "lambda_memory_size",
        "lambda_timeout",
        "log_retention_days",
        "api_throttling_burst_limit",
        "api_throttling_rate_limit",
        "cors_allowed_origins",
    ]:
        CONFIG_SET_PATHS.add(f"environments.{_env_name}.{_setting}")
REQUIRED_GH_VARIABLES = [
    "PROJECT_NAME",
    "LAMBDA_IMAGE_URI",
    "ENABLE_HTTP_VALIDATION",
    "ENABLE_DAST",
]
REQUIRED_GH_SECRETS = [
    "AWS_ROLE_TO_ASSUME_ARN",
    "AWS_PLAN_ROLE_TO_ASSUME_ARN",
    "AWS_REGION",
]
OPTIONAL_GH_SECRETS = ["SNYK_TOKEN"]
DEFAULT_BRANCH = "main"
REQUIRED_BRANCH_CHECKS = [
    "Security and Terraform Validate",
    "Terraform Plan",
]
CANCEL_INPUTS = {"b", "back", "cancel", "q", "quit"}
MENU_CANCEL_INPUTS = CANCEL_INPUTS | {"0"}


class InputCancelled(Exception):
    """Raised when an interactive menu prompt is cancelled by the user."""


class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BLUE = "\033[34m"


def supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def color(text: str, code: str) -> str:
    if not supports_color():
        return text
    return f"{code}{text}{Style.RESET}"


def ok(text: str = "OK") -> str:
    return color(text, Style.GREEN)


def warn(text: str = "WARN") -> str:
    return color(text, Style.YELLOW)


def fail(text: str = "FAIL") -> str:
    return color(text, Style.RED)


def info(text: str) -> str:
    return color(text, Style.CYAN)


def repo_root() -> Path:
    return Path.cwd()


def config_path(root: Path) -> Path:
    return root / CONFIG_FILE


def default_config() -> dict[str, Any]:
    return {
        "project_name": "devsecops-pipeline",
        "aws_region": "us-east-1",
        "lambda_image_uri": "",
        "enable_http_validation": False,
        "enable_dast": False,
        "terraform_admin_role_name": "",
        "backend": {
            "bucket": "replace-with-your-terraform-state-bucket",
            "key": "serverless-lambda/terraform.tfstate",
            "region": "us-east-1",
            "lock_table": "devsecops-pipeline-terraform-locks",
            "workspace_key_prefix": "environments",
        },
        "environments": {
            "dev": {
                "lambda_memory_size": 1024,
                "lambda_timeout": 120,
                "log_retention_days": 30,
                "api_throttling_burst_limit": 25,
                "api_throttling_rate_limit": 50,
                "cors_allowed_origins": ["*"],
            },
            "staging": {
                "lambda_memory_size": 1536,
                "lambda_timeout": 180,
                "log_retention_days": 90,
                "api_throttling_burst_limit": 50,
                "api_throttling_rate_limit": 100,
                "cors_allowed_origins": ["*"],
            },
            "prod": {
                "lambda_memory_size": 2048,
                "lambda_timeout": 240,
                "log_retention_days": 365,
                "api_throttling_burst_limit": 100,
                "api_throttling_rate_limit": 200,
                "cors_allowed_origins": ["*"],
            },
        },
    }


def preset_config(name: str) -> dict[str, Any]:
    cfg = default_config()
    if name == "minimal":
        cfg["enable_http_validation"] = False
        cfg["enable_dast"] = False
        cfg["environments"] = {
            "dev": {
                "lambda_memory_size": 512,
                "lambda_timeout": 60,
                "log_retention_days": 7,
                "api_throttling_burst_limit": 10,
                "api_throttling_rate_limit": 25,
                "cors_allowed_origins": ["*"],
            },
            "staging": {
                "lambda_memory_size": 1024,
                "lambda_timeout": 120,
                "log_retention_days": 30,
                "api_throttling_burst_limit": 25,
                "api_throttling_rate_limit": 50,
                "cors_allowed_origins": ["*"],
            },
            "prod": {
                "lambda_memory_size": 1024,
                "lambda_timeout": 180,
                "log_retention_days": 90,
                "api_throttling_burst_limit": 50,
                "api_throttling_rate_limit": 100,
                "cors_allowed_origins": ["*"],
            },
        }
    elif name == "strict":
        cfg["enable_http_validation"] = True
        cfg["enable_dast"] = True
        cfg["environments"]["dev"]["cors_allowed_origins"] = ["https://dev.example.com"]
        cfg["environments"]["staging"]["cors_allowed_origins"] = ["https://staging.example.com"]
        cfg["environments"]["prod"]["lambda_timeout"] = 300
        cfg["environments"]["prod"]["api_throttling_burst_limit"] = 50
        cfg["environments"]["prod"]["api_throttling_rate_limit"] = 100
        cfg["environments"]["prod"]["cors_allowed_origins"] = ["https://app.example.com"]
    elif name != "balanced":
        raise ValueError(f"Unknown preset: {name}")
    return cfg


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(root: Path) -> dict[str, Any]:
    cfg = default_config()
    path = config_path(root)
    if not path.exists():
        return cfg
    if tomllib is None:
        raise RuntimeError("Python 3.11+ is required to read TOML config files.")
    with path.open("rb") as handle:
        loaded = tomllib.load(handle)
    return deep_merge(cfg, loaded)


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_config_toml(cfg: dict[str, Any]) -> str:
    lines = [
        "# DevSecOps Pipeline Kit local configuration",
        "# Generated by: python cli/devsecops_cli.py init",
        "",
    ]
    for key in [
        "project_name",
        "aws_region",
        "lambda_image_uri",
        "enable_http_validation",
        "enable_dast",
        "terraform_admin_role_name",
    ]:
        lines.append(f"{key} = {toml_value(cfg[key])}")

    lines.append("")
    lines.append("[backend]")
    for key, value in cfg["backend"].items():
        lines.append(f"{key} = {toml_value(value)}")

    for env_name, env_cfg in cfg["environments"].items():
        lines.append("")
        lines.append(f"[environments.{env_name}]")
        for key, value in env_cfg.items():
            lines.append(f"{key} = {toml_value(value)}")

    lines.append("")
    return "\n".join(lines)


def write_config(root: Path, cfg: dict[str, Any]) -> None:
    config_path(root).write_text(dump_config_toml(cfg), encoding="utf-8")


def snapshot_id(operation: str, now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.UTC)
    safe_operation = re.sub(r"[^a-z0-9-]+", "-", operation.lower()).strip("-") or "change"
    return f"{current.strftime('%Y%m%dT%H%M%SZ')}-{safe_operation}"


def snapshot_base(root: Path) -> Path:
    return root / SNAPSHOT_DIR


def create_snapshot(root: Path, operation: str, description: str) -> Path:
    base = snapshot_base(root)
    base.mkdir(parents=True, exist_ok=True)
    base_id = snapshot_id(operation)
    snapshot_path = base / base_id
    counter = 2
    while snapshot_path.exists():
        snapshot_path = base / f"{base_id}-{counter}"
        counter += 1
    snapshot_path.mkdir(parents=True)

    files = []
    for relative_path in SNAPSHOT_FILES:
        source = root / relative_path
        target = snapshot_path / "files" / relative_path
        entry = {"path": str(relative_path), "present": source.exists()}
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        files.append(entry)

    manifest = {
        "id": snapshot_path.name,
        "created_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "operation": operation,
        "description": description,
        "files": files,
    }
    (snapshot_path / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return snapshot_path


def read_snapshot_manifest(snapshot_path: Path) -> dict[str, Any]:
    manifest_path = snapshot_path / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def snapshot_entry_relative_path(file_entry: dict[str, Any]) -> Path | None:
    raw_path = str(file_entry.get("path", ""))
    if raw_path not in SNAPSHOT_FILE_PATHS:
        return None
    return Path(raw_path)


def list_snapshots(root: Path) -> list[dict[str, Any]]:
    base = snapshot_base(root)
    if not base.exists():
        return []
    snapshots = []
    for snapshot_path in base.iterdir():
        if not snapshot_path.is_dir():
            continue
        manifest = read_snapshot_manifest(snapshot_path)
        if not manifest:
            continue
        manifest["_path"] = str(snapshot_path)
        snapshots.append(manifest)
    return sorted(snapshots, key=lambda item: str(item.get("id", "")), reverse=True)


def resolve_snapshot(root: Path, snapshot_id_value: str | None = None, last: bool = False) -> dict[str, Any] | None:
    snapshots = list_snapshots(root)
    if not snapshots:
        return None
    if last or not snapshot_id_value:
        return snapshots[0]
    for snapshot in snapshots:
        if snapshot.get("id") == snapshot_id_value:
            return snapshot
    return None


def file_line_counts(before: str, after: str) -> tuple[int, int]:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    added = max(0, len(after_lines) - len(before_lines))
    removed = max(0, len(before_lines) - len(after_lines))
    if added == 0 and removed == 0 and before != after:
        added = 1
        removed = 1
    return added, removed


def snapshot_changes(root: Path, snapshot: dict[str, Any]) -> list[dict[str, str]]:
    snapshot_path = Path(str(snapshot["_path"]))
    changes: list[dict[str, str]] = []
    for file_entry in snapshot.get("files", []):
        if not isinstance(file_entry, dict):
            continue
        relative = snapshot_entry_relative_path(file_entry)
        if relative is None:
            continue
        before_present = bool(file_entry.get("present"))
        before_path = snapshot_path / "files" / relative
        current_path = root / relative
        current_present = current_path.exists()

        if before_present and current_present:
            before_text = before_path.read_text(encoding="utf-8", errors="replace")
            current_text = current_path.read_text(encoding="utf-8", errors="replace")
            if before_text == current_text:
                continue
            added, removed = file_line_counts(before_text, current_text)
            detail = f"modified since snapshot (+{added}/-{removed} line estimate)"
        elif before_present and not current_present:
            detail = "deleted since snapshot; rollback will restore it"
        elif not before_present and current_present:
            detail = "created since snapshot; rollback will remove it"
        else:
            continue
        changes.append({"path": str(relative), "detail": detail})
    return changes


def snapshot_rows(snapshots: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [
            str(index + 1),
            str(snapshot.get("id", "")),
            str(snapshot.get("created_at", "")),
            str(snapshot.get("operation", "")),
        ]
        for index, snapshot in enumerate(snapshots)
    ]


def resolve_snapshot_selection(root: Path, selection: str) -> dict[str, Any] | None:
    snapshots = list_snapshots(root)
    if not snapshots:
        return None
    if selection.isdigit():
        index = int(selection) - 1
        if 0 <= index < len(snapshots):
            return snapshots[index]
    for snapshot in snapshots:
        if snapshot.get("id") == selection:
            return snapshot
    return None


def print_snapshot_detail(root: Path, snapshot: dict[str, Any]) -> None:
    draw_box(
        "Snapshot Detail",
        [
            f"ID: {snapshot.get('id', '')}",
            f"Created: {snapshot.get('created_at', '')}",
            f"Operation: {snapshot.get('operation', '')}",
            f"Description: {snapshot.get('description', '')}",
        ],
    )
    changes = snapshot_changes(root, snapshot)
    print()
    if changes:
        draw_table(["File", "Change since snapshot"], [[item["path"], item["detail"]] for item in changes])
    else:
        print(ok("No changes detected since this snapshot."))


def restore_snapshot(root: Path, snapshot: dict[str, Any], dry_run: bool = False) -> list[dict[str, str]]:
    changes = snapshot_changes(root, snapshot)
    if dry_run:
        return changes
    snapshot_path = Path(str(snapshot["_path"]))
    for file_entry in snapshot.get("files", []):
        if not isinstance(file_entry, dict):
            continue
        relative = snapshot_entry_relative_path(file_entry)
        if relative is None:
            continue
        destination = root / relative
        source = snapshot_path / "files" / relative
        if bool(file_entry.get("present")):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        elif destination.exists():
            destination.unlink()
    return changes


def print_snapshot_list(root: Path) -> list[dict[str, Any]]:
    snapshots = list_snapshots(root)
    if not snapshots:
        print(warn("No snapshots found."))
        return []
    draw_table(["#", "Snapshot", "Created", "Operation"], snapshot_rows(snapshots), title="Snapshots")
    return snapshots


def snapshot_before_change(root: Path, operation: str, description: str) -> None:
    path = create_snapshot(root, operation, description)
    print(info("Snapshot created: ") + path.name)


def parse_config_value(raw_value: str, current_value: Any) -> Any:
    if isinstance(current_value, bool):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        raise ValueError("Expected boolean: true/false.")
    if isinstance(current_value, int):
        try:
            return int(raw_value)
        except ValueError as exc:
            raise ValueError("Expected integer.") from exc
    if isinstance(current_value, list):
        if not raw_value.strip():
            return []
        return [item.strip() for item in raw_value.split(",") if item.strip()]
    return raw_value


def nested_get(cfg: dict[str, Any], dotted_path: str) -> Any:
    cursor: Any = cfg
    for part in dotted_path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            raise KeyError(dotted_path)
        cursor = cursor[part]
    return cursor


def nested_set(cfg: dict[str, Any], dotted_path: str, value: Any) -> None:
    cursor: Any = cfg
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            raise KeyError(dotted_path)
        cursor = cursor[part]
    if not isinstance(cursor, dict) or parts[-1] not in cursor:
        raise KeyError(dotted_path)
    cursor[parts[-1]] = value


def hcl_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(hcl_value(item) for item in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def terraform_tfvars(cfg: dict[str, Any]) -> str:
    lines = [
        "# Generated by DevSecOps Pipeline Kit. Do not commit this file.",
        f'project_name = {hcl_value(cfg["project_name"])}',
        f'aws_region = {hcl_value(cfg["aws_region"])}',
        f'lambda_image_uri = {hcl_value(cfg["lambda_image_uri"])}',
        f'terraform_admin_role_name = {hcl_value(cfg["terraform_admin_role_name"])}',
        "",
        "environment_config = {",
    ]
    for env_name, env_cfg in cfg["environments"].items():
        lines.append(f"  {env_name} = {{")
        for key, value in env_cfg.items():
            lines.append(f"    {key} = {hcl_value(value)}")
        lines.append("  }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def backend_tf(cfg: dict[str, Any]) -> str:
    backend = cfg["backend"]
    return textwrap.dedent(
        f"""\
        # Generated backend template. Copy into terraform/backend.tf when ready.
        terraform {{
          backend "s3" {{
            bucket               = {hcl_value(backend["bucket"])}
            key                  = {hcl_value(backend["key"])}
            region               = {hcl_value(backend["region"])}
            encrypt              = true
            dynamodb_table       = {hcl_value(backend["lock_table"])}
            workspace_key_prefix = {hcl_value(backend["workspace_key_prefix"])}
          }}
        }}
        """
    )


def github_variables(cfg: dict[str, Any]) -> str:
    return textwrap.dedent(
        f"""\
        # Repository variables to configure in GitHub.
        # Example with gh:
        #   gh variable set PROJECT_NAME --body "{cfg["project_name"]}"

        PROJECT_NAME={cfg["project_name"]}
        LAMBDA_IMAGE_URI={cfg["lambda_image_uri"]}
        ENABLE_HTTP_VALIDATION={str(cfg["enable_http_validation"]).lower()}
        ENABLE_DAST={str(cfg["enable_dast"]).lower()}
        """
    )


def checklist(cfg: dict[str, Any]) -> str:
    return textwrap.dedent(
        f"""\
        # DevSecOps Pipeline Setup Checklist

        ## GitHub Secrets

        - [ ] `AWS_ROLE_TO_ASSUME_ARN`
        - [ ] `AWS_PLAN_ROLE_TO_ASSUME_ARN` (optional, recommended)
        - [ ] `AWS_REGION` = `{cfg["aws_region"]}`
        - [ ] `SNYK_TOKEN` (optional)

        ## GitHub Variables

        - [ ] `PROJECT_NAME` = `{cfg["project_name"]}`
        - [ ] `LAMBDA_IMAGE_URI` = `{cfg["lambda_image_uri"] or "<immutable-image-uri>"}`
        - [ ] `ENABLE_HTTP_VALIDATION` = `{str(cfg["enable_http_validation"]).lower()}`
        - [ ] `ENABLE_DAST` = `{str(cfg["enable_dast"]).lower()}`

        ## Terraform Backend

        - [ ] State bucket: `{cfg["backend"]["bucket"]}`
        - [ ] Lock table: `{cfg["backend"]["lock_table"]}`
        - [ ] Backend key: `{cfg["backend"]["key"]}`

        ## Branch Protection

        - [ ] Require pull requests before merging to `main`
        - [ ] Require `Security and Terraform Validate`
        - [ ] Require `Terraform Plan`
        """
    )


def github_setup_script(cfg: dict[str, Any]) -> str:
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        # Generated by DevSecOps Pipeline Kit.
        # Review placeholder values before running.

        gh variable set PROJECT_NAME --body {shell_quote(cfg["project_name"])}
        gh variable set LAMBDA_IMAGE_URI --body {shell_quote(cfg["lambda_image_uri"] or "<immutable-image-uri>")}
        gh variable set ENABLE_HTTP_VALIDATION --body {shell_quote(str(cfg["enable_http_validation"]).lower())}
        gh variable set ENABLE_DAST --body {shell_quote(str(cfg["enable_dast"]).lower())}

        gh secret set AWS_REGION --body {shell_quote(cfg["aws_region"])}
        gh secret set AWS_ROLE_TO_ASSUME_ARN --body "<deploy-role-arn>"
        gh secret set AWS_PLAN_ROLE_TO_ASSUME_ARN --body "<plan-role-arn>"
        # Optional:
        # gh secret set SNYK_TOKEN --body "<snyk-token>"
        """
    )


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def github_expected_variables(cfg: dict[str, Any]) -> dict[str, str]:
    return {
        "PROJECT_NAME": str(cfg["project_name"]),
        "LAMBDA_IMAGE_URI": str(cfg["lambda_image_uri"]),
        "ENABLE_HTTP_VALIDATION": str(cfg["enable_http_validation"]).lower(),
        "ENABLE_DAST": str(cfg["enable_dast"]).lower(),
    }


def parse_gh_items(stdout: str, value_key: str | None = None) -> dict[str, str]:
    if not stdout.strip():
        return {}
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError:
        return parse_gh_plain_table(stdout, value_key=value_key)
    items: dict[str, str] = {}
    if not isinstance(decoded, list):
        return items
    for entry in decoded:
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        name = str(entry["name"])
        if value_key is None:
            items[name] = ""
        else:
            items[name] = str(entry.get(value_key, ""))
    return items


def parse_gh_plain_table(stdout: str, value_key: str | None = None) -> dict[str, str]:
    items: dict[str, str] = {}
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or line.upper().startswith("NAME"):
            continue
        parts = line.split()
        name = parts[0]
        items[name] = " ".join(parts[1:]) if value_key is not None and len(parts) > 1 else ""
    return items


def github_variable_checks(cfg: dict[str, Any], variables: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    expected = github_expected_variables(cfg)
    for name in REQUIRED_GH_VARIABLES:
        actual = variables.get(name)
        wanted = expected[name]
        if actual is None:
            checks.append(Check(f"GitHub variable {name}", "WARN", "Missing."))
        elif name == "LAMBDA_IMAGE_URI" and not wanted:
            checks.append(Check(f"GitHub variable {name}", "WARN", "Local config has no expected image URI."))
        elif actual != wanted:
            checks.append(Check(f"GitHub variable {name}", "WARN", f"Expected `{wanted}`, found `{actual}`."))
        elif name == "LAMBDA_IMAGE_URI" and not is_immutable_image(actual):
            checks.append(Check(f"GitHub variable {name}", "FAIL", "Value is not immutable."))
        else:
            checks.append(Check(f"GitHub variable {name}", "OK", actual or "Configured."))
    return checks


def github_secret_checks(secrets: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    for name in REQUIRED_GH_SECRETS:
        checks.append(
            Check(
                f"GitHub secret {name}",
                "OK" if name in secrets else "WARN",
                "Configured." if name in secrets else "Missing.",
            )
        )
    for name in OPTIONAL_GH_SECRETS:
        checks.append(
            Check(
                f"GitHub secret {name}",
                "OK" if name in secrets else "INFO",
                "Configured." if name in secrets else "Optional.",
                scored=False,
            )
        )
    return checks


def parse_json_object(stdout: str) -> dict[str, Any]:
    if not stdout.strip():
        return {}
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def required_status_check_names(protection: dict[str, Any]) -> set[str]:
    status_checks = protection.get("required_status_checks")
    if not isinstance(status_checks, dict):
        return set()
    names = set()
    contexts = status_checks.get("contexts", [])
    if isinstance(contexts, list):
        names.update(str(context) for context in contexts)
    checks = status_checks.get("checks", [])
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict) and check.get("context"):
                names.add(str(check["context"]))
    return names


def branch_protection_checks(
    branch: str,
    protected: bool | None,
    protection: dict[str, Any] | None,
    required_checks: list[str] | None = None,
) -> list[Check]:
    required_checks = required_checks or REQUIRED_BRANCH_CHECKS
    checks: list[Check] = []
    if protected is True:
        checks.append(Check(f"Branch `{branch}` protection", "OK", "Enabled."))
    elif protected is False:
        checks.append(Check(f"Branch `{branch}` protection", "WARN", "Branch is not protected."))
    else:
        checks.append(Check(f"Branch `{branch}` protection", "WARN", "Could not inspect branch protection."))

    if not protection:
        checks.append(Check("Pull request requirement", "WARN", "Could not inspect protection rules."))
        for required in required_checks:
            checks.append(Check(f"Required check `{required}`", "WARN", "Could not inspect status checks."))
        return checks

    pr_reviews = protection.get("required_pull_request_reviews")
    checks.append(
        Check(
            "Pull request requirement",
            "OK" if isinstance(pr_reviews, dict) else "WARN",
            "Pull request reviews are required." if isinstance(pr_reviews, dict) else "Pull request reviews are not required.",
        )
    )

    status_names = required_status_check_names(protection)
    checks.append(
        Check(
            "Required status checks",
            "OK" if status_names else "WARN",
            ", ".join(sorted(status_names)) if status_names else "No required status checks configured.",
        )
    )
    for required in required_checks:
        checks.append(
            Check(
                f"Required check `{required}`",
                "OK" if required in status_names else "WARN",
                "Configured." if required in status_names else "Missing from branch protection.",
            )
        )
    return checks


def parse_gh_runs(stdout: str) -> list[dict[str, Any]]:
    if not stdout.strip():
        return []
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [entry for entry in decoded if isinstance(entry, dict)]


def actions_run_rows(runs: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for run in runs:
        rows.append(
            [
                str(run.get("workflowName", "")),
                str(run.get("headBranch", "")),
                str(run.get("status", "")),
                str(run.get("conclusion") or ""),
                str(run.get("createdAt", "")),
            ]
        )
    return rows


def failed_job_rows(workflow_name: str, stdout: str) -> list[list[str]]:
    payload = parse_json_object(stdout)
    jobs = payload.get("jobs", [])
    rows: list[list[str]] = []
    if not isinstance(jobs, list):
        return rows
    for job in jobs:
        if not isinstance(job, dict):
            continue
        conclusion = str(job.get("conclusion") or "")
        if conclusion and conclusion not in {"success", "skipped"}:
            rows.append(
                [
                    workflow_name,
                    str(job.get("name", "")),
                    str(job.get("status", "")),
                    conclusion,
                ]
            )
    return rows


def draw_box(title: str, lines: list[str], width: int = 74) -> None:
    print("+" + "-" * (width - 2) + "+")
    title_text = f" {title} "
    print("|" + title_text.ljust(width - 2) + "|")
    print("+" + "-" * (width - 2) + "+")
    for line in lines:
        for wrapped in textwrap.wrap(line, width=width - 4) or [""]:
            print("| " + wrapped.ljust(width - 4) + " |")
    print("+" + "-" * (width - 2) + "+")


def draw_table(headers: list[str], rows: list[list[str]], title: str | None = None) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    if title:
        print(info(title))
    print(border)
    print("|" + "|".join(f" {headers[index].ljust(widths[index])} " for index in range(len(headers))) + "|")
    print(border)
    for row in rows:
        print("|" + "|".join(f" {row[index].ljust(widths[index])} " for index in range(len(headers))) + "|")
    print(border)


def progress_bar(score: int, width: int = 28) -> str:
    filled = max(0, min(width, round(width * score / 100)))
    return "[" + "#" * filled + "-" * (width - filled) + f"] {score:3d}%"


def is_cancel_input(value: str, include_zero: bool = True) -> bool:
    normalized = value.strip().lower()
    cancel_inputs = MENU_CANCEL_INPUTS if include_zero else CANCEL_INPUTS
    return normalized in cancel_inputs


def cancel_hint(allow_cancel: bool) -> str:
    return ", b/back/0 to cancel" if allow_cancel else ""


def prompt_text(label: str, default: str = "", allow_cancel: bool = False) -> str:
    options = []
    if default:
        options.append(default)
    if allow_cancel:
        options.append("b/back/0 to cancel")
    suffix = f" [{', '.join(options)}]" if options else ""
    value = input(f"{label}{suffix}: ").strip()
    if allow_cancel and is_cancel_input(value):
        raise InputCancelled
    return value or default


def prompt_bool(label: str, default: bool = False, allow_cancel: bool = False) -> bool:
    default_text = "y" if default else "n"
    while True:
        value = input(f"{label} [y/n, default {default_text}{cancel_hint(allow_cancel)}]: ").strip().lower()
        if allow_cancel and is_cancel_input(value):
            raise InputCancelled
        if not value:
            return default
        if value in {"y", "yes", "true", "1"}:
            return True
        if value in {"n", "no", "false", "0"}:
            return False
        print(warn("Use y or n."))


def prompt_int(label: str, default: int, allow_cancel: bool = False) -> int:
    while True:
        value = input(f"{label} [{default}{cancel_hint(allow_cancel)}]: ").strip()
        if allow_cancel and is_cancel_input(value):
            raise InputCancelled
        if not value:
            return default
        try:
            return int(value)
        except ValueError:
            print(warn("Use a number."))


def header(cfg: dict[str, Any] | None = None) -> None:
    lines = [
        "DevSecOps Pipeline Kit",
        "AWS Lambda CI/CD reference pipeline configurator",
    ]
    if cfg:
        lines.extend(
            [
                f"Project: {cfg['project_name']}",
                f"Region: {cfg['aws_region']}",
            ]
        )
    draw_box("Main", lines)


@dataclass
class Check:
    name: str
    status: str
    detail: str
    scored: bool = True


def validate_config(cfg: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "Config project name",
            "OK" if PROJECT_NAME_RE.match(cfg["project_name"]) else "FAIL",
            cfg["project_name"],
        )
    )
    checks.append(
        Check(
            "Config AWS region",
            "OK" if AWS_REGION_RE.match(cfg["aws_region"]) else "WARN",
            cfg["aws_region"],
        )
    )
    for env_name, env_cfg in cfg["environments"].items():
        numeric_rules = {
            "lambda_memory_size": (128, 10240),
            "lambda_timeout": (1, 900),
            "log_retention_days": (1, 3653),
            "api_throttling_burst_limit": (1, 5000),
            "api_throttling_rate_limit": (1, 10000),
        }
        for setting, limits in numeric_rules.items():
            value = env_cfg[setting]
            lower, upper = limits
            status = "OK" if isinstance(value, int) and lower <= value <= upper else "FAIL"
            checks.append(
                Check(
                    f"{env_name}.{setting}",
                    status,
                    str(value) if status == "OK" else f"{value} outside {lower}-{upper}.",
                )
            )
        origins = env_cfg["cors_allowed_origins"]
        checks.append(
            Check(
                f"{env_name}.cors_allowed_origins",
                "OK" if isinstance(origins, list) and all(isinstance(item, str) for item in origins) else "FAIL",
                ",".join(origins) if isinstance(origins, list) else "Expected list.",
            )
        )
    return checks


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_command(command: list[str], root: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def gh_command(root: Path, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return run_command(["gh", *args], root, timeout=timeout)


def collect_github_checks(root: Path, cfg: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    if not command_exists("gh"):
        return [
            Check("GitHub CLI", "WARN", "`gh` not found on PATH."),
            Check("GitHub auth", "WARN", "Install `gh` and run `gh auth login`."),
            Check("GitHub repository", "WARN", "Cannot inspect repository without `gh`."),
        ]

    checks.append(Check("GitHub CLI", "OK", "Installed."))

    auth = gh_command(root, ["auth", "status"])
    if auth.returncode != 0:
        checks.append(Check("GitHub auth", "WARN", compact_error(auth)))
        return checks
    checks.append(Check("GitHub auth", "OK", "Authenticated."))

    repo = gh_command(root, ["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if repo.returncode != 0 or not repo.stdout.strip():
        checks.append(Check("GitHub repository", "WARN", compact_error(repo)))
    else:
        checks.append(Check("GitHub repository", "OK", repo.stdout.strip()))

    variables_result = gh_command(root, ["variable", "list", "--json", "name,value"])
    if variables_result.returncode != 0:
        checks.append(Check("GitHub variables", "WARN", compact_error(variables_result)))
    else:
        checks.extend(github_variable_checks(cfg, parse_gh_items(variables_result.stdout, value_key="value")))

    secrets_result = gh_command(root, ["secret", "list", "--json", "name"])
    if secrets_result.returncode != 0:
        checks.append(Check("GitHub secrets", "WARN", compact_error(secrets_result)))
    else:
        checks.extend(github_secret_checks(parse_gh_items(secrets_result.stdout)))

    return checks


def collect_branch_checks(root: Path, branch: str = DEFAULT_BRANCH) -> list[Check]:
    if not command_exists("gh"):
        return [
            Check("GitHub CLI", "WARN", "`gh` not found on PATH."),
            Check(f"Branch `{branch}` protection", "WARN", "Cannot inspect branch protection without `gh`."),
        ]

    auth = gh_command(root, ["auth", "status"])
    if auth.returncode != 0:
        return [
            Check("GitHub auth", "WARN", compact_error(auth)),
            Check(f"Branch `{branch}` protection", "WARN", "Cannot inspect branch protection without GitHub auth."),
        ]

    repo = gh_command(root, ["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if repo.returncode != 0 or not repo.stdout.strip():
        return [
            Check("GitHub repository", "WARN", compact_error(repo)),
            Check(f"Branch `{branch}` protection", "WARN", "Cannot inspect branch protection without repo context."),
        ]

    repo_name = repo.stdout.strip()
    checks = [
        Check("GitHub CLI", "OK", "Installed."),
        Check("GitHub auth", "OK", "Authenticated."),
        Check("GitHub repository", "OK", repo_name),
    ]

    branch_result = gh_command(root, ["api", f"repos/{repo_name}/branches/{branch}"])
    if branch_result.returncode != 0:
        checks.append(Check(f"Branch `{branch}`", "WARN", compact_error(branch_result)))
        checks.extend(branch_protection_checks(branch, None, None))
        return checks

    branch_payload = parse_json_object(branch_result.stdout)
    protected_value = branch_payload.get("protected")
    protected = protected_value if isinstance(protected_value, bool) else None

    protection_result = gh_command(root, ["api", f"repos/{repo_name}/branches/{branch}/protection"])
    protection_payload: dict[str, Any] | None
    if protection_result.returncode == 0:
        protection_payload = parse_json_object(protection_result.stdout)
    else:
        protection_payload = None
        checks.append(Check("Protection details", "WARN", compact_error(protection_result)))

    checks.extend(branch_protection_checks(branch, protected, protection_payload))
    return checks


def github_status_rows(root: Path, limit: int = 5) -> tuple[list[list[str]], str | None]:
    if not command_exists("gh"):
        return [], "`gh` not found on PATH."
    result = gh_command(
        root,
        [
            "run",
            "list",
            "--limit",
            str(limit),
            "--json",
            "databaseId,workflowName,headBranch,status,conclusion,createdAt,url",
        ],
    )
    if result.returncode != 0:
        return [], compact_error(result)
    runs = parse_gh_runs(result.stdout)
    if not runs and result.stdout.strip():
        return [], "Could not parse `gh run list` output."
    return actions_run_rows(runs), None


def github_actions_status(root: Path, limit: int = 8, failed_jobs_limit: int = 3) -> tuple[list[list[str]], list[list[str]], str | None]:
    if not command_exists("gh"):
        return [], [], "`gh` not found on PATH."
    result = gh_command(
        root,
        [
            "run",
            "list",
            "--limit",
            str(limit),
            "--json",
            "databaseId,workflowName,headBranch,status,conclusion,createdAt,url",
        ],
    )
    if result.returncode != 0:
        return [], [], compact_error(result)
    runs = parse_gh_runs(result.stdout)
    if not runs and result.stdout.strip():
        return [], [], "Could not parse `gh run list` output."

    failed_rows: list[list[str]] = []
    failed_runs = [run for run in runs if str(run.get("conclusion") or "") == "failure"]
    for run in failed_runs[:failed_jobs_limit]:
        run_id = run.get("databaseId")
        if not run_id:
            continue
        job_result = gh_command(root, ["run", "view", str(run_id), "--json", "jobs"])
        if job_result.returncode != 0:
            failed_rows.append([str(run.get("workflowName", "")), "(jobs)", "unknown", compact_error(job_result)])
            continue
        failed_rows.extend(failed_job_rows(str(run.get("workflowName", "")), job_result.stdout))
    return actions_run_rows(runs), failed_rows, None


def apply_github_setup(root: Path, cfg: dict[str, Any], args: argparse.Namespace) -> int:
    if not command_exists("gh"):
        print(fail("`gh` not found on PATH."))
        return 1
    auth = gh_command(root, ["auth", "status"])
    if auth.returncode != 0:
        print(fail("GitHub CLI is not authenticated: ") + compact_error(auth))
        return auth.returncode

    expected_vars = github_expected_variables(cfg)
    if not expected_vars["LAMBDA_IMAGE_URI"]:
        print(warn("Skipping LAMBDA_IMAGE_URI because local config is empty."))
        expected_vars.pop("LAMBDA_IMAGE_URI")

    for name, value in expected_vars.items():
        command = ["variable", "set", name, "--body", value]
        print(info("$ gh " + " ".join(command[:3]) + " --body <value>"))
        result = gh_command(root, command)
        if result.returncode != 0:
            print(fail(compact_error(result)))
            return result.returncode

    secrets = {"AWS_REGION": cfg["aws_region"]}
    if args.deploy_role_arn:
        secrets["AWS_ROLE_TO_ASSUME_ARN"] = args.deploy_role_arn
    else:
        print(warn("Skipping AWS_ROLE_TO_ASSUME_ARN; pass --deploy-role-arn to set it."))
    if args.plan_role_arn:
        secrets["AWS_PLAN_ROLE_TO_ASSUME_ARN"] = args.plan_role_arn
    else:
        print(warn("Skipping AWS_PLAN_ROLE_TO_ASSUME_ARN; pass --plan-role-arn to set it."))
    if args.snyk_token:
        secrets["SNYK_TOKEN"] = args.snyk_token

    for name, value in secrets.items():
        command = ["secret", "set", name, "--body", value]
        print(info("$ gh " + " ".join(command[:3]) + " --body <value>"))
        result = gh_command(root, command)
        if result.returncode != 0:
            print(fail(compact_error(result)))
            return result.returncode

    print(ok("Applied GitHub repository variables/secrets available from config and arguments."))
    return 0


def is_immutable_image(image_uri: str) -> bool:
    if not image_uri:
        return False
    if "@sha256:" in image_uri:
        return True
    if ":" not in image_uri:
        return False
    tag = image_uri.rsplit(":", 1)[1]
    return tag not in {"latest", "bootstrap"}


def collect_checks(root: Path, cfg: dict[str, Any], deep: bool = False) -> list[Check]:
    checks: list[Check] = []
    config_exists = config_path(root).exists()
    checks.append(
        Check(
            "Local config",
            "OK" if config_exists else "WARN",
            str(config_path(root)) if config_exists else f"Run `devsecops init` to create {CONFIG_FILE}.",
        )
    )

    valid_project = bool(PROJECT_NAME_RE.match(cfg["project_name"]))
    checks.append(
        Check(
            "Project name",
            "OK" if valid_project else "FAIL",
            cfg["project_name"] if valid_project else "Must be 3-32 chars: lowercase, digits, hyphens.",
        )
    )

    required_files = [
        "terraform/main.tf",
        "terraform/modules/lambda/main.tf",
        ".github/workflows/deploy.yml",
    ]
    missing = [path for path in required_files if not (root / path).exists()]
    checks.append(
        Check(
            "Project files",
            "OK" if not missing else "FAIL",
            "Required files present." if not missing else "Missing: " + ", ".join(missing),
        )
    )

    for command, required in [("git", False), ("terraform", True), ("aws", False)]:
        exists = command_exists(command)
        checks.append(
            Check(
                f"`{command}` CLI",
                "OK" if exists else ("FAIL" if required else "WARN"),
                "Installed." if exists else "Not found on PATH.",
            )
        )

    image_uri = cfg["lambda_image_uri"]
    if image_uri and is_immutable_image(image_uri):
        image_status = "OK"
        image_detail = image_uri
    elif image_uri:
        image_status = "FAIL"
        image_detail = "Use an immutable tag or digest, not latest/bootstrap."
    else:
        image_status = "WARN"
        image_detail = "Required before production deploy."
    checks.append(Check("Lambda image URI", image_status, image_detail))

    backend_bucket = cfg["backend"]["bucket"]
    backend_ready = backend_bucket and not backend_bucket.startswith("replace-with")
    checks.append(
        Check(
            "Backend bucket",
            "OK" if backend_ready else "WARN",
            backend_bucket if backend_ready else "Set a real S3 state bucket name.",
        )
    )
    checks.append(Check("Backend lock table", "OK", cfg["backend"]["lock_table"]))

    config_failures = [check for check in validate_config(cfg) if check.status == "FAIL"]
    checks.append(
        Check(
            "Config schema",
            "OK" if not config_failures else "FAIL",
            "Environment settings valid."
            if not config_failures
            else f"{len(config_failures)} invalid setting(s). Run `devsecops validate-config`.",
        )
    )

    generated_tfvars = root / GENERATED_TFVARS
    checks.append(
        Check(
            "Rendered tfvars",
            "OK" if generated_tfvars.exists() else "WARN",
            str(generated_tfvars) if generated_tfvars.exists() else "Run `devsecops render`.",
        )
    )

    checks.append(
        Check(
            "HTTP validation",
            "OK" if cfg["enable_http_validation"] else "INFO",
            "Enabled." if cfg["enable_http_validation"] else "Disabled by config.",
            scored=False,
        )
    )
    checks.append(
        Check(
            "DAST",
            "OK" if cfg["enable_dast"] else "INFO",
            "Enabled." if cfg["enable_dast"] else "Disabled by config.",
            scored=False,
        )
    )

    if command_exists("git"):
        branch = run_command(["git", "branch", "--show-current"], root).stdout.strip()
        checks.append(
            Check(
                "Git branch",
                "OK" if branch == "main" else "WARN",
                branch or "Not inside a git branch.",
            )
        )

    if deep and command_exists("terraform"):
        root_validate = run_command(["terraform", "-chdir=terraform", "validate", "-no-color"], root)
        checks.append(
            Check(
                "Terraform validate",
                "OK" if root_validate.returncode == 0 else "FAIL",
                "Root module valid." if root_validate.returncode == 0 else compact_error(root_validate),
            )
        )
        bootstrap_validate = run_command(
            ["terraform", "-chdir=terraform/bootstrap", "validate", "-no-color"],
            root,
        )
        checks.append(
            Check(
                "Bootstrap validate",
                "OK" if bootstrap_validate.returncode == 0 else "FAIL",
                "Bootstrap module valid."
                if bootstrap_validate.returncode == 0
                else compact_error(bootstrap_validate),
            )
        )

    if deep and command_exists("aws"):
        identity = run_command(["aws", "sts", "get-caller-identity", "--output", "text"], root)
        checks.append(
            Check(
                "AWS identity",
                "OK" if identity.returncode == 0 else "WARN",
                "AWS credentials are usable." if identity.returncode == 0 else compact_error(identity),
            )
        )

    return checks


def compact_error(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stderr or result.stdout or "").strip().splitlines()
    return output[-1] if output else f"Command exited with {result.returncode}."


def readiness_score(checks: list[Check]) -> int:
    scored = [check for check in checks if check.scored]
    if not scored:
        return 100
    points = 0
    for check in scored:
        if check.status == "OK":
            points += 2
        elif check.status in {"WARN", "INFO"}:
            points += 1
    return round(points / (len(scored) * 2) * 100)


def readiness_action_for_check(check: Check) -> str:
    if check.name == "Local config":
        return "Run `devsecops init` or use menu option 2."
    if check.name == "Project name":
        return "Set a lowercase 3-32 character project name with `devsecops set project_name <name>`."
    if check.name == "Project files":
        return "Restore the required Terraform and GitHub workflow files."
    if check.name == "`git` CLI":
        return "Install Git and make sure it is available on PATH."
    if check.name == "`terraform` CLI":
        return "Install Terraform and make sure it is available on PATH."
    if check.name == "`aws` CLI":
        return "Install AWS CLI before cloud bootstrap or AWS identity checks."
    if check.name == "Lambda image URI":
        return "Set an immutable image with `devsecops set lambda_image_uri <image-uri> --render`."
    if check.name == "Backend bucket":
        return "Set `backend.bucket` to a real S3 state bucket and run `devsecops render`."
    if check.name == "Config schema":
        return "Run `devsecops validate-config`, fix invalid values, then run `devsecops render`."
    if check.name == "Rendered tfvars":
        return "Run `devsecops render`."
    if check.name == "Git branch":
        return "Switch to `main` when checking production deploy readiness."
    if check.name in {"Terraform validate", "Bootstrap validate"}:
        return "Run `terraform validate` in the reported module and fix the Terraform error."
    if check.name == "AWS identity":
        return "Configure AWS credentials and verify with `aws sts get-caller-identity`."
    return check.detail


def readiness_gap_rows(checks: list[Check]) -> list[list[str]]:
    return [
        [check.name, check.status, check.detail, readiness_action_for_check(check)]
        for check in checks
        if check.scored and check.status != "OK"
    ]


def print_readiness_details(root: Path, deep: bool = False) -> None:
    cfg = load_config(root)
    checks = collect_checks(root, cfg, deep=deep)
    score = readiness_score(checks)
    draw_box(
        "Readiness Details",
        [
            f"Current readiness: {score}%",
            "Only scored checks below 100% are listed here.",
            "Use `devsecops doctor` for the full check list.",
        ],
    )
    rows = readiness_gap_rows(checks)
    print()
    if rows:
        for check_name, status, detail, action in rows:
            label = fail(status) if status == "FAIL" else warn(status)
            print(f"{label} {check_name}")
            print(f"  Current: {detail}")
            print(f"  Fix: {action}")
            print()
    else:
        print(ok("All scored readiness checks are OK."))


def print_checks(checks: list[Check]) -> None:
    score = readiness_score(checks)
    print(info("Readiness: ") + progress_bar(score))
    print()
    name_width = max(len(check.name) for check in checks) + 2
    for check in checks:
        if check.status == "OK":
            label = ok("OK")
        elif check.status == "FAIL":
            label = fail("FAIL")
        elif check.status == "INFO":
            label = info("INFO")
        else:
            label = warn("WARN")
        print(f"{check.name.ljust(name_width)} {label.ljust(12)} {check.detail}")


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
    return "\n".join(lines)


def markdown_report(cfg: dict[str, Any], checks: list[Check]) -> str:
    score = readiness_score(checks)
    check_rows = [[check.name, check.status, check.detail] for check in checks]
    control_report_rows = control_rows(cfg)
    env_report_rows = env_rows(cfg)
    generated_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return "\n\n".join(
        [
            "# DevSecOps Pipeline Readiness Report",
            f"Generated: {generated_at}",
            f"Project: `{cfg['project_name']}`",
            f"Region: `{cfg['aws_region']}`",
            f"Readiness: `{score}%`",
            "## Checks\n\n" + markdown_table(["Check", "Status", "Detail"], check_rows),
            "## Environments\n\n"
            + markdown_table(["Env", "Memory", "Timeout", "Logs", "Burst/Rate", "CORS"], env_report_rows),
            "## Controls\n\n" + markdown_table(["Control", "State", "Notes"], control_report_rows),
            "## Next Actions\n\n" + "\n".join(next_actions(cfg, checks)),
            "",
        ]
    )


def next_actions(cfg: dict[str, Any], checks: list[Check]) -> list[str]:
    actions: list[str] = []
    if not cfg["lambda_image_uri"]:
        actions.append("- Set `LAMBDA_IMAGE_URI` to an immutable Lambda container image.")
    if cfg["backend"]["bucket"].startswith("replace-with"):
        actions.append("- Set a real Terraform backend S3 bucket and run `devsecops render`.")
    if any(check.name == "`aws` CLI" and check.status != "OK" for check in checks):
        actions.append("- Install or configure AWS CLI before running cloud bootstrap checks.")
    if not cfg["enable_http_validation"]:
        actions.append("- Enable `/health` validation after the workload implements that route.")
    if not cfg["enable_dast"]:
        actions.append("- Enable DAST only after the API surface is safe for passive scanning.")
    if not actions:
        actions.append("- No immediate configuration gaps detected.")
    return actions


def env_rows(cfg: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for env_name, env_cfg in cfg["environments"].items():
        rows.append(
            [
                env_name,
                str(env_cfg["lambda_memory_size"]),
                str(env_cfg["lambda_timeout"]),
                str(env_cfg["log_retention_days"]),
                f"{env_cfg['api_throttling_burst_limit']}/{env_cfg['api_throttling_rate_limit']}",
                ",".join(env_cfg["cors_allowed_origins"]),
            ]
        )
    return rows


def cmd_envs(args: argparse.Namespace) -> int:
    cfg = load_config(repo_root())
    draw_table(
        ["Env", "Memory", "Timeout", "Logs", "Burst/Rate", "CORS"],
        env_rows(cfg),
        title="Environment Configuration",
    )
    return 0


def control_rows(cfg: dict[str, Any]) -> list[list[str]]:
    image_status = "ON" if cfg["lambda_image_uri"] else "TODO"
    health_status = "ON" if cfg["enable_http_validation"] else "OFF"
    dast_status = "ON" if cfg["enable_dast"] else "OFF"
    return [
        ["GitHub OIDC", "ON", "Deploy and plan roles use short-lived AWS credentials."],
        ["Terraform state lock", "ON", f"DynamoDB table: {cfg['backend']['lock_table']}"],
        ["IaC scan", "ON", "Trivy config scan in GitHub Actions."],
        ["Immutable image", image_status, "LAMBDA_IMAGE_URI must avoid latest/bootstrap."],
        ["Container scan", "OPTIONAL", "Snyk runs when SNYK_TOKEN is configured."],
        ["Rollback", "ON", "Previous Lambda image is restored on failed deploy validation."],
        ["HTTP health", health_status, "Calls /health when ENABLE_HTTP_VALIDATION=true."],
        ["DAST", dast_status, "OWASP ZAP baseline when ENABLE_DAST=true."],
    ]


def cmd_controls(args: argparse.Namespace) -> int:
    cfg = load_config(repo_root())
    draw_table(["Control", "State", "Notes"], control_rows(cfg), title="Pipeline Controls")
    return 0


def architecture_lines() -> list[str]:
    return [
        "GitHub Actions",
        "|-- OIDC -> AWS STS -> deploy role",
        "|-- Terraform validate + Trivy IaC scan",
        "|-- Terraform plan comment on pull requests",
        "|-- Production apply on push to main",
        "AWS",
        "|-- S3 backend + DynamoDB lock",
        "|-- KMS key",
        "|-- ECR repository",
        "|-- Lambda container workload",
        "|-- API Gateway HTTP API",
        "|-- S3 workload data bucket",
        "|-- CloudWatch Logs + X-Ray + SQS DLQ",
    ]


def cmd_architecture(args: argparse.Namespace) -> int:
    draw_box("Architecture", architecture_lines())
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    header(cfg)
    print()
    for line in menu_status(root, cfg):
        print(line)
    print()
    cmd_envs(argparse.Namespace())
    print()
    cmd_controls(argparse.Namespace())
    print()
    draw_box("Architecture", architecture_lines())
    return 0


def cmd_validate_config(args: argparse.Namespace) -> int:
    cfg = load_config(repo_root())
    checks = validate_config(cfg)
    print_checks(checks)
    return 1 if any(check.status == "FAIL" for check in checks) else 0


def cmd_set(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    if args.key not in CONFIG_SET_PATHS:
        print(fail("Unknown config key: ") + args.key)
        print("Allowed keys:")
        for key in sorted(CONFIG_SET_PATHS):
            print(f"  {key}")
        return 1
    try:
        current = nested_get(cfg, args.key)
        value = parse_config_value(args.value, current)
        nested_set(cfg, args.key, value)
    except (KeyError, ValueError) as exc:
        print(fail(str(exc)))
        return 1
    snapshot_before_change(root, "set", f"Before setting {args.key} to {args.value}.")
    write_config(root, cfg)
    print(ok("Updated ") + f"{CONFIG_FILE}: {args.key} = {toml_value(value)}")
    if args.render:
        return run_render(root, snapshot=False)
    return 0


def cmd_preset(args: argparse.Namespace) -> int:
    root = repo_root()
    current = load_config(root)
    cfg = preset_config(args.name)
    for key in ["project_name", "aws_region", "lambda_image_uri", "terraform_admin_role_name", "backend"]:
        cfg[key] = current[key]
    snapshot_before_change(root, "preset", f"Before applying `{args.name}` preset.")
    write_config(root, cfg)
    print(ok("Applied preset ") + args.name)
    if args.render:
        return run_render(root, snapshot=False)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    checks = collect_checks(root, cfg, deep=args.deep)
    report = markdown_report(cfg, checks)
    if args.output:
        path = Path(args.output)
    else:
        path = root / DIST_DIR / "readiness-report.md"
    if not path.is_absolute():
        path = root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_before_change(root, "report", f"Before writing readiness report to {path.relative_to(root) if path.is_relative_to(root) else path}.")
    path.write_text(report, encoding="utf-8")
    print(ok("Wrote ") + str(path))
    if args.print:
        print()
        print(report)
    return 0


def cmd_github_setup(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    if args.apply:
        return apply_github_setup(root, cfg, args)
    script = github_setup_script(cfg)
    if args.write:
        path = root / DIST_DIR / "github-setup.sh"
        path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_before_change(root, "github-setup", "Before writing GitHub setup script.")
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
        print(ok("Wrote ") + str(path))
    else:
        print(script)
    return 0


def cmd_snapshots(args: argparse.Namespace) -> int:
    root = repo_root()
    snapshots = print_snapshot_list(root)
    if args.show:
        snapshot = resolve_snapshot_selection(root, args.show)
        if not snapshot:
            print(fail("Snapshot not found: ") + args.show)
            return 1
        print()
        print_snapshot_detail(root, snapshot)
    elif snapshots:
        print()
        print(info("Use `devsecops snapshots --show <number-or-id>` to inspect changes."))
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    root = repo_root()
    target = args.to
    if args.last or not target:
        snapshot = resolve_snapshot(root, last=True)
    else:
        snapshot = resolve_snapshot_selection(root, target)
    if not snapshot:
        print(fail("No matching snapshot found."))
        return 1
    print_snapshot_detail(root, snapshot)
    changes = snapshot_changes(root, snapshot)
    if args.dry_run:
        print()
        print(info("Dry run only. No files changed."))
        return 0
    if not changes:
        print(info("Snapshot matches current files. Nothing to roll back."))
        return 0
    if not args.yes:
        confirmation = input(f"\nRollback to {snapshot.get('id')}? Type yes to continue: ").strip().lower()
        if confirmation != "yes":
            print(info("Rollback cancelled."))
            return 0
    snapshot_before_change(root, "rollback", f"Before rolling back to {snapshot.get('id')}.")
    restore_snapshot(root, snapshot)
    print(ok("Rolled back to snapshot ") + str(snapshot.get("id")))
    return 0


def cmd_gh_doctor(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    checks = collect_github_checks(root, cfg)
    draw_box("GitHub", ["Repository readiness checks through GitHub CLI."])
    print_checks(checks)
    if args.strict and any(check.status == "FAIL" for check in checks if check.scored):
        return 1
    return 0


def cmd_gh_status(args: argparse.Namespace) -> int:
    rows, failed_rows, error = github_actions_status(repo_root(), limit=args.limit)
    if error:
        print(warn(error))
        return 1 if args.strict else 0
    if not rows:
        print(warn("No GitHub Actions runs found."))
        return 0
    draw_table(["Workflow", "Branch", "Status", "Conclusion", "Created"], rows, title="Recent GitHub Actions Runs")
    if failed_rows:
        print()
        draw_table(["Workflow", "Job", "Status", "Conclusion"], failed_rows, title="Failed Jobs")
    else:
        print(ok("No failed jobs found in inspected runs."))
    return 0


def cmd_branch_doctor(args: argparse.Namespace) -> int:
    checks = collect_branch_checks(repo_root(), branch=args.branch)
    draw_box("Branch Protection", [f"Repository branch policy checks for `{args.branch}`."])
    print_checks(checks)
    if args.strict and any(check.status == "FAIL" for check in checks if check.scored):
        return 1
    return 0


def run_init(root: Path, force: bool = False, defaults: bool = False, allow_cancel: bool = False) -> int:
    current = load_config(root)
    path = config_path(root)
    try:
        if path.exists() and not force:
            replace = prompt_bool(f"{CONFIG_FILE} exists. Replace it", False, allow_cancel=allow_cancel)
            if not replace:
                print(info("Kept existing config."))
                return 0

        cfg = default_config()
        if not defaults:
            if not allow_cancel:
                header(current)
            cfg["project_name"] = prompt_text("Project name", current["project_name"], allow_cancel=allow_cancel)
            cfg["aws_region"] = prompt_text("AWS region", current["aws_region"], allow_cancel=allow_cancel)
            cfg["lambda_image_uri"] = prompt_text(
                "Immutable Lambda image URI",
                current["lambda_image_uri"],
                allow_cancel=allow_cancel,
            )
            cfg["enable_http_validation"] = prompt_bool(
                "Enable /health smoke test",
                bool(current["enable_http_validation"]),
                allow_cancel=allow_cancel,
            )
            cfg["enable_dast"] = prompt_bool(
                "Enable OWASP ZAP DAST",
                bool(current["enable_dast"]),
                allow_cancel=allow_cancel,
            )
            cfg["backend"]["bucket"] = prompt_text(
                "Terraform state bucket",
                current["backend"]["bucket"],
                allow_cancel=allow_cancel,
            )
            cfg["backend"]["lock_table"] = prompt_text(
                "DynamoDB lock table",
                current["backend"]["lock_table"],
                allow_cancel=allow_cancel,
            )
            cfg["backend"]["region"] = prompt_text(
                "Backend AWS region",
                current["backend"]["region"],
                allow_cancel=allow_cancel,
            )

            for env_name, env_cfg in cfg["environments"].items():
                print()
                print(info(f"{env_name} environment"))
                current_env = current["environments"][env_name]
                env_cfg["lambda_memory_size"] = prompt_int(
                    f"{env_name} Lambda memory MB",
                    int(current_env["lambda_memory_size"]),
                    allow_cancel=allow_cancel,
                )
                env_cfg["lambda_timeout"] = prompt_int(
                    f"{env_name} Lambda timeout seconds",
                    int(current_env["lambda_timeout"]),
                    allow_cancel=allow_cancel,
                )
                env_cfg["log_retention_days"] = prompt_int(
                    f"{env_name} log retention days",
                    int(current_env["log_retention_days"]),
                    allow_cancel=allow_cancel,
                )
                env_cfg["api_throttling_burst_limit"] = prompt_int(
                    f"{env_name} API burst limit",
                    int(current_env["api_throttling_burst_limit"]),
                    allow_cancel=allow_cancel,
                )
                env_cfg["api_throttling_rate_limit"] = prompt_int(
                    f"{env_name} API rate limit",
                    int(current_env["api_throttling_rate_limit"]),
                    allow_cancel=allow_cancel,
                )
    except InputCancelled:
        print(info("Configuration cancelled. No files changed."))
        return 0

    snapshot_before_change(root, "init", "Before creating or replacing local pipeline config.")
    path.write_text(dump_config_toml(cfg), encoding="utf-8")
    print(ok("Created ") + str(path))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    return run_init(repo_root(), force=args.force, defaults=args.defaults)


def run_render(root: Path, snapshot: bool = True) -> int:
    cfg = load_config(root)
    dist = root / DIST_DIR
    dist.mkdir(parents=True, exist_ok=True)
    if snapshot:
        snapshot_before_change(root, "render", "Before rendering Terraform and GitHub helper artifacts.")

    outputs = {
        root / GENERATED_TFVARS: terraform_tfvars(cfg),
        dist / "backend.tf": backend_tf(cfg),
        dist / "github-variables.env": github_variables(cfg),
        dist / "github-setup.sh": github_setup_script(cfg),
        dist / "setup-checklist.md": checklist(cfg),
    }
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if path.name.endswith(".sh"):
            path.chmod(0o755)
        print(ok("Rendered ") + str(path))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    return run_render(repo_root())


def cmd_doctor(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    header(cfg)
    checks = collect_checks(root, cfg, deep=args.deep)
    print_checks(checks)
    if args.strict and any(check.status == "FAIL" for check in checks if check.scored):
        return 1
    return 0


def cmd_readiness(args: argparse.Namespace) -> int:
    print_readiness_details(repo_root(), deep=args.deep)
    return 0


def run_plan(root: Path, env_name: str, no_init: bool = False, create_workspace: bool = False) -> int:
    cfg = load_config(root)
    if env_name not in cfg["environments"]:
        print(fail("Unknown environment: ") + env_name)
        return 1
    if not (root / GENERATED_TFVARS).exists():
        print(warn("Missing generated tfvars. Running render first."))
        run_render(root)

    if not no_init:
        init_command = ["terraform", "-chdir=terraform", "init", "-input=false", "-no-color"]
        print(info("$ " + " ".join(init_command)))
        init_result = subprocess.run(init_command, cwd=root, check=False)
        if init_result.returncode != 0:
            return init_result.returncode

    select_command = ["terraform", "-chdir=terraform", "workspace", "select", env_name]
    print(info("$ " + " ".join(select_command)))
    select_result = subprocess.run(select_command, cwd=root, check=False)
    if select_result.returncode != 0:
        if not create_workspace:
            print(warn(f"Workspace `{env_name}` does not exist. Re-run with --create-workspace if this is expected."))
            return select_result.returncode
        new_command = ["terraform", "-chdir=terraform", "workspace", "new", env_name]
        print(info("$ " + " ".join(new_command)))
        new_result = subprocess.run(new_command, cwd=root, check=False)
        if new_result.returncode != 0:
            return new_result.returncode

    plan_command = ["terraform", "-chdir=terraform", "plan", "-input=false", "-no-color"]
    print(info("$ " + " ".join(plan_command)))
    plan_result = subprocess.run(plan_command, cwd=root, check=False)
    if plan_result.returncode != 0:
        return plan_result.returncode
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    return run_plan(
        repo_root(),
        args.environment,
        no_init=args.no_init,
        create_workspace=args.create_workspace,
    )


def cmd_bootstrap(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    bucket = cfg["backend"]["bucket"]
    if not bucket or bucket.startswith("replace-with"):
        print(fail("Set backend.bucket in ") + CONFIG_FILE + " before bootstrap.")
        return 1
    command = [
        "terraform",
        "-chdir=terraform/bootstrap",
        "apply" if args.apply else "plan",
        "-input=false",
        "-no-color",
        f"-var=state_bucket_name={bucket}",
        f"-var=lock_table_name={cfg['backend']['lock_table']}",
        f"-var=aws_region={cfg['backend']['region']}",
    ]
    if args.apply:
        command.insert(4, "-auto-approve")
    print(info("$ terraform -chdir=terraform/bootstrap init -input=false -no-color"))
    init = subprocess.run(
        ["terraform", "-chdir=terraform/bootstrap", "init", "-input=false", "-no-color"],
        cwd=root,
        check=False,
    )
    if init.returncode != 0:
        return init.returncode
    print(info("$ " + " ".join(command)))
    return subprocess.run(command, cwd=root, check=False).returncode


def explain_text(topic: str) -> list[str]:
    topics = {
        "rollback": [
            "Rollback captures the current Lambda image before apply.",
            "If Terraform apply, optional health validation, or optional DAST fails, CI updates the function back to the previous image URI and re-applies Terraform to remove state drift.",
        ],
        "oidc": [
            "GitHub Actions OIDC exchanges a GitHub identity token for short-lived AWS credentials.",
            "This avoids long-lived AWS keys in GitHub secrets and lets IAM scope deploy rights to repo, branch, and environment.",
        ],
        "dast": [
            "DAST runs OWASP ZAP baseline against the deployed API Gateway URL.",
            "It is disabled by default because this repository no longer owns application routes. Enable it only when the workload HTTP surface is safe for passive scanning.",
        ],
        "image": [
            "The pipeline deploys a prebuilt Lambda container image through LAMBDA_IMAGE_URI.",
            "Use a digest or immutable tag. The workflow rejects latest/bootstrap because rollback and auditability require a stable image identity.",
        ],
        "backend": [
            "Terraform remote state should live in S3 with DynamoDB locking.",
            "The CLI render command writes a backend.tf template; bootstrap can plan/apply the state bucket and lock table.",
        ],
    }
    return topics.get(
        topic,
        [
            "Available topics: rollback, oidc, dast, image, backend.",
            "Use `devsecops explain <topic>` for a focused control explanation.",
        ],
    )


def cmd_explain(args: argparse.Namespace) -> int:
    draw_box(f"Explain: {args.topic}", explain_text(args.topic))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    root = repo_root()
    path = config_path(root)
    if not path.exists():
        print(warn(f"{CONFIG_FILE} does not exist. Run `devsecops init`."))
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def menu_status(root: Path, cfg: dict[str, Any]) -> list[str]:
    checks = collect_checks(root, cfg, deep=False)
    score = readiness_score(checks)
    image_state = "configured" if cfg["lambda_image_uri"] else "missing"
    backend_state = "configured" if not cfg["backend"]["bucket"].startswith("replace-with") else "missing"
    return [
        f"Project: {cfg['project_name']}",
        f"Region: {cfg['aws_region']}",
        f"Lambda image: {image_state}",
        f"Backend: {backend_state}",
        f"Health check: {'enabled' if cfg['enable_http_validation'] else 'disabled'}",
        f"DAST: {'enabled' if cfg['enable_dast'] else 'disabled'}",
        "Readiness: " + progress_bar(score) + "  [i] details",
    ]


def clear_screen() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")
    else:
        print("\n" * 3)


def pause_for_menu() -> None:
    input("\n[Enter] Back to main menu")


def print_main_menu(root: Path) -> None:
    cfg = load_config(root)
    header(cfg)
    for line in menu_status(root, cfg):
        print(line)
    print()
    print("[1] Dashboard")
    print("[2] Configure pipeline")
    print("[3] Validate environment")
    print("[4] Render Terraform/GitHub config")
    print("[5] Bootstrap Terraform backend plan")
    print("[6] Run Terraform plan")
    print("[7] Security controls overview")
    print("[8] Environment table")
    print("[9] Export readiness report")
    print("[10] GitHub setup commands")
    print("[11] GitHub doctor")
    print("[12] GitHub Actions status")
    print("[13] Branch protection doctor")
    print("[14] Apply preset")
    print("[15] Show config")
    print("[16] Snapshots / Rollback")
    print("[0] Exit")


def open_menu_section(title: str, handler: Any, *args: Any) -> None:
    clear_screen()
    draw_box(title, ["[Enter] returns to the main menu after this section."])
    print()
    handler(*args)
    pause_for_menu()


def menu_plan_section(root: Path) -> None:
    clear_screen()
    draw_box("Run Terraform Plan", ["Enter an environment or type `b`, `back`, `0`, or `cancel` to return."])
    env_name = prompt_text("Environment", "dev")
    if is_cancel_input(env_name):
        return
    run_plan(root, env_name)
    pause_for_menu()


def menu_preset_section() -> None:
    clear_screen()
    draw_box("Apply Preset", ["Choose minimal, balanced, or strict. Type `b`, `back`, `0`, or `cancel` to return."])
    preset_name = prompt_text("Preset", "balanced")
    if is_cancel_input(preset_name):
        return
    cmd_preset(argparse.Namespace(name=preset_name, render=False))
    pause_for_menu()


def menu_config_section(root: Path) -> None:
    clear_screen()
    draw_box(
        "Configure Pipeline",
        [
            "Press Enter to keep the current value.",
            "Type `b`, `back`, `0`, or `cancel` at any prompt to return without saving.",
        ],
    )
    print()
    run_init(root, force=True, defaults=False, allow_cancel=True)
    pause_for_menu()


def menu_readiness_section(root: Path) -> None:
    clear_screen()
    print_readiness_details(root)
    pause_for_menu()


def menu_rollback_section(root: Path) -> None:
    clear_screen()
    draw_box(
        "Snapshots / Rollback",
        [
            "Snapshots are local restore points for CLI-owned generated files.",
            "Choose a number to inspect changes, or type `b`, `back`, `0`, or `cancel` to return.",
        ],
    )
    snapshots = print_snapshot_list(root)
    if not snapshots:
        pause_for_menu()
        return

    selection = prompt_text("Snapshot number or id", "1")
    if is_cancel_input(selection):
        return
    snapshot = resolve_snapshot_selection(root, selection)
    if not snapshot:
        print(fail("Snapshot not found."))
        pause_for_menu()
        return

    clear_screen()
    print_snapshot_detail(root, snapshot)
    print()
    print("[1] Preview rollback")
    print("[2] Roll back to this snapshot")
    print("[0] Back to main menu")
    action = input("\nChoose: ").strip()

    if action == "1":
        print()
        print(info("Preview only. No files changed."))
        pause_for_menu()
        return
    if action == "2":
        changes = snapshot_changes(root, snapshot)
        if not changes:
            print(info("Snapshot matches current files. Nothing to roll back."))
            pause_for_menu()
            return
        confirmation = input(f"\nRollback to {snapshot.get('id')}? Type yes to continue: ").strip().lower()
        if confirmation != "yes":
            print(info("Rollback cancelled."))
            pause_for_menu()
            return
        snapshot_before_change(root, "rollback", f"Before rolling back to {snapshot.get('id')}.")
        restore_snapshot(root, snapshot)
        print(ok("Rolled back to snapshot ") + str(snapshot.get("id")))
        pause_for_menu()


def cmd_menu(args: argparse.Namespace) -> int:
    root = repo_root()
    while True:
        clear_screen()
        print_main_menu(root)
        choice = input("\nChoose: ").strip()
        normalized_choice = choice.lower()
        if normalized_choice in {"i", "info", "readiness", "?"}:
            menu_readiness_section(root)
        elif choice == "1":
            open_menu_section("Dashboard", cmd_dashboard, argparse.Namespace())
        elif choice == "2":
            menu_config_section(root)
        elif choice == "3":
            open_menu_section("Validate Environment", cmd_doctor, argparse.Namespace(deep=True, strict=False))
        elif choice == "4":
            open_menu_section("Render Config", run_render, root)
        elif choice == "5":
            open_menu_section("Bootstrap Backend Plan", cmd_bootstrap, argparse.Namespace(apply=False))
        elif choice == "6":
            menu_plan_section(root)
        elif choice == "7":
            def show_controls_overview() -> None:
                for topic_name in ["oidc", "backend", "image", "rollback", "dast"]:
                    draw_box(f"Explain: {topic_name}", explain_text(topic_name))

            open_menu_section("Security Controls", show_controls_overview)
        elif choice == "8":
            open_menu_section("Environment Table", cmd_envs, argparse.Namespace())
        elif choice == "9":
            open_menu_section("Export Readiness Report", cmd_report, argparse.Namespace(deep=False, output=None, print=False))
        elif choice == "10":
            open_menu_section(
                "GitHub Setup Commands",
                cmd_github_setup,
                argparse.Namespace(
                    write=False,
                    apply=False,
                    deploy_role_arn=None,
                    plan_role_arn=None,
                    snyk_token=None,
                ),
            )
        elif choice == "11":
            open_menu_section("GitHub Doctor", cmd_gh_doctor, argparse.Namespace(strict=False))
        elif choice == "12":
            open_menu_section("GitHub Actions Status", cmd_gh_status, argparse.Namespace(strict=False, limit=8))
        elif choice == "13":
            open_menu_section("Branch Protection Doctor", cmd_branch_doctor, argparse.Namespace(branch=DEFAULT_BRANCH, strict=False))
        elif choice == "14":
            menu_preset_section()
        elif choice == "15":
            open_menu_section("Show Config", cmd_config, argparse.Namespace())
        elif choice == "16":
            menu_rollback_section(root)
        elif choice == "0":
            clear_screen()
            return 0
        else:
            clear_screen()
            print(warn("Unknown option."))
            pause_for_menu()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devsecops",
        description="Configure and validate this DevSecOps Lambda pipeline template.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command")
    parser.set_defaults(func=cmd_menu)

    menu_parser = subparsers.add_parser("menu", help="Open the interactive main menu.")
    menu_parser.set_defaults(func=cmd_menu)

    dashboard_parser = subparsers.add_parser("dashboard", help="Print a one-screen pipeline dashboard.")
    dashboard_parser.set_defaults(func=cmd_dashboard)

    envs_parser = subparsers.add_parser("envs", help="Print environment settings table.")
    envs_parser.set_defaults(func=cmd_envs)

    controls_parser = subparsers.add_parser("controls", help="Print security controls matrix.")
    controls_parser.set_defaults(func=cmd_controls)

    architecture_parser = subparsers.add_parser("architecture", help="Print architecture tree.")
    architecture_parser.set_defaults(func=cmd_architecture)

    init_parser = subparsers.add_parser("init", help="Create or update local pipeline config.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing config without asking.")
    init_parser.add_argument("--defaults", action="store_true", help="Write default config without prompts.")
    init_parser.set_defaults(func=cmd_init)

    doctor_parser = subparsers.add_parser("doctor", help="Check local readiness.")
    doctor_parser.add_argument("--deep", action="store_true", help="Run Terraform validate and AWS identity checks.")
    doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    doctor_parser.set_defaults(func=cmd_doctor)

    readiness_parser = subparsers.add_parser("readiness", help="Show what blocks 100% readiness.")
    readiness_parser.add_argument("--deep", action="store_true", help="Include Terraform/AWS deep checks.")
    readiness_parser.set_defaults(func=cmd_readiness)

    render_parser = subparsers.add_parser("render", help="Render ignored config artifacts.")
    render_parser.set_defaults(func=cmd_render)

    report_parser = subparsers.add_parser("report", help="Export a Markdown readiness report.")
    report_parser.add_argument("--deep", action="store_true", help="Include Terraform/AWS deep checks.")
    report_parser.add_argument("--output", help="Report output path. Defaults to dist/devsecops/readiness-report.md.")
    report_parser.add_argument("--print", action="store_true", help="Print report after writing it.")
    report_parser.set_defaults(func=cmd_report)

    snapshots_parser = subparsers.add_parser("snapshots", help="List local CLI snapshots.")
    snapshots_parser.add_argument("--show", help="Show snapshot details by number or id.")
    snapshots_parser.set_defaults(func=cmd_snapshots)

    rollback_parser = subparsers.add_parser("rollback", help="Restore CLI-owned files from a snapshot.")
    rollback_parser.add_argument("--to", help="Snapshot number or id to restore.")
    rollback_parser.add_argument("--last", action="store_true", help="Restore the newest snapshot.")
    rollback_parser.add_argument("--dry-run", action="store_true", help="Preview rollback without changing files.")
    rollback_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
    rollback_parser.set_defaults(func=cmd_rollback)

    github_setup_parser = subparsers.add_parser("github-setup", help="Print or write gh setup commands.")
    github_setup_parser.add_argument("--write", action="store_true", help="Write dist/devsecops/github-setup.sh.")
    github_setup_parser.add_argument("--apply", action="store_true", help="Apply safe GitHub variables/secrets with gh.")
    github_setup_parser.add_argument("--deploy-role-arn", help="Value for AWS_ROLE_TO_ASSUME_ARN when using --apply.")
    github_setup_parser.add_argument("--plan-role-arn", help="Value for AWS_PLAN_ROLE_TO_ASSUME_ARN when using --apply.")
    github_setup_parser.add_argument("--snyk-token", help="Optional SNYK_TOKEN value when using --apply.")
    github_setup_parser.set_defaults(func=cmd_github_setup)

    gh_setup_parser = subparsers.add_parser("gh-setup", help="Alias for github-setup.")
    gh_setup_parser.add_argument("--write", action="store_true", help="Write dist/devsecops/github-setup.sh.")
    gh_setup_parser.add_argument("--apply", action="store_true", help="Apply safe GitHub variables/secrets with gh.")
    gh_setup_parser.add_argument("--deploy-role-arn", help="Value for AWS_ROLE_TO_ASSUME_ARN when using --apply.")
    gh_setup_parser.add_argument("--plan-role-arn", help="Value for AWS_PLAN_ROLE_TO_ASSUME_ARN when using --apply.")
    gh_setup_parser.add_argument("--snyk-token", help="Optional SNYK_TOKEN value when using --apply.")
    gh_setup_parser.set_defaults(func=cmd_github_setup)

    gh_doctor_parser = subparsers.add_parser("gh-doctor", help="Check GitHub CLI, repo variables, and repo secrets.")
    gh_doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    gh_doctor_parser.set_defaults(func=cmd_gh_doctor)

    gh_status_parser = subparsers.add_parser("gh-status", help="Show recent GitHub Actions runs.")
    gh_status_parser.add_argument("--strict", action="store_true", help="Exit non-zero when status cannot be read.")
    gh_status_parser.add_argument("--limit", type=int, default=8, help="Number of workflow runs to inspect.")
    gh_status_parser.set_defaults(func=cmd_gh_status)

    actions_status_parser = subparsers.add_parser("actions-status", help="Show recent GitHub Actions runs and failed jobs.")
    actions_status_parser.add_argument("--strict", action="store_true", help="Exit non-zero when status cannot be read.")
    actions_status_parser.add_argument("--limit", type=int, default=8, help="Number of workflow runs to inspect.")
    actions_status_parser.set_defaults(func=cmd_gh_status)

    branch_doctor_parser = subparsers.add_parser("branch-doctor", help="Check branch protection and required checks.")
    branch_doctor_parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Branch to inspect.")
    branch_doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    branch_doctor_parser.set_defaults(func=cmd_branch_doctor)

    set_parser = subparsers.add_parser("set", help="Set a local config value.")
    set_parser.add_argument("key", help="Config key, for example backend.bucket.")
    set_parser.add_argument("value", help="New value. Lists use comma-separated values.")
    set_parser.add_argument("--render", action="store_true", help="Render artifacts after updating config.")
    set_parser.set_defaults(func=cmd_set)

    validate_config_parser = subparsers.add_parser("validate-config", help="Validate local config values.")
    validate_config_parser.set_defaults(func=cmd_validate_config)

    preset_parser = subparsers.add_parser("preset", help="Apply an environment configuration preset.")
    preset_parser.add_argument("name", choices=sorted(PRESETS))
    preset_parser.add_argument("--render", action="store_true", help="Render artifacts after applying preset.")
    preset_parser.set_defaults(func=cmd_preset)

    plan_parser = subparsers.add_parser("plan", help="Run Terraform plan for an environment.")
    plan_parser.add_argument("environment", choices=["dev", "staging", "prod"])
    plan_parser.add_argument("--no-init", action="store_true", help="Skip Terraform init.")
    plan_parser.add_argument("--create-workspace", action="store_true", help="Create the workspace if it is missing.")
    plan_parser.set_defaults(func=cmd_plan)

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Plan or apply backend bootstrap.")
    bootstrap_parser.add_argument("--apply", action="store_true", help="Apply backend bootstrap with auto-approve.")
    bootstrap_parser.set_defaults(func=cmd_bootstrap)

    explain_parser = subparsers.add_parser("explain", help="Explain a pipeline security control.")
    explain_parser.add_argument("topic", nargs="?", default="all")
    explain_parser.set_defaults(func=cmd_explain)

    config_parser = subparsers.add_parser("config", help="Print local config.")
    config_parser.set_defaults(func=cmd_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print()
        print(warn("Interrupted."))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
