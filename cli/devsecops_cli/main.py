#!/usr/bin/env python3
"""DevSecOps Pipeline Kit CLI.

This CLI is intentionally dependency-free so it can run before the project has
any Python environment configured. It uses a local TOML config and generates
ignored Terraform/GitHub helper artifacts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback guard
    tomllib = None  # type: ignore[assignment]


VERSION = "0.6.1"
CONFIG_SCHEMA_VERSION = 1
CONFIG_FILE = ".devsecops-pipeline.toml"
EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_MISSING_EXTERNAL_TOOL = 2
EXIT_AUTH_FAILED = 3
EXIT_UNEXPECTED_ERROR = 70
EXIT_INTERRUPTED = 130
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
GENERATED_ARTIFACT_DOC = "docs/generated-artifacts.md"
PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,31}$")
AWS_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
PRESET_ORDER = ["minimal", "balanced", "strict", "enterprise", "student-demo"]
PRESETS = set(PRESET_ORDER)
PRESET_DESCRIPTIONS = {
    "minimal": "Low-cost settings for early local and development experimentation.",
    "balanced": "Default reference settings for a practical multi-environment pipeline.",
    "strict": "Validation-focused settings with HTTP smoke testing and DAST enabled.",
    "enterprise": "Locked-down CORS, longer log retention, and stricter production gates.",
    "student-demo": "Small, simple settings for classroom demos and short-lived walkthroughs.",
}
ENVIRONMENTS = ["dev", "staging", "prod"]
CONFIG_SET_PATHS = {
    "project_name",
    "aws_region",
    "lambda_image_uri",
    "enable_snyk_scan",
    "enable_http_validation",
    "enable_dast",
    "use_prod_approval_environment",
    "use_separate_aws_plan_role",
    "terraform_admin_role_name",
    "backend.bucket",
    "backend.key",
    "backend.region",
    "backend.lock_table",
    "backend.workspace_key_prefix",
}
for _env_name in ENVIRONMENTS:
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
    "ENABLE_SNYK_SCAN",
    "ENABLE_HTTP_VALIDATION",
    "ENABLE_DAST",
    "PROD_APPROVAL_ENVIRONMENT",
]
BASE_REQUIRED_GH_SECRETS = [
    "AWS_ROLE_TO_ASSUME_ARN",
    "AWS_REGION",
]
PLAN_ROLE_SECRET = "AWS_PLAN_ROLE_TO_ASSUME_ARN"
SNYK_TOKEN_SECRET = "SNYK_TOKEN"
PROD_APPROVAL_ENVIRONMENT = "prod"
NO_APPROVAL_ENVIRONMENT = "devsecops-no-approval"
STRICT_CORS_ORIGINS = {
    "dev": ["https://dev.example.com"],
    "staging": ["https://staging.example.com"],
    "prod": ["https://app.example.com"],
}
DEFAULT_BRANCH = "main"
READINESS_CATEGORIES = ["Local", "Terraform", "GitHub", "AWS", "Security", "Deployment"]
REQUIRED_BRANCH_CHECKS = [
    "Security and Terraform Validate",
    "Terraform Plan",
]
RUNBOOKS_DIR = "docs/runbooks"
RUNBOOK_FAILED_PLAN = f"{RUNBOOKS_DIR}/failed-terraform-plan.md"
RUNBOOK_FAILED_APPLY = f"{RUNBOOKS_DIR}/failed-terraform-apply.md"
RUNBOOK_FAILED_VALIDATION = f"{RUNBOOKS_DIR}/failed-validation.md"
RUNBOOK_MISSING_IMAGE = f"{RUNBOOKS_DIR}/missing-image.md"
RUNBOOK_FAILED_ROLLBACK = f"{RUNBOOKS_DIR}/failed-rollback.md"
CANCEL_INPUTS = {"b", "back", "cancel", "q", "quit"}
MENU_CANCEL_INPUTS = CANCEL_INPUTS | {"0"}
ECR_IMAGE_RE = re.compile(
    r"^(?P<registry>\d{12}\.dkr\.ecr\.(?P<region>[^.]+)\.amazonaws\.com)/"
    r"(?P<repository>[^:@]+)(?::(?P<tag>[^@]+)|@(?P<digest>sha256:[A-Fa-f0-9]{64}))$"
)


class InputCancelled(Exception):
    """Raised when an interactive menu prompt is cancelled by the user."""


@dataclass
class EcrImageRef:
    registry: str
    region: str
    repository: str
    tag: str | None = None
    digest: str | None = None


@dataclass
class ActionsStatus:
    runs: list[list[str]]
    failed_jobs: list[list[str]]
    failed_steps: list[list[str]]
    next_actions: list[str]
    error: str | None = None


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
        "schema_version": CONFIG_SCHEMA_VERSION,
        "project_name": "devsecops-pipeline",
        "aws_region": "us-east-1",
        "lambda_image_uri": "",
        "enable_snyk_scan": False,
        "enable_http_validation": False,
        "enable_dast": False,
        "use_prod_approval_environment": True,
        "use_separate_aws_plan_role": True,
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
        cfg["enable_snyk_scan"] = True
        cfg["enable_http_validation"] = True
        cfg["enable_dast"] = True
        apply_cors_policy(cfg, strict=True)
        cfg["environments"]["prod"]["lambda_timeout"] = 300
        cfg["environments"]["prod"]["api_throttling_burst_limit"] = 50
        cfg["environments"]["prod"]["api_throttling_rate_limit"] = 100
    elif name == "enterprise":
        cfg["enable_snyk_scan"] = True
        cfg["enable_http_validation"] = True
        cfg["enable_dast"] = True
        cfg["use_prod_approval_environment"] = True
        cfg["use_separate_aws_plan_role"] = True
        cfg["environments"] = {
            "dev": {
                "lambda_memory_size": 1536,
                "lambda_timeout": 180,
                "log_retention_days": 90,
                "api_throttling_burst_limit": 20,
                "api_throttling_rate_limit": 40,
                "cors_allowed_origins": ["https://dev.example.com"],
            },
            "staging": {
                "lambda_memory_size": 2048,
                "lambda_timeout": 240,
                "log_retention_days": 365,
                "api_throttling_burst_limit": 40,
                "api_throttling_rate_limit": 80,
                "cors_allowed_origins": ["https://staging.example.com"],
            },
            "prod": {
                "lambda_memory_size": 3072,
                "lambda_timeout": 300,
                "log_retention_days": 1095,
                "api_throttling_burst_limit": 50,
                "api_throttling_rate_limit": 100,
                "cors_allowed_origins": ["https://app.example.com"],
            },
        }
    elif name == "student-demo":
        cfg["enable_snyk_scan"] = False
        cfg["enable_http_validation"] = False
        cfg["enable_dast"] = False
        cfg["use_prod_approval_environment"] = False
        cfg["use_separate_aws_plan_role"] = False
        cfg["environments"] = {
            "dev": {
                "lambda_memory_size": 512,
                "lambda_timeout": 45,
                "log_retention_days": 7,
                "api_throttling_burst_limit": 10,
                "api_throttling_rate_limit": 20,
                "cors_allowed_origins": ["*"],
            },
            "staging": {
                "lambda_memory_size": 512,
                "lambda_timeout": 60,
                "log_retention_days": 14,
                "api_throttling_burst_limit": 10,
                "api_throttling_rate_limit": 20,
                "cors_allowed_origins": ["*"],
            },
            "prod": {
                "lambda_memory_size": 1024,
                "lambda_timeout": 90,
                "log_retention_days": 30,
                "api_throttling_burst_limit": 20,
                "api_throttling_rate_limit": 40,
                "cors_allowed_origins": ["*"],
            },
        }
    elif name != "balanced":
        raise ValueError(f"Unknown preset: {name}")
    return cfg


def prod_approval_environment(cfg: dict[str, Any]) -> str:
    return PROD_APPROVAL_ENVIRONMENT if cfg["use_prod_approval_environment"] else NO_APPROVAL_ENVIRONMENT


def apply_cors_policy(cfg: dict[str, Any], strict: bool) -> None:
    for env_name, env_cfg in cfg["environments"].items():
        env_cfg["cors_allowed_origins"] = list(STRICT_CORS_ORIGINS[env_name]) if strict else ["*"]


def uses_strict_cors(cfg: dict[str, Any]) -> bool:
    return all(cfg["environments"][env_name]["cors_allowed_origins"] == STRICT_CORS_ORIGINS[env_name] for env_name in ENVIRONMENTS)


def compose_config(current: dict[str, Any], answers: dict[str, bool]) -> dict[str, Any]:
    cfg = deep_merge(default_config(), current)
    cfg["enable_snyk_scan"] = bool(answers["enable_snyk_scan"])
    cfg["enable_dast"] = bool(answers["enable_dast"])
    cfg["enable_http_validation"] = bool(answers["enable_http_validation"])
    cfg["use_prod_approval_environment"] = bool(answers["use_prod_approval_environment"])
    cfg["use_separate_aws_plan_role"] = bool(answers["use_separate_aws_plan_role"])
    apply_cors_policy(cfg, strict=bool(answers["use_strict_cors"]))
    return cfg


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def migrate_config(raw_cfg: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(raw_cfg)
    version = cfg.get("schema_version", 1)
    if not isinstance(version, int):
        return cfg
    while version < CONFIG_SCHEMA_VERSION:
        # Future migrations should update cfg in place and increment version.
        version += 1
        cfg["schema_version"] = version
    return cfg


def normalize_config(cfg: dict[str, Any]) -> dict[str, Any]:
    normalized = deep_merge(default_config(), migrate_config(cfg))
    normalized["schema_version"] = normalized.get("schema_version", CONFIG_SCHEMA_VERSION)
    return normalized


def load_config(root: Path) -> dict[str, Any]:
    path = config_path(root)
    if not path.exists():
        return default_config()
    if tomllib is None:
        raise RuntimeError("Python 3.11+ is required to read TOML config files.")
    with path.open("rb") as handle:
        loaded = tomllib.load(handle)
    return normalize_config(loaded)


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_config_toml(cfg: dict[str, Any]) -> str:
    normalized = normalize_config(cfg)
    lines = [
        "# DevSecOps Pipeline Kit local source configuration",
        "# Managed by: devsecops CLI. Do not commit this file.",
        "",
    ]
    for key in [
        "schema_version",
        "project_name",
        "aws_region",
        "lambda_image_uri",
        "enable_snyk_scan",
        "enable_http_validation",
        "enable_dast",
        "use_prod_approval_environment",
        "use_separate_aws_plan_role",
        "terraform_admin_role_name",
    ]:
        lines.append(f"{key} = {toml_value(normalized[key])}")

    lines.append("")
    lines.append("[backend]")
    for key, value in normalized["backend"].items():
        lines.append(f"{key} = {toml_value(value)}")

    for env_name, env_cfg in normalized["environments"].items():
        lines.append("")
        lines.append(f"[environments.{env_name}]")
        for key, value in env_cfg.items():
            lines.append(f"{key} = {toml_value(value)}")

    lines.append("")
    return "\n".join(lines)


def write_config(root: Path, cfg: dict[str, Any]) -> None:
    config_path(root).write_text(dump_config_toml(cfg), encoding="utf-8")


def clean_config(preset_name: str = "balanced") -> dict[str, Any]:
    if preset_name not in PRESETS:
        raise ValueError(f"Unknown preset: {preset_name}")
    return normalize_config(preset_config(preset_name))


def config_schema() -> dict[str, Any]:
    environment_fields = {
        "lambda_memory_size": {"type": "integer", "minimum": 128, "maximum": 10240},
        "lambda_timeout": {"type": "integer", "minimum": 1, "maximum": 900},
        "log_retention_days": {"type": "integer", "minimum": 1, "maximum": 3653},
        "api_throttling_burst_limit": {"type": "integer", "minimum": 1, "maximum": 5000},
        "api_throttling_rate_limit": {"type": "integer", "minimum": 1, "maximum": 10000},
        "cors_allowed_origins": {"type": "array", "items": "string"},
    }
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "config_file": CONFIG_FILE,
        "secrets_allowed": False,
        "presets": PRESET_ORDER,
        "fields": {
            "schema_version": {"type": "integer", "current": CONFIG_SCHEMA_VERSION},
            "project_name": {"type": "string", "pattern": PROJECT_NAME_RE.pattern},
            "aws_region": {"type": "string", "pattern": AWS_REGION_RE.pattern},
            "lambda_image_uri": {"type": "string", "secret": False},
            "enable_snyk_scan": {"type": "boolean"},
            "enable_http_validation": {"type": "boolean"},
            "enable_dast": {"type": "boolean"},
            "use_prod_approval_environment": {"type": "boolean"},
            "use_separate_aws_plan_role": {"type": "boolean"},
            "terraform_admin_role_name": {"type": "string", "secret": False},
            "backend": {
                "type": "object",
                "fields": {
                    "bucket": {"type": "string"},
                    "key": {"type": "string"},
                    "region": {"type": "string", "pattern": AWS_REGION_RE.pattern},
                    "lock_table": {"type": "string"},
                    "workspace_key_prefix": {"type": "string"},
                },
            },
            "environments": {
                "type": "object",
                "required": ENVIRONMENTS,
                "fields": {env_name: environment_fields for env_name in ENVIRONMENTS},
            },
        },
    }


def config_schema_markdown() -> str:
    rows = [
        ["schema_version", "integer", str(CONFIG_SCHEMA_VERSION)],
        ["project_name", "string", PROJECT_NAME_RE.pattern],
        ["aws_region", "string", AWS_REGION_RE.pattern],
        ["lambda_image_uri", "string", "Immutable image URI; not a secret."],
        ["enable_snyk_scan", "boolean", ""],
        ["enable_http_validation", "boolean", ""],
        ["enable_dast", "boolean", ""],
        ["use_prod_approval_environment", "boolean", ""],
        ["use_separate_aws_plan_role", "boolean", ""],
        ["terraform_admin_role_name", "string", "Optional role name; not a secret."],
        ["backend.*", "object", "S3 backend names and region."],
        ["environments.<env>.*", "object", "dev, staging, and prod settings."],
    ]
    return "\n\n".join(
        [
            "# DevSecOps Config Schema",
            f"Current schema version: `{CONFIG_SCHEMA_VERSION}`",
            "Secrets are not allowed in `.devsecops-pipeline.toml`.",
            markdown_table(["Field", "Type", "Notes"], rows),
            "",
        ]
    )


def canonical_config_text(cfg: dict[str, Any]) -> str:
    return dump_config_toml(cfg)


def unified_text_diff(before: str, after: str, fromfile: str, tofile: str) -> str:
    diff_lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    return "\n".join(diff_lines) + ("\n" if diff_lines else "")


def config_file_diff(root: Path) -> str:
    path = config_path(root)
    current_text = path.read_text(encoding="utf-8") if path.exists() else ""
    clean_text = canonical_config_text(load_config(root))
    return unified_text_diff(current_text, clean_text, str(path), f"{CONFIG_FILE} (canonical)")


def config_preset_diff(root: Path, preset_name: str) -> str:
    current_text = canonical_config_text(load_config(root))
    preset_text = canonical_config_text(clean_config(preset_name))
    return unified_text_diff(current_text, preset_text, CONFIG_FILE, f"preset:{preset_name}")


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


def rollback_boundary_lines() -> list[str]:
    return [
        "Local snapshot restore only: restores CLI-owned files from `.devsecops/snapshots/`.",
        "It does not change AWS Lambda, Terraform state, GitHub Actions, or deployed traffic.",
        f"For deployment rollback diagnostics, use `{RUNBOOK_FAILED_ROLLBACK}` and GitHub Actions logs.",
    ]


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


def hcl_attribute_line(key: str, value: Any, width: int) -> str:
    return f"{key.ljust(width)} = {hcl_value(value)}"


def cli_owned_comment(command: str) -> str:
    return "\n".join(
        [
            "# CLI-owned generated file. Do not edit directly.",
            f"# Update {CONFIG_FILE} and rerun `{command}`.",
            f"# See {GENERATED_ARTIFACT_DOC} for ownership rules.",
        ]
    )


def cli_owned_markdown_notice(command: str, artifact: str) -> str:
    return (
        f"CLI-owned generated {artifact}. Do not edit directly; "
        f"update `{CONFIG_FILE}` and rerun `{command}`."
    )


def terraform_tfvars(cfg: dict[str, Any]) -> str:
    top_level = [
        ("project_name", cfg["project_name"]),
        ("aws_region", cfg["aws_region"]),
        ("lambda_image_uri", cfg["lambda_image_uri"]),
        ("terraform_admin_role_name", cfg["terraform_admin_role_name"]),
    ]
    top_level_width = max(len(key) for key, _ in top_level)
    environment_width = max(len(key) for env_cfg in cfg["environments"].values() for key in env_cfg)
    lines = cli_owned_comment("devsecops render").splitlines() + [""]
    lines.extend(hcl_attribute_line(key, value, top_level_width) for key, value in top_level)
    lines.extend(["", "environment_config = {"])
    for env_name, env_cfg in cfg["environments"].items():
        lines.append(f"  {env_name} = {{")
        for key, value in env_cfg.items():
            lines.append(f"    {hcl_attribute_line(key, value, environment_width)}")
        lines.append("  }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def backend_tf(cfg: dict[str, Any]) -> str:
    backend = cfg["backend"]
    lines = cli_owned_comment("devsecops render").splitlines() + [
        "# Review and copy into terraform/backend.tf when ready.",
        "terraform {",
        '  backend "s3" {',
        f'    bucket               = {hcl_value(backend["bucket"])}',
        f'    key                  = {hcl_value(backend["key"])}',
        f'    region               = {hcl_value(backend["region"])}',
        "    encrypt              = true",
        f'    dynamodb_table       = {hcl_value(backend["lock_table"])}',
        f'    workspace_key_prefix = {hcl_value(backend["workspace_key_prefix"])}',
        "  }",
        "}",
        "",
    ]
    return "\n".join(lines)


def github_variables(cfg: dict[str, Any]) -> str:
    lines = cli_owned_comment("devsecops render").splitlines() + [
        "# Repository variables to configure in GitHub.",
        "# Example with gh:",
        f'#   gh variable set PROJECT_NAME --body "{cfg["project_name"]}"',
        "",
        f'PROJECT_NAME={cfg["project_name"]}',
        f'LAMBDA_IMAGE_URI={cfg["lambda_image_uri"]}',
        f'ENABLE_SNYK_SCAN={str(cfg["enable_snyk_scan"]).lower()}',
        f'ENABLE_HTTP_VALIDATION={str(cfg["enable_http_validation"]).lower()}',
        f'ENABLE_DAST={str(cfg["enable_dast"]).lower()}',
        f"PROD_APPROVAL_ENVIRONMENT={prod_approval_environment(cfg)}",
        "",
    ]
    return "\n".join(lines)


def checklist(cfg: dict[str, Any]) -> str:
    snyk_label = "`SNYK_TOKEN`" if cfg["enable_snyk_scan"] else "`SNYK_TOKEN` (optional)"
    return textwrap.dedent(
        f"""\
        # DevSecOps Pipeline Setup Checklist

        {cli_owned_markdown_notice("devsecops render", "checklist")}

        ## GitHub Secrets

        - [ ] `AWS_ROLE_TO_ASSUME_ARN`
        - [ ] `AWS_PLAN_ROLE_TO_ASSUME_ARN`
        - [ ] `AWS_REGION` = `{cfg["aws_region"]}`
        - [ ] {snyk_label}

        ## GitHub Variables

        - [ ] `PROJECT_NAME` = `{cfg["project_name"]}`
        - [ ] `LAMBDA_IMAGE_URI` = `{cfg["lambda_image_uri"] or "<immutable-image-uri>"}`
        - [ ] `ENABLE_SNYK_SCAN` = `{str(cfg["enable_snyk_scan"]).lower()}`
        - [ ] `ENABLE_HTTP_VALIDATION` = `{str(cfg["enable_http_validation"]).lower()}`
        - [ ] `ENABLE_DAST` = `{str(cfg["enable_dast"]).lower()}`
        - [ ] `PROD_APPROVAL_ENVIRONMENT` = `{prod_approval_environment(cfg)}`

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
    snyk_token_command = (
        'gh secret set SNYK_TOKEN --body "<snyk-token>"'
        if cfg["enable_snyk_scan"]
        else '# Optional: gh secret set SNYK_TOKEN --body "<snyk-token>"'
    )
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        *cli_owned_comment("devsecops render").splitlines(),
        "# Review placeholder values before running.",
        "",
        f'gh variable set PROJECT_NAME --body {shell_quote(cfg["project_name"])}',
        f'gh variable set LAMBDA_IMAGE_URI --body {shell_quote(cfg["lambda_image_uri"] or "<immutable-image-uri>")}',
        f'gh variable set ENABLE_SNYK_SCAN --body {shell_quote(str(cfg["enable_snyk_scan"]).lower())}',
        f'gh variable set ENABLE_HTTP_VALIDATION --body {shell_quote(str(cfg["enable_http_validation"]).lower())}',
        f'gh variable set ENABLE_DAST --body {shell_quote(str(cfg["enable_dast"]).lower())}',
        f"gh variable set PROD_APPROVAL_ENVIRONMENT --body {shell_quote(prod_approval_environment(cfg))}",
        "",
        f'gh secret set AWS_REGION --body {shell_quote(cfg["aws_region"])}',
        'gh secret set AWS_ROLE_TO_ASSUME_ARN --body "<deploy-role-arn>"',
        'gh secret set AWS_PLAN_ROLE_TO_ASSUME_ARN --body "<plan-role-arn>"',
        snyk_token_command,
        "",
    ]
    return "\n".join(lines)


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def github_expected_variables(cfg: dict[str, Any]) -> dict[str, str]:
    return {
        "PROJECT_NAME": str(cfg["project_name"]),
        "LAMBDA_IMAGE_URI": str(cfg["lambda_image_uri"]),
        "ENABLE_SNYK_SCAN": str(cfg["enable_snyk_scan"]).lower(),
        "ENABLE_HTTP_VALIDATION": str(cfg["enable_http_validation"]).lower(),
        "ENABLE_DAST": str(cfg["enable_dast"]).lower(),
        "PROD_APPROVAL_ENVIRONMENT": prod_approval_environment(cfg),
    }


