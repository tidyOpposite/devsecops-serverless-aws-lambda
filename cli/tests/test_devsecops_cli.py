import argparse
import importlib
import tempfile
import unittest
import json
import io
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

cli = importlib.import_module("devsecops_cli.main")
GOLDEN_DIR = Path(__file__).with_name("golden")
RENDERED_ARTIFACTS = [
    cli.GENERATED_TFVARS,
    cli.DIST_DIR / "backend.tf",
    cli.DIST_DIR / "github-variables.env",
    cli.DIST_DIR / "github-setup.sh",
    cli.DIST_DIR / "setup-checklist.md",
]


def golden_config() -> dict[str, object]:
    cfg = cli.preset_config("strict")
    cfg["project_name"] = "golden-pipeline"
    cfg["aws_region"] = "eu-central-1"
    cfg["lambda_image_uri"] = "123456789012.dkr.ecr.eu-central-1.amazonaws.com/golden-pipeline:sha-abc123"
    cfg["terraform_admin_role_name"] = "golden-terraform-admin"
    cfg["backend"] = {
        "bucket": "golden-pipeline-tfstate",
        "key": "golden-pipeline/terraform.tfstate",
        "region": "eu-central-1",
        "lock_table": "golden-pipeline-locks",
        "workspace_key_prefix": "envs",
    }
    return cfg


def read_golden(name: str) -> str:
    return (GOLDEN_DIR / name).read_text(encoding="utf-8")


