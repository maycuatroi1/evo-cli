"""Install the GitHub CLI (gh) and report whether it is authenticated."""

import platform
import re
import shutil
import subprocess

import rich_click as click
from rich.panel import Panel
from rich.text import Text

from evo_cli.console import CommandError, console, error, info, resolve_executable, run_command, step, success, warning

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo setup gh[/cyan]                    install gh, then check its auth status\n"
    "  [cyan]evo setup gh --reinstall[/cyan]        install again even if gh is present\n"
    "  [cyan]evo setup gh --skip-auth-check[/cyan]  install only; do not run `gh auth status`"
)

RELEASES_URL = "https://github.com/cli/cli/releases/latest"

# gh is not in the default Debian/Ubuntu repositories, so the official
# instructions add GitHub's keyring and apt source first. Kept close to upstream:
# https://github.com/cli/cli/blob/trunk/docs/install_linux.md
APT_SCRIPT = (
    "set -e\n"
    "(type -p wget >/dev/null || (sudo apt update && sudo apt install wget -y))\n"
    "sudo mkdir -p -m 755 /etc/apt/keyrings\n"
    "out=$(mktemp)\n"
    "wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg\n"
    "cat $out | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null\n"
    "sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg\n"
    "sudo mkdir -p -m 755 /etc/apt/sources.list.d\n"
    'echo "deb [arch=$(dpkg --print-architecture) '
    "signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] "
    'https://cli.github.com/packages stable main" '
    "| sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null\n"
    "sudo apt update\n"
    "sudo apt install gh -y\n"
)

# Fedora ships gh in its own repositories; only fall back to GitHub's rpm repo
# when the distro does not carry the package.
DNF_SCRIPT = (
    "sudo dnf install -y gh || {\n"
    "  sudo dnf install -y dnf5-plugins\n"
    "  sudo dnf config-manager addrepo --from-repofile=https://cli.github.com/packages/rpm/gh-cli.repo\n"
    "  sudo dnf install -y gh\n"
    "}\n"
)

ZYPPER_SCRIPT = (
    "set -e\n"
    "sudo zypper addrepo https://cli.github.com/packages/rpm/gh-cli.repo\n"
    "sudo zypper ref\n"
    "sudo zypper install -y gh\n"
)


