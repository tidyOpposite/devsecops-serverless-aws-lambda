# Release v0.10.0

Release date: 2026-06-13

Milestone 9 makes the pre-1.0 stability contract explicit. Users can inspect
which commands, flags, JSON payloads, config schema behavior, and generated
artifacts are stable enough to script against.

## Highlights

* Added `devsecops inventory --format json|markdown|human` with command
  status, stable flags, deprecation policy, JSON output contract, generated
  artifact contract, config migration policy, and exit code contract.
* Added [Stability contract](stability-contract.md) for stable commands,
  compatibility aliases, experimental commands, JSON output shapes, config
  migrations, and generated artifact compatibility.
* Promoted Terraform helper commands to the stable contract so the documented
  first-success workflow no longer depends on experimental behavior.
* Added fail-closed config loading for future schema versions. A CLI that does
  not understand a future `.devsecops-pipeline.toml` schema refuses to render
  or overwrite CLI-owned files.
* Added generated artifact compatibility notes that explain when re-rendering
  is required and what diffs users should expect.
* Added production evidence workflow documentation from Milestone 8 so release
  records can capture setup, workflow, Terraform, AWS, health, log, image, and
  rollback-readiness evidence.

## Install

The canonical `v0.10.0` package path is the GitHub Release wheel:

```bash
python3.11 -m pipx install --python python3.11 \
  "devsecops-pipeline-cli @ https://github.com/tidyOpposite/devsecops-serverless-aws-lambda/releases/download/v0.10.0/devsecops_pipeline_cli-0.10.0-py3-none-any.whl"
devsecops --version
```

For the latest-release install command, see
[Distribution and compatibility](distribution.md).

## Verify Artifacts

```bash
VERSION="0.10.0"
TAG="v${VERSION}"
BASE_URL="https://github.com/tidyOpposite/devsecops-serverless-aws-lambda/releases/download/${TAG}"
WHEEL="devsecops_pipeline_cli-${VERSION}-py3-none-any.whl"

curl -fsSLO "${BASE_URL}/SHA256SUMS"
curl -fsSLO "${BASE_URL}/${WHEEL}"
grep " ${WHEEL}$" SHA256SUMS > "${WHEEL}.SHA256SUMS"
shasum -a 256 -c "${WHEEL}.SHA256SUMS"
```

## Stability Inventory

```bash
devsecops inventory --format json
devsecops inventory --status stable --format markdown
devsecops config schema --format markdown
```

## Upgrade Notes

`v0.10.0` does not change config schema semantics. Current schema version
remains `1`.

Existing schema version `1` files and legacy files without `schema_version`
continue to normalize to schema version `1`. Config files with a future
`schema_version` are refused until the CLI is upgraded.

After upgrading, review generated artifact diffs before applying any GitHub or
AWS mutations:

```bash
devsecops config validate
devsecops config diff
devsecops render --dry-run
devsecops inventory --format json
```

## Validation

The release was validated with:

```bash
PYTHONPATH=cli python3 -m unittest discover -s cli/tests
PYTHONPATH=cli python3 -m devsecops_cli.main inventory --status stable --format json
python3 -m build
git diff --check
```
