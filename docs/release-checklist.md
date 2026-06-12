# Release Checklist

Use this checklist for every `vX.Y.Z` release. Replace `0.8.0` and `v0.8.0`
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
* `docs/release-v0.8.0.md`
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
  docs/release-checklist.md \
  docs/upgrade-guide.md \
  docs/release-v0.8.0.md \
  docs/command-inventory.md
git commit -m "Release v0.8.0 distribution readiness"
```

## 5. Tag And Push

Create an annotated tag from the release commit:

```bash
git tag -a v0.8.0 -m "Release v0.8.0"
git push origin main
git push origin v0.8.0
```

The `Publish GitHub Release` workflow runs on `v*.*.*` tags.

## 6. Verify The Published Release

After the workflow finishes:

```bash
VERSION="0.8.0"
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
`docs/release-v0.8.0.md`.