def gh_version():
    """Return the installed gh version string, or None if gh is not on PATH."""
    if not shutil.which("gh"):
        return None
    try:
        result = subprocess.run(
            resolve_executable(["gh", "--version"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    match = re.search(r"\d+\.\d+\.\d+", result.stdout or "")
    return match.group(0) if match else "unknown"


def linux_install_command():
    if shutil.which("apt-get"):
        return ["bash", "-c", APT_SCRIPT]
    if shutil.which("dnf"):
        return ["bash", "-c", DNF_SCRIPT]
    if shutil.which("pacman"):
        return ["sudo", "pacman", "-S", "--noconfirm", "github-cli"]
    if shutil.which("apk"):
        return ["sudo", "apk", "add", "github-cli"]
    if shutil.which("zypper"):
        return ["bash", "-c", ZYPPER_SCRIPT]
    return None


def windows_install_command():
    if shutil.which("winget"):
        # The accept flags keep winget from stopping on an interactive agreement prompt.
        return [
            "winget",
            "install",
            "--id",
            "GitHub.cli",
            "--source",
            "winget",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ]
    if shutil.which("scoop"):
        return ["scoop", "install", "gh"]
    if shutil.which("choco"):
        return ["choco", "install", "gh", "-y"]
    return None


def install_command():
    """The gh install command for this platform, or None if none is usable."""
    system = platform.system()
    if system == "Darwin":
        return ["brew", "install", "gh"] if shutil.which("brew") else None
    if system == "Linux":
        return linux_install_command()
    if system == "Windows":
        return windows_install_command()
    return None


def missing_package_manager_hint():
    system = platform.system()
    if system == "Darwin":
        info("Homebrew not found. Install it from https://brew.sh, then re-run.")
    elif system == "Linux":
        info("No supported package manager found (apt-get, dnf, pacman, apk, zypper).")
    elif system == "Windows":
        info("No supported package manager found (winget, scoop, choco).")
    else:
        info(f"Unsupported platform: {system}")
    info(f"You can also grab a binary from [accent]{RELEASES_URL}[/accent]")


def install_gh(reinstall=False):
    """Install the GitHub CLI. Returns True when `gh` ends up runnable."""
    step("Installing GitHub CLI")

    version = gh_version()
    if version and not reinstall:
        info(f"gh already installed ({version}); skipping install")
        return True

    command = install_command()
    if command is None:
        error("Could not install gh automatically.")
        missing_package_manager_hint()
        return False

    try:
        run_command(command, status="Installing gh", stdin=subprocess.DEVNULL, timeout=900)
    except CommandError as exc:
        error(f"Installing gh failed: {exc}")
        info(f"Install it manually from [accent]{RELEASES_URL}[/accent]")
        return False

    version = gh_version()
    if not version:
        warning("gh installed but it is not on PATH yet")
        info("Open a new terminal, then run `gh --version` to confirm.")
        return False

    success(f"gh installed ({version})")
    return True


def check_gh_auth():
    """Report whether gh holds GitHub credentials.

    `gh auth status` is read-only and exits non-zero when logged out, so it is
    safe here. `gh auth login` is not: it needs a TTY and a browser, so leave it
    to the user rather than blocking the run on it.
    """
    step("Checking GitHub authentication")
    result = run_command(
        ["gh", "auth", "status"],
        status="Running gh auth status",
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    if getattr(result, "returncode", None) == 0:
        success("gh is authenticated")
        return True

    warning("gh is not authenticated")
    info("Run [accent]gh auth login[/accent] in your terminal; it opens a browser to sign in.")
    return False


def print_summary(installed, authenticated, auth_checked):
    console.print()
    if not installed:
        console.print(
            Panel(
                f"gh was not installed. Install it manually from [accent]{RELEASES_URL}[/accent], then re-run.",
                title="setup gh incomplete",
                border_style="warning",
                expand=False,
            )
        )
        return

    lines = [f"GitHub CLI [accent]{gh_version()}[/accent] is ready."]
    if not auth_checked:
        lines.append("Auth check skipped; run [accent]gh auth status[/accent] to see if you are signed in.")
    elif authenticated:
        lines.append("You are signed in; `gh pr`, `gh issue` and Claude Code's GitHub workflows will work.")
    else:
        lines.append("Sign in with [accent]gh auth login[/accent] to finish setting it up.")

    console.print(
        Panel(
            "\n".join(lines),
            title="setup gh complete",
            border_style="success",
            expand=False,
        )
    )


@click.command("gh", epilog=EPILOG)
@click.option("--reinstall", is_flag=True, help="Install even if gh is already present.")
@click.option("--skip-auth-check", is_flag=True, help="Skip the `gh auth status` check afterwards.")
def setup_gh(reinstall, skip_auth_check):
    """Install the GitHub CLI (gh) and check whether it is signed in.

    Uses the package manager for your platform - Homebrew on macOS, apt/dnf/
    pacman/apk/zypper on Linux, winget/scoop/choco on Windows - following
    GitHub's official instructions (on Debian/Ubuntu that means adding GitHub's
    keyring and apt source, since gh is not in the default repos).

    An existing install is left alone unless --reinstall is passed. Signing in is
    left to you: `gh auth login` needs an interactive terminal and a browser, so
    this command only reports whether credentials are already present.
    """
    step("evo setup gh")
    installed = install_gh(reinstall)
    authenticated = False
    if installed and not skip_auth_check:
        authenticated = check_gh_auth()
    print_summary(installed, authenticated, auth_checked=not skip_auth_check)
