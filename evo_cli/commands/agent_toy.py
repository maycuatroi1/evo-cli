import os
import re
import shutil
from pathlib import Path

import rich_click as click
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from evo_cli.commands.claude_code import ensure_claude_on_path, is_windows
from evo_cli.console import CommandError, console, error, info, run_command, step, success, warning

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo setup agent-toy[/cyan]                      install every toy\n"
    "  [cyan]evo setup agent-toy --list[/cyan]               show the catalogue\n"
    "  [cyan]evo setup agent-toy plannotator[/cyan]          install a single toy\n"
    "  [cyan]evo setup agent-toy context-mode agent-skills[/cyan]\n"
    "  [cyan]evo setup agent-toy plannotator --minimal[/cyan]  binary only, no agent wiring\n"
    "  [cyan]evo setup agent-toy --scope project[/cyan]      install into the current project"
)

PLANNOTATOR_INSTALL_SH = "https://plannotator.ai/install.sh"
PLANNOTATOR_INSTALL_PS1 = "https://plannotator.ai/install.ps1"
PLANNOTATOR_MARKETPLACE = "backnotprop/plannotator"

CONTEXT_MODE_MARKETPLACE = "mksglu/context-mode"

SKILLFISH_PACKAGE = "skillfish@latest"
AGENT_SKILLS_REPO = "maycuatroi1/agent-skills"

ENTRY_RE = re.compile(r"^[^\w]*([\w][\w.@/-]*)\s*$")

INSTALLED = "installed"
PARTIAL = "partial"
FAILED = "failed"

RESULT_STYLES = {
    INSTALLED: "[success]installed[/success]",
    PARTIAL: "[warning]partial[/warning]",
    FAILED: "[error]failed[/error]",
}


def parse_entries(output):
    names = []
    for line in (output or "").splitlines():
        if ":" in line:
            continue
        match = ENTRY_RE.match(line)
        if match:
            names.append(match.group(1))
    return names


def claude_entries(*args):
    result = run_command(["claude", "plugin", *args], capture=True, check=False)
    return parse_entries(getattr(result, "stdout", ""))


def ensure_marketplace(name, source, scope):
    if name in claude_entries("marketplace", "list"):
        info(f"Marketplace [accent]{name}[/accent] already configured; refreshing it")
        run_command(
            ["claude", "plugin", "marketplace", "update", name],
            status=f"Updating marketplace {name}",
            timeout=300,
            check=False,
        )
        return True

    try:
        run_command(
            ["claude", "plugin", "marketplace", "add", source, "--scope", scope],
            status=f"Adding marketplace {source}",
            timeout=300,
        )
    except CommandError as exc:
        error(f"Could not add marketplace {source}: {exc}")
        return False
    return True


def ensure_plugin(plugin, marketplace, scope):
    target = f"{plugin}@{marketplace}"

    if target in claude_entries("list"):
        info(f"Plugin [accent]{target}[/accent] already installed; updating it")
        try:
            run_command(
                ["claude", "plugin", "update", target, "--scope", scope],
                status=f"Updating {target}",
                timeout=600,
            )
        except CommandError as exc:
            warning(f"Could not update {target}: {exc}")
        return True

    try:
        run_command(
            ["claude", "plugin", "install", target, "--scope", scope],
            status=f"Installing {target}",
            timeout=600,
        )
    except CommandError as exc:
        error(f"Could not install {target}: {exc}")
        return False

    success(f"Plugin [accent]{target}[/accent] installed")
    return True


def plannotator_install_command(minimal):
    if is_windows():
        if not shutil.which("powershell"):
            return None
        script = f"irm {PLANNOTATOR_INSTALL_PS1} | iex"
        if minimal:
            script = f"$env:PLANNOTATOR_MINIMAL=1; {script}"
        return ["powershell", "-NoProfile", "-Command", script]

    if not (shutil.which("curl") and shutil.which("bash")):
        return None
    script = f"curl -fsSL {PLANNOTATOR_INSTALL_SH} | bash"
    if minimal:
        script += " -s -- --minimal"
    return ["bash", "-c", script]


def plannotator_binary():
    found = shutil.which("plannotator")
    if found:
        return found

    candidates = []
    if is_windows():
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "plannotator" / "plannotator.exe")
        candidates.append(Path.home() / ".local" / "bin" / "plannotator.exe")
    else:
        candidates.append(Path.home() / ".local" / "bin" / "plannotator")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def run_plannotator_installer(minimal, attempts=2):
    command = plannotator_install_command(minimal)
    if command is None:
        error("No usable installer here: PowerShell (Windows) or curl + bash (POSIX) is required.")
        return False

    for attempt in range(1, attempts + 1):
        try:
            run_command(command, status="Running the plannotator installer", timeout=900)
            return True
        except CommandError as exc:
            if attempt < attempts:
                warning(f"The plannotator installer failed ({exc}); retrying")
            else:
                error(f"The plannotator installer failed: {exc}")
    return False


def install_plannotator(options):
    plugin_ok = True
    if options["minimal"]:
        info("Minimal install: no plugin, skills, hooks or slash commands")
    elif not options["claude"]:
        warning("Claude Code is not on PATH; skipping its plugin. Run `evo setup claude` first.")
        plugin_ok = False
    else:
        plugin_ok = ensure_marketplace("plannotator", PLANNOTATOR_MARKETPLACE, options["scope"]) and ensure_plugin(
            "plannotator", "plannotator", options["scope"]
        )

    installed = run_plannotator_installer(options["minimal"])
    if not installed:
        binary = plannotator_binary()
        if not binary:
            return FAILED
        warning(f"The installer errored but the binary is in place ({binary})")
        warning("Skills and hooks for the other agents may be missing; re-run to finish them.")

    return INSTALLED if (plugin_ok and installed) else PARTIAL


