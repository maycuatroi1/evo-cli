from __future__ import annotations

import json as jsonlib
import sys

import rich_click as click
from rich.table import Table
from rich.text import Text

from evo_cli.commands.route import _dns, _probe
from evo_cli.console import console, error, info, step, success, warning

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo route check medium.com[/cyan]              why can't I get there?\n"
    "  [cyan]evo route check a.com b.com[/cyan]             several at once\n"
    "  [cyan]evo route check medium.com --json[/cyan]       machine-readable\n\n"
    "[dim]Separates the two blocks that look identical from the browser but need\n"
    "opposite fixes: a poisoned DNS answer (the route is fine, the address is a\n"
    "lie) versus DPI reading the server name out of your TLS handshake (the\n"
    "address is right and hosts/DNS tricks cannot help).[/dim]"
)

VERDICT_STYLE = {
    _probe.OPEN: "success",
    _probe.DNS_POISONED: "warning",
    _probe.SNI_BLOCKED: "error",
    _probe.IP_BLOCKED: "error",
    _probe.UNREACHABLE: "error",
}

ADVICE = {
    _probe.OPEN: "Nothing to do.",
    _probe.DNS_POISONED: "Run `evo route open {host}` to pin the working address, or fix DNS at the source.",
    _probe.SNI_BLOCKED: "hosts and DNS cannot help here. Run `evo route open {host}` to bring up the DPI bypass.",
    _probe.IP_BLOCKED: "The address answers TCP but nothing completes - you need a tunnel or VPN.",
    _probe.UNREACHABLE: "No address resolved or answered. Check the name, then your link.",
}


def render(report):
    host = report["host"]
    verdict = report["verdict"]
    style = VERDICT_STYLE.get(verdict, "info")

    console.print()
    console.print(f"[bold]{host}[/bold]  [{style}]{verdict}[/{style}] - {_probe.VERDICT_TEXT[verdict]}")

    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("resolver", style="accent")
    table.add_column("answer")
    system = report["system_ips"]
    table.add_row("system", _fmt_ips(system) if system else "[error]no answer[/error]")
    for label, ips in report["answers"].items():
        if ips is None:
            table.add_row(label, "[dim]no reply[/dim]")
        else:
            table.add_row(label, _fmt_ips(ips))
    console.print()
    console.print(table)

    if report["probes"]:
        probe_table = Table(box=None, pad_edge=False, show_edge=False)
        probe_table.add_column("address", style="accent")
        probe_table.add_column("tcp")
        probe_table.add_column("tls")
        probe_table.add_column("time")
        for row in report["probes"]:
            probe_table.add_row(
                row["ip"],
                "[success]open[/success]" if row["tcp"] else "[error]no[/error]",
                "[success]ok[/success]" if row["tls"] else _tls_note(row),
                f"{row['ms']:.0f}ms" if row["ms"] is not None else "-",
            )
        console.print()
        console.print(probe_table)

    control = report["control"]
    if control is not None:
        console.print()
        if control["tls"]:
            info(
                f"Control test: the same address accepts SNI [accent]{control['sni']}[/accent] - "
                "the path is open, the name is what gets you blocked."
            )
        else:
            info(f"Control test: SNI [accent]{control['sni']}[/accent] fails too - the address itself is the problem.")

    step("Verdict")
    message = ADVICE[verdict].format(host=host)
    if verdict == _probe.OPEN:
        success(f"{host} is reachable. {message}")
    elif verdict == _probe.DNS_POISONED:
        warning(f"DNS hands back a bogus address for {host}, but {report['best']} works. {message}")
    else:
        error(f"{host}: {_probe.VERDICT_TEXT[verdict]}. {message}")


def _fmt_ips(ips):
    if not ips:
        return "[dim]empty[/dim]"
    parts = []
    for ip in ips:
        parts.append(f"[error]{ip}[/error]" if _dns.is_bogus(ip) else ip)
    return ", ".join(parts)


def _tls_note(row):
    if row["reset"]:
        return "[error]reset[/error]"
    if row["error"] and "timeout" in row["error"]:
        return "[error]timeout[/error]"
    return "[error]fail[/error]"


@click.command("check", epilog=EPILOG)
@click.argument("hosts", nargs=-1, required=True)
@click.option("-t", "--timeout", default=6.0, show_default=True, help="Per-connection timeout (seconds).")
@click.option("--no-vn", is_flag=True, help="Skip the Vietnamese ISP resolvers.")
@click.option("--json", "as_json", is_flag=True, help="Print the report as JSON.")
def check(hosts, timeout, no_vn, as_json):
    """Work out what stands between you and `HOSTS`.

    Asks several resolvers what the name means, then opens a real TLS
    connection to each address it gets back. When nothing completes, it runs
    one more handshake to the **same address** announcing a harmless server
    name: if that one succeeds, the route is open and the block is keyed on the
    name, which is what separates **sni-blocked** from **ip-blocked**.

    Pure Python sockets - no admin rights, same behaviour on Windows and Linux.
    """
    reports = []
    try:
        for host in hosts:
            with console.status(f"[info]probing {host}...[/info]", spinner="dots"):
                reports.append(_probe.diagnose(host, timeout=timeout, include_vn=not no_vn))
    except Exception as exc:
        error(str(exc))
        sys.exit(1)

    if as_json:
        console.print_json(jsonlib.dumps(reports, ensure_ascii=False))
        return reports

    for report in reports:
        render(report)
    return reports
