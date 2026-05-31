import json
import re
import shutil
import subprocess
from pathlib import Path

import rich_click as click
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from evo_cli.console import CommandError, console, error, info, run_command, step, success, warning

# Claude Code 2.1.154 through 2.1.158 ship a streaming tool-result delivery
# regression: commands execute correctly but their output is returned to the
# model empty, duplicated, or out of order. See
# https://gist.github.com/0xdhx/d7086a66b48bbdf6047950a1707801cc
AFFECTED_MIN = (2, 1, 154)
AFFECTED_MAX = (2, 1, 158)
GOOD_VERSION = "2.1.153"

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo f-claude[/cyan]                  check, then fix if affected (asks first)\n"
    "  [cyan]evo f-claude --check[/cyan]          diagnose only, change nothing\n"
    "  [cyan]evo f-claude -y[/cyan]               fix without the confirmation prompt\n"
    "  [cyan]evo f-claude --pin-version 2.1.153[/cyan]\n"
    "  [cyan]evo f-claude --no-downgrade[/cyan]   only disable the auto-updater\n"
    "  [cyan]evo f-claude --force[/cyan]          apply the fix regardless of version\n"
    "  [cyan]evo f-claude --unpin[/cyan]          undo the fix: re-enable updates, install latest"
)


def claude_binary():
    return shutil.which("claude")


def parse_version(text):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def version_str(version):
    return ".".join(str(part) for part in version) if version else "unknown"


def probe_version():
    """Read the installed Claude Code version without the noisy command echo."""
    try:
        result = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_version(result.stdout) or parse_version(result.stderr)


def is_affected(version):
    return bool(version) and AFFECTED_MIN <= version <= AFFECTED_MAX


def settings_path():
    return Path.home() / ".claude" / "settings.json"