def rendered_artifact_contents(root: Path) -> dict[str, str]:
    return {str(path): (root / path).read_text(encoding="utf-8") for path in RENDERED_ARTIFACTS}


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
            config_text = (root / cli.CONFIG_FILE).read_text(encoding="utf-8")
        self.assertEqual(loaded["schema_version"], cli.CONFIG_SCHEMA_VERSION)
        self.assertEqual(loaded["project_name"], "abc-project")
        self.assertEqual(loaded["environments"]["prod"]["lambda_timeout"], 240)
        self.assertIn("schema_version = 1", config_text)

    def test_load_legacy_config_without_schema_version_migrates_to_current_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / cli.CONFIG_FILE).write_text('project_name = "legacy-app"\n', encoding="utf-8")
            loaded = cli.load_config(root)

        self.assertEqual(loaded["schema_version"], cli.CONFIG_SCHEMA_VERSION)
        self.assertEqual(loaded["project_name"], "legacy-app")

    def test_clean_config_has_no_secret_fields(self) -> None:
        rendered = cli.dump_config_toml(cli.clean_config("balanced"))
        forbidden = ["AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "GITHUB_TOKEN", "SNYK_TOKEN", "PRIVATE_KEY"]
        for token in forbidden:
            self.assertNotIn(token, rendered)

    def test_config_new_show_schema_diff_and_reset_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.object(cli, "repo_root", return_value=root):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.cmd_config(argparse.Namespace(config_command="new", preset="minimal", force=False, render=False)), 0)

                created = root / cli.CONFIG_FILE
                self.assertTrue(created.exists())
                self.assertIn("schema_version = 1", created.read_text(encoding="utf-8"))

                show_json = io.StringIO()
                with redirect_stdout(show_json):
                    self.assertEqual(cli.cmd_config(argparse.Namespace(config_command="show", format="json")), 0)
                self.assertEqual(json.loads(show_json.getvalue())["environments"]["dev"]["lambda_memory_size"], 512)

                schema_json = io.StringIO()
                with redirect_stdout(schema_json):
                    self.assertEqual(cli.cmd_config(argparse.Namespace(config_command="schema", format="json")), 0)
                self.assertEqual(json.loads(schema_json.getvalue())["schema_version"], cli.CONFIG_SCHEMA_VERSION)

                diff_output = io.StringIO()
                with redirect_stdout(diff_output):
                    self.assertEqual(cli.cmd_config(argparse.Namespace(config_command="diff", preset=None, exit_code=True)), 0)
                self.assertIn("No config diff detected", diff_output.getvalue())

                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.cmd_config(argparse.Namespace(config_command="reset", preset="balanced", render=False)), 0)
                reset = cli.load_config(root)
                self.assertEqual(reset["environments"]["dev"]["lambda_memory_size"], 1024)

    def test_config_new_refuses_existing_config_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cli.clean_config("minimal"))
            with patch.object(cli, "repo_root", return_value=root):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    result = cli.cmd_config(argparse.Namespace(config_command="new", preset="balanced", force=False, render=False))

        self.assertEqual(result, 1)
        self.assertIn("already exists", buffer.getvalue())

    def test_config_diff_reports_canonical_drift_and_preset_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cli.clean_config("minimal"))
            config_file = root / cli.CONFIG_FILE
            config_file.write_text(config_file.read_text(encoding="utf-8") + "# manual note\n", encoding="utf-8")

            canonical_diff = cli.config_file_diff(root)
            preset_diff = cli.config_preset_diff(root, "balanced")

        self.assertIn("-# manual note", canonical_diff)
        self.assertIn("preset:balanced", preset_diff)
        self.assertIn("lambda_memory_size = 1024", preset_diff)

    def test_clean_config_and_render_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.object(cli, "repo_root", return_value=root):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.cmd_config(argparse.Namespace(config_command="new", preset="balanced", force=False, render=True)), 0)
                first_config = (root / cli.CONFIG_FILE).read_text(encoding="utf-8")
                first_tfvars = (root / cli.GENERATED_TFVARS).read_text(encoding="utf-8")

                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.cmd_config(argparse.Namespace(config_command="reset", preset="balanced", render=True)), 0)
                second_config = (root / cli.CONFIG_FILE).read_text(encoding="utf-8")
                second_tfvars = (root / cli.GENERATED_TFVARS).read_text(encoding="utf-8")

        self.assertEqual(first_config, second_config)
        self.assertEqual(first_tfvars, second_tfvars)

    def test_rendered_artifacts_have_stable_diffs_across_repeated_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, golden_config())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.run_render(root, snapshot=False), 0)
            first = rendered_artifact_contents(root)

            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.run_render(root, snapshot=False), 0)
            second = rendered_artifact_contents(root)

        self.assertEqual(first, second)

    def test_e2e_config_validate_render_report_in_temp_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.object(cli, "repo_root", return_value=root):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["config", "new", "--preset", "balanced"]), 0)
                    self.assertEqual(cli.main(["config", "validate"]), 0)
                    self.assertEqual(cli.main(["render"]), 0)
                    self.assertEqual(cli.main(["report"]), 0)

            self.assertTrue((root / cli.CONFIG_FILE).exists())
            self.assertTrue((root / cli.GENERATED_TFVARS).exists())
            self.assertTrue((root / cli.DIST_DIR / "github-setup.sh").exists())
            report = root / cli.DIST_DIR / "readiness-report.md"
            self.assertTrue(report.exists())
            self.assertIn("# DevSecOps Pipeline Readiness Report", report.read_text(encoding="utf-8"))

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
        self.assertIn("CLI-owned generated file", rendered)
        self.assertIn('project_name              = "devsecops-pipeline"', rendered)
        self.assertIn("environment_config = {", rendered)
        self.assertIn("prod = {", rendered)

    def test_generated_terraform_tfvars_matches_golden_fixture(self) -> None:
        self.assertEqual(cli.terraform_tfvars(golden_config()), read_golden("terraform_generated.auto.tfvars.golden"))

    def test_generated_github_artifacts_match_golden_fixtures(self) -> None:
        cfg = golden_config()
        self.assertEqual(cli.github_variables(cfg), read_golden("github-variables.env"))
        self.assertEqual(cli.github_setup_script(cfg), read_golden("github-setup.sh"))

    def test_generated_artifacts_carry_cli_owned_notice(self) -> None:
        cfg = cli.default_config()
        artifacts = [
            cli.terraform_tfvars(cfg),
            cli.backend_tf(cfg),
            cli.github_variables(cfg),
            cli.github_setup_script(cfg),
            cli.checklist(cfg),
            cli.markdown_report(cfg, []),
        ]

        for artifact in artifacts:
            self.assertIn("CLI-owned generated", artifact)
            self.assertIn("Do not edit directly", artifact)

    def test_help_documents_product_contract_and_first_run(self) -> None:
        help_text = cli.build_parser().format_help()
        self.assertIn("CLI product", help_text)
        self.assertIn("Product boundary:", help_text)
        self.assertIn("devsecops config new --preset balanced", help_text)
        self.assertIn("devsecops config validate", help_text)
        self.assertIn("devsecops config diff", help_text)
        self.assertIn("devsecops render", help_text)
        self.assertIn("devsecops readiness", help_text)
        self.assertIn("devsecops report", help_text)
        self.assertIn("docs/command-inventory.md", help_text)
        self.assertIn("docs/generated-artifacts.md", help_text)

    def test_top_level_help_is_grouped_and_legacy_aliases_are_not_primary_choices(self) -> None:
        help_text = cli.build_parser().format_help()
        self.assertIn("{menu,config,doctor,render,github,terraform,snapshot,readiness,report,dashboard,explain}", help_text)
        self.assertIn("Legacy aliases still work", help_text)
        self.assertIn("Stable exit codes", help_text)
        self.assertNotIn("==SUPPRESS==", help_text)
        self.assertNotIn("gh-doctor           ", help_text)
        self.assertNotIn("aws-doctor          ", help_text)

    def test_readiness_json_output_contains_checks_and_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cli.default_config())
            with patch.object(cli, "repo_root", return_value=root):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    result = cli.cmd_readiness(argparse.Namespace(deep=False, format="json"))

        payload = json.loads(buffer.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["kind"], "readiness")
        self.assertIn("checks", payload)
        self.assertIn("gaps", payload)

    def test_doctor_group_json_and_legacy_alias_share_payload_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cli.default_config())
            with patch.object(cli, "repo_root", return_value=root):
                grouped = io.StringIO()
                with redirect_stdout(grouped):
                    grouped_result = cli.main(["doctor", "local", "--format", "json"])
                legacy = io.StringIO()
                with redirect_stdout(legacy):
                    legacy_result = cli.main(["doctor", "--format", "json"])

        grouped_payload = json.loads(grouped.getvalue())
        legacy_payload = json.loads(legacy.getvalue())
        self.assertEqual(grouped_result, 0)
        self.assertEqual(legacy_result, 0)
        self.assertEqual(grouped_payload["kind"], "doctor-local")
        self.assertEqual(legacy_payload["kind"], "doctor-local")

    def test_grouped_config_set_updates_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cli.default_config())
            with patch.object(cli, "repo_root", return_value=root):
                with redirect_stdout(io.StringIO()):
                    result = cli.main(["config", "set", "backend.bucket", "state-bucket"])

            cfg = cli.load_config(root)

        self.assertEqual(result, 0)
        self.assertEqual(cfg["backend"]["bucket"], "state-bucket")

    def test_grouped_snapshot_list_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.create_snapshot(root, "test", "Test snapshot")
            with patch.object(cli, "repo_root", return_value=root):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    result = cli.main(["snapshot", "list", "--format", "json"])

        payload = json.loads(buffer.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["kind"], "snapshots")
        self.assertEqual(len(payload["snapshots"]), 1)

    def test_grouped_terraform_plan_dispatches_legacy_plan_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.object(cli, "repo_root", return_value=root), patch.object(cli, "run_plan", return_value=0) as run_plan:
                result = cli.main(["terraform", "plan", "dev", "--create-workspace"])

        self.assertEqual(result, 0)
        run_plan.assert_called_once_with(root, "dev", no_init=False, create_workspace=True)

    def test_github_group_status_json_reports_missing_gh_without_failing_non_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.object(cli, "repo_root", return_value=root), patch.object(cli, "command_exists", return_value=False):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    result = cli.main(["github", "status", "--format", "json"])

        payload = json.loads(buffer.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["kind"], "github-actions-status")
        self.assertIn("error", payload)

    def test_doctor_github_strict_returns_missing_tool_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cli.default_config())
            with patch.object(cli, "repo_root", return_value=root), patch.object(cli, "command_exists", return_value=False):
                with redirect_stdout(io.StringIO()):
                    result = cli.main(["doctor", "github", "--strict", "--format", "json"])

        self.assertEqual(result, cli.EXIT_MISSING_EXTERNAL_TOOL)

    def test_collect_github_checks_warns_when_gh_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(cli, "command_exists", return_value=False):
                checks = cli.collect_github_checks(Path(tmpdir), cli.default_config())

        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["GitHub CLI"].status, "WARN")
        self.assertIn("`gh` not found", by_name["GitHub CLI"].detail)

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

    def test_config_validate_command_fails_before_external_tools_on_bad_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg = cli.default_config()
            cfg["environments"]["dev"]["lambda_timeout"] = 901
            cli.write_config(root, cfg)
            with patch.object(cli, "repo_root", return_value=root):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    result = cli.cmd_config(argparse.Namespace(config_command="validate"))

        self.assertEqual(result, cli.EXIT_VALIDATION_FAILED)
        self.assertIn("dev.lambda_timeout", buffer.getvalue())

    def test_doctor_local_strict_reports_unavailable_terraform_as_missing_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cli.default_config())

            def fake_command_exists(name: str) -> bool:
                return name not in {"git", "terraform", "aws"}

            with patch.object(cli, "repo_root", return_value=root), patch.object(cli, "command_exists", side_effect=fake_command_exists):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    result = cli.main(["doctor", "local", "--strict", "--format", "json"])

        payload = json.loads(buffer.getvalue())
        terraform_check = next(check for check in payload["checks"] if check["name"] == "`terraform` CLI")
        self.assertEqual(result, cli.EXIT_MISSING_EXTERNAL_TOOL)
        self.assertEqual(terraform_check["status"], "FAIL")
        self.assertIn("Not found on PATH", terraform_check["detail"])

    def test_preset_strict_enables_validation_controls(self) -> None:
        cfg = cli.preset_config("strict")
        self.assertTrue(cfg["enable_snyk_scan"])
        self.assertTrue(cfg["enable_http_validation"])
        self.assertTrue(cfg["enable_dast"])
        self.assertEqual(cfg["environments"]["prod"]["lambda_timeout"], 300)

    def test_preset_profiles_are_valid_and_include_phase_five_profiles(self) -> None:
        self.assertIn("enterprise", cli.PRESET_ORDER)
        self.assertIn("student-demo", cli.PRESET_ORDER)

        for preset_name in cli.PRESET_ORDER:
            cfg = cli.preset_config(preset_name)
            failures = [check for check in cli.validate_config(cfg) if check.status == "FAIL"]
            self.assertEqual(failures, [], preset_name)

        enterprise = cli.preset_config("enterprise")
        self.assertTrue(enterprise["enable_snyk_scan"])
        self.assertTrue(enterprise["enable_http_validation"])
        self.assertTrue(enterprise["enable_dast"])
        self.assertEqual(enterprise["environments"]["prod"]["log_retention_days"], 1095)
        self.assertNotIn("*", enterprise["environments"]["prod"]["cors_allowed_origins"])

        demo = cli.preset_config("student-demo")
        self.assertFalse(demo["enable_http_validation"])
        self.assertFalse(demo["enable_dast"])
        self.assertEqual(demo["environments"]["dev"]["lambda_memory_size"], 512)

    def test_preset_list_show_apply_and_legacy_apply(self) -> None:
        list_output = io.StringIO()
        with redirect_stdout(list_output):
            self.assertEqual(cli.cmd_preset(argparse.Namespace(command="list", name=None, render=False)), 0)
        self.assertIn("enterprise", list_output.getvalue())
        self.assertIn("student-demo", list_output.getvalue())

        show_output = io.StringIO()
        with redirect_stdout(show_output):
            self.assertEqual(cli.cmd_preset(argparse.Namespace(command="show", name="strict", render=False)), 0)
        self.assertIn("Preset: strict", show_output.getvalue())
        self.assertIn("prod.lambda_timeout", show_output.getvalue())

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg = cli.default_config()
            cfg["project_name"] = "kept-project"
            cli.write_config(root, cfg)
            with patch.object(cli, "repo_root", return_value=root), patch.object(cli, "run_render", return_value=0) as run_render:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.cmd_preset(argparse.Namespace(command="apply", name="enterprise", render=True)), 0)
                applied = cli.load_config(root)
                self.assertEqual(applied["project_name"], "kept-project")
                self.assertEqual(applied["environments"]["prod"]["log_retention_days"], 1095)
                run_render.assert_called_once()

                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.cmd_preset(argparse.Namespace(command="student-demo", name=None, render=False)), 0)
                legacy_applied = cli.load_config(root)
                self.assertEqual(legacy_applied["environments"]["prod"]["log_retention_days"], 30)

    def test_compose_config_applies_control_answers(self) -> None:
        cfg = cli.compose_config(
            cli.default_config(),
            {
                "enable_snyk_scan": True,
                "enable_dast": True,
                "enable_http_validation": True,
                "use_strict_cors": True,
                "use_prod_approval_environment": False,
                "use_separate_aws_plan_role": False,
            },
        )
        self.assertTrue(cfg["enable_snyk_scan"])
        self.assertTrue(cfg["enable_dast"])
        self.assertTrue(cfg["enable_http_validation"])
        self.assertFalse(cfg["use_prod_approval_environment"])
        self.assertFalse(cfg["use_separate_aws_plan_role"])
        self.assertTrue(cli.uses_strict_cors(cfg))
        self.assertEqual(cli.prod_approval_environment(cfg), cli.NO_APPROVAL_ENVIRONMENT)

    def test_compose_command_writes_config_artifacts_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            answers = [True, True, True, True, False, False]
            with patch.object(cli, "repo_root", return_value=root), patch.object(cli, "prompt_bool", side_effect=answers):
                with redirect_stdout(io.StringIO()):
                    result = cli.cmd_compose(argparse.Namespace())

            self.assertEqual(result, 0)
            cfg = cli.load_config(root)
            self.assertTrue(cfg["enable_snyk_scan"])
            self.assertTrue(cfg["enable_dast"])
            self.assertTrue(cfg["enable_http_validation"])
            self.assertTrue(cli.uses_strict_cors(cfg))
            self.assertFalse(cfg["use_prod_approval_environment"])
            self.assertFalse(cfg["use_separate_aws_plan_role"])
            self.assertTrue((root / cli.CONFIG_FILE).exists())
            self.assertTrue((root / cli.GENERATED_TFVARS).exists())
            self.assertTrue((root / cli.DIST_DIR / "github-setup.sh").exists())
            report = root / cli.DIST_DIR / "readiness-report.md"
            self.assertTrue(report.exists())
            self.assertIn("Snyk container scan", report.read_text(encoding="utf-8"))

    def test_github_setup_script_contains_expected_commands(self) -> None:
        cfg = cli.default_config()
        cfg["lambda_image_uri"] = "123456789012.dkr.ecr.us-east-1.amazonaws.com/app:sha-abc123"
        cfg["enable_snyk_scan"] = True
        script = cli.github_setup_script(cfg)
        self.assertIn("gh variable set PROJECT_NAME", script)
        self.assertIn("gh variable set ENABLE_SNYK_SCAN", script)
        self.assertIn("gh variable set PROD_APPROVAL_ENVIRONMENT", script)
        self.assertIn("gh secret set AWS_ROLE_TO_ASSUME_ARN", script)
        self.assertIn("gh secret set SNYK_TOKEN", script)
        self.assertIn("sha-abc123", script)

    def test_markdown_report_contains_checks_and_actions(self) -> None:
        cfg = cli.default_config()
        checks = [cli.Check("Lambda image URI", "WARN", "Required before production deploy.")]
        report = cli.markdown_report(cfg, checks)
        self.assertIn("# DevSecOps Pipeline Readiness Report", report)
        self.assertIn("## Score Breakdown", report)
        self.assertIn("## Checks", report)
        self.assertIn("Set `LAMBDA_IMAGE_URI`", report)

    def test_readiness_breakdown_groups_scores_by_area(self) -> None:
        checks = [
            cli.Check("Local config", "OK", "ok"),
            cli.Check("Terraform validate", "OK", "ok"),
            cli.Check("GitHub variable PROJECT_NAME", "WARN", "missing"),
            cli.Check("AWS identity", "FAIL", "missing"),
            cli.Check("DAST", "INFO", "disabled", scored=False),
            cli.Check("Lambda image URI", "WARN", "missing"),
        ]
        rows = cli.readiness_breakdown_rows(checks, compact=True)
        by_area = {row[0]: row for row in rows}
        self.assertEqual(by_area["Local"][1], "100%")
        self.assertEqual(by_area["Terraform"][1], "100%")
        self.assertEqual(by_area["GitHub"][1], "50%")
        self.assertEqual(by_area["AWS"][1], "0%")
        self.assertEqual(by_area["Security"][1], "50%")
        self.assertEqual(by_area["Deployment"][2], "1")

    def test_dashboard_renders_compact_and_full_modes(self) -> None:
        cfg = cli.default_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cfg)
            compact = io.StringIO()
            with redirect_stdout(compact):
                cli.render_dashboard(root, mode="compact")
            compact_output = compact.getvalue()
            self.assertIn("Readiness", compact_output)
            self.assertIn("Run `devsecops dashboard --mode full`", compact_output)

            full = io.StringIO()
            dashboard_checks = [
                cli.Check("Local config", "OK", "ok"),
                cli.Check("GitHub variable PROJECT_NAME", "WARN", "missing"),
            ]
            with patch.object(cli, "collect_dashboard_checks", return_value=dashboard_checks):
                with redirect_stdout(full):
                    cli.render_dashboard(root, mode="full")
            full_output = full.getvalue()
            self.assertIn("Pipeline Controls", full_output)
            self.assertIn("GitHub", full_output)

    def test_tui_falls_back_to_compact_dashboard_without_rich(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, cli.default_config())
            with patch.object(cli, "repo_root", return_value=root), patch.object(cli, "render_rich_tui", return_value=False), patch.object(
                cli,
                "render_dashboard",
            ) as render_dashboard:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    result = cli.cmd_tui(argparse.Namespace())

        self.assertEqual(result, 0)
        self.assertIn("Rich/Textual UI is optional", buffer.getvalue())
        render_dashboard.assert_called_once_with(root, mode="compact", clear=False)

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
            "ENABLE_SNYK_SCAN": "false",
            "ENABLE_HTTP_VALIDATION": "false",
            "ENABLE_DAST": "false",
            "PROD_APPROVAL_ENVIRONMENT": "prod",
        }
        checks = cli.github_variable_checks(cfg, variables)
        self.assertTrue(any(check.name.endswith("PROJECT_NAME") and check.status == "WARN" for check in checks))
        self.assertTrue(any(check.name.endswith("LAMBDA_IMAGE_URI") and check.status == "WARN" for check in checks))

    def test_github_secret_checks_mark_required_and_optional(self) -> None:
        cfg = cli.default_config()
        checks = cli.github_secret_checks(cfg, {"AWS_REGION": "", "AWS_ROLE_TO_ASSUME_ARN": ""})
        self.assertTrue(any(check.name.endswith("AWS_PLAN_ROLE_TO_ASSUME_ARN") and check.status == "WARN" for check in checks))
        self.assertTrue(any(check.name.endswith("SNYK_TOKEN") and check.status == "INFO" for check in checks))

        cfg["use_separate_aws_plan_role"] = False
        cfg["enable_snyk_scan"] = True
        checks = cli.github_secret_checks(cfg, {"AWS_REGION": "", "AWS_ROLE_TO_ASSUME_ARN": ""})
        self.assertTrue(any(check.name.endswith("AWS_PLAN_ROLE_TO_ASSUME_ARN") and check.status == "INFO" for check in checks))
        self.assertTrue(any(check.name.endswith("SNYK_TOKEN") and check.status == "WARN" for check in checks))

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

    def test_parse_ecr_image_uri_tag_and_digest(self) -> None:
        tagged = cli.parse_ecr_image_uri("123456789012.dkr.ecr.us-east-1.amazonaws.com/app/service:sha-abc123")
        self.assertIsNotNone(tagged)
        self.assertEqual(tagged.region, "us-east-1")
        self.assertEqual(tagged.repository, "app/service")
        self.assertEqual(tagged.tag, "sha-abc123")

        digest = "sha256:" + "a" * 64
        digested = cli.parse_ecr_image_uri(f"123456789012.dkr.ecr.eu-central-1.amazonaws.com/app@{digest}")
        self.assertIsNotNone(digested)
        self.assertEqual(digested.digest, digest)
        self.assertIsNone(cli.parse_ecr_image_uri("repo.example/app:sha-abc123"))

    def test_collect_aws_checks_warns_when_aws_cli_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(cli, "command_exists", return_value=False):
                checks = cli.collect_aws_checks(Path(tmpdir), cli.default_config(), env_name="prod")

        self.assertEqual(checks[0].name, "AWS CLI")
        self.assertEqual(checks[0].status, "WARN")
        self.assertTrue(any(check.name == "Lambda function" and check.status == "WARN" for check in checks))

    def test_collect_aws_checks_warns_when_credentials_are_missing(self) -> None:
        def fake_run_command(command: list[str], root: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 255, stdout="", stderr="Unable to locate credentials")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(cli, "command_exists", side_effect=lambda name: name == "aws"), patch.object(
                cli,
                "run_command",
                side_effect=fake_run_command,
            ):
                checks = cli.collect_aws_checks(Path(tmpdir), cli.default_config(), env_name="prod")

        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["AWS CLI"].status, "OK")
        self.assertEqual(by_name["AWS identity"].status, "WARN")
        self.assertIn("Unable to locate credentials", by_name["AWS identity"].detail)
        self.assertIn("without valid AWS credentials", by_name["State bucket"].detail)

    def test_collect_aws_checks_successful_resources(self) -> None:
        cfg = cli.default_config()
        cfg["backend"]["bucket"] = "state-bucket"
        cfg["lambda_image_uri"] = "123456789012.dkr.ecr.us-east-1.amazonaws.com/devsecops-pipeline-prod-lambda-repo:sha-abc123"

        def completed(command: list[str], stdout: str = "{}") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        def fake_run_command(command: list[str], root: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
            command_text = " ".join(command)
            if "sts get-caller-identity" in command_text:
                return completed(command, json.dumps({"Arn": "arn:aws:iam::123456789012:user/test", "Account": "123456789012"}))
            if "s3api head-bucket" in command_text:
                return completed(command)
            if "dynamodb describe-table" in command_text:
                return completed(command, json.dumps({"Table": {"TableStatus": "ACTIVE"}}))
            if "ecr describe-repositories" in command_text:
                return completed(command, json.dumps({"repositories": [{"repositoryName": "devsecops-pipeline-prod-lambda-repo"}]}))
            if "iam get-role" in command_text:
                return completed(command, json.dumps({"Role": {"Arn": "arn:aws:iam::123456789012:role/devsecops-pipeline-prod-lambda-exec-role"}}))
            if "lambda get-function-configuration" in command_text:
                return completed(command, json.dumps({"FunctionName": "devsecops-pipeline-prod-lambda", "State": "Active"}))
            if "apigatewayv2 get-apis" in command_text:
                return completed(
                    command,
                    json.dumps({"Items": [{"Name": "devsecops-pipeline-prod-http-api", "ApiEndpoint": "https://api.example"}]}),
                )
            if "logs describe-log-groups" in command_text:
                return completed(
                    command,
                    json.dumps({"logGroups": [{"logGroupName": "/aws/lambda/devsecops-pipeline-prod-lambda", "retentionInDays": 365}]}),
                )
            if "ecr describe-images" in command_text:
                return completed(command, json.dumps({"imageDetails": [{"imageTags": ["sha-abc123"]}]}))
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="unexpected command")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(cli, "command_exists", return_value=True), patch.object(cli, "run_command", side_effect=fake_run_command):
                checks = cli.collect_aws_checks(Path(tmpdir), cfg, env_name="prod")

        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["AWS identity"].status, "OK")
        self.assertEqual(by_name["State bucket"].status, "OK")
        self.assertEqual(by_name["Lock table"].status, "OK")
        self.assertEqual(by_name["ECR repository"].status, "OK")
        self.assertEqual(by_name["Lambda execution role"].status, "OK")
        self.assertEqual(by_name["Lambda function"].status, "OK")
        self.assertEqual(by_name["API Gateway"].status, "OK")
        self.assertEqual(by_name["CloudWatch log group"].status, "OK")
        self.assertEqual(by_name["Configured ECR image"].status, "OK")

    def test_collect_checks_deep_includes_aws_resource_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            aws_checks = [cli.Check("Lambda execution role", "OK", "deployed")]
            with patch.object(cli, "command_exists", return_value=False), patch.object(
                cli,
                "collect_aws_checks",
                return_value=aws_checks,
            ) as collect_aws:
                checks = cli.collect_checks(root, cli.default_config(), deep=True)

        self.assertIn(aws_checks[0], checks)
        collect_aws.assert_called_once()

    def test_aws_doctor_strict_fails_on_scored_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.object(cli, "repo_root", return_value=root), patch.object(cli, "load_config", return_value=cli.default_config()), patch.object(
                cli,
                "collect_aws_checks",
                return_value=[cli.Check("AWS CLI", "WARN", "`aws` not found on PATH.")],
            ):
                with redirect_stdout(io.StringIO()):
                    result = cli.cmd_aws_doctor(argparse.Namespace(environment="prod", strict=True))

        self.assertEqual(result, cli.EXIT_MISSING_EXTERNAL_TOOL)

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

    def test_snapshot_restore_reverts_overwritten_cli_owned_generated_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cli.write_config(root, golden_config())
            with redirect_stdout(io.StringIO()):
                cli.run_render(root, snapshot=False)
            original_tfvars = (root / cli.GENERATED_TFVARS).read_text(encoding="utf-8")

            snapshot_path = cli.create_snapshot(root, "render", "Before generated overwrite")
            snapshot = cli.read_snapshot_manifest(snapshot_path)
            snapshot["_path"] = str(snapshot_path)

            (root / cli.GENERATED_TFVARS).write_text('project_name = "manually-overwritten"\n', encoding="utf-8")
            cli.restore_snapshot(root, snapshot)

            self.assertEqual((root / cli.GENERATED_TFVARS).read_text(encoding="utf-8"), original_tfvars)

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

    def test_snapshot_restore_does_not_overwrite_user_owned_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            unmanaged = root / "README.md"
            unmanaged.write_text("user version before snapshot\n", encoding="utf-8")
            snapshot_path = cli.create_snapshot(root, "render", "Before generated files")
            snapshot = cli.read_snapshot_manifest(snapshot_path)
            snapshot["_path"] = str(snapshot_path)

            unmanaged.write_text("user version after snapshot\n", encoding="utf-8")
            cli.restore_snapshot(root, snapshot)

            self.assertEqual(unmanaged.read_text(encoding="utf-8"), "user version after snapshot\n")

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

    def test_menu_status_and_main_menu_include_grouped_navigation_sections(self) -> None:
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
        for section in ["Core", "Config", "Diagnostics", "Terraform", "GitHub", "Reference", "Recovery"]:
            self.assertIn(section, output)
        self.assertIn("[1] Dashboard", output)
        self.assertIn("[2] Render artifacts", output)
        self.assertIn("[3] Readiness report", output)
        self.assertIn("[4] Interactive setup", output)
        self.assertIn("[5] Apply preset", output)
        self.assertIn("[6] Show config", output)
        self.assertIn("[7] Composer", output)
        self.assertIn("[8] Doctor local/deep", output)
        self.assertIn("[9] AWS doctor", output)
        self.assertIn("[10] Readiness details", output)
        self.assertIn("[11] Bootstrap plan", output)
        self.assertIn("[12] Terraform plan", output)
        self.assertIn("[13] Setup commands", output)
        self.assertIn("[14] GitHub doctor", output)
        self.assertIn("[15] Actions status", output)
        self.assertIn("[16] Security controls", output)
        self.assertIn("[17] Environment table", output)
        self.assertIn("[18] Snapshots / rollback", output)
        self.assertIn("[0] Exit", output)
        self.assertNotIn("[i] Readiness details", output)

        item_lines = [line for line in output.splitlines() if line.startswith("  [")]
        self.assertTrue(item_lines)
        for line in item_lines:
            self.assertLessEqual(line.count("["), 3)

    def test_pause_for_menu_clears_screen_before_returning_to_main_menu(self) -> None:
        with patch("builtins.input", return_value=""), patch.object(cli, "clear_screen") as clear_screen:
            cli.pause_for_menu()

        clear_screen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
