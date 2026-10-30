from __future__ import annotations

import http.client
import sys

import rich_click as click
from rich.text import Text

from evo_cli.commands.route import _dpi, _hosts, _page, _probe, _spoofdpi
from evo_cli.console import console, error, info, step, success, warning

OPEN_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo route open store.steampowered.com[/cyan]     pick the fix that fits\n"
    "  [cyan]evo route open medium.com --dry-run[/cyan]       say what it would do\n"
    "  [cyan]evo route open x.com --method hosts[/cyan]       force the address pin\n"
    "  [cyan]evo route open x.com --method dpi[/cyan]         force the DPI bypass\n"
    "  [cyan]evo route open x.com --host-only[/cyan]          leave what its page loads alone\n\n"
    "[dim]Diagnoses first, then applies only the fix that matches: pinning an address\n"
    "for a poisoned name, or the packet-level bypass for a name-keyed block.\n"
    "Then reads the page: the bypass takes in the blocked hosts it loads from,\n"
    "whole domains at a time because CDNs rotate; a pin only names them.\n"
    "Needs administrator rights because it edits hosts / installs a service;\n"
    "on macOS the bypass is SpoofDPI as your own LaunchAgent and needs no sudo.[/dim]"
)

CLOSE_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo route close medium.com[/cyan]      drop its pin, or take it out of the bypass\n"
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


def _apply_spoofdpi(host, dry_run):
    if dry_run:
        state = _spoofdpi.service_state()
        if state["running"] and host in state["hosts"]:
            info(f"{host} already goes through the DPI bypass - nothing to do.")
        else:
            info(f"Would run SpoofDPI on {_spoofdpi.listen()} as the system proxy, splitting the handshake for {host}")
        return None
    if _dpi.is_admin():
        error("Run this without sudo: SpoofDPI runs as your own LaunchAgent and sets your proxy.")
        sys.exit(1)
    try:
        state = _spoofdpi.ensure(host)
    except RuntimeError as exc:
        error(str(exc))
        sys.exit(1)
    if not state["running"]:
        error(f"SpoofDPI would not start - see {_spoofdpi.log_path()}")
        sys.exit(1)
    success(f"DPI bypass is running on {_spoofdpi.listen()}, system proxy set on {', '.join(state['proxy'])}")
    return state


def _apply_dpi(report, dry_run):
    if _dpi.is_macos():
        return _apply_spoofdpi(report["host"], dry_run)
    if not _dpi.is_windows():
        error("The DPI bypass runs on Windows and macOS. On Linux use a tunnel, or a tool like zapret.")
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


def _page_hosts(host, proxy, timeout):
    """The blocked hosts ``host``'s page needs, or None when the page cannot be read."""
    with console.status(f"[info]reading the page at {host}...[/info]", spinner="dots"):
        try:
            return _page.blocked_hosts(host, proxy, _spoofdpi.opened(), timeout=timeout)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            warning(f"Could not read the page at {host}, so what it loads is unchecked: {exc}")
            return None


def _open_page_hosts(host, timeout):
    """The macOS bypass lists hosts by name, so everything the page loads has to go on the list too."""
    proxy = (_spoofdpi.LISTEN_HOST, _spoofdpi.LISTEN_PORT)
    blocked = _page_hosts(host, f"http://{_spoofdpi.listen()}", timeout)
    if blocked is None:
        return
    if not blocked:
        success(f"Nothing the page at {host} loads is blocked")
        return

    info(f"The page at {host} also needs {', '.join(blocked)}")
    domains = sorted({_page.registrable(name) for name in blocked})
    try:
        state = _spoofdpi.ensure(*domains)
    except RuntimeError as exc:
        error(str(exc))
        sys.exit(1)
    if not state["running"]:
        error(f"SpoofDPI would not restart - see {_spoofdpi.log_path()}")
        sys.exit(1)

    with console.status("[info]re-checking through the proxy...[/info]", spinner="dots"):
        failed = [name for name in blocked if not _probe.proxy_probe(name, proxy, timeout=timeout)["tls"]]
    if failed:
        warning(f"Still failing through the bypass: {', '.join(failed)}. See {_spoofdpi.log_path()}")
    success(f"Added {', '.join(domains)} to the bypass - `evo route close <host>` takes one back out")


