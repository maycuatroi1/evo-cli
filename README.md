# Evolution CLI (Develop by Dev And for Dev)

[![codecov](https://codecov.io/gh/maycuatroi/evo-cli/branch/main/graph/badge.svg?token=evo-cli_token_here)](https://codecov.io/gh/maycuatroi/evo-cli)
[![CI](https://github.com/maycuatroi/evo-cli/actions/workflows/main.yml/badge.svg)](https://github.com/maycuatroi/evo-cli/actions/workflows/main.yml)

Awesome evo_cli created by maycuatroi

## Install it from PyPI

```bash
pip install evo_cli
```

## Usage

```py
from evo_cli import BaseClass
from evo_cli import base_function

BaseClass().base_method()
base_function()
```

```bash
$ python -m evo_cli
#or
$ evo_cli
```

## Development

Read the [CONTRIBUTING.md](CONTRIBUTING.md) file.

## Automatic Release to PyPI

This project is configured to automatically release to PyPI when changes are pushed to the main branch. The process:

1. Automatically bumps the version based on commit messages:
   - Commits with "BREAKING CHANGE" trigger a major version bump
   - Commits with "feat" trigger a minor version bump
   - All other commits trigger a patch version bump

2. Builds and publishes the package to PyPI

3. Creates a GitHub release with auto-generated release notes

### Setup Requirements

To enable automatic PyPI releases, you need to:

1. Create a PyPI API token:
   - Go to https://pypi.org/manage/account/token/
   - Create a new token with scope "Entire account"
   - Copy the token value

2. Add the token to your GitHub repository secrets:
   - Go to your GitHub repository → Settings → Secrets and variables → Actions
   - Create a new repository secret named `PYPI_API_TOKEN`
   - Paste your PyPI token as the value

After these steps, the automated release process will work whenever changes are pushed to the main branch.
