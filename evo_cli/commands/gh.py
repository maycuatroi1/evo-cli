"""Install the GitHub CLI (gh) and report whether it is authenticated."""

import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import rich_click as click
from rich.panel import Panel
from rich.text import Text

from evo_cli.console import (
    CommandError,
    console,
    download_file,
    error,
    info,
    resolve_executable,
    run_command,
    step,
    success,
    warning,
)

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo setup gh[/cyan]                    install gh, then check its auth status\n"
    "  [cyan]evo setup gh --reinstall[/cyan]        install again even if gh is present\n"
    "  [cyan]evo setup gh --user[/cyan]             install into ~/.local/bin; never touch the system\n"
    "  [cyan]evo setup gh --skip-auth-check[/cyan]  install only; do not run `gh auth status`"
)

RELEASES_URL = "https://github.com/cli/cli/releases/latest"
DOWNLOAD_URL = "https://github.com/cli/cli/releases/download"

# Where a userspace install puts the binary. Not on PATH in every shell, so
# callers have to check.
USER_BIN = Path.home() / ".local" / "bin"

# uname machine -> the arch slug used in gh's release asset names.
ARCH_SLUGS = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv6l": "armv6",
    "armv7l": "armv6",
    "i386": "386",
    "i686": "386",
}

# gh is not in the default Debian/Ubuntu repositories, so the official
# instructions add GitHub's keyring and apt source first. Kept close to upstream:
# https://github.com/cli/cli/blob/trunk/docs/install_linux.md
APT_SCRIPT = """set -e
(type -p wget >/dev/null || ($SUDO apt update && $SUDO apt install wget -y))
$SUDO mkdir -p -m 755 /etc/apt/keyrings
out=$(mktemp)
wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg
cat $out | $SUDO tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null
$SUDO chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
$SUDO mkdir -p -m 755 /etc/apt/sources.list.d
echo "deb [arch=$(dpkg --print-architecture) \
signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] \
https://cli.github.com/packages stable main" | $SUDO tee /etc/apt/sources.list.d/github-cli.list > /dev/null
$SUDO apt update
$SUDO apt install gh -y
"""

# Fedora ships gh in its own repositories; only fall back to GitHub's rpm repo
# when the distro does not carry the package.
DNF_SCRIPT = """$SUDO dnf install -y gh || {
  $SUDO dnf install -y dnf5-plugins
  $SUDO dnf config-manager addrepo --from-repofile=https://cli.github.com/packages/rpm/gh-cli.repo
  $SUDO dnf install -y gh
}
"""

ZYPPER_SCRIPT = """set -e
$SUDO zypper addrepo https://cli.github.com/packages/rpm/gh-cli.repo
$SUDO zypper ref
$SUDO zypper install -y gh
"""


def is_root():
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() == 0


def can_elevate():
    """Whether privileged package-manager commands are possible at all.

    Containers commonly run as an unprivileged user with no sudo installed (the
    Jupyter `jovyan` image, for one), so every system-wide install path is out
    and only a userspace install can work.
    """
    return is_root() or bool(shutil.which("sudo"))


def shell_script(script):
    """Wrap a package-manager script, defining $SUDO to '' when already root."""
    sudo = "" if is_root() else "sudo"
    return ["bash", "-c", f"SUDO={sudo}\n{script}"]


def elevated(argv):
    """Prefix a command with sudo unless we are already root."""
    return list(argv) if is_root() else ["sudo", *argv]


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
        return shell_script(APT_SCRIPT)
    if shutil.which("dnf"):
        return shell_script(DNF_SCRIPT)
    if shutil.which("pacman"):
        return elevated(["pacman", "-S", "--noconfirm", "github-cli"])
    if shutil.which("apk"):
        return elevated(["apk", "add", "github-cli"])
    if shutil.which("zypper"):
        return shell_script(ZYPPER_SCRIPT)
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
    """The system-wide gh install command for this platform, or None if unusable."""
    system = platform.system()
    if system == "Darwin":
        return ["brew", "install", "gh"] if shutil.which("brew") else None
    if system == "Linux":
        return linux_install_command() if can_elevate() else None
    if system == "Windows":
        return windows_install_command()
    return None


def latest_gh_version():
    """Resolve the latest gh version from the releases redirect.

    /releases/latest redirects to /releases/tag/vX.Y.Z, so the version can be
    read without spending an unauthenticated GitHub API call (60/hour per IP).
    """
    request = urllib.request.Request(RELEASES_URL, headers={"User-Agent": "evo-cli"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final_url = response.geturl()
    except OSError as exc:
        warning(f"Could not reach {RELEASES_URL}: {exc}")
        return None
    match = re.search(r"/tag/v?(\d+\.\d+\.\d+)", final_url or "")
    return match.group(1) if match else None


def release_arch():
    return ARCH_SLUGS.get(platform.machine().lower())


def asset_url(version, arch, system):
    """URL of the official release archive for this platform."""
    if system == "Darwin":
        return f"{DOWNLOAD_URL}/v{version}/gh_{version}_macOS_{arch}.zip"
    return f"{DOWNLOAD_URL}/v{version}/gh_{version}_linux_{arch}.tar.gz"


def extract_gh_binary(archive, destination):
    """Pull just bin/gh out of the release archive and write it to destination.

    Only the one member is read, so a hostile archive cannot write outside the
    destination the way a blanket extractall() could.
    """
    if str(archive).endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            names = [n for n in bundle.namelist() if n.endswith("bin/gh")]
            if not names:
                return False
            with bundle.open(names[0]) as source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target)
    else:
        with tarfile.open(archive) as bundle:
            members = [m for m in bundle.getmembers() if m.name.endswith("bin/gh") and m.isfile()]
            if not members:
                return False
            source = bundle.extractfile(members[0])
            if source is None:
                return False
            with source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target)

    destination.chmod(0o755)
    return True


