from __future__ import annotations

import json as jsonlib

import rich_click as click
from rich.table import Table
from rich.text import Text

from evo_cli.commands.route import _dpi, _hosts
from evo_cli.console import console, info, step, success

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo route status[/cyan]           what is currently in place\n"
    "  [cyan]evo route status --json[/cyan]    machine-readable\n\n"
    "[dim]Shows the two layers evo can put in your way on purpose - pinned\n"
    "addresses and the DPI bypass - so a stale pin never becomes a mystery.[/dim]"
)


def collect():
    return {
        "hosts_file": str(_hosts.hosts_path()),
        "pinned": _hosts.read_block(),
        "dpi": _dpi.service_state(),
        "admin": _dpi.is_admin(),
    }


@click.command("status", epilog=EPILOG)
@click.option("--json", "as_json", is_flag=True, help="Print the status as JSON.")
def status(as_json):
    """Show which route fixes are currently in place.

    Pinned addresses go stale when a CDN moves, and a pin that outlives its
    reason looks exactly like a broken site - so it is worth being able to see
    them without going digging in `hosts`.
    """
    state = collect()

    if as_json:
        console.print_json(jsonlib.dumps(state, ensure_ascii=False))
        return state

    step("Pinned addresses")
    pinned = state["pinned"]
    if pinned:
        table = Table(box=None, pad_edge=False, show_edge=False)
        table.add_column("host", style="accent")
        table.add_column("address")
        for host in sorted(pinned):
            table.add_row(host, pinned[host])
        console.print(table)
        console.print()
        info(f"In {state['hosts_file']} - remove with `evo route close <host>`")
    else:
        info("None - evo has not pinned any address.")

    step("DPI bypass")
    dpi = state["dpi"]
    if dpi["reason"]:
        info(f"Not available: {dpi['reason']}")
    elif not dpi["installed"]:
        info("Not installed.")
    elif dpi["running"]:
        success("Running")
        console.print(f"  [dim]{dpi['args']}[/dim]")
    else:
        info("Installed but stopped.")
        console.print(f"  [dim]{dpi['args']}[/dim]")

    return state
