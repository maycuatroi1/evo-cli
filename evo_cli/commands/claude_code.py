"""Install Claude Code and register its default MCP servers."""

import os
import platform
import shutil
import subprocess
from pathlib import Path

import rich_click as click
from rich.panel import Panel
from rich.text import Text

from evo_cli.commands.fix_claude import is_affected, probe_version, version_str
from evo_cli.commands.gh import check_gh_auth, install_gh
from evo_cli.commands.mcp import add_to_claude
from evo_cli.console import CommandError, console, error, info, run_command, step, success, warning
from evo_cli.mcp_registry import MCP_REGISTRY

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo setup claude[/cyan]                        install Claude Code + gh + the default MCP servers\n"
    "  [cyan]evo setup claude --skip-install[/cyan]         only register MCP servers\n"
    "  [cyan]evo setup claude --no-mcp --no-gh[/cyan]       only install the CLI\n"
    "  [cyan]evo setup claude --mcp playwright --mcp github[/cyan]\n"
    "  [cyan]evo setup claude --method npm[/cyan]           install via npm instead of the native installer\n"
    "  [cyan]evo setup claude --install-version 2.1.153[/cyan]\n"
    "  [cyan]evo setup claude --reinstall[/cyan]            reinstall even if already present"
)

INSTALL_SH = "https://claude.ai/install.sh"
INSTALL_PS1 = "https://claude.ai/install.ps1"
NPM_PACKAGE = "@anthropic-ai/claude-code"

# Claude Code already ships web search, file editing and shell tools, so the
# defaults only cover what it cannot do on its own: drive a real browser
# (playwright) and pull current library docs (context7). Add any other server
# from the library later with `evo mcp add <name>`.
DEFAULT_MCP_SERVERS = ("playwright", "context7")

# The native installer drops the binary in one of these; neither is on PATH until
# a new shell is started.
LOCAL_BIN_DIRS = (
    Path.home() / ".local" / "bin",
    Path.home() / ".claude" / "local",
)


def is_windows():
    return platform.system() == "Windows"


def ensure_claude_on_path():
    """Return the `claude` binary, making a fresh install visible to this process.

    The native installer writes to ~/.local/bin, which the shell that launched
    evo has not picked up yet, so `claude` stays invisible until the user opens a
    new terminal. Prepend the install dir to PATH so the MCP and doctor steps can
    still run in this same invocation.
    """
    found = shutil.which("claude")
    if found:
        return found

    binary = "claude.exe" if is_windows() else "claude"
    for directory in LOCAL_BIN_DIRS:
        candidate = directory / binary
        if candidate.exists():
            os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"
            return str(candidate)
    return None


def native_install_command(version=None):
    """Build the official installer command for this platform."""
    if is_windows():
        return ["powershell", "-NoProfile", "-Command", f"irm {INSTALL_PS1} | iex"]
    script = f"curl -fsSL {INSTALL_SH} | bash"
    if version:
        script += f" -s {version}"
    return ["bash", "-c", script]


def npm_install_command(version=None):
    package = f"{NPM_PACKAGE}@{version}" if version else NPM_PACKAGE
    return ["npm", "install", "-g", package]


def native_install_available(version=None):
    """Whether the native installer can run here.

    install.ps1 takes no version argument, so a pinned version on Windows has to
    go through npm instead.
    """
    if is_windows():
        return bool(shutil.which("powershell")) and not version
    return bool(shutil.which("curl") and shutil.which("bash"))


def install_attempts(method, version):
    """Ordered (label, command) install attempts for the requested method."""
    attempts = []
    if method in ("auto", "native") and native_install_available(version):
        attempts.append(("native installer", native_install_command(version)))
    if method in ("auto", "npm") and shutil.which("npm"):
        attempts.append(("npm", npm_install_command(version)))
    return attempts


def install_claude(method="auto", version=None, reinstall=False):
    """Install the Claude Code CLI. Returns True when `claude` ends up runnable."""
    step("Installing Claude Code")

    if ensure_claude_on_path() and not reinstall:
        info(f"Claude Code already installed ({version_str(probe_version())}); skipping install")
        info("Pass --reinstall to install it again, or run `claude update` to update.")
        return True

    attempts = install_attempts(method, version)
    if not attempts:
        error(f"No usable install method for --method {method} on {platform.system()}.")
        if is_windows():
            info(f"Install manually: [accent]irm {INSTALL_PS1} | iex[/accent] (PowerShell)")
        else:
            info(f"Install manually: [accent]curl -fsSL {INSTALL_SH} | bash[/accent]")
        return False

    for label, command in attempts:
        info(f"Installing via {label}")
        try:
            run_command(command, status=f"Installing Claude Code via {label}", timeout=600)
        except CommandError as exc:
            warning(f"Install via {label} failed: {exc}")
            continue

        if ensure_claude_on_path():
            success(f"Claude Code installed ({version_str(probe_version())})")
            return True
        warning(f"Install via {label} finished but `claude` is not on PATH")

    error("Could not install Claude Code.")
    info("Open a new shell and retry, or add the install directory (~/.local/bin) to PATH.")
    return False


def configure_mcp_servers(names, scope):
    """Register the named library MCP servers with Claude Code."""
    added = []
    for name in names:
        if add_to_claude(name, MCP_REGISTRY[name], scope):
            added.append(name)
    return added


