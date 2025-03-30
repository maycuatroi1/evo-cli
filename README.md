# Evolution CLI (Develop by Dev And for Dev)

[![codecov](https://codecov.io/gh/maycuatroi/evo-cli/branch/main/graph/badge.svg?token=evo-cli_token_here)](https://codecov.io/gh/maycuatroi/evo-cli)
[![CI](https://github.com/maycuatroi/evo-cli/actions/workflows/main.yml/badge.svg)](https://github.com/maycuatroi/evo-cli/actions/workflows/main.yml)

Awesome evo_cli created by maycuatroi

## Install it from PyPI

```bash
pip install evo_cli
```

### Available Commands

#### SSH Setup

Set up SSH with key-based authentication:

```bash
evo setupssh
```

Options:
- `-H, --host` - SSH server hostname or IP address
- `-u, --user` - SSH username
- `-p, --password` - SSH password (not recommended, use interactive mode instead)
- `-i, --identity` - Path to existing identity file to use
- `--help-examples` - Show usage examples

#### Miniconda Installation

Install Miniconda with cross-platform support:

```bash
evo miniconda
```

Options:
- `-p, --prefix` - Installation directory (default: ~/miniconda3 or %USERPROFILE%\miniconda3)
- `-f, --force` - Force reinstallation even if Miniconda is already installed
- `--help-examples` - Show usage examples
