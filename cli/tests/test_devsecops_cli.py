import tempfile
import unittest
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import devsecops_cli as cli


class DevSecOpsCliTests(unittest.TestCase):
    def test_immutable_image_validation(self) -> None:
        self.assertTrue(cli.is_immutable_image("repo.example/app:sha-abc123"))
        self.assertTrue(cli.is_immutable_image("repo.example/app@sha256:" + "a" * 64))
        self.assertFalse(cli.is_immutable_image(""))
        self.assertFalse(cli.is_immutable_image("repo.example/app"))
        self.assertFalse(cli.is_immutable_image("repo.example/app:latest"))
        self.assertFalse(cli.is_immutable_image("repo.example/app:bootstrap"))

    def test_config_dump_load_roundtrip(self) -> None:
        cfg = cli.default_config()
        cfg["project_name"] = "abc-project"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cfg)
            loaded = cli.load_config(root)
        self.assertEqual(loaded["project_name"], "abc-project")
        self.assertEqual(loaded["environments"]["prod"]["lambda_timeout"], 240)

    def test_nested_set_and_parse(self) -> None:
        cfg = cli.default_config()
        current = cli.nested_get(cfg, "environments.dev.lambda_timeout")
        cli.nested_set(cfg, "environments.dev.lambda_timeout", cli.parse_config_value("300", current))
        self.assertEqual(cfg["environments"]["dev"]["lambda_timeout"], 300)

        current_origins = cli.nested_get(cfg, "environments.dev.cors_allowed_origins")
        cli.nested_set(
            cfg,
            "environments.dev.cors_allowed_origins",
            cli.parse_config_value("https://example.com,https://app.example.com", current_origins),
        )
        self.assertEqual(
            cfg["environments"]["dev"]["cors_allowed_origins"],
            ["https://example.com", "https://app.example.com"],
        )

    def test_prompt_bool_zero_stays_false_outside_cancel_mode(self) -> None:
        with patch("builtins.input", return_value="0"):
            self.assertFalse(cli.prompt_bool("Flag", True))

    def test_tfvars_contains_environment_config(self) -> None:
        rendered = cli.terraform_tfvars(cli.default_config())
        self.assertIn('project_name = "devsecops-pipeline"', rendered)
        self.assertIn("environment_config = {", rendered)
        self.assertIn("prod = {", rendered)

    def test_readiness_score_weights_warn_as_half_credit(self) -> None:
        checks = [
            cli.Check("one", "OK", "ok"),
            cli.Check("two", "WARN", "warn"),
            cli.Check("three", "FAIL", "fail"),
            cli.Check("info", "INFO", "ignored", scored=False),
        ]
        self.assertEqual(cli.readiness_score(checks), 50)

    def test_readiness_gap_rows_show_only_scored_gaps_with_fix(self) -> None:
        rows = cli.readiness_gap_rows(
            [
                cli.Check("Project name", "OK", "valid"),
                cli.Check("Lambda image URI", "WARN", "Required before production deploy."),
                cli.Check("HTTP validation", "INFO", "Disabled.", scored=False),
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Lambda image URI")
        self.assertIn("devsecops set lambda_image_uri", rows[0][3])

    def test_config_validation_catches_bad_values(self) -> None:
        cfg = cli.default_config()
        cfg["environments"]["dev"]["lambda_timeout"] = 901
        failures = [check for check in cli.validate_config(cfg) if check.status == "FAIL"]
        self.assertTrue(any(check.name == "dev.lambda_timeout" for check in failures))

    def test_preset_strict_enables_validation_controls(self) -> None:
        cfg = cli.preset_config("strict")
        self.assertTrue(cfg["enable_http_validation"])
        self.assertTrue(cfg["enable_dast"])
        self.assertEqual(cfg["environments"]["prod"]["lambda_timeout"], 300)

    def test_github_setup_script_contains_expected_commands(self) -> None:
        cfg = cli.default_config()
        cfg["lambda_image_uri"] = "123456789012.dkr.ecr.us-east-1.amazonaws.com/app:sha-abc123"
        script = cli.github_setup_script(cfg)
        self.assertIn("gh variable set PROJECT_NAME", script)
        self.assertIn("gh secret set AWS_ROLE_TO_ASSUME_ARN", script)
        self.assertIn("sha-abc123", script)

    def test_markdown_report_contains_checks_and_actions(self) -> None:
        cfg = cli.default_config()
        checks = [cli.Check("Lambda image URI", "WARN", "Required before production deploy.")]
        report = cli.markdown_report(cfg, checks)
        self.assertIn("# DevSecOps Pipeline Readiness Report", report)
        self.assertIn("## Checks", report)
        self.assertIn("Set `LAMBDA_IMAGE_URI`", report)

    def test_parse_gh_items_from_json(self) -> None:
        payload = json.dumps(
            [
                {"name": "PROJECT_NAME", "value": "devsecops-pipeline"},
                {"name": "ENABLE_DAST", "value": "false"},
            ]
        )
        parsed = cli.parse_gh_items(payload, value_key="value")
        self.assertEqual(parsed["PROJECT_NAME"], "devsecops-pipeline")
        self.assertEqual(parsed["ENABLE_DAST"], "false")

    def test_github_variable_checks_find_mismatch(self) -> None:
        cfg = cli.default_config()
        cfg["lambda_image_uri"] = "repo.example/app:sha-abc123"
        variables = {
            "PROJECT_NAME": "wrong",
            "LAMBDA_IMAGE_URI": "repo.example/app:latest",
            "ENABLE_HTTP_VALIDATION": "false",
            "ENABLE_DAST": "false",
        }
        checks = cli.github_variable_checks(cfg, variables)
        self.assertTrue(any(check.name.endswith("PROJECT_NAME") and check.status == "WARN" for check in checks))
        self.assertTrue(any(check.name.endswith("LAMBDA_IMAGE_URI") and check.status == "WARN" for check in checks))

    def test_github_secret_checks_mark_required_and_optional(self) -> None:
        checks = cli.github_secret_checks({"AWS_REGION": "", "AWS_ROLE_TO_ASSUME_ARN": ""})
        self.assertTrue(any(check.name.endswith("AWS_PLAN_ROLE_TO_ASSUME_ARN") and check.status == "WARN" for check in checks))
        self.assertTrue(any(check.name.endswith("SNYK_TOKEN") and check.status == "INFO" for check in checks))

    def test_branch_protection_checks_required_statuses(self) -> None:
        protection = {
            "required_pull_request_reviews": {"required_approving_review_count": 1},
            "required_status_checks": {
                "contexts": ["Security and Terraform Validate"],
                "checks": [{"context": "Terraform Plan"}],
            },
        }
        checks = cli.branch_protection_checks("main", True, protection)
        self.assertTrue(any(check.name == "Branch `main` protection" and check.status == "OK" for check in checks))
        self.assertTrue(any(check.name == "Required check `Terraform Plan`" and check.status == "OK" for check in checks))

    def test_branch_protection_checks_missing_required_status(self) -> None:
        protection = {
            "required_pull_request_reviews": None,
            "required_status_checks": {"contexts": ["Security and Terraform Validate"]},
        }
        checks = cli.branch_protection_checks("main", True, protection)
        self.assertTrue(any(check.name == "Pull request requirement" and check.status == "WARN" for check in checks))
        self.assertTrue(any(check.name == "Required check `Terraform Plan`" and check.status == "WARN" for check in checks))

    def test_actions_run_rows_and_failed_jobs(self) -> None:
        runs = cli.parse_gh_runs(
            json.dumps(
                [
                    {
                        "databaseId": 10,
                        "workflowName": "Deploy",
                        "headBranch": "main",
                        "status": "completed",
                        "conclusion": "failure",
                        "createdAt": "2026-06-05T00:00:00Z",
                    }
                ]
            )
        )
        self.assertEqual(cli.actions_run_rows(runs)[0][0], "Deploy")

        failed = cli.failed_job_rows(
            "Deploy",
            json.dumps(
                {
                    "jobs": [
                        {"name": "Terraform Plan", "status": "completed", "conclusion": "success"},
                        {"name": "Apply", "status": "completed", "conclusion": "failure"},
                    ]
                }
            ),
        )
        self.assertEqual(failed, [["Deploy", "Apply", "completed", "failure"]])

    def test_snapshots_are_listed_newest_first_and_selectable_by_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.object(
                cli,
                "snapshot_id",
                side_effect=["20260101T000000Z-old", "20260102T000000Z-new"],
            ):
                cli.create_snapshot(root, "old", "Older snapshot")
                cli.create_snapshot(root, "new", "Newer snapshot")

            snapshots = cli.list_snapshots(root)
            self.assertEqual(
                [snapshot["id"] for snapshot in snapshots],
                ["20260102T000000Z-new", "20260101T000000Z-old"],
            )
            self.assertEqual(cli.resolve_snapshot_selection(root, "1")["id"], "20260102T000000Z-new")
            self.assertEqual(
                cli.resolve_snapshot_selection(root, "20260101T000000Z-old")["operation"],
                "old",
            )

    def test_snapshot_changes_and_restore_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg = cli.default_config()
            cfg["project_name"] = "before-app"
            cli.write_config(root, cfg)

            snapshot_path = cli.create_snapshot(root, "set", "Before project rename")
            snapshot = cli.read_snapshot_manifest(snapshot_path)
            snapshot["_path"] = str(snapshot_path)

            cfg["project_name"] = "after-app"
            cli.write_config(root, cfg)

            changes = cli.snapshot_changes(root, snapshot)
            self.assertTrue(any(change["path"] == cli.CONFIG_FILE for change in changes))

            cli.restore_snapshot(root, snapshot)
            restored = cli.load_config(root)
            self.assertEqual(restored["project_name"], "before-app")

    def test_snapshot_restore_removes_file_created_after_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot_path = cli.create_snapshot(root, "render", "Before generated files")
            snapshot = cli.read_snapshot_manifest(snapshot_path)
            snapshot["_path"] = str(snapshot_path)

            generated = root / cli.GENERATED_TFVARS
            generated.parent.mkdir(parents=True)
            generated.write_text('project_name = "after-app"\n', encoding="utf-8")

            changes = cli.snapshot_changes(root, snapshot)
            self.assertTrue(any(change["path"] == str(cli.GENERATED_TFVARS) for change in changes))

            cli.restore_snapshot(root, snapshot)
            self.assertFalse(generated.exists())

    def test_snapshot_restore_ignores_paths_outside_cli_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot_path = cli.create_snapshot(root, "render", "Before generated files")
            snapshot = cli.read_snapshot_manifest(snapshot_path)
            snapshot["_path"] = str(snapshot_path)
            snapshot["files"].append({"path": "README.md", "present": False})

            unmanaged = root / "README.md"
            unmanaged.write_text("unmanaged file\n", encoding="utf-8")

            cli.restore_snapshot(root, snapshot)
            self.assertTrue(unmanaged.exists())

    def test_init_cancel_from_menu_mode_does_not_write_config_or_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            buffer = io.StringIO()
            with patch("builtins.input", return_value="0"), redirect_stdout(buffer):
                result = cli.run_init(root, force=True, allow_cancel=True)

            self.assertEqual(result, 0)
            self.assertFalse((root / cli.CONFIG_FILE).exists())
            self.assertEqual(cli.list_snapshots(root), [])
            self.assertIn("Configuration cancelled", buffer.getvalue())

    def test_menu_status_and_main_menu_include_navigation_items(self) -> None:
        cfg = cli.default_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cfg)
            status = cli.menu_status(root, cfg)
            self.assertTrue(any("Readiness:" in line for line in status))

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                cli.print_main_menu(root)
            output = buffer.getvalue()
        self.assertTrue(any("[i] details" in line for line in status))
        self.assertIn("[1] Dashboard", output)
        self.assertIn("[i] details", output)
        self.assertIn("[16] Snapshots / Rollback", output)
        self.assertIn("[0] Exit", output)


if __name__ == "__main__":
    unittest.main()