def install_context_mode(options):
    if not options["claude"]:
        error("Claude Code is not on PATH; run `evo setup claude` first.")
        return FAILED

    if not ensure_marketplace("context-mode", CONTEXT_MODE_MARKETPLACE, options["scope"]):
        return FAILED
    return INSTALLED if ensure_plugin("context-mode", "context-mode", options["scope"]) else FAILED


def install_agent_skills(options):
    if not shutil.which("npx"):
        error("npx was not found; install Node.js 18+ and retry.")
        return FAILED

    command = [
        "npx",
        "-y",
        SKILLFISH_PACKAGE,
        "add",
        AGENT_SKILLS_REPO,
        "--all",
        "--yes",
        "--force",
        "--project" if options["scope"] == "project" else "--global",
    ]
    try:
        run_command(command, status="Installing agent-skills via skillfish", timeout=900)
    except CommandError as exc:
        error(f"skillfish failed: {exc}")
        return FAILED

    success("agent-skills installed")
    return INSTALLED


TOYS = {
    "plannotator": {
        "summary": "Browser review surface for plans, diffs and HTML your agent produces",
        "url": "https://github.com/backnotprop/plannotator",
        "install": install_plannotator,
    },
    "context-mode": {
        "summary": "Sandboxed command execution and an indexed knowledge base that keep raw output out of context",
        "url": "https://github.com/mksglu/context-mode",
        "install": install_context_mode,
    },
    "agent-skills": {
        "summary": "The personal skill library (credentials, slides, gitnexus, life CLI, TTS, ...)",
        "url": "https://github.com/maycuatroi1/agent-skills",
        "install": install_agent_skills,
    },
}


def print_catalogue():
    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("Toy", style="accent", no_wrap=True)
    table.add_column("What it does")
    table.add_column("Source", style="dim")
    for name, toy in TOYS.items():
        table.add_row(name, toy["summary"], toy["url"])
    console.print()
    console.print(table)
    console.print()
    console.print("Install everything with [accent]evo setup agent-toy[/accent], or name the ones you want.")


def print_summary(results, scope):
    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("Toy", style="accent", no_wrap=True)
    table.add_column("Result", no_wrap=True)
    for name, status in results:
        table.add_row(name, RESULT_STYLES[status])

    console.print()
    console.print(table)

    usable = [name for name, status in results if status != FAILED]
    lines = []
    if "plannotator" in usable:
        lines.append("Plannotator opens plans in your browser as soon as your agent proposes one.")
    if "context-mode" in usable:
        lines.append("Run [accent]ctx doctor[/accent] inside Claude Code to verify the hooks.")
    if "agent-skills" in usable:
        target = "./.claude/skills" if scope == "project" else "~/.claude/skills"
        lines.append(f"Skills landed in [accent]{target}[/accent].")
    if lines:
        lines.append("")
        lines.append("Restart Claude Code so the new plugins, hooks and skills load.")

    complete = all(status == INSTALLED for _, status in results)
    console.print(
        Panel(
            Text.from_markup("\n".join(lines) if lines else "Nothing was installed."),
            title="setup agent-toy complete" if complete else "setup agent-toy incomplete",
            border_style="success" if complete else "warning",
            expand=False,
        )
    )


@click.command("agent-toy", epilog=EPILOG)
@click.argument("names", nargs=-1, metavar="[TOY]...")
@click.option("--list", "list_only", is_flag=True, help="Show the catalogue and exit.")
@click.option(
    "--scope",
    type=click.Choice(["user", "project", "local"]),
    default="user",
    show_default=True,
    help="Where to install: `user` is machine-wide, `project` scopes to the current repo.",
)
@click.option("--minimal", is_flag=True, help="plannotator: install only the binary, no hooks or skills.")
def agent_toy(names, list_only, scope, minimal):
    """Install the agent toolbox: plannotator, context-mode and agent-skills.

    Each toy is pulled at its latest version and installed idempotently: a
    marketplace that is already configured gets refreshed, a plugin that is
    already installed gets updated, and skills are overwritten in place. Name
    one or more toys to install a subset, or pass no argument to install all of
    them. Run with --list to see what is on offer.

    plannotator needs PowerShell (Windows) or curl + bash (POSIX); context-mode
    and the plannotator plugin need the Claude Code CLI (`evo setup claude`);
    agent-skills needs Node.js 18+ for npx.
    """
    if list_only:
        print_catalogue()
        return

    unknown = [name for name in names if name not in TOYS]
    if unknown:
        error(f"Unknown toy(s): {', '.join(unknown)}. Run `evo setup agent-toy --list` to see the catalogue.")
        return

    selected = list(names) or list(TOYS)
    options = {"scope": scope, "minimal": minimal, "claude": bool(ensure_claude_on_path())}

    results = []
    for name in selected:
        step(f"evo setup agent-toy: {name}")
        info(TOYS[name]["url"])
        results.append((name, TOYS[name]["install"](options)))

    print_summary(results, scope)