def _warn_page_hosts(host, timeout):
    """A pin cannot reach past the name it pins, and the bypass will not start under sudo, so this only reports."""
    blocked = _page_hosts(host, None, timeout)
    if blocked:
        warning(f"The page at {host} also needs {', '.join(blocked)} - run `evo route open <host>` on each")


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
@click.option("--host-only", is_flag=True, help="Open HOST alone, not what its page loads from.")
def open_route(host, method, dry_run, timeout, host_only):
    """Open a route to `HOST` by applying the fix its diagnosis calls for.

    A poisoned name gets its working address pinned in hosts. A name-keyed DPI
    block gets the packet-level bypass, because no amount of hosts or DNS
    editing survives a filter that reads the server name out of the handshake.

    Re-checks afterwards and reports whether the route actually opened, then
    reads the page, because a site with its CDN still blocked is not open.
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
        if verdict in (_probe.DNS_POISONED, _probe.ADDRESS_BLOCKED):
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
        if method == "dpi" and _dpi.is_macos() and not host_only:
            info("Would then read its page and add the blocked hosts it loads from")
        return report

    step("Verify")
    if method == "dpi" and _dpi.is_macos():
        row = _verify_through_proxy(host, timeout)
        if row["tls"] and not host_only:
            _open_page_hosts(host, timeout)
        return row
    with console.status(f"[info]re-checking {host}...[/info]", spinner="dots"):
        after = _probe.diagnose(host, timeout=timeout)
    if after["verdict"] in (_probe.OPEN, _probe.DNS_POISONED) and after["working"]:
        success(f"{host} is reachable now ({after['best']})")
        if method == "hosts" and not host_only:
            _warn_page_hosts(host, timeout)
    else:
        warning(f"{host} still reports [bold]{after['verdict']}[/bold]. Run `evo route check {host}` for detail.")
    return after


def _verify_through_proxy(host, timeout):
    """The macOS bypass lives in a proxy, which a fresh diagnosis over raw sockets would never use."""
    proxy = (_spoofdpi.LISTEN_HOST, _spoofdpi.LISTEN_PORT)
    with console.status(f"[info]re-checking {host} through the proxy...[/info]", spinner="dots"):
        row = _probe.proxy_probe(host, proxy, timeout=timeout)
    if row["tls"]:
        success(f"{host} is reachable through the DPI bypass")
        info(f"Browsers pick it up from the system proxy; curl and other CLIs need -x http://{_spoofdpi.listen()}")
        for override in _spoofdpi.browser_overrides():
            warning(f"{override} holds the browser's proxy setting, so that browser skips the bypass - disable it")
    else:
        warning(f"{host} still fails through the bypass: {row['error']}. See {_spoofdpi.log_path()}")
    return row


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
        closed = False
        if _dpi.is_macos() and host in _spoofdpi.opened():
            _spoofdpi.forget(host)
            success(f"Took {host} out of the DPI bypass")
            closed = True
        if host in _hosts.read_block():
            _require_admin("Editing hosts")
            _hosts.unpin(host)
            success(f"Unpinned {host}")
            closed = True
        if not closed:
            info(f"{host} was not opened by evo - nothing changed.")

    if drop_dpi:
        if _dpi.is_macos():
            _spoofdpi.remove()
            success("DPI bypass stopped and the system proxy cleared")
            return
        if not _dpi.is_windows():
            error("The DPI bypass runs on Windows and macOS.")
            sys.exit(1)
        _require_admin("Removing the DPI bypass")
        if _dpi.remove():
            success("DPI bypass stopped and removed")
        else:
            warning("Could not remove the service - it may already be gone.")