def ensure_user_bin_on_path():
    """Make a just-installed ~/.local/bin/gh visible to this process.

    The user's shell has not re-read PATH yet, so without this the auth check
    below would not find the binary we just wrote.
    """
    entries = os.environ.get("PATH", "").split(os.pathsep)
    if str(USER_BIN) in entries:
        return True
    os.environ["PATH"] = f"{USER_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
    return False


def install_from_release():
    """Install gh into ~/.local/bin straight from the official release archive.

    This is the path for machines where a system-wide install is impossible: no
    root, no sudo, no usable package manager. It needs no privileges because it
    only writes under the user's home.
    """
    step("Installing gh from the official release")
    system = platform.system()
    if system not in ("Linux", "Darwin"):
        error(f"No userspace install available for {system}.")
        return False

    arch = release_arch()
    if not arch:
        error(f"Unsupported architecture: {platform.machine()}")
        return False

    version = latest_gh_version()
    if not version:
        error("Could not work out the latest gh version.")
        return False

    url = asset_url(version, arch, system)
    info(f"Downloading gh {version} ({system.lower()}/{arch})")
    USER_BIN.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        archive = Path(tmpdir) / url.rsplit("/", 1)[-1]
        try:
            download_file(url, archive, description=f"gh {version}")
        except OSError as exc:
            error(f"Download failed: {exc}")
            return False

        if not extract_gh_binary(archive, USER_BIN / "gh"):
            error(f"No gh binary inside {archive.name}")
            return False

    already_on_path = ensure_user_bin_on_path()
    success(f"gh {version} installed to [accent]{USER_BIN / 'gh'}[/accent]")
    if not already_on_path:
        warning(f"{USER_BIN} is not on your PATH")
        info('Add this to your shell rc: [accent]export PATH="$HOME/.local/bin:$PATH"[/accent]')
    return True


def install_gh(reinstall=False, user=False):
    """Install the GitHub CLI. Returns True when `gh` ends up runnable."""
    step("Installing GitHub CLI")

    version = gh_version()
    if version and not reinstall:
        info(f"gh already installed ({version}); skipping install")
        return True

    if user:
        info("Installing into ~/.local/bin as requested")
        return install_from_release()

    command = install_command()
    if command is None:
        if platform.system() == "Linux" and not can_elevate():
            warning("No root and no sudo, so a system-wide install is not possible")
        else:
            warning("No usable package manager found")
        info("Falling back to a userspace install in ~/.local/bin")
        return install_from_release()

    try:
        run_command(command, status="Installing gh", stdin=subprocess.DEVNULL, timeout=900)
    except CommandError:
        warning("The package-manager install failed; falling back to ~/.local/bin")
        return install_from_release()

    if not gh_version():
        warning("gh installed but it is not on PATH yet")
        info("Open a new terminal, then run `gh --version` to confirm.")
        return False

    success(f"gh installed ({gh_version()})")
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
            Text.from_markup("\n".join(lines)),
            title="setup gh complete",
            border_style="success",
            expand=False,
        )
    )


@click.command("gh", epilog=EPILOG)
@click.option("--reinstall", is_flag=True, help="Install even if gh is already present.")
@click.option(
    "--user",
    is_flag=True,
    help="Install into ~/.local/bin from the official release; never touch system packages.",
)
@click.option("--skip-auth-check", is_flag=True, help="Skip the `gh auth status` check afterwards.")
def setup_gh(reinstall, user, skip_auth_check):
    """Install the GitHub CLI (gh) and check whether it is signed in.

    Uses the package manager for your platform - Homebrew on macOS, apt/dnf/
    pacman/apk/zypper on Linux, winget/scoop/choco on Windows - following
    GitHub's official instructions (on Debian/Ubuntu that means adding GitHub's
    keyring and apt source, since gh is not in the default repos).

    Where a system-wide install is impossible - a container with no root and no
    sudo - it falls back to downloading the official release into ~/.local/bin,
    which needs no privileges. Force that path with --user.

    An existing install is left alone unless --reinstall is passed. Signing in is
    left to you: `gh auth login` needs an interactive terminal and a browser, so
    this command only reports whether credentials are already present.
    """
    step("evo setup gh")
    installed = install_gh(reinstall, user)
    authenticated = False
    if installed and not skip_auth_check:
        authenticated = check_gh_auth()
    print_summary(installed, authenticated, auth_checked=not skip_auth_check)
