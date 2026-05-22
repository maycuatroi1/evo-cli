# evo-cli

## Project

A personal-use CLI toolbox. Each `evo` subcommand automates a setup or workflow
task in some domain (dev environment, networking, security, OSINT, etc.). The
codebase is meant to grow: new needs are added as new subcommands.

## Using the CLI

Run `evo --help` to list the available tools, and `evo <command> -h` for the
options and examples of a specific command. Check these before assuming a tool
does or does not exist.

## Credentials

When a task needs credentials (API keys, tokens, passwords), load the
`/credentials-utils` skill to handle them - use it both to save new credentials
and to load existing ones. Never hardcode secrets in the repo.

## Stack

- Python 3.9+, packaged with `pyproject.toml` (setuptools backend).
- CLI built on `click` / `rich-click`; terminal output via `rich`.
- Entry point: `evo` maps to `evo_cli.__main__:main`.

## Layout

- `evo_cli/cli.py` - click group, registers all commands.
- `evo_cli/console.py` - shared rich Console, log helpers, `run_command`, `download_file`.
- `evo_cli/commands/` - one module per subcommand.

## Dev

- Install: `pip install -e .[test]`
- Lint / format: `make lint` / `make fmt` (ruff)
- Test: `make test` (pytest + coverage)
- Version lives in `evo_cli/VERSION`, bumped via `bumpversion`.

## Adding a command

1. Create `evo_cli/commands/<name>.py` with a `@click.command` function.
2. Register it in `evo_cli/cli.py` via `cli.add_command(...)`.
3. Use the helpers in `evo_cli/console.py` for consistent output.