def required_github_secrets(cfg: dict[str, Any]) -> list[str]:
    required = [*BASE_REQUIRED_GH_SECRETS, PLAN_ROLE_SECRET]
    if cfg["enable_snyk_scan"]:
        required.append(SNYK_TOKEN_SECRET)
    return required


def optional_github_secrets(cfg: dict[str, Any]) -> list[str]:
    optional: list[str] = []
    if not cfg["enable_snyk_scan"]:
        optional.append(SNYK_TOKEN_SECRET)
    return optional


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


def github_secret_checks(cfg: dict[str, Any], secrets: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    for name in required_github_secrets(cfg):
        checks.append(
            Check(
                f"GitHub secret {name}",
                "OK" if name in secrets else "WARN",
                "Configured." if name in secrets else "Missing.",
            )
        )
    for name in optional_github_secrets(cfg):
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


def runbook_for_failure(workflow_name: str, job_name: str, step_name: str, conclusion: str = "") -> str:
    text = " ".join([workflow_name, job_name, step_name, conclusion]).lower()
    if "rollback" in text:
        return RUNBOOK_FAILED_ROLLBACK
    if "require lambda image" in text or "lambda_image_uri" in text or "image uri" in text or "snyk" in text:
        return RUNBOOK_MISSING_IMAGE
    if "apply" in text or "deploy" in text:
        return RUNBOOK_FAILED_APPLY
    if "ecr image" in text or "image" in text:
        return RUNBOOK_MISSING_IMAGE
    if "plan" in text:
        return RUNBOOK_FAILED_PLAN
    if any(marker in text for marker in ["validate", "fmt", "trivy", "health", "smoke", "dast", "zap"]):
        return RUNBOOK_FAILED_VALIDATION
    return "docs/troubleshooting.md#actions-status-cannot-show-workflow-runs"


def failed_step_rows(workflow_name: str, stdout: str) -> list[list[str]]:
    payload = parse_json_object(stdout)
    jobs = payload.get("jobs", [])
    rows: list[list[str]] = []
    if not isinstance(jobs, list):
        return rows
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_name = str(job.get("name", ""))
        job_conclusion = str(job.get("conclusion") or "")
        if not job_conclusion or job_conclusion in {"success", "skipped"}:
            continue
        steps = job.get("steps", [])
        added_step = False
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_conclusion = str(step.get("conclusion") or "")
                if step_conclusion and step_conclusion not in {"success", "skipped"}:
                    step_name = str(step.get("name", ""))
                    runbook = runbook_for_failure(workflow_name, job_name, step_name, step_conclusion)
                    rows.append([workflow_name, job_name, step_name, step_conclusion, runbook])
                    added_step = True
        if not added_step:
            runbook = runbook_for_failure(workflow_name, job_name, "", job_conclusion)
            rows.append([workflow_name, job_name, "(job failed before step details)", job_conclusion, runbook])
    return rows


def actions_next_actions(run: dict[str, Any], failed_steps: list[list[str]]) -> list[str]:
    run_id = str(run.get("databaseId") or "")
    run_url = str(run.get("url") or "")
    log_command = f"gh run view {run_id} --log-failed" if run_id else "gh run view <run-id> --log-failed"
    actions = []
    for workflow, job, step, conclusion, runbook in failed_steps:
        location = f"{workflow} / {job}"
        if step and not step.startswith("("):
            location += f" / {step}"
        suffix = f" Open {run_url}" if run_url else ""
        actions.append(f"{location} ended with {conclusion}. Run `{log_command}` and follow `{runbook}`.{suffix}")
    return actions


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
    schema_version = cfg.get("schema_version")
    checks.append(
        Check(
            "Config schema version",
            "OK" if schema_version == CONFIG_SCHEMA_VERSION else "FAIL",
            str(schema_version)
            if schema_version == CONFIG_SCHEMA_VERSION
            else f"Expected schema_version = {CONFIG_SCHEMA_VERSION}.",
        )
    )
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
    for key in [
        "enable_snyk_scan",
        "enable_http_validation",
        "enable_dast",
        "use_prod_approval_environment",
        "use_separate_aws_plan_role",
    ]:
        checks.append(
            Check(
                f"Config {key}",
                "OK" if isinstance(cfg.get(key), bool) else "FAIL",
                str(cfg.get(key)) if isinstance(cfg.get(key), bool) else "Expected boolean.",
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


def aws_command(root: Path, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return run_command(["aws", *args], root, timeout=timeout)


def aws_json(root: Path, args: list[str], timeout: int = 30) -> tuple[Any, subprocess.CompletedProcess[str]]:
    result = aws_command(root, [*args, "--output", "json"], timeout=timeout)
    if result.returncode != 0:
        return {}, result
    try:
        return json.loads(result.stdout or "{}"), result
    except json.JSONDecodeError:
        return {}, result


def expected_name_prefix(cfg: dict[str, Any], env_name: str) -> str:
    return f"{cfg['project_name']}-{env_name}"


def expected_ecr_repository_name(cfg: dict[str, Any], env_name: str) -> str:
    return f"{expected_name_prefix(cfg, env_name)}-lambda-repo"


def expected_lambda_function_name(cfg: dict[str, Any], env_name: str) -> str:
    return f"{expected_name_prefix(cfg, env_name)}-lambda"


def expected_lambda_execution_role_name(cfg: dict[str, Any], env_name: str) -> str:
    return f"{expected_name_prefix(cfg, env_name)}-lambda-exec-role"


def expected_api_gateway_name(cfg: dict[str, Any], env_name: str) -> str:
    return f"{expected_name_prefix(cfg, env_name)}-http-api"


def expected_lambda_log_group_name(cfg: dict[str, Any], env_name: str) -> str:
    return f"/aws/lambda/{expected_lambda_function_name(cfg, env_name)}"


def parse_ecr_image_uri(image_uri: str) -> EcrImageRef | None:
    match = ECR_IMAGE_RE.match(image_uri)
    if not match:
        return None
    return EcrImageRef(
        registry=match.group("registry"),
        region=match.group("region"),
        repository=match.group("repository"),
        tag=match.group("tag"),
        digest=match.group("digest"),
    )


def is_resource_missing(result: subprocess.CompletedProcess[str]) -> bool:
    text = f"{result.stderr}\n{result.stdout}"
    missing_markers = [
        "NotFound",
        "NotFoundException",
        "ResourceNotFoundException",
        "RepositoryNotFoundException",
        "ImageNotFoundException",
        "NoSuchBucket",
        "ResourceNotFound",
    ]
    return any(marker in text for marker in missing_markers)


def missing_or_error_detail(result: subprocess.CompletedProcess[str], missing_detail: str) -> str:
    return missing_detail if is_resource_missing(result) else compact_error(result)


def collect_aws_checks(root: Path, cfg: dict[str, Any], env_name: str = "prod") -> list[Check]:
    checks: list[Check] = []
    region = str(cfg["aws_region"])
    backend = cfg["backend"]
    backend_region = str(backend["region"])
    backend_bucket = str(backend["bucket"])
    lock_table = str(backend["lock_table"])

    ecr_repository = expected_ecr_repository_name(cfg, env_name)
    lambda_function = expected_lambda_function_name(cfg, env_name)
    lambda_execution_role = expected_lambda_execution_role_name(cfg, env_name)
    api_name = expected_api_gateway_name(cfg, env_name)
    lambda_log_group = expected_lambda_log_group_name(cfg, env_name)

    if not command_exists("aws"):
        return [
            Check("AWS CLI", "WARN", "`aws` not found on PATH."),
            Check("AWS identity", "WARN", "Cannot inspect AWS without AWS CLI."),
            Check("State bucket", "WARN", "Cannot inspect backend bucket without AWS CLI."),
            Check("Lock table", "WARN", "Cannot inspect DynamoDB lock table without AWS CLI."),
            Check("ECR repository", "WARN", "Cannot inspect ECR repository without AWS CLI."),
            Check("Lambda execution role", "WARN", "Cannot inspect IAM role without AWS CLI."),
            Check("Lambda function", "WARN", "Cannot inspect Lambda function without AWS CLI."),
            Check("API Gateway", "WARN", "Cannot inspect API Gateway without AWS CLI."),
            Check("CloudWatch log group", "WARN", "Cannot inspect log group without AWS CLI."),
            Check("Configured ECR image", "WARN", "Cannot inspect configured image without AWS CLI."),
        ]

    checks.append(Check("AWS CLI", "OK", "Installed."))

    identity_payload, identity_result = aws_json(root, ["sts", "get-caller-identity"])
    if identity_result.returncode != 0:
        checks.append(Check("AWS identity", "WARN", compact_error(identity_result)))
        checks.extend(
            [
                Check("State bucket", "WARN", "Cannot inspect backend bucket without valid AWS credentials."),
                Check("Lock table", "WARN", "Cannot inspect DynamoDB lock table without valid AWS credentials."),
                Check("ECR repository", "WARN", "Cannot inspect ECR repository without valid AWS credentials."),
                Check("Lambda execution role", "WARN", "Cannot inspect IAM role without valid AWS credentials."),
                Check("Lambda function", "WARN", "Cannot inspect Lambda function without valid AWS credentials."),
                Check("API Gateway", "WARN", "Cannot inspect API Gateway without valid AWS credentials."),
                Check("CloudWatch log group", "WARN", "Cannot inspect log group without valid AWS credentials."),
                Check("Configured ECR image", "WARN", "Cannot inspect configured image without valid AWS credentials."),
            ]
        )
        return checks
    identity_detail = str(identity_payload.get("Arn") or identity_payload.get("Account") or "AWS credentials are usable.")
    checks.append(Check("AWS identity", "OK", identity_detail))

    if not backend_bucket or backend_bucket.startswith("replace-with"):
        checks.append(Check("State bucket", "WARN", "Set backend.bucket before checking remote state."))
    else:
        bucket_result = aws_command(root, ["s3api", "head-bucket", "--bucket", backend_bucket, "--region", backend_region])
        checks.append(
            Check(
                "State bucket",
                "OK" if bucket_result.returncode == 0 else "WARN",
                backend_bucket if bucket_result.returncode == 0 else missing_or_error_detail(bucket_result, "Bucket not found or not accessible."),
            )
        )

    if not lock_table:
        checks.append(Check("Lock table", "WARN", "Set backend.lock_table before checking state locking."))
    else:
        table_payload, table_result = aws_json(
            root,
            ["dynamodb", "describe-table", "--table-name", lock_table, "--region", backend_region],
        )
        table_status = ""
        if isinstance(table_payload, dict):
            table = table_payload.get("Table", {})
            if isinstance(table, dict):
                table_status = str(table.get("TableStatus", ""))
        checks.append(
            Check(
                "Lock table",
                "OK" if table_result.returncode == 0 else "WARN",
                f"{lock_table} ({table_status or 'found'})"
                if table_result.returncode == 0
                else missing_or_error_detail(table_result, "DynamoDB lock table not found or not accessible."),
            )
        )

    _, repo_result = aws_json(
        root,
        ["ecr", "describe-repositories", "--repository-names", ecr_repository, "--region", region],
    )
    checks.append(
        Check(
            "ECR repository",
            "OK" if repo_result.returncode == 0 else "WARN",
            ecr_repository if repo_result.returncode == 0 else missing_or_error_detail(repo_result, "ECR repository not deployed yet."),
        )
    )

    role_payload, role_result = aws_json(root, ["iam", "get-role", "--role-name", lambda_execution_role])
    role_arn = ""
    if isinstance(role_payload, dict):
        role = role_payload.get("Role", {})
        if isinstance(role, dict):
            role_arn = str(role.get("Arn") or "")
    checks.append(
        Check(
            "Lambda execution role",
            "OK" if role_result.returncode == 0 else "WARN",
            role_arn
            if role_result.returncode == 0 and role_arn
            else lambda_execution_role
            if role_result.returncode == 0
            else missing_or_error_detail(role_result, "Lambda execution role not deployed yet."),
        )
    )

    lambda_payload, lambda_result = aws_json(
        root,
        ["lambda", "get-function-configuration", "--function-name", lambda_function, "--region", region],
    )
    lambda_state = ""
    if isinstance(lambda_payload, dict):
        lambda_state = str(lambda_payload.get("State") or lambda_payload.get("LastUpdateStatus") or "")
    checks.append(
        Check(
            "Lambda function",
            "OK" if lambda_result.returncode == 0 else "WARN",
            f"{lambda_function} ({lambda_state or 'found'})"
            if lambda_result.returncode == 0
            else missing_or_error_detail(lambda_result, "Lambda function not deployed yet."),
        )
    )

    apis_payload, apis_result = aws_json(root, ["apigatewayv2", "get-apis", "--region", region])
    api_detail = "API Gateway not deployed yet."
    api_status = "WARN"
    if apis_result.returncode == 0 and isinstance(apis_payload, dict):
        items = apis_payload.get("Items", [])
        if isinstance(items, list):
            match = next((item for item in items if isinstance(item, dict) and item.get("Name") == api_name), None)
            if match:
                api_status = "OK"
                api_detail = str(match.get("ApiEndpoint") or api_name)
    elif apis_result.returncode != 0:
        api_detail = compact_error(apis_result)
    checks.append(Check("API Gateway", api_status, api_detail))

    logs_payload, logs_result = aws_json(
        root,
        ["logs", "describe-log-groups", "--log-group-name-prefix", lambda_log_group, "--region", region],
    )
    log_detail = "Lambda log group not deployed yet."
    log_status = "WARN"
    if logs_result.returncode == 0 and isinstance(logs_payload, dict):
        groups = logs_payload.get("logGroups", [])
        if isinstance(groups, list):
            match = next((group for group in groups if isinstance(group, dict) and group.get("logGroupName") == lambda_log_group), None)
            if match:
                retention = match.get("retentionInDays")
                log_status = "OK"
                log_detail = f"{lambda_log_group} ({retention} day retention)" if retention else lambda_log_group
    elif logs_result.returncode != 0:
        log_detail = compact_error(logs_result)
    checks.append(Check("CloudWatch log group", log_status, log_detail))

    image_uri = str(cfg["lambda_image_uri"])
    if not image_uri:
        checks.append(Check("Configured ECR image", "WARN", "Set lambda_image_uri before checking image existence."))
    else:
        image_ref = parse_ecr_image_uri(image_uri)
        if image_ref is None:
            checks.append(Check("Configured ECR image", "INFO", "Image URI is not an AWS ECR URI; existence check skipped.", scored=False))
        else:
            image_id = f"imageTag={image_ref.tag}" if image_ref.tag else f"imageDigest={image_ref.digest}"
            image_payload, image_result = aws_json(
                root,
                [
                    "ecr",
                    "describe-images",
                    "--repository-name",
                    image_ref.repository,
                    "--image-ids",
                    image_id,
                    "--region",
                    image_ref.region,
                ],
            )
            image_details = image_payload.get("imageDetails", []) if isinstance(image_payload, dict) else []
            image_found = image_result.returncode == 0 and bool(image_details)
            if image_found:
                image_detail = image_uri
            elif image_result.returncode == 0:
                image_detail = "Configured ECR image not found."
            else:
                image_detail = missing_or_error_detail(image_result, "Configured ECR image not found.")
            checks.append(
                Check(
                    "Configured ECR image",
                    "OK" if image_found else "WARN",
                    image_detail,
                )
            )

    return checks


def resolve_health_url(root: Path, url: str | None = None) -> tuple[str, str]:
    if url:
        return url.strip(), "argument"
    if not command_exists("terraform"):
        return "", "`terraform` not found on PATH and --url was not provided."
    result = run_command(["terraform", "-chdir=terraform", "output", "-no-color", "-raw", "api_gateway_health_url"], root)
    if result.returncode != 0:
        return "", compact_error(result)
    return result.stdout.strip(), "terraform output api_gateway_health_url"


def fetch_health_url(url: str, timeout: int = 20) -> tuple[int | None, str]:
    request = urllib.request.Request(url, headers={"User-Agent": f"devsecops-cli/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            preview = response.read(200).decode("utf-8", errors="replace").strip()
            return status, preview or f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        return int(exc.code), str(exc)
    except urllib.error.URLError as exc:
        return None, str(exc.reason)
    except TimeoutError:
        return None, f"Timed out after {timeout}s."


def collect_health_checks(root: Path, cfg: dict[str, Any], url: str | None = None, timeout: int = 20) -> list[Check]:
    health_url, source = resolve_health_url(root, url)
    checks = [
        Check(
            "Health endpoint URL",
            "OK" if health_url else "FAIL",
            f"{health_url} ({source})" if health_url else source,
        )
    ]
    if not health_url:
        return checks
    status, detail = fetch_health_url(health_url, timeout=timeout)
    ok_status = status is not None and 200 <= status < 400
    checks.append(
        Check(
            "Health response",
            "OK" if ok_status else "FAIL",
            f"HTTP {status}: {detail}" if status is not None else detail,
        )
    )
    return checks


def inspect_aws_outputs(root: Path, cfg: dict[str, Any], env_name: str = "prod") -> tuple[dict[str, str], list[Check]]:
    region = str(cfg["aws_region"])
    lambda_function = expected_lambda_function_name(cfg, env_name)
    api_name = expected_api_gateway_name(cfg, env_name)
    lambda_log_group = expected_lambda_log_group_name(cfg, env_name)
    outputs = {
        "environment": env_name,
        "aws_region": region,
        "lambda_function_name": lambda_function,
        "lambda_state": "",
        "lambda_last_update_status": "",
        "lambda_image_uri": "",
        "api_gateway_name": api_name,
        "api_gateway_invoke_url": "",
        "api_gateway_health_url": "",
        "cloudwatch_log_group": lambda_log_group,
        "cloudwatch_retention_days": "",
    }
    checks: list[Check] = []

    if not command_exists("aws"):
        checks.append(Check("AWS CLI", "WARN", "`aws` not found on PATH."))
        checks.append(Check("AWS outputs", "WARN", "Cannot inspect deployed outputs without AWS CLI."))
        return outputs, checks

    checks.append(Check("AWS CLI", "OK", "Installed."))
    identity_payload, identity_result = aws_json(root, ["sts", "get-caller-identity"])
    if identity_result.returncode != 0:
        checks.append(Check("AWS identity", "WARN", compact_error(identity_result)))
        checks.append(Check("AWS outputs", "WARN", "Cannot inspect deployed outputs without valid AWS credentials."))
        return outputs, checks
    checks.append(Check("AWS identity", "OK", str(identity_payload.get("Arn") or identity_payload.get("Account") or "AWS credentials are usable.")))

    lambda_payload, lambda_result = aws_json(root, ["lambda", "get-function", "--function-name", lambda_function, "--region", region])
    if lambda_result.returncode == 0 and isinstance(lambda_payload, dict):
        configuration = lambda_payload.get("Configuration", {})
        if not isinstance(configuration, dict):
            configuration = lambda_payload
        outputs["lambda_state"] = str(configuration.get("State") or "")
        outputs["lambda_last_update_status"] = str(configuration.get("LastUpdateStatus") or "")
        code = lambda_payload.get("Code", {})
        if isinstance(code, dict):
            outputs["lambda_image_uri"] = str(code.get("ImageUri") or "")
        outputs["lambda_image_uri"] = outputs["lambda_image_uri"] or str(configuration.get("ImageUri") or "")
        checks.append(Check("Lambda function", "OK", f"{lambda_function} ({outputs['lambda_state'] or 'found'})"))
    else:
        checks.append(Check("Lambda function", "WARN", missing_or_error_detail(lambda_result, "Lambda function not deployed yet.")))

    apis_payload, apis_result = aws_json(root, ["apigatewayv2", "get-apis", "--region", region])
    if apis_result.returncode == 0 and isinstance(apis_payload, dict):
        items = apis_payload.get("Items", [])
        match = None
        if isinstance(items, list):
            match = next((item for item in items if isinstance(item, dict) and item.get("Name") == api_name), None)
        if match:
            endpoint = str(match.get("ApiEndpoint") or "")
            outputs["api_gateway_invoke_url"] = endpoint
            outputs["api_gateway_health_url"] = endpoint.rstrip("/") + "/health" if endpoint else ""
            checks.append(Check("API Gateway", "OK", endpoint or api_name))
        else:
            checks.append(Check("API Gateway", "WARN", "API Gateway not deployed yet."))
    else:
        checks.append(Check("API Gateway", "WARN", compact_error(apis_result)))

    logs_payload, logs_result = aws_json(
        root,
        ["logs", "describe-log-groups", "--log-group-name-prefix", lambda_log_group, "--region", region],
    )
    if logs_result.returncode == 0 and isinstance(logs_payload, dict):
        groups = logs_payload.get("logGroups", [])
        match = None
        if isinstance(groups, list):
            match = next((group for group in groups if isinstance(group, dict) and group.get("logGroupName") == lambda_log_group), None)
        if match:
            retention = match.get("retentionInDays")
            outputs["cloudwatch_retention_days"] = str(retention or "")
            checks.append(Check("CloudWatch log group", "OK", lambda_log_group))
        else:
            checks.append(Check("CloudWatch log group", "WARN", "Lambda log group not deployed yet."))
    else:
        checks.append(Check("CloudWatch log group", "WARN", compact_error(logs_result)))

    return outputs, checks


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
        checks.extend(github_secret_checks(cfg, parse_gh_items(secrets_result.stdout)))

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


def collect_github_actions_status(root: Path, limit: int = 8, failed_jobs_limit: int = 3) -> ActionsStatus:
    if not command_exists("gh"):
        return ActionsStatus([], [], [], [], "`gh` not found on PATH.")
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
        return ActionsStatus([], [], [], [], compact_error(result))
    runs = parse_gh_runs(result.stdout)
    if not runs and result.stdout.strip():
        return ActionsStatus([], [], [], [], "Could not parse `gh run list` output.")

    failed_rows: list[list[str]] = []
    failed_step_rows_result: list[list[str]] = []
    next_actions_result: list[str] = []
    failed_runs = [run for run in runs if str(run.get("conclusion") or "") == "failure"]
    for run in failed_runs[:failed_jobs_limit]:
        run_id = run.get("databaseId")
        if not run_id:
            continue
        job_result = gh_command(root, ["run", "view", str(run_id), "--json", "jobs"])
        if job_result.returncode != 0:
            failed_rows.append([str(run.get("workflowName", "")), "(jobs)", "unknown", compact_error(job_result)])
            run_id_text = str(run_id)
            run_url = str(run.get("url") or "")
            next_actions_result.append(
                f"Could not inspect failed jobs for run {run_id_text}. Run `gh run view {run_id_text} --log-failed` "
                f"and see `docs/troubleshooting.md#actions-status-cannot-show-workflow-runs`."
                + (f" Open {run_url}" if run_url else "")
            )
            failed_step_rows_result.append(
                [
                    str(run.get("workflowName", "")),
                    "(jobs)",
                    "(could not inspect failed steps)",
                    "unknown",
                    "docs/troubleshooting.md#actions-status-cannot-show-workflow-runs",
                ]
            )
            continue
        workflow_name = str(run.get("workflowName", ""))
        failed_rows.extend(failed_job_rows(workflow_name, job_result.stdout))
        run_failed_steps = failed_step_rows(workflow_name, job_result.stdout)
        failed_step_rows_result.extend(run_failed_steps)
        next_actions_result.extend(actions_next_actions(run, run_failed_steps))
    return ActionsStatus(actions_run_rows(runs), failed_rows, failed_step_rows_result, next_actions_result)


def github_actions_status(root: Path, limit: int = 8, failed_jobs_limit: int = 3) -> tuple[list[list[str]], list[list[str]], str | None]:
    status = collect_github_actions_status(root, limit=limit, failed_jobs_limit=failed_jobs_limit)
    return status.runs, status.failed_jobs, status.error


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
        print(warn("Skipping AWS_PLAN_ROLE_TO_ASSUME_ARN; pass --plan-role-arn because Terraform plan no longer falls back to the deploy role."))
    if args.snyk_token:
        secrets["SNYK_TOKEN"] = args.snyk_token
    elif cfg["enable_snyk_scan"]:
        print(warn("Skipping SNYK_TOKEN; pass --snyk-token because enable_snyk_scan is true."))

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


def image_uri_from_config_or_override(cfg: dict[str, Any], image_uri: str | None = None) -> str:
    return str(image_uri if image_uri is not None else cfg["lambda_image_uri"]).strip()


def collect_image_preflight_checks(
    cfg: dict[str, Any],
    image_uri: str | None = None,
    env_name: str = "prod",
) -> list[Check]:
    checks: list[Check] = []
    resolved_uri = image_uri_from_config_or_override(cfg, image_uri)
    image_ref = parse_ecr_image_uri(resolved_uri) if resolved_uri else None
    expected_shape = "123456789012.dkr.ecr.<region>.amazonaws.com/<repository>:<immutable-tag> or @sha256:<digest>"

    checks.append(
        Check(
            "Lambda image URI",
            "OK" if resolved_uri else "FAIL",
            resolved_uri if resolved_uri else "Set lambda_image_uri or pass --image-uri.",
        )
    )
    checks.append(
        Check(
            "Lambda image shape",
            "OK" if image_ref else ("FAIL" if resolved_uri else "WARN"),
            f"ECR image URI for repository `{image_ref.repository}`."
            if image_ref
            else f"Expected {expected_shape}."
            if resolved_uri
            else "Cannot inspect shape until an image URI is set.",
        )
    )
    checks.append(
        Check(
            "Lambda image immutability",
            "OK" if is_immutable_image(resolved_uri) else ("FAIL" if resolved_uri else "WARN"),
            "Uses an immutable tag or digest."
            if is_immutable_image(resolved_uri)
            else "Use an immutable tag or digest; do not use latest or bootstrap."
            if resolved_uri
            else "Cannot inspect immutability until an image URI is set.",
        )
    )

    if image_ref:
        expected_region = str(cfg["aws_region"])
        checks.append(
            Check(
                "Lambda image region",
                "OK" if image_ref.region == expected_region else "FAIL",
                image_ref.region
                if image_ref.region == expected_region
                else f"Image region `{image_ref.region}` does not match aws_region `{expected_region}`.",
            )
        )
        expected_repository = expected_ecr_repository_name(cfg, env_name)
        checks.append(
            Check(
                "Lambda image repository",
                "OK" if image_ref.repository == expected_repository else "WARN",
                image_ref.repository
                if image_ref.repository == expected_repository
                else (
                    f"Configured image uses `{image_ref.repository}`; Terraform also creates `{expected_repository}`. "
                    "This is allowed for bring-your-own images if the deploy role can pull it."
                ),
                scored=False,
            )
        )
    else:
        checks.append(Check("Lambda image region", "WARN", "Cannot compare image region until the URI matches ECR shape."))
        checks.append(Check("Lambda image repository", "WARN", "Cannot compare repository until the URI matches ECR shape.", scored=False))

    return checks


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
    if image_uri:
        for preflight_check in collect_image_preflight_checks(cfg):
            if preflight_check.name in {"Lambda image shape", "Lambda image region"}:
                checks.append(preflight_check)

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
            "Snyk container scan",
            "OK" if cfg["enable_snyk_scan"] else "INFO",
            "Enabled; requires SNYK_TOKEN." if cfg["enable_snyk_scan"] else "Disabled by config.",
            scored=False,
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
            "Prod approval environment",
            "OK" if cfg["use_prod_approval_environment"] else "INFO",
            f"GitHub environment: {prod_approval_environment(cfg)}",
            scored=False,
        )
    )
    checks.append(
        Check(
            "Separate AWS plan role",
            "OK" if cfg["use_separate_aws_plan_role"] else "WARN",
            "AWS_PLAN_ROLE_TO_ASSUME_ARN is required; deploy role fallback is disabled."
            if cfg["use_separate_aws_plan_role"]
            else "Enable this control; workflows still require AWS_PLAN_ROLE_TO_ASSUME_ARN and do not fall back to the deploy role.",
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

    if deep:
        checks.extend(collect_aws_checks(root, cfg, env_name="prod"))

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


def readiness_action_detail_for_check(check: Check) -> str:
    if check.name == "Local config":
        return "Run `devsecops config new --preset balanced` or open `devsecops menu`."
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
    if check.name == "AWS CLI":
        return "Install AWS CLI and make sure `aws` is available on PATH."
    if check.name == "State bucket":
        return "Set `backend.bucket`, run `devsecops render`, then `devsecops bootstrap --apply`."
    if check.name == "Lock table":
        return "Set `backend.lock_table`, run `devsecops render`, then `devsecops bootstrap --apply`."
    if check.name == "ECR repository":
        return "Run a Terraform plan/apply path that creates the ECR repository before publishing images."
    if check.name == "Lambda execution role":
        return "Run a Terraform apply path that creates the Lambda execution role, then re-run `devsecops aws-doctor`."
    if check.name == "Lambda function":
        return "Run a manual production deploy after setting an immutable `lambda_image_uri`."
    if check.name == "API Gateway":
        return "Run a successful workload deploy, then inspect Terraform output `api_gateway_invoke_url`."
    if check.name == "CloudWatch log group":
        return "Deploy the Lambda function; Terraform creates the log group before Lambda creation."
    if check.name == "Configured ECR image":
        return "Publish the configured ECR image or update `lambda_image_uri` to an existing immutable image."
    if check.name == "Lambda image URI":
        return "Set `LAMBDA_IMAGE_URI` to an immutable image with `devsecops set lambda_image_uri <image-uri> --render`."
    if check.name == "Lambda image shape":
        return "Use an ECR Lambda image URI such as `123456789012.dkr.ecr.us-east-1.amazonaws.com/app:sha-abc123`."
    if check.name == "Lambda image immutability":
        return "Use an immutable tag or digest, then run `devsecops preflight --image-uri <image-uri>`."
    if check.name == "Lambda image region":
        return "Publish or select an image in the same region as `aws_region`, then rerun `devsecops preflight`."
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
    if check.name == "AWS outputs":
        return "Install/configure AWS CLI, then run `devsecops aws outputs --environment prod` again."
    if check.name == "Health endpoint URL":
        return "Pass `--url <health-url>` or deploy once so Terraform output `api_gateway_health_url` exists."
    if check.name == "Health response":
        return "Inspect Lambda logs and workload `/health` behavior, then rerun `devsecops health`."
    return check.detail


def troubleshooting_anchor_for_check(check: Check) -> str:
    name = check.name
    if name == "Local config":
        return "#local-config-is-missing"
    if name in {"Project name", "Config schema"} or name.startswith("Config ") or "." in name:
        return "#config-validation-fails"
    if name == "Project files":
        return "#project-files-are-missing"
    if name == "`terraform` CLI":
        return "#terraform-cli-is-not-found"
    if name in {"`aws` CLI", "AWS CLI", "AWS identity"}:
        return "#aws-doctor-cannot-inspect-resources"
    if name == "AWS outputs":
        return "#check-aws-account-and-deployed-resources"
    if name.startswith("Health "):
        return "#health-check-returns-500"
    if name in {"Backend bucket", "State bucket", "Lock table", "Backend lock table"}:
        return "#readiness-says-backend-bucket-is-missing"
    if name in {
        "Lambda image URI",
        "Lambda image shape",
        "Lambda image immutability",
        "Lambda image region",
        "Configured ECR image",
    }:
        return "#lambda-image-uri-is-missing-or-invalid"
    if name == "`git` CLI" or name == "Git branch":
        return "#git-or-branch-readiness-fails"
    if name.startswith("GitHub") or name.endswith("secret") or name.endswith("variable"):
        return "#github-repository-variables-or-secrets-are-missing"
    if name.startswith("Branch `") or name.startswith("Required check") or name == "Protection details":
        return "#branch-protection-doctor-reports-missing-checks"
    if name in {"Terraform validate", "Bootstrap validate"}:
        return "#terraform-validation-fails"
    return "#start-here"


def readiness_action_for_check(check: Check) -> str:
    action = readiness_action_detail_for_check(check)
    anchor = troubleshooting_anchor_for_check(check)
    return f"{action} See `docs/troubleshooting.md{anchor}`."


def readiness_gap_rows(checks: list[Check]) -> list[list[str]]:
    return [
        [check.name, check.status, check.detail, readiness_action_for_check(check)]
        for check in checks
        if check.scored and check.status != "OK"
    ]


def readiness_category_for_check(check: Check) -> str:
    name = check.name
    if name.startswith("GitHub") or name.startswith("Branch `") or name.startswith("Required check"):
        return "GitHub"
    if name == "Protection details":
        return "GitHub"
    if name in {
        "`terraform` CLI",
        "Terraform validate",
        "Bootstrap validate",
        "Backend bucket",
        "Backend lock table",
        "Rendered tfvars",
        "State bucket",
        "Lock table",
    }:
        return "Terraform"
    if name in {
        "`aws` CLI",
        "AWS CLI",
        "AWS identity",
        "ECR repository",
        "Lambda execution role",
        "API Gateway",
        "CloudWatch log group",
    }:
        return "AWS"
    if name in {
        "Snyk container scan",
        "HTTP validation",
        "DAST",
        "Separate AWS plan role",
    }:
        return "Security"
    if name in {
        "Lambda image URI",
        "Configured ECR image",
        "Lambda function",
        "Prod approval environment",
    }:
        return "Deployment"
    return "Local"


def grouped_readiness_checks(checks: list[Check]) -> dict[str, list[Check]]:
    grouped = {category: [] for category in READINESS_CATEGORIES}
    for check in checks:
        grouped[readiness_category_for_check(check)].append(check)
    return grouped


def readiness_score_for_category(checks: list[Check]) -> int | None:
    if not checks:
        return None
    points = 0
    for check in checks:
        if check.status == "OK":
            points += 2
        elif check.status in {"WARN", "INFO"}:
            points += 1
    return round(points / (len(checks) * 2) * 100)


def readiness_breakdown_rows(checks: list[Check], compact: bool = False) -> list[list[str]]:
    grouped = grouped_readiness_checks(checks)
    rows: list[list[str]] = []
    for category in READINESS_CATEGORIES:
        category_checks = grouped[category]
        score = readiness_score_for_category(category_checks)
        score_text = "n/a" if score is None else f"{score}%"
        if compact:
            gaps = sum(1 for check in category_checks if check.status != "OK")
            rows.append([category, score_text, str(gaps)])
        else:
            status_counts = {
                "OK": sum(1 for check in category_checks if check.status == "OK"),
                "WARN": sum(1 for check in category_checks if check.status == "WARN"),
                "FAIL": sum(1 for check in category_checks if check.status == "FAIL"),
                "INFO": sum(1 for check in category_checks if check.status == "INFO"),
            }
            rows.append(
                [
                    category,
                    score_text,
                    str(status_counts["OK"]),
                    str(status_counts["WARN"]),
                    str(status_counts["FAIL"]),
                    str(status_counts["INFO"]),
                ]
            )
    return rows


def overall_breakdown_score(checks: list[Check]) -> int:
    scores = [score for score in (readiness_score_for_category(group) for group in grouped_readiness_checks(checks).values()) if score is not None]
    return round(sum(scores) / len(scores)) if scores else 100


def score_status(score: int | None) -> str:
    if score is None:
        return info("n/a")
    if score >= 90:
        return ok(f"{score}%")
    if score >= 60:
        return warn(f"{score}%")
    return fail(f"{score}%")


def print_readiness_breakdown(checks: list[Check], compact: bool = False) -> None:
    grouped = grouped_readiness_checks(checks)
    rows: list[list[str]] = []
    for raw_row in readiness_breakdown_rows(checks, compact=compact):
        score = readiness_score_for_category(grouped[raw_row[0]])
        row = [raw_row[0], score_status(score), *raw_row[2:]]
        rows.append(row)
    headers = ["Area", "Score", "Gaps"] if compact else ["Area", "Score", "OK", "WARN", "FAIL", "INFO"]
    draw_table(headers, rows, title="Readiness")
    print()
    print(f"Overall: {score_status(overall_breakdown_score(checks))}  legacy score: {progress_bar(readiness_score(checks), width=18)}")


def print_gap_summary(checks: list[Check], limit: int = 3) -> None:
    rows = readiness_gap_rows(checks)[:limit]
    if not rows:
        print(ok("Readiness gaps: none."))
        return
    print(info("Readiness gaps ([i] details):"))
    for check_name, status, detail, action in rows:
        label = fail(status) if status == "FAIL" else warn(status)
        print(f"  {label} {check_name}: {detail}")
        print(f"    Fix: {action}")


def collect_dashboard_checks(root: Path, cfg: dict[str, Any], mode: str = "full") -> list[Check]:
    checks = collect_checks(root, cfg, deep=mode == "full")
    if mode == "full":
        checks.extend(collect_github_checks(root, cfg))
        checks.extend(collect_branch_checks(root))
    return checks


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
        if check.scored and check.status != "OK":
            print(f"{''.ljust(name_width)} {'Fix'.ljust(12)} {readiness_action_for_check(check)}")


def check_to_dict(check: Check) -> dict[str, Any]:
    return {
        "name": check.name,
        "status": check.status,
        "detail": check.detail,
        "scored": check.scored,
    }


def readiness_breakdown_dicts(checks: list[Check]) -> list[dict[str, Any]]:
    rows = readiness_breakdown_rows(checks, compact=False)
    return [
        {
            "area": row[0],
            "score": int(row[1].rstrip("%")) if row[1].endswith("%") else None,
            "ok": int(row[2]),
            "warn": int(row[3]),
            "fail": int(row[4]),
            "info": int(row[5]),
        }
        for row in rows
    ]


def readiness_gap_dicts(checks: list[Check]) -> list[dict[str, str]]:
    return [
        {"name": name, "status": status, "detail": detail, "action": action}
        for name, status, detail, action in readiness_gap_rows(checks)
    ]


def checks_payload(kind: str, checks: list[Check], context: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind,
        "score": readiness_score(checks),
        "overall_breakdown_score": overall_breakdown_score(checks),
        "breakdown": readiness_breakdown_dicts(checks),
        "gaps": readiness_gap_dicts(checks),
        "checks": [check_to_dict(check) for check in checks],
    }
    if context:
        payload["context"] = context
    return payload


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def print_compact_checks(checks: list[Check], title: str | None = None) -> None:
    if title:
        print(info(title))
    print(f"Score: {readiness_score(checks)}%")
    gaps = [check for check in checks if check.scored and check.status in {"WARN", "FAIL"}]
    if not gaps:
        print(ok("No scored gaps."))
        return
    for check in gaps:
        label = fail(check.status) if check.status == "FAIL" else warn(check.status)
        print(f"{label} {check.name}: {check.detail}")
        print(f"  Fix: {readiness_action_for_check(check)}")


def strict_exit_code(checks: list[Check], strict: bool = False, fail_on_warn: bool = False) -> int:
    if not strict:
        return EXIT_OK
    scored_gaps = [
        check
        for check in checks
        if check.scored and (check.status != "OK" if fail_on_warn else check.status == "FAIL")
    ]
    if not scored_gaps:
        return EXIT_OK
    gap_text = "\n".join(f"{check.name} {check.detail}".lower() for check in scored_gaps)
    if "not found on path" in gap_text:
        return EXIT_MISSING_EXTERNAL_TOOL
    if "auth" in gap_text or "authenticated" in gap_text or "login" in gap_text:
        return EXIT_AUTH_FAILED
    return EXIT_VALIDATION_FAILED


def emit_check_output(
    title: str,
    checks: list[Check],
    output_format: str = "human",
    context: dict[str, Any] | None = None,
) -> None:
    if output_format == "json":
        emit_json(checks_payload(title.lower().replace(" ", "-"), checks, context=context))
    elif output_format == "compact":
        print_compact_checks(checks, title=title)
    else:
        if context:
            lines = [f"{key}: {value}" for key, value in context.items()]
            draw_box(title, lines)
        else:
            draw_box(title, [])
        print_checks(checks)


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
    breakdown_rows = readiness_breakdown_rows(checks, compact=True)
    control_report_rows = control_rows(cfg)
    env_report_rows = env_rows(cfg)
    generated_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return "\n\n".join(
        [
            "# DevSecOps Pipeline Readiness Report",
            cli_owned_markdown_notice("devsecops report", "report"),
            f"Generated: {generated_at}",
            f"Project: `{cfg['project_name']}`",
            f"Region: `{cfg['aws_region']}`",
            f"Readiness: `{score}%`",
            "## Score Breakdown\n\n" + markdown_table(["Area", "Score", "Gaps"], breakdown_rows),
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
    for check_name, status, _detail, action in readiness_gap_rows(checks):
        actions.append(f"- {check_name} ({status}): {action}")
    if actions:
        return actions
    if not cfg["lambda_image_uri"]:
        actions.append("- Set `LAMBDA_IMAGE_URI` to an immutable Lambda container image.")
    if cfg["backend"]["bucket"].startswith("replace-with"):
        actions.append("- Set a real Terraform backend S3 bucket and run `devsecops render`.")
    if any(check.name == "`aws` CLI" and check.status != "OK" for check in checks):
        actions.append("- Install or configure AWS CLI before running cloud bootstrap checks.")
    if cfg["enable_snyk_scan"]:
        actions.append("- Configure `SNYK_TOKEN` so the enabled container scan can run.")
    if cfg["use_separate_aws_plan_role"]:
        actions.append("- Configure `AWS_PLAN_ROLE_TO_ASSUME_ARN`; Terraform plan workflows do not fall back to the deploy role.")
    else:
        actions.append("- Enable separate AWS plan role and configure `AWS_PLAN_ROLE_TO_ASSUME_ARN`; deploy-role fallback is disabled.")
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


def preset_rows() -> list[list[str]]:
    rows: list[list[str]] = []
    for name in PRESET_ORDER:
        cfg = preset_config(name)
        rows.append(
            [
                name,
                "on" if cfg["enable_snyk_scan"] else "off",
                "on" if cfg["enable_http_validation"] else "off",
                "on" if cfg["enable_dast"] else "off",
                PRESET_DESCRIPTIONS[name],
            ]
        )
    return rows


def preset_detail_rows(cfg: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = [
        ["Snyk container scan", "on" if cfg["enable_snyk_scan"] else "off"],
        ["HTTP validation", "on" if cfg["enable_http_validation"] else "off"],
        ["DAST", "on" if cfg["enable_dast"] else "off"],
        ["Prod approval environment", prod_approval_environment(cfg)],
        ["Separate AWS plan role", "on" if cfg["use_separate_aws_plan_role"] else "off"],
    ]
    for env_name, env_cfg in cfg["environments"].items():
        rows.extend(
            [
                [f"{env_name}.lambda_memory_size", str(env_cfg["lambda_memory_size"])],
                [f"{env_name}.lambda_timeout", str(env_cfg["lambda_timeout"])],
                [f"{env_name}.log_retention_days", str(env_cfg["log_retention_days"])],
                [f"{env_name}.api_throttling_burst_limit", str(env_cfg["api_throttling_burst_limit"])],
                [f"{env_name}.api_throttling_rate_limit", str(env_cfg["api_throttling_rate_limit"])],
                [f"{env_name}.cors_allowed_origins", ",".join(env_cfg["cors_allowed_origins"])],
            ]
        )
    return rows


def print_preset_list() -> None:
    draw_table(["Preset", "Snyk", "HTTP", "DAST", "Description"], preset_rows(), title="Policy Presets")


def print_preset_detail(name: str) -> int:
    if name not in PRESETS:
        print(fail("Unknown preset: ") + name)
        print("Available presets: " + ", ".join(PRESET_ORDER))
        return 1
    cfg = preset_config(name)
    draw_box(f"Preset: {name}", [PRESET_DESCRIPTIONS[name]])
    print()
    draw_table(["Setting", "Value"], preset_detail_rows(cfg))
    return 0


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
    snyk_status = "ON" if cfg["enable_snyk_scan"] else "OFF"
    health_status = "ON" if cfg["enable_http_validation"] else "OFF"
    dast_status = "ON" if cfg["enable_dast"] else "OFF"
    approval_status = "ON" if cfg["use_prod_approval_environment"] else "OFF"
    plan_role_status = "ON" if cfg["use_separate_aws_plan_role"] else "OFF"
    return [
        ["GitHub OIDC", "ON", "Deploy and plan roles use short-lived AWS credentials."],
        ["Prod approval", approval_status, f"Deploy job uses `{prod_approval_environment(cfg)}` GitHub environment."],
        ["Separate plan role", plan_role_status, "Terraform plan workflows require `AWS_PLAN_ROLE_TO_ASSUME_ARN`; deploy-role fallback is disabled."],
        ["Terraform state lock", "ON", f"DynamoDB table: {cfg['backend']['lock_table']}"],
        ["IaC scan", "ON", "Trivy config scan in GitHub Actions."],
        ["Immutable image", image_status, "LAMBDA_IMAGE_URI must avoid latest/bootstrap."],
        ["Container scan", snyk_status, "Snyk runs when enabled and SNYK_TOKEN is configured."],
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
        "|-- Manual production apply via workflow_dispatch",
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


def render_dashboard(root: Path, mode: str = "full", clear: bool = False) -> None:
    if clear:
        clear_screen()
    mode = mode if mode in {"compact", "full"} else "full"
    full = mode == "full"
    cfg = load_config(root)
    checks = collect_dashboard_checks(root, cfg, mode=mode)
    header(cfg)
    print()
    for line in menu_status(root, cfg, checks=checks):
        print(line)
    print()
    print_readiness_breakdown(checks, compact=not full)
    print()
    print_gap_summary(checks, limit=3 if full else 2)
    if not full:
        print()
        print(info("Run `devsecops dashboard --mode full` for GitHub/AWS/deep Terraform diagnostics."))
        return
    print()
    draw_table(
        ["Env", "Memory", "Timeout", "Logs", "Burst/Rate", "CORS"],
        env_rows(cfg),
        title="Environment Configuration",
    )
    print()
    draw_table(["Control", "State", "Notes"], control_rows(cfg), title="Pipeline Controls")
    print()
    draw_box("Architecture", architecture_lines())


def cmd_dashboard(args: argparse.Namespace) -> int:
    root = repo_root()
    interval = max(1, int(args.interval))
    while True:
        render_dashboard(root, mode=args.mode, clear=bool(args.watch))
        if not args.watch:
            return 0
        print()
        print(info(f"Watching dashboard every {interval}s. Press Ctrl-C to stop."))
        time.sleep(interval)


def render_rich_tui(root: Path) -> bool:
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
    except ImportError:
        return False

    cfg = load_config(root)
    checks = collect_dashboard_checks(root, cfg, mode="compact")
    console = Console()
    console.print(
        Panel(
            f"Project: {cfg['project_name']}\nRegion: {cfg['aws_region']}\nOverall: {overall_breakdown_score(checks)}%",
            title="DevSecOps Pipeline Kit",
        )
    )

    table = Table(title="Readiness")
    table.add_column("Area")
    table.add_column("Score", justify="right")
    table.add_column("Gaps", justify="right")
    for area, score, gaps in readiness_breakdown_rows(checks, compact=True):
        table.add_row(area, score, gaps)
    console.print(table)

    gap_table = Table(title="[i] Readiness Details")
    gap_table.add_column("Check")
    gap_table.add_column("Status")
    gap_table.add_column("Fix")
    for check_name, status, _detail, action in readiness_gap_rows(checks)[:5]:
        gap_table.add_row(check_name, status, action)
    if gap_table.row_count:
        console.print(gap_table)
    else:
        console.print(Panel("All scored readiness checks are OK.", title="Readiness Details"))
    console.print("[dim]Full Textual mode is intentionally deferred until doctor workflows stabilize.[/dim]")
    return True


def cmd_tui(args: argparse.Namespace) -> int:
    root = repo_root()
    if render_rich_tui(root):
        return 0
    print(warn("Rich/Textual UI is optional and not installed. Falling back to compact dashboard."))
    print(info('Install optional UI dependencies with `pipx install ".[tui]"` or `python3 -m pip install -e ".[tui]"`.'))
    print()
    render_dashboard(root, mode="compact", clear=False)
    return 0


def cmd_validate_config(args: argparse.Namespace) -> int:
    cfg = load_config(repo_root())
    checks = validate_config(cfg)
    emit_check_output("Config", checks, output_format=getattr(args, "format", "human"))
    return EXIT_VALIDATION_FAILED if any(check.status == "FAIL" for check in checks) else EXIT_OK


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


def apply_preset(root: Path, name: str, render: bool = False) -> int:
    if name not in PRESETS:
        print(fail("Unknown preset: ") + name)
        print("Available presets: " + ", ".join(PRESET_ORDER))
        return 1
    current = load_config(root)
    cfg = preset_config(name)
    for key in ["project_name", "aws_region", "lambda_image_uri", "terraform_admin_role_name", "backend"]:
        cfg[key] = current[key]
    snapshot_before_change(root, "preset", f"Before applying `{name}` preset.")
    write_config(root, cfg)
    print(ok("Applied preset ") + name)
    if render:
        return run_render(root, snapshot=False)
    return 0


def cmd_preset(args: argparse.Namespace) -> int:
    command = args.command
    name = args.name
    if command is None:
        print_preset_list()
        return 0
    if command == "list":
        if name:
            print(fail("`preset list` does not take a preset name."))
            return 1
        print_preset_list()
        return 0
    if command == "show":
        if not name:
            print(fail("Usage: devsecops preset show <name>"))
            return 1
        return print_preset_detail(name)
    if command == "apply":
        if not name:
            print(fail("Usage: devsecops preset apply <name> [--render]"))
            return 1
        return apply_preset(repo_root(), name, render=args.render)
    if command in PRESETS and name is None:
        return apply_preset(repo_root(), command, render=args.render)
    print(fail("Unknown preset command: ") + command)
    print("Usage: devsecops preset list | show <name> | apply <name> [--render]")
    return 1


def compose_summary_rows(cfg: dict[str, Any]) -> list[list[str]]:
    return [
        ["Snyk container scan", "yes" if cfg["enable_snyk_scan"] else "no"],
        ["DAST", "yes" if cfg["enable_dast"] else "no"],
        ["Health check", "yes" if cfg["enable_http_validation"] else "no"],
        ["Strict CORS", "yes" if uses_strict_cors(cfg) else "no"],
        ["Prod approval environment", "yes" if cfg["use_prod_approval_environment"] else "no"],
        ["Separate AWS plan role", "yes" if cfg["use_separate_aws_plan_role"] else "no"],
    ]


def cmd_compose(args: argparse.Namespace) -> int:
    root = repo_root()
    current = load_config(root)
    draw_box(
        "Pipeline Composer",
        [
            "Choose controls for the generated local config, Terraform/GitHub helper artifacts, and readiness report.",
            "Type `b`, `back`, `0`, or `cancel` at any prompt to stop without writing files.",
        ],
    )
    try:
        answers = {
            "enable_snyk_scan": prompt_bool("Enable Snyk container scan", bool(current["enable_snyk_scan"]), allow_cancel=True),
            "enable_dast": prompt_bool("Enable DAST", bool(current["enable_dast"]), allow_cancel=True),
            "enable_http_validation": prompt_bool("Enable health check", bool(current["enable_http_validation"]), allow_cancel=True),
            "use_strict_cors": prompt_bool("Use strict CORS", uses_strict_cors(current), allow_cancel=True),
            "use_prod_approval_environment": prompt_bool(
                "Use prod approval environment",
                bool(current["use_prod_approval_environment"]),
                allow_cancel=True,
            ),
            "use_separate_aws_plan_role": prompt_bool(
                "Use separate AWS plan role",
                bool(current["use_separate_aws_plan_role"]),
                allow_cancel=True,
            ),
        }
    except InputCancelled:
        print(info("Compose cancelled. No files changed."))
        return 0

    cfg = compose_config(current, answers)
    snapshot_before_change(root, "compose", "Before composing pipeline controls.")
    write_config(root, cfg)
    print(ok("Updated ") + str(config_path(root)))
    draw_table(["Control", "Enabled"], compose_summary_rows(cfg), title="Composed Pipeline")

    render_result = run_render(root, snapshot=False)
    if render_result != 0:
        return render_result

    report_path = root / DIST_DIR / "readiness-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    checks = collect_checks(root, load_config(root), deep=False)
    report_path.write_text(markdown_report(cfg, checks), encoding="utf-8")
    print(ok("Wrote ") + str(report_path))
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
    draw_box("Local Snapshot Restore", rollback_boundary_lines())
    print()
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
    emit_check_output(
        "GitHub",
        checks,
        output_format=getattr(args, "format", "human"),
        context={"scope": "repository readiness checks through GitHub CLI"},
    )
    return strict_exit_code(checks, strict=getattr(args, "strict", False), fail_on_warn=True)


def cmd_gh_status(args: argparse.Namespace) -> int:
    status = collect_github_actions_status(repo_root(), limit=args.limit)
    output_format = getattr(args, "format", "human")
    if status.error:
        if output_format == "json":
            emit_json(
                {
                    "kind": "github-actions-status",
                    "error": status.error,
                    "runs": [],
                    "failed_jobs": [],
                    "failed_steps": [],
                    "next_actions": [],
                }
            )
        else:
            print(warn(status.error))
        if not getattr(args, "strict", False):
            return EXIT_OK
        if "not found on PATH" in status.error:
            return EXIT_MISSING_EXTERNAL_TOOL
        if "auth" in status.error.lower() or "login" in status.error.lower():
            return EXIT_AUTH_FAILED
        return EXIT_VALIDATION_FAILED
    if output_format == "json":
        emit_json(
            {
                "kind": "github-actions-status",
                "runs": status.runs,
                "failed_jobs": status.failed_jobs,
                "failed_steps": status.failed_steps,
                "next_actions": status.next_actions,
            }
        )
        return EXIT_VALIDATION_FAILED if getattr(args, "strict", False) and status.failed_jobs else EXIT_OK
    if not status.runs:
        print(warn("No GitHub Actions runs found."))
        return EXIT_OK
    if output_format == "compact":
        print(info("Recent GitHub Actions Runs"))
        for row in status.runs:
            print(" | ".join(row[:4]))
        if status.failed_jobs:
            print(warn(f"Failed jobs: {len(status.failed_jobs)}"))
        for action in status.next_actions:
            print("Fix: " + action)
    else:
        draw_table(["Workflow", "Branch", "Status", "Conclusion", "Created"], status.runs, title="Recent GitHub Actions Runs")
        if status.failed_jobs:
            print()
            draw_table(["Workflow", "Job", "Status", "Conclusion"], status.failed_jobs, title="Failed Jobs")
        if status.failed_steps:
            print()
            draw_table(["Workflow", "Job", "Step", "Conclusion", "Runbook"], status.failed_steps, title="Failed Steps")
        if status.next_actions:
            print()
            draw_box("Next Actions", status.next_actions)
        if not status.failed_jobs:
            print(ok("No failed jobs found in inspected runs."))
    return EXIT_VALIDATION_FAILED if getattr(args, "strict", False) and status.failed_jobs else EXIT_OK


def cmd_branch_doctor(args: argparse.Namespace) -> int:
    checks = collect_branch_checks(repo_root(), branch=args.branch)
    emit_check_output(
        "Branch Protection",
        checks,
        output_format=getattr(args, "format", "human"),
        context={"branch": args.branch},
    )
    return strict_exit_code(checks, strict=getattr(args, "strict", False), fail_on_warn=True)


def cmd_doctor(args: argparse.Namespace) -> int:
    command = getattr(args, "doctor_command", None) or "local"
    if command == "github":
        return cmd_gh_doctor(args)
    if command == "aws":
        return cmd_aws_doctor(args)
    if command == "branch":
        return cmd_branch_doctor(args)
    if command == "actions":
        return cmd_gh_status(args)
    if command == "all":
        root = repo_root()
        cfg = load_config(root)
        checks = collect_checks(root, cfg, deep=getattr(args, "deep", False))
        checks.extend(collect_github_checks(root, cfg))
        checks.extend(collect_branch_checks(root, branch=getattr(args, "branch", DEFAULT_BRANCH)))
        checks.extend(collect_aws_checks(root, cfg, env_name=getattr(args, "environment", "prod")))
        emit_check_output(
            "Doctor All",
            checks,
            output_format=getattr(args, "format", "human"),
            context={"environment": getattr(args, "environment", "prod"), "branch": getattr(args, "branch", DEFAULT_BRANCH)},
        )
        return strict_exit_code(checks, strict=getattr(args, "strict", False), fail_on_warn=True)
    else:
        root = repo_root()
        cfg = load_config(root)
        checks = collect_checks(root, cfg, deep=getattr(args, "deep", False))
        emit_check_output(
            "Doctor Local",
            checks,
            output_format=getattr(args, "format", "human"),
            context={"deep": getattr(args, "deep", False)},
        )
        return strict_exit_code(checks, strict=getattr(args, "strict", False))


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
            cfg["enable_snyk_scan"] = prompt_bool(
                "Enable Snyk container scan",
                bool(current["enable_snyk_scan"]),
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
            cfg["use_prod_approval_environment"] = prompt_bool(
                "Use prod approval environment",
                bool(current["use_prod_approval_environment"]),
                allow_cancel=allow_cancel,
            )
            cfg["use_separate_aws_plan_role"] = prompt_bool(
                "Use separate AWS plan role",
                bool(current["use_separate_aws_plan_role"]),
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


def cmd_preflight(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    checks = collect_image_preflight_checks(
        cfg,
        image_uri=getattr(args, "image_uri", None),
        env_name=getattr(args, "environment", "prod"),
    )
    emit_check_output(
        "Preflight",
        checks,
        output_format=getattr(args, "format", "human"),
        context={"environment": getattr(args, "environment", "prod"), "aws_region": cfg["aws_region"]},
    )
    return EXIT_VALIDATION_FAILED if any(check.status == "FAIL" for check in checks) else EXIT_OK


def dry_run_config(root: Path, preset_name: str, image_uri: str | None = None) -> tuple[dict[str, Any], str]:
    if config_path(root).exists():
        cfg = load_config(root)
        source = str(CONFIG_FILE)
    else:
        cfg = clean_config(preset_name)
        source = f"clean `{preset_name}` preset (not written)"
    if image_uri:
        cfg["lambda_image_uri"] = image_uri
    return cfg, source


def cmd_dry_run(args: argparse.Namespace) -> int:
    root = repo_root()
    env_name = getattr(args, "environment", "prod")
    cfg, source = dry_run_config(root, getattr(args, "preset", "balanced"), getattr(args, "image_uri", None))
    outputs = render_outputs(root, cfg)
    image_checks = collect_image_preflight_checks(cfg, env_name=env_name)
    readiness_checks = collect_checks(root, cfg, deep=False)

    draw_box(
        "First Successful Pipeline Dry Run",
        [
            "No files changed.",
            "AWS credentials are not required for this dry run.",
            f"Config source: {source}",
            f"Environment target: {env_name}",
        ],
    )
    print_render_plan(root, outputs, title="Files that would be rendered")
    print()
    print_checks(image_checks)
    print()
    print_gap_summary(readiness_checks, limit=8)
    print()
    print(info("Next documented path: docs/first-successful-pipeline.md"))
    return EXIT_OK


def render_outputs(root: Path, cfg: dict[str, Any]) -> dict[Path, str]:
    dist = root / DIST_DIR
    return {
        root / GENERATED_TFVARS: terraform_tfvars(cfg),
        dist / "backend.tf": backend_tf(cfg),
        dist / "github-variables.env": github_variables(cfg),
        dist / "github-setup.sh": github_setup_script(cfg),
        dist / "setup-checklist.md": checklist(cfg),
    }


def display_path(root: Path, path: Path) -> str:
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def render_plan_rows(root: Path, outputs: dict[Path, str]) -> list[list[str]]:
    rows = []
    for path, content in outputs.items():
        if not path.exists():
            state = "create"
        elif path.read_text(encoding="utf-8") == content:
            state = "no change"
        else:
            state = "update"
        rows.append([display_path(root, path), state, f"{len(content.splitlines())} lines"])
    return rows


def print_render_plan(root: Path, outputs: dict[Path, str], title: str = "Render Plan") -> None:
    draw_table(["File", "Action", "Size"], render_plan_rows(root, outputs), title=title)


def run_render(root: Path, snapshot: bool = True, dry_run: bool = False) -> int:
    cfg = load_config(root)
    outputs = render_outputs(root, cfg)
    if dry_run:
        print(info("Dry run only. No files changed."))
        print_render_plan(root, outputs)
        return 0

    dist = root / DIST_DIR
    dist.mkdir(parents=True, exist_ok=True)
    if snapshot:
        snapshot_before_change(root, "render", "Before rendering Terraform and GitHub helper artifacts.")

    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if path.name.endswith(".sh"):
            path.chmod(0o755)
        print(ok("Rendered ") + str(path))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    return run_render(repo_root(), dry_run=getattr(args, "dry_run", False))


def cmd_readiness(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    checks = collect_checks(root, cfg, deep=getattr(args, "deep", False))
    output_format = getattr(args, "format", "human")
    if output_format == "json":
        emit_json(checks_payload("readiness", checks, context={"deep": getattr(args, "deep", False)}))
    elif output_format == "compact":
        print_readiness_breakdown(checks, compact=True)
        print()
        print_gap_summary(checks, limit=5)
    else:
        print_readiness_details(root, deep=getattr(args, "deep", False))
    return strict_exit_code(checks, strict=getattr(args, "strict", False), fail_on_warn=True)


def cmd_health(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    checks = collect_health_checks(root, cfg, url=getattr(args, "url", None), timeout=getattr(args, "timeout", 20))
    emit_check_output(
        "Health",
        checks,
        output_format=getattr(args, "format", "human"),
        context={"source": "url" if getattr(args, "url", None) else "terraform output", "timeout_seconds": getattr(args, "timeout", 20)},
    )
    return EXIT_VALIDATION_FAILED if any(check.status == "FAIL" for check in checks) else EXIT_OK


def cmd_aws_doctor(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    checks = collect_aws_checks(root, cfg, env_name=args.environment)
    emit_check_output(
        "AWS",
        checks,
        output_format=getattr(args, "format", "human"),
        context={"environment": args.environment},
    )
    return strict_exit_code(checks, strict=getattr(args, "strict", False), fail_on_warn=True)


def cmd_aws_outputs(args: argparse.Namespace) -> int:
    root = repo_root()
    cfg = load_config(root)
    outputs, checks = inspect_aws_outputs(root, cfg, env_name=getattr(args, "environment", "prod"))
    output_format = getattr(args, "format", "human")
    if output_format == "json":
        emit_json(
            {
                "kind": "aws-outputs",
                "environment": getattr(args, "environment", "prod"),
                "outputs": outputs,
                "checks": [check_to_dict(check) for check in checks],
                "next_actions": [readiness_action_for_check(check) for check in checks if check.scored and check.status != "OK"],
            }
        )
    else:
        draw_box(
            "AWS Deployed Outputs",
            [
                "Read-only inspection of deployed Lambda, API Gateway, and log resources.",
                f"Environment: {outputs['environment']}",
                f"Region: {outputs['aws_region']}",
            ],
        )
        print()
        draw_table(["Output", "Value"], [[key, value or "(not found)"] for key, value in outputs.items()])
        print()
        print_compact_checks(checks, title="AWS Output Checks")
    return strict_exit_code(checks, strict=getattr(args, "strict", False), fail_on_warn=True)


def cmd_aws(args: argparse.Namespace) -> int:
    command = getattr(args, "aws_command", None) or "outputs"
    if command == "doctor":
        return cmd_aws_doctor(args)
    if command == "outputs":
        return cmd_aws_outputs(args)
    print(fail("Unknown AWS command: ") + str(command))
    print("Usage: devsecops aws [doctor|outputs]")
    return EXIT_VALIDATION_FAILED


def cmd_github(args: argparse.Namespace) -> int:
    command = getattr(args, "github_command", None) or "status"
    if command == "setup":
        return cmd_github_setup(args)
    if command == "doctor":
        return cmd_gh_doctor(args)
    if command == "status":
        return cmd_gh_status(args)
    if command == "branch":
        return cmd_branch_doctor(args)
    print(fail("Unknown GitHub command: ") + str(command))
    print("Usage: devsecops github [setup|doctor|status|branch]")
    return EXIT_VALIDATION_FAILED


def cmd_terraform(args: argparse.Namespace) -> int:
    command = getattr(args, "terraform_command", None)
    if command == "plan":
        return cmd_plan(args)
    if command == "bootstrap":
        return cmd_bootstrap(args)
    print(fail("Usage: devsecops terraform plan <env> | bootstrap [--apply]"))
    return EXIT_VALIDATION_FAILED


def cmd_snapshot(args: argparse.Namespace) -> int:
    root = repo_root()
    command = getattr(args, "snapshot_command", None) or "list"
    output_format = getattr(args, "format", "human")
    if command == "list":
        snapshots = list_snapshots(root)
        if output_format == "json":
            emit_json({"kind": "snapshots", "snapshots": snapshots})
        elif snapshots:
            draw_table(["#", "Snapshot", "Created", "Operation"], snapshot_rows(snapshots), title="Snapshots")
        else:
            print(warn("No snapshots found."))
        return EXIT_OK
    if command == "show":
        selection = getattr(args, "selection", None)
        snapshot = resolve_snapshot_selection(root, selection) if selection else resolve_snapshot(root, last=True)
        if not snapshot:
            print(warn("Snapshot not found."))
            return EXIT_VALIDATION_FAILED
        if output_format == "json":
            payload = dict(snapshot)
            payload["changes"] = snapshot_changes(root, snapshot)
            emit_json({"kind": "snapshot", "snapshot": payload})
        else:
            print_snapshot_detail(root, snapshot)
        return EXIT_OK
    if command == "restore":
        rollback_args = argparse.Namespace(
            to=getattr(args, "to", None),
            last=getattr(args, "last", False),
            dry_run=getattr(args, "dry_run", False),
            yes=getattr(args, "yes", False),
        )
        return cmd_rollback(rollback_args)
    print(fail("Unknown snapshot command: ") + str(command))
    print("Usage: devsecops snapshot [list|show|restore]")
    return EXIT_VALIDATION_FAILED


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
    command = getattr(args, "config_command", None) or "show"

    if command in {"new", "create"}:
        preset_name = getattr(args, "preset", "balanced")
        path = config_path(root)
        if path.exists() and not getattr(args, "force", False):
            print(fail(f"{CONFIG_FILE} already exists. Use `devsecops config reset` or pass `--force`."))
            return 1
        if path.exists():
            snapshot_before_change(root, "config-new", "Before replacing local source config.")
        cfg = clean_config(preset_name)
        write_config(root, cfg)
        print(ok("Created clean config ") + str(path))
        if getattr(args, "render", False):
            return run_render(root, snapshot=False)
        return 0

    if command == "reset":
        preset_name = getattr(args, "preset", "balanced")
        snapshot_before_change(root, "config-reset", f"Before resetting local source config to `{preset_name}`.")
        cfg = clean_config(preset_name)
        write_config(root, cfg)
        print(ok("Reset config ") + f"{CONFIG_FILE} to `{preset_name}` preset.")
        if getattr(args, "render", False):
            return run_render(root, snapshot=False)
        return 0

    if command == "validate":
        return cmd_validate_config(args)

    if command == "set":
        return cmd_set(args)

    if command == "schema":
        output_format = getattr(args, "format", "json")
        if output_format == "markdown":
            print(config_schema_markdown())
        else:
            print(json.dumps(config_schema(), indent=2, sort_keys=True))
        return 0

    if command == "diff":
        path = config_path(root)
        if not path.exists():
            print(warn(f"{CONFIG_FILE} does not exist. Run `devsecops config new --preset balanced`."))
            return 1
        preset_name = getattr(args, "preset", None)
        diff = config_preset_diff(root, preset_name) if preset_name else config_file_diff(root)
        if diff:
            print(diff, end="")
            return 1 if getattr(args, "exit_code", False) else 0
        print(ok("No config diff detected."))
        return 0

    if command != "show":
        print(fail("Unknown config command: ") + str(command))
        print("Usage: devsecops config [show|new|validate|diff|reset|schema]")
        return 1

    path = config_path(root)
    if not path.exists():
        print(warn(f"{CONFIG_FILE} does not exist. Run `devsecops config new --preset balanced`."))
        return 1
    output_format = getattr(args, "format", "toml")
    if output_format == "json":
        print(json.dumps(load_config(root), indent=2, sort_keys=True))
    else:
        print(path.read_text(encoding="utf-8"))
    return 0


def menu_status(root: Path, cfg: dict[str, Any], checks: list[Check] | None = None) -> list[str]:
    checks = checks or collect_checks(root, cfg, deep=False)
    score = readiness_score(checks)
    breakdown_score = overall_breakdown_score(checks)
    image_state = "configured" if cfg["lambda_image_uri"] else "missing"
    backend_state = "configured" if not cfg["backend"]["bucket"].startswith("replace-with") else "missing"
    return [
        f"Project: {cfg['project_name']}",
        f"Region: {cfg['aws_region']}",
        f"Lambda image: {image_state}",
        f"Backend: {backend_state}",
        f"Health check: {'enabled' if cfg['enable_http_validation'] else 'disabled'}",
        f"DAST: {'enabled' if cfg['enable_dast'] else 'disabled'}",
        "Readiness: " + progress_bar(breakdown_score) + f"  scored: {score}%  [i] details",
    ]


def clear_screen() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")
    else:
        print("\n" * 3)


def pause_for_menu() -> None:
    input("\n[Enter] Back to main menu")
    clear_screen()


def print_menu_section(title: str, items: list[tuple[str, str]], columns: int = 3) -> None:
    print(info(title))
    for index in range(0, len(items), columns):
        cells = [f"[{key}] {label}" for key, label in items[index : index + columns]]
        print("  " + "  ".join(cell.ljust(28) for cell in cells))


def print_main_menu(root: Path) -> None:
    cfg = load_config(root)
    checks = collect_checks(root, cfg, deep=False)
    score = readiness_score(checks)
    breakdown_score = overall_breakdown_score(checks)
    image_state = "set" if cfg["lambda_image_uri"] else "missing"
    backend_state = "set" if not cfg["backend"]["bucket"].startswith("replace-with") else "missing"
    health_state = "on" if cfg["enable_http_validation"] else "off"
    dast_state = "on" if cfg["enable_dast"] else "off"
    gaps = readiness_gap_rows(checks)[:2]

    print(color("DevSecOps Pipeline Kit", Style.BOLD))
    print(f"{cfg['project_name']} | {cfg['aws_region']} | readiness {breakdown_score}% | scored {score}%")
    print(f"image: {image_state} | backend: {backend_state} | health: {health_state} | DAST: {dast_state}")
    if gaps:
        print()
        print(info("Top gaps:"))
        for name, status, _detail, action in gaps:
            label = fail(status) if status == "FAIL" else warn(status)
            print(f"  {label} {name}: {action}")
    else:
        print()
        print(ok("Top gaps: none."))
    print()
    print_menu_section(
        "Core",
        [
            ("1", "Dashboard"),
            ("2", "Render artifacts"),
            ("3", "Readiness report"),
        ],
    )
    print_menu_section(
        "Config",
        [
            ("4", "Interactive setup"),
            ("5", "Apply preset"),
            ("6", "Show config"),
            ("7", "Composer"),
        ],
    )
    print_menu_section(
        "Diagnostics",
        [
            ("8", "Doctor local/deep"),
            ("9", "AWS doctor"),
            ("10", "Readiness details"),
        ],
    )
    print_menu_section(
        "Terraform",
        [
            ("11", "Bootstrap plan"),
            ("12", "Terraform plan"),
        ],
    )
    print_menu_section(
        "GitHub",
        [
            ("13", "Setup commands"),
            ("14", "GitHub doctor"),
            ("15", "Actions status"),
        ],
    )
    print_menu_section(
        "Reference",
        [
            ("16", "Security controls"),
            ("17", "Environment table"),
        ],
    )
    print_menu_section(
        "Recovery",
        [
            ("18", "Snapshots / rollback"),
            ("0", "Exit"),
        ],
    )


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
    draw_box("Apply Preset", [f"Choose one of: {', '.join(PRESET_ORDER)}. Type `b`, `back`, `0`, or `cancel` to return."])
    preset_name = prompt_text("Preset", "balanced")
    if is_cancel_input(preset_name):
        return
    cmd_preset(argparse.Namespace(command="apply", name=preset_name, render=False))
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


def menu_config_hub(root: Path) -> None:
    clear_screen()
    draw_box("Config", ["Create, inspect, validate, and edit local source config."])
    print()
    print("[1] New clean config (balanced)")
    print("[2] Interactive setup")
    print("[3] Show config")
    print("[4] Validate config")
    print("[5] Diff config")
    print("[6] Set config value")
    print("[7] Apply preset")
    print("[8] Pipeline composer")
    print("[0] Back")
    choice = input("\nChoose: ").strip().lower()
    if choice in MENU_CANCEL_INPUTS:
        clear_screen()
        return
    if choice == "1":
        cmd_config(argparse.Namespace(config_command="new", preset="balanced", force=False, render=False))
    elif choice == "2":
        run_init(root, force=True, defaults=False, allow_cancel=True)
    elif choice == "3":
        cmd_config(argparse.Namespace(config_command="show", format="toml"))
    elif choice == "4":
        cmd_config(argparse.Namespace(config_command="validate", format="human"))
    elif choice == "5":
        cmd_config(argparse.Namespace(config_command="diff", preset=None, exit_code=False))
    elif choice == "6":
        key = prompt_text("Config key", "backend.bucket")
        if is_cancel_input(key):
            clear_screen()
            return
        value = prompt_text("Value", "")
        if is_cancel_input(value):
            clear_screen()
            return
        render_after = prompt_bool("Render artifacts after update", False)
        cmd_config(argparse.Namespace(config_command="set", key=key, value=value, render=render_after))
    elif choice == "7":
        preset_name = prompt_text("Preset", "balanced")
        if is_cancel_input(preset_name):
            clear_screen()
            return
        cmd_preset(argparse.Namespace(command="apply", name=preset_name, render=False))
    elif choice == "8":
        cmd_compose(argparse.Namespace())
    else:
        print(warn("Unknown option."))
    pause_for_menu()


def menu_readiness_section(root: Path) -> None:
    clear_screen()
    print_readiness_details(root)
    pause_for_menu()


def menu_doctor_hub(root: Path) -> None:
    clear_screen()
    draw_box("Doctor", ["Run local, GitHub, AWS, branch, Actions, or full diagnostics."])
    print()
    print("[1] Local")
    print("[2] Local deep")
    print("[3] GitHub")
    print("[4] AWS prod")
    print("[5] Actions status")
    print("[6] Branch protection")
    print("[7] All compact")
    print("[0] Back")
    choice = input("\nChoose: ").strip().lower()
    if choice in MENU_CANCEL_INPUTS:
        clear_screen()
        return
    if choice == "1":
        cmd_doctor(argparse.Namespace(doctor_command="local", deep=False, strict=False, format="human"))
    elif choice == "2":
        cmd_doctor(argparse.Namespace(doctor_command="local", deep=True, strict=False, format="human"))
    elif choice == "3":
        cmd_doctor(argparse.Namespace(doctor_command="github", strict=False, format="human"))
    elif choice == "4":
        cmd_doctor(argparse.Namespace(doctor_command="aws", environment="prod", strict=False, format="human"))
    elif choice == "5":
        cmd_doctor(argparse.Namespace(doctor_command="actions", strict=False, limit=8, format="human"))
    elif choice == "6":
        cmd_doctor(argparse.Namespace(doctor_command="branch", branch=DEFAULT_BRANCH, strict=False, format="human"))
    elif choice == "7":
        cmd_doctor(
            argparse.Namespace(
                doctor_command="all",
                deep=False,
                branch=DEFAULT_BRANCH,
                environment="prod",
                strict=False,
                format="compact",
            )
        )
    else:
        print(warn("Unknown option."))
    pause_for_menu()


def menu_terraform_hub(root: Path) -> None:
    clear_screen()
    draw_box("Terraform", ["Plan environments or inspect backend bootstrap changes."])
    print()
    print("[1] Bootstrap backend plan")
    print("[2] Plan dev")
    print("[3] Plan staging")
    print("[4] Plan prod")
    print("[5] Custom plan")
    print("[0] Back")
    choice = input("\nChoose: ").strip().lower()
    if choice in MENU_CANCEL_INPUTS:
        clear_screen()
        return
    if choice == "1":
        cmd_bootstrap(argparse.Namespace(apply=False))
    elif choice in {"2", "3", "4"}:
        env_name = {"2": "dev", "3": "staging", "4": "prod"}[choice]
        run_plan(root, env_name)
    elif choice == "5":
        env_name = prompt_text("Environment", "dev")
        if is_cancel_input(env_name):
            clear_screen()
            return
        run_plan(root, env_name)
    else:
        print(warn("Unknown option."))
    pause_for_menu()


def menu_github_hub() -> None:
    clear_screen()
    draw_box("GitHub", ["Prepare repository settings and inspect GitHub readiness."])
    print()
    print("[1] Setup commands")
    print("[2] GitHub doctor")
    print("[3] Actions status")
    print("[4] Branch protection")
    print("[0] Back")
    choice = input("\nChoose: ").strip().lower()
    if choice in MENU_CANCEL_INPUTS:
        clear_screen()
        return
    if choice == "1":
        cmd_github_setup(
            argparse.Namespace(
                write=False,
                apply=False,
                deploy_role_arn=None,
                plan_role_arn=None,
                snyk_token=None,
            )
        )
    elif choice == "2":
        cmd_gh_doctor(argparse.Namespace(strict=False, format="human"))
    elif choice == "3":
        cmd_gh_status(argparse.Namespace(strict=False, limit=8, format="human"))
    elif choice == "4":
        cmd_branch_doctor(argparse.Namespace(branch=DEFAULT_BRANCH, strict=False, format="human"))
    else:
        print(warn("Unknown option."))
    pause_for_menu()


def menu_reference_hub() -> None:
    clear_screen()
    draw_box("Reference", ["Inspect controls, environments, architecture, or a focused control explanation."])
    print()
    print("[1] Security controls")
    print("[2] Environment table")
    print("[3] Architecture")
    print("[4] Explain control")
    print("[0] Back")
    choice = input("\nChoose: ").strip().lower()
    if choice in MENU_CANCEL_INPUTS:
        clear_screen()
        return
    if choice == "1":
        cmd_controls(argparse.Namespace())
    elif choice == "2":
        cmd_envs(argparse.Namespace())
    elif choice == "3":
        cmd_architecture(argparse.Namespace())
    elif choice == "4":
        topic = prompt_text("Topic", "oidc")
        if is_cancel_input(topic):
            clear_screen()
            return
        cmd_explain(argparse.Namespace(topic=topic))
    else:
        print(warn("Unknown option."))
    pause_for_menu()


def menu_rollback_section(root: Path) -> None:
    clear_screen()
    draw_box(
        "Snapshots / Rollback",
        rollback_boundary_lines() + ["Choose a number to inspect changes, or type `b`, `back`, `0`, or `cancel` to return."],
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
    draw_box("Local Snapshot Restore", rollback_boundary_lines())
    print()
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
        if normalized_choice in {"10", "i", "info", "readiness", "?"}:
            menu_readiness_section(root)
        elif normalized_choice in {"d", "1", "dashboard"}:
            open_menu_section("Dashboard", cmd_dashboard, argparse.Namespace(mode="full", watch=False, interval=5))
        elif normalized_choice in {"c", "config"}:
            menu_config_hub(root)
        elif choice == "2":
            open_menu_section("Render Config", run_render, root)
        elif normalized_choice in {"o", "doctor"}:
            menu_doctor_hub(root)
        elif choice == "3":
            open_menu_section("Export Readiness Report", cmd_report, argparse.Namespace(deep=False, output=None, print=False))
        elif normalized_choice in {"r", "render"}:
            open_menu_section("Render Config", run_render, root)
        elif choice == "4":
            menu_config_section(root)
        elif normalized_choice in {"t", "terraform"}:
            menu_terraform_hub(root)
        elif choice == "5":
            menu_preset_section()
        elif choice == "6":
            open_menu_section("Show Config", cmd_config, argparse.Namespace())
        elif normalized_choice in {"h", "help", "reference"}:
            menu_reference_hub()
        elif choice == "7":
            open_menu_section("Pipeline Composer", cmd_compose, argparse.Namespace())
        elif choice == "8":
            open_menu_section("Validate Environment", cmd_doctor, argparse.Namespace(deep=True, strict=False))
        elif choice == "9":
            open_menu_section("AWS Doctor", cmd_aws_doctor, argparse.Namespace(environment="prod", strict=False))
        elif choice == "11":
            open_menu_section("Bootstrap Backend Plan", cmd_bootstrap, argparse.Namespace(apply=False))
        elif choice == "12":
            menu_plan_section(root)
        elif normalized_choice in {"g", "github"}:
            menu_github_hub()
        elif choice == "13":
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
        elif choice == "14":
            open_menu_section("GitHub Doctor", cmd_gh_doctor, argparse.Namespace(strict=False, format="human"))
        elif choice == "15":
            open_menu_section("GitHub Actions Status", cmd_gh_status, argparse.Namespace(strict=False, limit=8, format="human"))
        elif choice == "16":
            def show_controls_overview() -> None:
                for topic_name in ["oidc", "backend", "image", "rollback", "dast"]:
                    draw_box(f"Explain: {topic_name}", explain_text(topic_name))

            open_menu_section("Security Controls", show_controls_overview)
        elif choice == "17":
            open_menu_section("Environment Table", cmd_envs, argparse.Namespace())
        elif normalized_choice in {"p", "report"}:
            open_menu_section("Export Readiness Report", cmd_report, argparse.Namespace(deep=False, output=None, print=False))
        elif normalized_choice in {"s", "18", "snapshot", "snapshots"}:
            menu_rollback_section(root)
        elif normalized_choice in {"0", "q", "quit", "exit"}:
            clear_screen()
            return 0
        else:
            clear_screen()
            print(warn("Unknown option."))
            pause_for_menu()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devsecops",
        description="CLI product for creating, validating, rendering, and diagnosing a secure AWS Lambda delivery pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Product boundary:
              The CLI owns local config and generated helper artifacts.
              Terraform, GitHub Actions, AWS, and scanners are transparent execution layers.

            Recommended first run:
              devsecops config new --preset balanced
              devsecops config validate
              devsecops config diff
              devsecops dry-run --image-uri <immutable-ecr-image-uri>
              devsecops render
              devsecops readiness
              devsecops readiness --strict --format compact
              devsecops report

            Docs:
              README.md
              docs/command-inventory.md
              docs/generated-artifacts.md

            Legacy aliases still work:
              init, set, validate-config, preset, compose, snapshots, rollback,
              github-setup, gh-doctor, aws-doctor, actions-status, branch-doctor,
              plan, bootstrap, envs, controls, architecture, tui

            Stable exit codes:
              0 ok, 1 validation failed, 2 missing external tool,
              3 authentication failed, 70 unexpected runtime error, 130 interrupted
            """
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="{menu,config,dry-run,preflight,health,doctor,aws,render,github,terraform,snapshot,readiness,report,dashboard,explain}",
    )
    parser.set_defaults(func=cmd_menu)

    menu_parser = subparsers.add_parser("menu", help="Open the interactive main menu.")
    menu_parser.set_defaults(func=cmd_menu)

    dashboard_parser = subparsers.add_parser("dashboard", help="Print a one-screen pipeline dashboard.")
    dashboard_parser.add_argument("--watch", action="store_true", help="Auto-refresh the dashboard until interrupted.")
    dashboard_parser.add_argument("--interval", type=int, default=5, help="Seconds between dashboard refreshes in watch mode.")
    dashboard_parser.add_argument("--mode", choices=["compact", "full"], default="full", help="Dashboard detail level.")
    dashboard_parser.set_defaults(func=cmd_dashboard)

    tui_parser = subparsers.add_parser("tui", help=argparse.SUPPRESS)
    tui_parser.set_defaults(func=cmd_tui)

    envs_parser = subparsers.add_parser("envs", help=argparse.SUPPRESS)
    envs_parser.set_defaults(func=cmd_envs)

    controls_parser = subparsers.add_parser("controls", help=argparse.SUPPRESS)
    controls_parser.set_defaults(func=cmd_controls)

    architecture_parser = subparsers.add_parser("architecture", help=argparse.SUPPRESS)
    architecture_parser.set_defaults(func=cmd_architecture)

    init_parser = subparsers.add_parser("init", help=argparse.SUPPRESS)
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing config without asking.")
    init_parser.add_argument("--defaults", action="store_true", help="Write default config without prompts.")
    init_parser.set_defaults(func=cmd_init)

    doctor_parser = subparsers.add_parser("doctor", help="Run local, GitHub, AWS, branch, or Actions diagnostics.")
    doctor_parser.add_argument("--deep", action="store_true", help="Run Terraform validate and AWS resource checks.")
    doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    doctor_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    doctor_parser.set_defaults(func=cmd_doctor, doctor_command="local")
    doctor_subparsers = doctor_parser.add_subparsers(dest="doctor_command", metavar="{local,github,aws,branch,actions,all}")

    doctor_local_parser = doctor_subparsers.add_parser("local", help="Check local config, files, tools, and render state.")
    doctor_local_parser.add_argument("--deep", action="store_true", help="Run Terraform validate and AWS resource checks.")
    doctor_local_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    doctor_local_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    doctor_local_parser.set_defaults(func=cmd_doctor)

    doctor_github_parser = doctor_subparsers.add_parser("github", help="Check GitHub CLI, repository variables, and secrets.")
    doctor_github_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    doctor_github_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    doctor_github_parser.set_defaults(func=cmd_doctor)

    doctor_aws_parser = doctor_subparsers.add_parser("aws", help="Check AWS identity, backend, and deployed resources.")
    doctor_aws_parser.add_argument("--environment", choices=ENVIRONMENTS, default="prod", help="Environment resources to inspect.")
    doctor_aws_parser.add_argument("--strict", action="store_true", help="Exit non-zero on WARN or FAIL scored checks.")
    doctor_aws_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    doctor_aws_parser.set_defaults(func=cmd_doctor)

    doctor_branch_parser = doctor_subparsers.add_parser("branch", help="Check branch protection and required checks.")
    doctor_branch_parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Branch to inspect.")
    doctor_branch_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    doctor_branch_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    doctor_branch_parser.set_defaults(func=cmd_doctor)

    doctor_actions_parser = doctor_subparsers.add_parser("actions", help="Show recent GitHub Actions runs and failed jobs.")
    doctor_actions_parser.add_argument("--strict", action="store_true", help="Exit non-zero when status cannot be read.")
    doctor_actions_parser.add_argument("--limit", type=int, default=8, help="Number of workflow runs to inspect.")
    doctor_actions_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    doctor_actions_parser.set_defaults(func=cmd_doctor)

    doctor_all_parser = doctor_subparsers.add_parser("all", help="Run local, GitHub, branch, and AWS checks together.")
    doctor_all_parser.add_argument("--deep", action="store_true", help="Run Terraform validate in local checks.")
    doctor_all_parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Branch to inspect.")
    doctor_all_parser.add_argument("--environment", choices=ENVIRONMENTS, default="prod", help="AWS environment resources to inspect.")
    doctor_all_parser.add_argument("--strict", action="store_true", help="Exit non-zero on WARN or FAIL scored checks.")
    doctor_all_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    doctor_all_parser.set_defaults(func=cmd_doctor)

    readiness_parser = subparsers.add_parser("readiness", help="Show what blocks 100%% readiness.")
    readiness_parser.add_argument("--deep", action="store_true", help="Include Terraform/AWS deep checks.")
    readiness_parser.add_argument("--strict", action="store_true", help="Exit non-zero on any scored readiness gap.")
    readiness_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    readiness_parser.set_defaults(func=cmd_readiness)

    dry_run_parser = subparsers.add_parser("dry-run", help="Preview the first-success path without writing files or requiring AWS.")
    dry_run_parser.add_argument("--preset", choices=PRESET_ORDER, default="balanced", help="Preset to preview when no local config exists.")
    dry_run_parser.add_argument("--image-uri", help="Immutable ECR image URI to preview without writing it to config.")
    dry_run_parser.add_argument("--environment", choices=ENVIRONMENTS, default="prod", help="Environment target for image naming checks.")
    dry_run_parser.set_defaults(func=cmd_dry_run)

    preflight_parser = subparsers.add_parser("preflight", help="Run local preflight checks for the Lambda image before deploy.")
    preflight_parser.add_argument("--image-uri", help="Image URI to check. Defaults to lambda_image_uri from local config.")
    preflight_parser.add_argument("--environment", choices=ENVIRONMENTS, default="prod", help="Environment target for image naming checks.")
    preflight_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    preflight_parser.set_defaults(func=cmd_preflight)

    health_parser = subparsers.add_parser("health", help="Validate the deployed /health endpoint outside GitHub Actions.")
    health_parser.add_argument("--url", help="Health URL to check. Defaults to Terraform output api_gateway_health_url.")
    health_parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    health_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    health_parser.set_defaults(func=cmd_health)

    render_parser = subparsers.add_parser("render", help="Render CLI-owned Terraform/GitHub helper artifacts.")
    render_parser.add_argument("--dry-run", action="store_true", help="Preview generated files without writing them.")
    render_parser.set_defaults(func=cmd_render)

    report_parser = subparsers.add_parser("report", help="Export a CLI-owned Markdown readiness report.")
    report_parser.add_argument("--deep", action="store_true", help="Include Terraform/AWS deep checks.")
    report_parser.add_argument("--output", help="Report output path. Defaults to dist/devsecops/readiness-report.md.")
    report_parser.add_argument("--print", action="store_true", help="Print report after writing it.")
    report_parser.set_defaults(func=cmd_report)

    snapshots_parser = subparsers.add_parser("snapshots", help=argparse.SUPPRESS)
    snapshots_parser.add_argument("--show", help="Show snapshot details by number or id.")
    snapshots_parser.set_defaults(func=cmd_snapshots)

    rollback_parser = subparsers.add_parser("rollback", help=argparse.SUPPRESS)
    rollback_parser.add_argument("--to", help="Snapshot number or id to restore.")
    rollback_parser.add_argument("--last", action="store_true", help="Restore the newest snapshot.")
    rollback_parser.add_argument("--dry-run", action="store_true", help="Preview rollback without changing files.")
    rollback_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
    rollback_parser.set_defaults(func=cmd_rollback)

    github_parser = subparsers.add_parser("github", help="Manage GitHub setup, status, and repository diagnostics.")
    github_parser.set_defaults(func=cmd_github, github_command="status", strict=False, limit=8, format="human")
    github_subparsers = github_parser.add_subparsers(dest="github_command", metavar="{setup,doctor,status,branch}")

    github_group_setup_parser = github_subparsers.add_parser("setup", help="Print, write, or apply GitHub setup commands.")
    github_group_setup_parser.add_argument("--write", action="store_true", help="Write dist/devsecops/github-setup.sh.")
    github_group_setup_parser.add_argument("--apply", action="store_true", help="Apply safe GitHub variables/secrets with gh.")
    github_group_setup_parser.add_argument("--deploy-role-arn", help="Value for AWS_ROLE_TO_ASSUME_ARN when using --apply.")
    github_group_setup_parser.add_argument("--plan-role-arn", help="Value for AWS_PLAN_ROLE_TO_ASSUME_ARN when using --apply.")
    github_group_setup_parser.add_argument("--snyk-token", help="Optional SNYK_TOKEN value when using --apply.")
    github_group_setup_parser.set_defaults(func=cmd_github)

    github_group_doctor_parser = github_subparsers.add_parser("doctor", help="Check GitHub CLI, variables, and secrets.")
    github_group_doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    github_group_doctor_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    github_group_doctor_parser.set_defaults(func=cmd_github)

    github_group_status_parser = github_subparsers.add_parser("status", help="Show recent GitHub Actions runs and failed jobs.")
    github_group_status_parser.add_argument("--strict", action="store_true", help="Exit non-zero when status cannot be read.")
    github_group_status_parser.add_argument("--limit", type=int, default=8, help="Number of workflow runs to inspect.")
    github_group_status_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    github_group_status_parser.set_defaults(func=cmd_github)

    github_group_branch_parser = github_subparsers.add_parser("branch", help="Check branch protection and required checks.")
    github_group_branch_parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Branch to inspect.")
    github_group_branch_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    github_group_branch_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    github_group_branch_parser.set_defaults(func=cmd_github)

    aws_parser = subparsers.add_parser("aws", help="Inspect AWS deployed resources and AWS readiness.")
    aws_parser.set_defaults(func=cmd_aws, aws_command="outputs", environment="prod", strict=False, format="human")
    aws_subparsers = aws_parser.add_subparsers(dest="aws_command", metavar="{outputs,doctor}")

    aws_outputs_parser = aws_subparsers.add_parser("outputs", help="Inspect deployed Lambda/API Gateway outputs from AWS.")
    aws_outputs_parser.add_argument("--environment", choices=ENVIRONMENTS, default="prod", help="Environment resources to inspect.")
    aws_outputs_parser.add_argument("--strict", action="store_true", help="Exit non-zero on WARN or FAIL checks.")
    aws_outputs_parser.add_argument("--format", choices=["human", "json"], default="human", help="Output mode.")
    aws_outputs_parser.set_defaults(func=cmd_aws)

    aws_group_doctor_parser = aws_subparsers.add_parser("doctor", help="Check AWS identity, backend, and deployed resources.")
    aws_group_doctor_parser.add_argument("--environment", choices=ENVIRONMENTS, default="prod", help="Environment resources to inspect.")
    aws_group_doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on WARN or FAIL scored checks.")
    aws_group_doctor_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    aws_group_doctor_parser.set_defaults(func=cmd_aws)

    github_setup_parser = subparsers.add_parser("github-setup", help=argparse.SUPPRESS)
    github_setup_parser.add_argument("--write", action="store_true", help="Write dist/devsecops/github-setup.sh.")
    github_setup_parser.add_argument("--apply", action="store_true", help="Apply safe GitHub variables/secrets with gh.")
    github_setup_parser.add_argument("--deploy-role-arn", help="Value for AWS_ROLE_TO_ASSUME_ARN when using --apply.")
    github_setup_parser.add_argument("--plan-role-arn", help="Value for AWS_PLAN_ROLE_TO_ASSUME_ARN when using --apply.")
    github_setup_parser.add_argument("--snyk-token", help="Optional SNYK_TOKEN value when using --apply.")
    github_setup_parser.set_defaults(func=cmd_github_setup)

    gh_setup_parser = subparsers.add_parser("gh-setup", help=argparse.SUPPRESS)
    gh_setup_parser.add_argument("--write", action="store_true", help="Write dist/devsecops/github-setup.sh.")
    gh_setup_parser.add_argument("--apply", action="store_true", help="Apply safe GitHub variables/secrets with gh.")
    gh_setup_parser.add_argument("--deploy-role-arn", help="Value for AWS_ROLE_TO_ASSUME_ARN when using --apply.")
    gh_setup_parser.add_argument("--plan-role-arn", help="Value for AWS_PLAN_ROLE_TO_ASSUME_ARN when using --apply.")
    gh_setup_parser.add_argument("--snyk-token", help="Optional SNYK_TOKEN value when using --apply.")
    gh_setup_parser.set_defaults(func=cmd_github_setup)

    gh_doctor_parser = subparsers.add_parser("gh-doctor", help=argparse.SUPPRESS)
    gh_doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    gh_doctor_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    gh_doctor_parser.set_defaults(func=cmd_gh_doctor)

    aws_doctor_parser = subparsers.add_parser("aws-doctor", help=argparse.SUPPRESS)
    aws_doctor_parser.add_argument("--environment", choices=ENVIRONMENTS, default="prod", help="Environment resources to inspect.")
    aws_doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on WARN or FAIL scored checks.")
    aws_doctor_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    aws_doctor_parser.set_defaults(func=cmd_aws_doctor)

    gh_status_parser = subparsers.add_parser("gh-status", help=argparse.SUPPRESS)
    gh_status_parser.add_argument("--strict", action="store_true", help="Exit non-zero when status cannot be read.")
    gh_status_parser.add_argument("--limit", type=int, default=8, help="Number of workflow runs to inspect.")
    gh_status_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    gh_status_parser.set_defaults(func=cmd_gh_status)

    actions_status_parser = subparsers.add_parser("actions-status", help=argparse.SUPPRESS)
    actions_status_parser.add_argument("--strict", action="store_true", help="Exit non-zero when status cannot be read.")
    actions_status_parser.add_argument("--limit", type=int, default=8, help="Number of workflow runs to inspect.")
    actions_status_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    actions_status_parser.set_defaults(func=cmd_gh_status)

    branch_doctor_parser = subparsers.add_parser("branch-doctor", help=argparse.SUPPRESS)
    branch_doctor_parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Branch to inspect.")
    branch_doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed scored checks.")
    branch_doctor_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    branch_doctor_parser.set_defaults(func=cmd_branch_doctor)

    set_parser = subparsers.add_parser("set", help=argparse.SUPPRESS)
    set_parser.add_argument("key", help="Config key, for example backend.bucket.")
    set_parser.add_argument("value", help="New value. Lists use comma-separated values.")
    set_parser.add_argument("--render", action="store_true", help="Render artifacts after updating config.")
    set_parser.set_defaults(func=cmd_set)

    validate_config_parser = subparsers.add_parser("validate-config", help=argparse.SUPPRESS)
    validate_config_parser.add_argument("--format", choices=["human", "compact", "json"], default="human", help="Output mode.")
    validate_config_parser.set_defaults(func=cmd_validate_config)

    preset_parser = subparsers.add_parser("preset", help=argparse.SUPPRESS)
    preset_parser.add_argument(
        "command",
        nargs="?",
        help="Use `list`, `show`, `apply`, or a preset name for backward-compatible apply.",
    )
    preset_parser.add_argument("name", nargs="?", help="Preset name for `show` or `apply`.")
    preset_parser.add_argument("--render", action="store_true", help="Render artifacts after applying preset.")
    preset_parser.set_defaults(func=cmd_preset)

    compose_parser = subparsers.add_parser("compose", help=argparse.SUPPRESS)
    compose_parser.set_defaults(func=cmd_compose)

    terraform_parser = subparsers.add_parser("terraform", help="Run Terraform plan and backend bootstrap helpers.")
    terraform_parser.set_defaults(func=cmd_terraform)
    terraform_subparsers = terraform_parser.add_subparsers(dest="terraform_command", metavar="{plan,bootstrap}")

    terraform_plan_parser = terraform_subparsers.add_parser("plan", help="Run Terraform plan for an environment.")
    terraform_plan_parser.add_argument("environment", choices=ENVIRONMENTS)
    terraform_plan_parser.add_argument("--no-init", action="store_true", help="Skip Terraform init.")
    terraform_plan_parser.add_argument("--create-workspace", action="store_true", help="Create the workspace if it is missing.")
    terraform_plan_parser.set_defaults(func=cmd_terraform)

    terraform_bootstrap_parser = terraform_subparsers.add_parser("bootstrap", help="Plan or apply backend bootstrap.")
    terraform_bootstrap_parser.add_argument("--apply", action="store_true", help="Apply backend bootstrap with auto-approve.")
    terraform_bootstrap_parser.set_defaults(func=cmd_terraform)

    snapshot_parser = subparsers.add_parser("snapshot", help="List, inspect, or restore local CLI snapshots.")
    snapshot_parser.add_argument("--format", choices=["human", "json"], default="human", help="Output mode for default list.")
    snapshot_parser.set_defaults(func=cmd_snapshot, snapshot_command="list")
    snapshot_subparsers = snapshot_parser.add_subparsers(dest="snapshot_command", metavar="{list,show,restore}")

    snapshot_list_parser = snapshot_subparsers.add_parser("list", help="List local CLI snapshots.")
    snapshot_list_parser.add_argument("--format", choices=["human", "json"], default="human", help="Output mode.")
    snapshot_list_parser.set_defaults(func=cmd_snapshot)

    snapshot_show_parser = snapshot_subparsers.add_parser("show", help="Show snapshot details by number or id.")
    snapshot_show_parser.add_argument("selection", nargs="?", help="Snapshot number or id. Defaults to newest snapshot.")
    snapshot_show_parser.add_argument("--format", choices=["human", "json"], default="human", help="Output mode.")
    snapshot_show_parser.set_defaults(func=cmd_snapshot)

    snapshot_restore_parser = snapshot_subparsers.add_parser("restore", help="Restore CLI-owned files from a snapshot.")
    snapshot_restore_parser.add_argument("--to", help="Snapshot number or id to restore.")
    snapshot_restore_parser.add_argument("--last", action="store_true", help="Restore the newest snapshot.")
    snapshot_restore_parser.add_argument("--dry-run", action="store_true", help="Preview rollback without changing files.")
    snapshot_restore_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
    snapshot_restore_parser.set_defaults(func=cmd_snapshot)

    plan_parser = subparsers.add_parser("plan", help=argparse.SUPPRESS)
    plan_parser.add_argument("environment", choices=ENVIRONMENTS)
    plan_parser.add_argument("--no-init", action="store_true", help="Skip Terraform init.")
    plan_parser.add_argument("--create-workspace", action="store_true", help="Create the workspace if it is missing.")
    plan_parser.set_defaults(func=cmd_plan)

    bootstrap_parser = subparsers.add_parser("bootstrap", help=argparse.SUPPRESS)
    bootstrap_parser.add_argument("--apply", action="store_true", help="Apply backend bootstrap with auto-approve.")
    bootstrap_parser.set_defaults(func=cmd_bootstrap)

    explain_parser = subparsers.add_parser("explain", help="Explain a pipeline security control.")
    explain_parser.add_argument("topic", nargs="?", default="all")
    explain_parser.set_defaults(func=cmd_explain)

    config_parser = subparsers.add_parser("config", help="Manage local source config.")
    config_parser.add_argument(
        "--format",
        choices=["toml", "json"],
        default="toml",
        help="Output format for `devsecops config` compatibility show mode.",
    )
    config_parser.set_defaults(func=cmd_config, config_command="show")
    config_subparsers = config_parser.add_subparsers(dest="config_command")

    config_show_parser = config_subparsers.add_parser("show", help="Print local source config.")
    config_show_parser.add_argument("--format", choices=["toml", "json"], default="toml", help="Output format.")
    config_show_parser.set_defaults(func=cmd_config)

    config_new_parser = config_subparsers.add_parser("new", help="Create a clean local source config.")
    config_new_parser.add_argument("--preset", choices=PRESET_ORDER, default="balanced", help="Preset to use for the clean config.")
    config_new_parser.add_argument("--force", action="store_true", help="Replace an existing config after taking a snapshot.")
    config_new_parser.add_argument("--render", action="store_true", help="Render artifacts after writing config.")
    config_new_parser.set_defaults(func=cmd_config)

    config_validate_parser = config_subparsers.add_parser("validate", help="Validate local source config values.")
    config_validate_parser.set_defaults(func=cmd_config)

    config_diff_parser = config_subparsers.add_parser("diff", help="Show canonical config diff or compare against a preset.")
    config_diff_parser.add_argument("--preset", choices=PRESET_ORDER, help="Compare current config against a clean preset.")
    config_diff_parser.add_argument("--exit-code", action="store_true", help="Exit 1 when a diff is present.")
    config_diff_parser.set_defaults(func=cmd_config)

    config_reset_parser = config_subparsers.add_parser("reset", help="Reset local source config to a clean preset.")
    config_reset_parser.add_argument("--preset", choices=PRESET_ORDER, default="balanced", help="Preset to reset to.")
    config_reset_parser.add_argument("--render", action="store_true", help="Render artifacts after resetting config.")
    config_reset_parser.set_defaults(func=cmd_config)

    config_set_parser = config_subparsers.add_parser("set", help="Set a local source config value.")
    config_set_parser.add_argument("key", help="Config key, for example backend.bucket.")
    config_set_parser.add_argument("value", help="New value. Lists use comma-separated values.")
    config_set_parser.add_argument("--render", action="store_true", help="Render artifacts after updating config.")
    config_set_parser.set_defaults(func=cmd_config)

    config_create_parser = config_subparsers.add_parser("create", help="Alias for config new.")
    config_create_parser.add_argument("--preset", choices=PRESET_ORDER, default="balanced", help="Preset to use for the clean config.")
    config_create_parser.add_argument("--force", action="store_true", help="Replace an existing config after taking a snapshot.")
    config_create_parser.add_argument("--render", action="store_true", help="Render artifacts after writing config.")
    config_create_parser.set_defaults(func=cmd_config)

    config_schema_parser = config_subparsers.add_parser("schema", help="Print the local config schema contract.")
    config_schema_parser.add_argument("--format", choices=["json", "markdown"], default="json", help="Schema output format.")
    config_schema_parser.set_defaults(func=cmd_config)
    if hasattr(subparsers, "_choices_actions"):
        subparsers._choices_actions = [  # type: ignore[attr-defined]
            choice for choice in subparsers._choices_actions if getattr(choice, "help", None) != argparse.SUPPRESS
        ]
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print()
        print(warn("Interrupted."))
        return EXIT_INTERRUPTED
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(fail("Unexpected error: ") + str(exc))
        return EXIT_UNEXPECTED_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