def run_doctor():
    """Run Claude Code's own diagnostic. Never fatal: it is a report, not a gate."""
    step("Verifying installation")
    result = run_command(
        ["claude", "doctor"],
        status="Running claude doctor",
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    if getattr(result, "returncode", None) == 0:
        success("`claude doctor` reported no problems")
    else:
        warning("`claude doctor` reported problems; see its output above")


def setup_github_cli():
    """Install gh and report its auth state. Returns one of: ready, unauthenticated, missing.

    Claude Code shells out to `gh` for PRs, issues and the GitHub API, so a fresh
    machine wants it too. A failure here is not fatal: Claude Code still works,
    it just cannot touch GitHub until gh is installed and signed in.
    """
    if not install_gh():
        return "missing"
    return "ready" if check_gh_auth() else "unauthenticated"


def print_summary(installed, added, scope, gh_state=None):
    console.print()
    if not installed:
        console.print(
            Panel(
                "Claude Code is not installed, so nothing was configured.\n\n"
                f"Install it with [accent]curl -fsSL {INSTALL_SH} | bash[/accent] "
                "(or [accent]evo setup claude --method npm[/accent]), then re-run this command.",
                title="setup claude incomplete",
                border_style="warning",
                expand=False,
            )
        )
        return

    version = probe_version()
    lines = [f"Claude Code [accent]{version_str(version)}[/accent] is ready."]
    if added:
        lines.append(f"MCP servers registered ({scope} scope): [accent]{', '.join(added)}[/accent]")
        lines.append("Run [accent]/mcp[/accent] inside Claude Code to authenticate any server that needs OAuth.")
    lines.append("")
    lines.append("Run [accent]claude[/accent] to start; the first run opens a browser to sign in.")
    lines.append(
        "Add more MCP servers anytime with [accent]evo mcp add <name>[/accent] ([accent]evo mcp list[/accent])."
    )
    if gh_state == "unauthenticated":
        lines.append("Sign in to GitHub with [accent]gh auth login[/accent] so Claude Code can open PRs.")
    elif gh_state == "missing":
        lines.append("[warning]GitHub CLI is missing; retry with [accent]evo setup gh[/accent].[/warning]")
    if is_affected(version):
        lines.append("")
        lines.append(
            "[warning]This build has the tool-result bug; run [accent]evo f-claude[/accent] to fix it.[/warning]"
        )

    console.print(
        Panel(
            Text.from_markup("\n".join(lines)),
            title="setup claude complete",
            border_style="success",
            expand=False,
        )
    )


def run_setup_claude(skip_install, reinstall, method, install_version, mcp_names, no_mcp, scope, no_gh):
    step("evo setup claude")

    if skip_install:
        info("Skipping install as requested")
        installed = bool(ensure_claude_on_path())
        if not installed:
            error("`claude` was not found on PATH; nothing to configure.")
    else:
        installed = install_claude(method, install_version, reinstall)

    gh_state = None
    if no_gh:
        info("Skipping the GitHub CLI as requested")
    else:
        gh_state = setup_github_cli()

    added = []
    if not installed:
        pass
    elif no_mcp:
        info("Skipping MCP registration as requested")
    else:
        added = configure_mcp_servers(mcp_names or DEFAULT_MCP_SERVERS, scope)

    if installed:
        run_doctor()

    print_summary(installed, added, scope, gh_state)


@click.command("claude", epilog=EPILOG)
@click.option("--skip-install", is_flag=True, help="Skip installing the CLI; only register MCP servers.")
@click.option("--reinstall", is_flag=True, help="Install even if Claude Code is already present.")
@click.option(
    "--method",
    type=click.Choice(["auto", "native", "npm"]),
    default="auto",
    show_default=True,
    help="Install method. `auto` tries the native installer, then falls back to npm.",
)
@click.option(
    "--install-version",
    metavar="VERSION",
    help="Install a specific version instead of the latest, e.g. 2.1.153.",
)
@click.option(
    "--mcp",
    "mcp_names",
    multiple=True,
    metavar="NAME",
    help=f"MCP server from the library to register (repeatable). Default: {', '.join(DEFAULT_MCP_SERVERS)}.",
)
@click.option("--no-mcp", is_flag=True, help="Skip MCP server registration.")
@click.option("--no-gh", is_flag=True, help="Skip installing the GitHub CLI (gh).")
@click.option(
    "--scope",
    type=click.Choice(["local", "user", "project"]),
    default="user",
    show_default=True,
    help="Claude Code config scope for the MCP servers.",
)
def setup_claude(skip_install, reinstall, method, install_version, mcp_names, no_mcp, no_gh, scope):
    """Install Claude Code, the GitHub CLI, and the default MCP servers.

    Installs the CLI with Anthropic's native installer (falling back to npm),
    installs the GitHub CLI that Claude Code shells out to for PRs and issues,
    registers the MCP servers Claude Code lacks natively - playwright for browser
    automation and context7 for up-to-date library docs - and runs `claude doctor`
    to verify the result. Everything is idempotent: existing installs are left
    alone unless --reinstall is passed, and servers already registered are skipped.

    Pick your own servers with --mcp (repeatable, from `evo mcp list`), or skip
    them with --no-mcp; skip the GitHub CLI with --no-gh (`evo setup gh` installs
    it on its own). No credentials are written: `gh auth login` and OAuth MCP
    servers (/mcp inside Claude Code) stay in your hands.
    """
    unknown = [name for name in mcp_names if name not in MCP_REGISTRY]
    if unknown:
        error(f"Unknown MCP server(s): {', '.join(unknown)}. Run `evo mcp list` to see the library.")
        return

    run_setup_claude(skip_install, reinstall, method, install_version, mcp_names, no_mcp, scope, no_gh)
