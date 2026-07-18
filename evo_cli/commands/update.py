import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import rich_click as click
from rich.table import Table
from rich.text import Text

from evo_cli import __version__
from evo_cli.console import CommandError, console, info, run_command, step, success, warning

PACKAGE = "evo_cli"
PYPI_JSON = "https://pypi.org/pypi/{package}/json"

VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)(?:[-_.]?(dev|alpha|a|beta|b|rc|c|pre)[-_.]?(\d*))?")
PRE_RANK = {"dev": -4, "alpha": -3, "a": -3, "beta": -2, "b": -2, "pre": -1, "rc": -1, "c": -1}

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo update[/cyan]              update to the newest release\n"
    "  [cyan]evo update --check[/cyan]      only report whether an update exists\n"
    "  [cyan]evo update --pre[/cyan]        include pre-release versions\n"
    "  [cyan]evo update --dry-run[/cyan]    print the commands without running them\n"
    "  [cyan]evo update --force[/cyan]      reinstall even when already current\n\n"
    "[dim]An editable checkout is updated with git (fast-forward only, refusing to touch a\n"
    "dirty tree); a pipx, uv or pip install is upgraded with its own installer.[/dim]"
)


def parse_version(value):
    match = VERSION_RE.match(str(value).strip().lower())
    if not match:
        return ((0,), 0, 0)
    release = tuple(int(part) for part in match.group(1).split("."))
    label = match.group(2)
    if label is None:
        return (release, 0, 0)
    return (release, PRE_RANK.get(label, -1), int(match.group(3) or 0))


def is_prerelease(value):
    return VERSION_RE.match(str(value).strip().lower()) is not None and parse_version(value)[1] < 0


