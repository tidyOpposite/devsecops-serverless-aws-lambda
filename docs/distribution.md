# Distribution And Compatibility

This document is the install, verification, shell completion, and compatibility
contract for released DevSecOps Pipeline Kit CLI versions.

## Published Package Path

The canonical package path for `v0.10.0` is GitHub Releases:

* Repository: `tidyOpposite/devsecops-serverless-aws-lambda`
* Release tag format: `vX.Y.Z`
* Python package name: `devsecops-pipeline-cli`
* Release assets:
  * `devsecops_pipeline_cli-<version>-py3-none-any.whl`
  * `devsecops_pipeline_cli-<version>.tar.gz`
  * `SHA256SUMS`

PyPI publication is intentionally deferred. Until a PyPI publishing workflow is
added and documented, install from GitHub Release wheel assets rather than from
the source tree.

## Install The Latest Release

Use a Python 3.11+ interpreter. Set `PYTHON` when your binary is named
`python3.12`, `python3.13`, or another supported version.

```bash
PYTHON="${PYTHON:-python3.11}"

"${PYTHON}" -m pip install --user pipx
"${PYTHON}" -m pipx ensurepath
export PATH="$HOME/.local/bin:$PATH"

WHEEL_URL="$(
  "${PYTHON}" - <<'PY'
import json
import urllib.request

repo = "tidyOpposite/devsecops-serverless-aws-lambda"
with urllib.request.urlopen(f"https://api.github.com/repos/{repo}/releases/latest") as response:
    release = json.load(response)

for asset in release["assets"]:
    if asset["name"].endswith("-py3-none-any.whl"):
        print(asset["browser_download_url"])
        break
else:
    raise SystemExit("No wheel asset found on the latest release.")
PY
)"

"${PYTHON}" -m pipx install --python "${PYTHON}" "devsecops-pipeline-cli @ ${WHEEL_URL}"
devsecops --version
```

Pinned install for `v0.10.0`:

```bash
python3.11 -m pipx install --python python3.11 \
  "devsecops-pipeline-cli @ https://github.com/tidyOpposite/devsecops-serverless-aws-lambda/releases/download/v0.10.0/devsecops_pipeline_cli-0.10.0-py3-none-any.whl"
devsecops --version
```

Development installs still use the local checkout:

```bash
python3 -m pip install -e .
devsecops --version
```

## Upgrade

Install the latest wheel again with `--force`, then validate local config before
rendering or applying anything:

```bash
PYTHON="${PYTHON:-python3.11}"
"${PYTHON}" -m pipx install --force --python "${PYTHON}" "devsecops-pipeline-cli @ ${WHEEL_URL}"
devsecops --version
devsecops config validate
devsecops config diff
devsecops render --dry-run
```

Read [Upgrade guide](upgrade-guide.md) before upgrading across a release that
changes `.devsecops-pipeline.toml` schema behavior.

## Verify Release Artifacts

Every GitHub Release includes `SHA256SUMS` for the wheel and source
distribution.

```bash
VERSION="0.10.0"
TAG="v${VERSION}"
BASE_URL="https://github.com/tidyOpposite/devsecops-serverless-aws-lambda/releases/download/${TAG}"
WHEEL="devsecops_pipeline_cli-${VERSION}-py3-none-any.whl"

curl -fsSLO "${BASE_URL}/SHA256SUMS"
curl -fsSLO "${BASE_URL}/${WHEEL}"
grep " ${WHEEL}$" SHA256SUMS > "${WHEEL}.SHA256SUMS"
shasum -a 256 -c "${WHEEL}.SHA256SUMS"

python3.11 -m pipx install --python python3.11 "./${WHEEL}"
devsecops --version
```

On Linux, `sha256sum -c "${WHEEL}.SHA256SUMS"` is equivalent.

## Shell Completion

The CLI can print completion scripts for common shells.

Bash:

```bash
mkdir -p ~/.local/share/bash-completion/completions
devsecops completion bash > ~/.local/share/bash-completion/completions/devsecops
```

Zsh:

```bash
mkdir -p ~/.zfunc
devsecops completion zsh > ~/.zfunc/_devsecops
printf '\nfpath=(~/.zfunc $fpath)\nautoload -Uz compinit\ncompinit\n' >> ~/.zshrc
```

Fish:

```bash
mkdir -p ~/.config/fish/completions
devsecops completion fish > ~/.config/fish/completions/devsecops.fish
```

For a different binary name, pass `--program`:

```bash
devsecops completion bash --program devsecops-dev
```

## Compatibility Matrix

| Component | Supported versions | Release gate |
| --- | --- | --- |
| Ubuntu Linux | Ubuntu 22.04 LTS and 24.04 LTS on x86_64 or arm64 | GitHub Actions runs package, unit, golden, install, Terraform fmt, init, and validate checks on Ubuntu. |
| macOS | macOS 13 or newer on Intel or Apple Silicon | Supported for local CLI usage with Python, Terraform, AWS CLI, GitHub CLI, and shell completion installed by the operator. |
| Windows | WSL2 Ubuntu is supported. Native Windows is not a `v0.10.0` release target. | Native PowerShell/CMD completion and path behavior are not release-gated. |
| Python | 3.11, 3.12, and 3.13 | CLI tests, packaging, and install smoke run in CI for all supported Python versions. |
| Terraform CLI | 1.5.0 or newer | Terraform `required_version` is `>= 1.5.0`; CI validates with the pinned workflow version. |
| GitHub CLI | 2.45.0 or newer | Required for `gh variable`, `gh secret`, `gh api`, and `gh run` workflows. Missing-tool behavior is tested; run `devsecops doctor github` in real repositories. |
| AWS CLI | 2.15.0 or newer | Required for AWS diagnostics, backend checks, ECR inspection, Lambda outputs, and health-adjacent workflows. AWS CLI v1 is not supported. |

Newer versions may work, but they are not considered supported until this matrix
or the CI release gate is updated.
