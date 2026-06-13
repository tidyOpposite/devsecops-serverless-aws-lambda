# Release Checklist

Use this checklist for every `vX.Y.Z` release. Replace `0.10.0` and `v0.10.0`
with the target version.

## 1. Prepare The Version

```bash
git status --short --branch
```

Update all version references together:

* `cli/devsecops_cli/main.py`
* `cli/devsecops_cli/__init__.py`
* `pyproject.toml`
* `cli/pyproject.toml`
* `cli/tests/test_devsecops_cli.py`

Update release documentation:

* `CHANGELOG.md`
* `docs/release-v0.10.0.md`
* `README.md` release links when a new release note is added
* `ROADMAP.md` milestone status when a milestone ships

## 2. Validate Locally

```bash
PYTHONPATH=cli python3 -m unittest discover -s cli/tests
python3 -m pip install --upgrade build setuptools wheel
rm -rf build
python3 -m build
python3 -m pip install --no-deps --no-build-isolation .
devsecops --version
devsecops completion bash >/tmp/devsecops.bash
devsecops completion zsh >/tmp/_devsecops
devsecops completion fish >/tmp/devsecops.fish
git diff --check
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform init -backend=false -input=false -no-color
terraform -chdir=terraform validate -no-color
```

If `gh` and AWS credentials are available, also run:

```bash
devsecops doctor github --format compact
devsecops doctor aws --environment prod --format compact
```

## 3. Build And Check Artifacts

```bash
rm -rf build dist
python3 -m build
cd dist
shasum -a 256 *.whl *.tar.gz > SHA256SUMS
shasum -a 256 -c SHA256SUMS
cd ..
```

The tag workflow repeats this build on GitHub Actions and publishes:

* wheel
* source distribution
* `SHA256SUMS`

## 4. Commit

```bash
git status --short
git add \
  cli/devsecops_cli/main.py \
  cli/devsecops_cli/__init__.py \
  pyproject.toml \
  cli/pyproject.toml \
  cli/tests/test_devsecops_cli.py \
  .github/workflows/ci.yml \
  .github/workflows/release.yml \
  CHANGELOG.md \
  README.md \
  ROADMAP.md \
  docs/distribution.md \
  docs/first-successful-pipeline.md \
  docs/generated-artifacts.md \
  docs/production-deployment-evidence.md \
  docs/release-checklist.md \
  docs/stability-contract.md \
  docs/upgrade-guide.md \
  docs/release-v0.10.0.md \
  docs/command-inventory.md
git commit -m "Release v0.10.0 stability contract"
```

## 5. Tag And Push

Create an annotated tag from the release commit:

```bash
git tag -a v0.10.0 -m "Release v0.10.0"
git push origin main
git push origin v0.10.0
```

The `Publish GitHub Release` workflow runs on `v*.*.*` tags.

## 6. Verify The Published Release

After the workflow finishes:

```bash
VERSION="0.10.0"
TAG="v${VERSION}"
BASE_URL="https://github.com/tidyOpposite/devsecops-serverless-aws-lambda/releases/download/${TAG}"
WHEEL="devsecops_pipeline_cli-${VERSION}-py3-none-any.whl"

curl -fsSLO "${BASE_URL}/SHA256SUMS"
curl -fsSLO "${BASE_URL}/${WHEEL}"
grep " ${WHEEL}$" SHA256SUMS > "${WHEEL}.SHA256SUMS"
shasum -a 256 -c "${WHEEL}.SHA256SUMS"
python3.11 -m pipx install --force --python python3.11 "./${WHEEL}"
devsecops --version
```

Confirm the release page includes the expected notes from
`docs/release-v0.10.0.md`.

## 7. Production Evidence Gate

For `v0.9.0` and later releases that claim production proof, run
[Production deployment evidence](production-deployment-evidence.md) against the
target AWS account and GitHub repository before closing the release.

Attach or link the evidence bundle in the release record. At minimum, the
bundle must include:

* release install and checksum proof;
* strict config validation and readiness JSON;
* Markdown readiness report and JSON audit report;
* GitHub doctor, branch doctor, and workflow run evidence;
* Terraform outputs and `devsecops aws outputs` JSON;
* `devsecops doctor aws --strict` JSON;
* `/health` validation output and response body;
* CloudWatch log group/tail evidence;
* active Lambda image and rollback-readiness notes.

If the walkthrough exposes a failure mode not covered by
[Operational runbooks](runbooks/README.md), update the runbook set before
publishing the release.

## 8. Stability Contract Gate

For `v0.10.0` and later releases, verify the public contract before publishing:

```bash
devsecops inventory --format json
devsecops inventory --status stable --format markdown
devsecops config schema --format json
devsecops config schema --format markdown
devsecops render --dry-run
```

Confirm:

* first-success docs use only stable commands;
* aliases have documented stable replacements and deprecation expectations;
* experimental commands are not required by README, first-success, production
  evidence, or release workflows;
* every JSON `kind` used by scripts appears in the inventory contract;
* schema-changing releases include migration tests and upgrade notes before
  generated files are re-rendered;
* generated artifact diffs match the expected compatibility notes in
  [Generated artifacts](generated-artifacts.md).