def read_settings(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def autoupdater_disabled(settings):
    value = settings.get("env", {}).get("DISABLE_AUTOUPDATER")
    return str(value) == "1"


def disable_autoupdater():
    """Add env.DISABLE_AUTOUPDATER=1 to settings.json, backing it up first."""
    path = settings_path()
    try:
        settings = read_settings(path)
    except json.JSONDecodeError as exc:
        error(f"{path} is not valid JSON ({exc}); fix it by hand, leaving it untouched.")
        return False

    if autoupdater_disabled(settings):
        info("Auto-updater already disabled in settings.json")
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(".json.bak")
        shutil.copy2(path, backup)
        info(f"Backed up settings to [accent]{backup}[/accent]")

    env = settings.setdefault("env", {})
    env["DISABLE_AUTOUPDATER"] = "1"
    path.write_text(json.dumps(settings, indent=2) + "\n")
    success("Disabled the auto-updater (env.DISABLE_AUTOUPDATER=1)")
    return True


def enable_autoupdater():
    """Remove env.DISABLE_AUTOUPDATER from settings.json, backing it up first."""
    path = settings_path()
    try:
        settings = read_settings(path)
    except json.JSONDecodeError as exc:
        error(f"{path} is not valid JSON ({exc}); fix it by hand, leaving it untouched.")
        return False

    if not autoupdater_disabled(settings):
        info("Auto-updater was not pinned in settings.json")
        return True

    backup = path.with_suffix(".json.bak")
    shutil.copy2(path, backup)
    info(f"Backed up settings to [accent]{backup}[/accent]")

    env = settings.get("env", {})
    env.pop("DISABLE_AUTOUPDATER", None)
    if not env:
        settings.pop("env", None)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    success("Re-enabled the auto-updater (removed env.DISABLE_AUTOUPDATER)")
    return True


def install_version(target):
    """Install the given Claude Code build and respawn background sessions."""
    step(f"Install Claude Code {target}")
    try:
        run_command(["claude", "install", target])
    except CommandError:
        error(f"`claude install {target}` failed. Your previous version is unchanged.")
        return False

    step("Respawn background sessions")
    # No background sessions is fine, so do not treat a non-zero exit as fatal.
    run_command(["claude", "respawn", "--all"], check=False)
    return True


def render_status(version, affected, updater_disabled):
    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column(style="info", no_wrap=True)
    table.add_column()
    table.add_row("Installed version", f"[accent]{version_str(version)}[/accent]")
    table.add_row("Affected range", f"{version_str(AFFECTED_MIN)} - {version_str(AFFECTED_MAX)}")
    verdict = "[error]AFFECTED[/error]" if affected else "[success]not affected[/success]"
    table.add_row("Status", verdict)
    updater = "[success]disabled[/success]" if updater_disabled else "[warning]enabled[/warning]"
    table.add_row("Auto-updater", updater)
    console.print(table)


def print_aftercare(pinned_version):
    console.print()
    console.print(
        Panel(
            Text.from_markup(
                f"Pinned to [accent]{pinned_version}[/accent] with the auto-updater off.\n\n"
                "[bold]Trade-offs of staying on 2.1.153[/bold]\n"
                "  - top model becomes Opus 4.7 (no 4.8)\n"
                "  - no dynamic workflows / plugin auto-loading / auto mode on Bedrock-Vertex-Foundry\n\n"
                "[bold]If you must stay on an affected build, mitigate[/bold]\n"
                "  - redirect output to a file, then read it back\n"
                "  - use stdbuf -oL / unbuffer / PYTHONUNBUFFERED=1\n"
                "  - prefer serial tool calls or smaller batches\n\n"
                "[dim]Re-enable updates later by removing DISABLE_AUTOUPDATER and running "
                "`claude install latest`.[/dim]"
            ),
            title="f-claude complete",
            border_style="success",
            expand=False,
        )
    )


def run_unpin(check_only, yes):
    console.print()
    if check_only:
        info("Run `evo f-claude --unpin` to remove the pin and update to the latest build.")
        return
    info("Planned actions: re-enable the auto-updater, install Claude Code latest, respawn background sessions.")
    if not yes and not Confirm.ask("[accent]Proceed?[/accent]", default=True):
        warning("Aborted; nothing was changed.")
        return

    step("Re-enable auto-updater")
    if not enable_autoupdater():
        return
    if not install_version("latest"):
        return

    step("Verify")
    new_version = probe_version()
    info(f"Now running [accent]{version_str(new_version)}[/accent]")
    if new_version and is_affected(new_version):
        warning("Heads up: the latest build is still in the affected range. Re-run `evo f-claude` to pin again.")
        return
    success("Unpinned: auto-updates re-enabled and updated to the latest build.")


def run_fix_claude(check_only, pin_version, no_downgrade, yes, force, unpin):
    if not claude_binary():
        error("`claude` was not found on PATH. Is Claude Code installed?")
        return

    version = probe_version()
    if not version:
        error("Could not read the Claude Code version from `claude --version`.")
        return

    try:
        settings = read_settings(settings_path())
        updater_disabled = autoupdater_disabled(settings)
    except json.JSONDecodeError:
        updater_disabled = False

    affected = is_affected(version)
    render_status(version, affected, updater_disabled)

    if unpin:
        run_unpin(check_only, yes)
        return

    if check_only:
        console.print()
        if affected:
            warning("This version is affected. Run `evo f-claude` to fix it.")
        else:
            success("Nothing to fix.")
            if not updater_disabled:
                info("Tip: `evo f-claude --no-downgrade` pins the auto-updater so it cannot jump to a bad build.")
        return

    if not (affected or force):
        console.print()
        success("Your Claude Code version is not affected; no fix applied.")
        if not updater_disabled:
            info("Pass --force to apply the fix anyway, or --no-downgrade to just disable auto-update.")
        return

    plan = ["disable the auto-updater"]
    if not no_downgrade:
        plan.append(f"install Claude Code {pin_version}")
        plan.append("respawn background sessions")
    console.print()
    info("Planned actions: " + ", ".join(plan) + ".")
    if not yes and not Confirm.ask("[accent]Proceed?[/accent]", default=True):
        warning("Aborted; nothing was changed.")
        return

    step("Disable auto-updater")
    if not disable_autoupdater():
        return

    if no_downgrade:
        console.print()
        success("Auto-updater disabled. Skipped the downgrade as requested.")
        return

    if not install_version(pin_version):
        return

    step("Verify")
    new_version = probe_version()
    info(f"Now running [accent]{version_str(new_version)}[/accent]")
    if new_version and is_affected(new_version):
        warning("Still on an affected version. The downgrade may not have taken effect.")
        return
    if parse_version(pin_version) and new_version != parse_version(pin_version):
        warning(f"Expected {pin_version} but found {version_str(new_version)}.")

    print_aftercare(pin_version)


@click.command("f-claude", epilog=EPILOG)
@click.option("-c", "--check", "check_only", is_flag=True, help="Diagnose only; make no changes.")
@click.option(
    "--pin-version",
    default=GOOD_VERSION,
    show_default=True,
    help="Known-good version to install when downgrading.",
)
@click.option("--no-downgrade", is_flag=True, help="Only disable the auto-updater; skip the reinstall.")
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("-f", "--force", is_flag=True, help="Apply the fix even if the version is not in the affected range.")
@click.option("--unpin", is_flag=True, help="Undo the fix: re-enable the auto-updater and install the latest build.")
def f_claude(check_only, pin_version, no_downgrade, yes, force, unpin):
    """Check for the Claude Code 2.1.154-2.1.158 tool-result bug and fix it.

    Those builds execute commands correctly but corrupt the results returned to
    the model (empty, duplicated, or out-of-order delivery). This command
    detects an affected version, disables the auto-updater in
    `~/.claude/settings.json`, downgrades to a known-good build, respawns
    background sessions, and verifies the result.

    Use `--unpin` to reverse the fix once a fixed build ships: it re-enables the
    auto-updater and installs the latest version.
    """
    step("evo f-claude")
    run_fix_claude(check_only, pin_version, no_downgrade, yes, force, unpin)
