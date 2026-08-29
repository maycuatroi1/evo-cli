from __future__ import annotations

import sys

import rich_click as click
from rich.text import Text

from evo_cli.commands.route import _dpi, _hosts, _probe
from evo_cli.console import console, error, info, step, success, warning

OPEN_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo route open store.steampowered.com[/cyan]     pick the fix that fits\n"
    "  [cyan]evo route open medium.com --dry-run[/cyan]       say what it would do\n"
    "  [cyan]evo route open x.com --method hosts[/cyan]       force the address pin\n"
    "  [cyan]evo route open x.com --method dpi[/cyan]         force the DPI bypass\n\n"
    "[dim]Diagnoses first, then applies only the fix that matches: pinning an address\n"
    "for a poisoned name, or the packet-level bypass for a name-keyed block.\n"
    "Needs administrator rights because it edits hosts / installs a service.[/dim]"
)

CLOSE_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo route close medium.com[/cyan]      drop that one pinned address\n"
    "  [cyan]evo route close --all[/cyan]           drop every pin evo added\n"
    "  [cyan]evo route close --dpi[/cyan]           stop and remove the DPI bypass\n\n"
    "[dim]Only touches the block evo owns, marked in hosts by `# >>> evo route >>>`.\n"
    "Anything else in the file is left exactly as it was.[/dim]"
)


def _require_admin(action):
    if _dpi.is_admin():
        return
    hint = "start an elevated PowerShell and re-run" if _dpi.is_windows() else "re-run with sudo"
    error(f"{action} needs administrator rights - {hint}.")
    sys.exit(1)


def _apply_hosts(report, dry_run):
    host, best = report["host"], report["best"]
    if not best:
        error(f"No working address for {host} to pin.")
        sys.exit(1)
    if dry_run:
        info(f"Would pin [accent]{best}[/accent] for {host} in {_hosts.hosts_path()}")
        return None
    _require_admin("Editing hosts")
    _hosts.pin(host, best)
    success(f"Pinned [accent]{best}[/accent] for {host}")
    return best


def _apply_dpi(report, dry_run):
    if not _dpi.is_windows():
        error("The DPI bypass is Windows-only. On Linux use a tunnel, or a tool like zapret.")
        sys.exit(1)
    if dry_run:
        state = _dpi.service_state()
        if state["running"]:
            info("DPI bypass is already running - nothing to do.")
        else:
            info(f"Would install and start GoodbyeDPI with {' '.join(_dpi.DEFAULT_ARGS)}")
        return None
    _require_admin("Installing the DPI bypass")
    state = _dpi.ensure()
    if state["running"]:
        success("DPI bypass is running")
    else:
        error("DPI bypass was installed but would not start.")
        sys.exit(1)
    return state


@click.command("open", epilog=OPEN_EPILOG)
@click.argument("host")
@click.option(
    "--method",
    type=click.Choice(["auto", "hosts", "dpi"]),
    default="auto",
    show_default=True,
    help="Which fix to apply. `auto` picks from the diagnosis.",
)
@click.option("-n", "--dry-run", is_flag=True, help="Say what would change, change nothing.")
@click.option("-t", "--timeout", default=6.0, show_default=True, help="Per-connection timeout (seconds).")
def open_route(host, method, dry_run, timeout):
    """Open a route to `HOST` by applying the fix its diagnosis calls for.

    A poisoned name gets its working address pinned in hosts. A name-keyed DPI
    block gets the packet-level bypass, because no amount of hosts or DNS
    editing survives a filter that reads the server name out of the handshake.

    Re-checks afterwards and reports whether the route actually opened.
    """
    step(f"evo route open {host}")

    with console.status(f"[info]diagnosing {host}...[/info]", spinner="dots"):
        report = _probe.diagnose(host, timeout=timeout)

    verdict = report["verdict"]
    console.print(f"Diagnosis: [bold]{verdict}[/bold] - {_probe.VERDICT_TEXT[verdict]}")

    if method == "auto":
        if verdict == _probe.OPEN:
            success(f"{host} is already reachable - nothing to do.")
            return report
        if verdict == _probe.DNS_POISONED:
            method = "hosts"
        elif verdict == _probe.SNI_BLOCKED:
            method = "dpi"
        else:
            error(f"{_probe.VERDICT_TEXT[verdict]} - this needs a tunnel or VPN, which evo will not set up for you.")
            sys.exit(1)

    if method == "hosts":
        _apply_hosts(report, dry_run)
    else:
        _apply_dpi(report, dry_run)

    if dry_run:
        return report

    step("Verify")
    with console.status(f"[info]re-checking {host}...[/info]", spinner="dots"):
        after = _probe.diagnose(host, timeout=timeout)
    if after["verdict"] in (_probe.OPEN, _probe.DNS_POISONED) and after["working"]:
        success(f"{host} is reachable now ({after['best']})")
    else:
        warning(f"{host} still reports [bold]{after['verdict']}[/bold]. Run `evo route check {host}` for detail.")
    return after


@click.command("close", epilog=CLOSE_EPILOG)
@click.argument("host", required=False)
@click.option("--all", "drop_all", is_flag=True, help="Remove every address evo pinned.")
@click.option("--dpi", "drop_dpi", is_flag=True, help="Stop and remove the DPI bypass service.")
def close_route(host, drop_all, drop_dpi):
    """Undo what `evo route open` put in place.

    Without `--dpi` this only touches the hosts block evo owns; the rest of the
    file is left untouched.
    """
    if not any([host, drop_all, drop_dpi]):
        error("Nothing to do - name a HOST, or pass --all / --dpi.")
        sys.exit(1)

    step("evo route close")

    if drop_all:
        _require_admin("Editing hosts")
        entries = _hosts.read_block()
        _hosts.clear()
        success(f"Removed {len(entries)} pinned address(es)")
    elif host:
        _require_admin("Editing hosts")
        _, removed = _hosts.unpin(host)
        if removed:
            success(f"Unpinned {host}")
        else:
            info(f"{host} was not pinned by evo - nothing changed.")

    if drop_dpi:
        if not _dpi.is_windows():
            error("The DPI bypass is Windows-only.")
            sys.exit(1)
        _require_admin("Removing the DPI bypass")
        if _dpi.remove():
            success("DPI bypass stopped and removed")
        else:
            warning("Could not remove the service - it may already be gone.")
