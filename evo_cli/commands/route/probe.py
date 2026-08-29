from __future__ import annotations

import json as jsonlib
import sys

import rich_click as click
from rich.table import Table
from rich.text import Text

from evo_cli.commands.route import _dns, _probe
from evo_cli.console import console, error, step, success, warning

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo route probe store.steampowered.com[/cyan]      which address actually works?\n"
    "  [cyan]evo route probe medium.com --all[/cyan]            list every address, not just live ones\n"
    "  [cyan]evo route probe x.com --json[/cyan]                machine-readable\n\n"
    "[dim]Collects addresses from every resolver it can reach, then opens a real TLS\n"
    "connection to each one and ranks by handshake time. A CDN often has a node\n"
    "inside the country that answers while the international address is filtered.[/dim]"
)


@click.command("probe", epilog=EPILOG)
@click.argument("host")
@click.option("-t", "--timeout", default=6.0, show_default=True, help="Per-connection timeout (seconds).")
@click.option("--all", "show_all", is_flag=True, help="Show addresses that failed too.")
@click.option("--no-vn", is_flag=True, help="Skip the Vietnamese ISP resolvers.")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def probe(host, timeout, show_all, no_vn, as_json):
    """Find every address for `HOST` and rank the ones that answer.

    Useful on its own when a site is up but slow: the fastest live address is
    frequently a local CDN node that only some resolvers hand out.
    """
    try:
        with console.status(f"[info]collecting addresses for {host}...[/info]", spinner="dots"):
            answers = _dns.gather(host, include_vn=not no_vn)

        candidates = answers["trusted"] or [ip for ip in (answers["system"] or []) if not _dns.is_bogus(ip)]
        if not candidates:
            error(f"No usable address for {host}.")
            sys.exit(1)

        with console.status(f"[info]testing {len(candidates)} addresses...[/info]", spinner="dots"):
            rows = _probe.rank(host, candidates, timeout=timeout)
    except Exception as exc:
        error(str(exc))
        sys.exit(1)

    if as_json:
        console.print_json(jsonlib.dumps({"host": host, "answers": answers, "probes": rows}, ensure_ascii=False))
        return rows

    live = [row for row in rows if row["tls"]]
    shown = rows if show_all else (live or rows)

    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("address", style="accent")
    table.add_column("tls")
    table.add_column("time")
    table.add_column("seen from")

    origin = _origin_map(answers["resolvers"])
    for row in shown:
        table.add_row(
            row["ip"],
            "[success]ok[/success]" if row["tls"] else "[error]blocked[/error]",
            f"{row['ms']:.0f}ms" if row["ms"] is not None else "-",
            ", ".join(origin.get(row["ip"], [])) or "[dim]system[/dim]",
        )
    console.print()
    console.print(table)

    step("Verdict")
    if live:
        best = live[0]
        success(
            f"{len(live)}/{len(rows)} addresses answer. Fastest: [accent]{best['ip']}[/accent] ({best['ms']:.0f}ms)"
        )
    else:
        warning(f"None of the {len(rows)} addresses complete a handshake - run `evo route check {host}` to see why.")
    return rows


def _origin_map(resolvers):
    origin = {}
    for label, ips in resolvers.items():
        for ip in ips or []:
            origin.setdefault(ip, []).append(label)
    return origin