def fetch_pypi(package, timeout):
    request = urllib.request.Request(PYPI_JSON.format(package=package), headers={"User-Agent": "evo-cli"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_version(data, allow_pre):
    candidates = []
    for version, files in (data.get("releases") or {}).items():
        if not files or all(item.get("yanked") for item in files):
            continue
        if not allow_pre and is_prerelease(version):
            continue
        candidates.append(version)
    if candidates:
        return max(candidates, key=parse_version)
    return (data.get("info") or {}).get("version")


def read_direct_url():
    try:
        dist = metadata.distribution(PACKAGE)
    except metadata.PackageNotFoundError:
        return {}
    try:
        raw = dist.read_text("direct_url.json")
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def editable_source():
    data = read_direct_url()
    if not (data.get("dir_info") or {}).get("editable"):
        return None
    url = data.get("url") or ""
    if not url.startswith("file:"):
        return None
    path = Path(url2pathname(urlparse(url).path))
    return path if path.is_dir() else None


def detect_install():
    source = editable_source()
    if source is not None:
        return {"mode": "editable", "location": source}
    prefix = Path(sys.prefix).resolve()
    parts = [part.lower() for part in prefix.parts]
    if "pipx" in parts:
        return {"mode": "pipx", "location": prefix}
    if "uv" in parts and "tools" in parts:
        return {"mode": "uv", "location": prefix}
    return {"mode": "pip", "location": prefix}


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def git_detail(result, fallback):
    lines = ((result.stderr or "") + (result.stdout or "")).strip().splitlines()
    return lines[-1] if lines else fallback


def report(install, current, latest):
    table = Table(show_header=False, box=None, expand=False)
    table.add_column(style="info", no_wrap=True)
    table.add_column()
    table.add_row("Installed", f"[accent]{current}[/accent]")
    table.add_row("Latest on PyPI", f"[accent]{latest}[/accent]" if latest else "[dim]unknown[/dim]")
    table.add_row("Install mode", install["mode"])
    table.add_row("Location", f"[dim]{install['location']}[/dim]")
    console.print(table)
    console.print()


def build_commands(mode, allow_pre):
    target = f"{PACKAGE}"
    if mode == "pipx":
        if shutil.which("pipx") is None:
            raise click.ClickException("This looks like a pipx install but pipx is not on PATH.")
        cmd = ["pipx", "upgrade", target]
        if allow_pre:
            cmd.append("--pip-args=--pre")
        return [cmd]
    if mode == "uv":
        if shutil.which("uv") is None:
            raise click.ClickException("This looks like a uv tool install but uv is not on PATH.")
        cmd = ["uv", "tool", "upgrade", target]
        if allow_pre:
            cmd.append("--prerelease=allow")
        return [cmd]
    probe = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True)
    if probe.returncode != 0:
        raise click.ClickException(f"pip is not available for {sys.executable}. Install pip or use pipx/uv.")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", target]
    if allow_pre:
        cmd.append("--pre")
    return [cmd]


def execute(commands, dry_run):
    for cmd in commands:
        if dry_run:
            console.print(f"[cmd]$ {' '.join(str(part) for part in cmd)}[/cmd]")
            continue
        try:
            run_command(cmd)
        except CommandError as exc:
            raise click.ClickException(str(exc)) from exc


def update_editable(path, check, force, dry_run):
    if shutil.which("git") is None:
        raise click.ClickException("git is not installed or is not on PATH")

    status = git(path, "status", "--porcelain")
    if status.returncode != 0:
        raise click.ClickException(f"{path} is not a git repository")

    info(f"Editable checkout at [accent]{path}[/accent]")
    if not dry_run:
        fetched = git(path, "fetch", "--prune")
        if fetched.returncode != 0:
            warning(f"git fetch failed: {git_detail(fetched, 'unknown error')}")

    upstream = git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode != 0:
        raise click.ClickException("The checkout has no upstream branch to update from.")
    tracking = upstream.stdout.strip()

    counts = git(path, "rev-list", "--left-right", "--count", "HEAD...@{u}")
    ahead, behind = 0, 0
    if counts.returncode == 0 and counts.stdout.split():
        numbers = counts.stdout.split()
        ahead, behind = int(numbers[0]), int(numbers[1])

    if behind == 0 and not force:
        success(f"Already up to date with [accent]{tracking}[/accent].")
        if ahead:
            info(f"{ahead} local commit(s) not pushed yet.")
        return

    info(f"{behind} new commit(s) on [accent]{tracking}[/accent].")
    if check:
        info("Run [accent]evo update[/accent] to apply them.")
        return
    if status.stdout.strip():
        if dry_run:
            warning("The checkout has uncommitted changes, so the update would stop here.")
            return
        raise click.ClickException("The checkout has uncommitted changes. Commit or stash them first.")

    before = git(path, "rev-parse", "HEAD").stdout.strip()
    if dry_run:
        console.print(f"[cmd]$ git -C {path} pull --ff-only --prune[/cmd]")
        return

    pulled = git(path, "pull", "--ff-only", "--prune")
    if pulled.returncode != 0:
        raise click.ClickException(f"git pull failed: {git_detail(pulled, 'unknown error')}")

    after = git(path, "rev-parse", "HEAD").stdout.strip()
    head = git(path, "log", "-1", "--oneline").stdout.strip()
    success(f"Updated to {head or after}")

    changed = git(path, "diff", "--name-only", before, after).stdout.split()
    if force or any(name in ("pyproject.toml", "setup.py", "setup.cfg") for name in changed):
        info("Dependency metadata changed, reinstalling the package.")
        execute([[sys.executable, "-m", "pip", "install", "-e", str(path), "--quiet"]], dry_run)

    version_file = path / "evo_cli" / "VERSION"
    if version_file.is_file():
        success(f"evo is now at [accent]{version_file.read_text(encoding='utf-8').strip()}[/accent]")


def update_package(mode, current, latest, check, allow_pre, force, dry_run):
    if latest is None:
        if check:
            raise click.ClickException("Cannot determine the latest version from PyPI.")
        warning("Skipping the version check and upgrading anyway.")
    elif parse_version(latest) <= parse_version(current) and not force:
        success(f"evo [accent]{current}[/accent] is already the latest release.")
        return
    elif latest:
        info(f"Update available: [accent]{current}[/accent] -> [accent]{latest}[/accent]")

    if check:
        info("Run [accent]evo update[/accent] to install it.")
        return

    execute(build_commands(mode, allow_pre), dry_run)
    if not dry_run:
        success(f"evo updated to [accent]{latest or 'the latest release'}[/accent].")
        info("Open a new shell if the `evo` command still reports the old version.")


@click.command("update", epilog=EPILOG, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-c", "--check", is_flag=True, help="Only report whether a newer version is available.")
@click.option("--pre", "allow_pre", is_flag=True, help="Consider pre-release versions.")
@click.option("-f", "--force", is_flag=True, help="Reinstall even when already up to date.")
@click.option("-n", "--dry-run", is_flag=True, help="Print the commands instead of running them.")
@click.option("--timeout", type=float, default=15.0, show_default=True, help="PyPI request timeout in seconds.")
def update(check, allow_pre, force, dry_run, timeout):
    """Update **evo** itself to the latest version.

    Detects how `evo` was installed and uses the matching updater: a git
    fast-forward for an editable checkout, otherwise `pipx upgrade`,
    `uv tool upgrade` or `pip install --upgrade`.

    Use `--check` to see what is available without changing anything.
    """
    step("evo update")
    install = detect_install()

    latest = None
    try:
        latest = latest_version(fetch_pypi(PACKAGE, timeout), allow_pre)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        warning(f"Cannot reach PyPI ({exc})")

    report(install, __version__, latest)

    if install["mode"] == "editable":
        update_editable(install["location"], check, force, dry_run)
        return
    update_package(install["mode"], __version__, latest, check, allow_pre, force, dry_run)
