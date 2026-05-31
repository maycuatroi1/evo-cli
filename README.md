# Evolution CLI (Develop by Dev And for Dev)

[![codecov](https://codecov.io/gh/maycuatroi/evo-cli/branch/main/graph/badge.svg?token=evo-cli_token_here)](https://codecov.io/gh/maycuatroi/evo-cli)
[![CI](https://github.com/maycuatroi/evo-cli/actions/workflows/main.yml/badge.svg)](https://github.com/maycuatroi/evo-cli/actions/workflows/main.yml)

Awesome evo_cli created by maycuatroi

## Install it from PyPI

```bash
pip install evo-cli
```

### Available Commands

`evo` is built with [click](https://click.palletsprojects.com/) and
[rich](https://rich.readthedocs.io/). Run `evo --help` or `evo <command> -h`
for colorized help, option tables, and examples.

#### SSH Setup

Set up SSH with key-based authentication:

```bash
evo setupssh
```

Options:
- `-H, --host` - SSH server hostname or IP address
- `-u, --user` - SSH username
- `-p, --password` - SSH password (prefer the interactive prompt)
- `-P, --port` - SSH port (default: 22)
- `-i, --identity` - Existing private key to install instead of generating one

#### Miniconda Installation

Install Miniconda with cross-platform support:

```bash
evo miniconda
```

Options:
- `-p, --prefix` - Installation directory (default: ~/miniconda3 or %USERPROFILE%\miniconda3)
- `-f, --force` - Force reinstallation even if Miniconda is already installed

#### Cloudflare SSH Tunnel

Expose this Ubuntu machine's SSH server through a Cloudflare named tunnel, so you can reach it from anywhere without opening a public inbound port:

```bash
evo cfssh -H dev.example.com
```

It installs `cloudflared`, logs in to Cloudflare, creates a named tunnel, writes `/etc/cloudflared/config.yml` with an `ssh://` ingress rule, routes a proxied DNS record, and installs the `cloudflared` systemd service. Requires a Cloudflare account with a domain managed in Cloudflare.

Options:
- `-H, --hostname` - Public hostname for SSH, e.g. `dev.example.com`
- `-n, --name` - Tunnel name (default: first label of the hostname)
- `-P, --ssh-port` - Local SSH port to forward (default: 22)
- `--no-service` - Configure only, do not install the systemd service

To connect from a client machine, install `cloudflared` and add to `~/.ssh/config`:

```
Host dev.example.com
  User <your-user>
  ProxyCommand cloudflared access ssh --hostname %h
```

#### Fix Claude Code

Detect and fix the Claude Code 2.1.154-2.1.158 tool-result delivery bug (commands run but their output is returned to the model empty, duplicated, or out of order):

```bash
evo f-claude
```

It checks the installed version against the affected range, disables the auto-updater in `~/.claude/settings.json` (backing it up first), downgrades to a known-good build, respawns background sessions, and verifies the result.

Options:
- `-c, --check` - Diagnose only; make no changes
- `--pin-version` - Known-good version to install when downgrading (default: 2.1.153)
- `--no-downgrade` - Only disable the auto-updater; skip the reinstall
- `-y, --yes` - Skip the confirmation prompt
- `-f, --force` - Apply the fix even if the version is not in the affected range
- `--unpin` - Undo the fix: re-enable the auto-updater and install the latest build
