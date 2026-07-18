from __future__ import annotations

import sys

import rich_click as click
from rich.table import Table

from evo_cli.console import console

TONE_STYLE = {"ok": "green", "active": "cyan", "warn": "yellow", "bad": "red", "idle": "dim"}
LEVEL_STYLE = {"ok": "green", "info": "cyan", "warn": "yellow", "error": "red", "unknown": "dim"}
LEVEL_MARK = {"ok": "OK   ", "info": "NOTE ", "warn": "WARN ", "error": "FAIL ", "unknown": "?    "}


def table(*columns: str) -> Table:
    grid = Table(box=None, pad_edge=False, show_edge=False, header_style="bold")
    for column in columns:
        grid.add_column(column, overflow="fold")
    return grid


def bar(done: int, total: int, width: int = 12) -> str:
    if not total:
        return "[dim]" + "-" * width + "[/]"
    filled = round(done * width / total)
    return f"[green]{'#' * filled}[/][dim]{'.' * (width - filled)}[/]"


def one_line(value: object, width: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= width else text[: max(1, width - 3)] + "..."


def title_width(reserved: int = 26) -> int:
    return max(28, min(console.width, 140) - reserved)


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def print_warnings(warnings: list) -> None:
    for item in warnings:
        style = LEVEL_STYLE.get(item["level"], "dim")
        console.print(f"  [{style}]{LEVEL_MARK.get(item['level'], '     ')}[/] {item['text']}")


def print_graph(title: str, graph: dict) -> None:
    """Text rendering of a DAG. The dashboard draws it; this is the same data as a list.

    A node-link diagram is unreadable to a screen reader and unavailable in a terminal, so
    every graph the UI draws must also be printable as an adjacency list.
    """
    console.print(
        f"\n[bold cyan]{title}[/] [dim]{len(graph['nodes'])} nodes, {len(graph['edges'])} edges, "
        f"depth {graph['depth']}, {'acyclic' if graph['acyclic'] else 'CYCLIC'}[/]"
    )
    if not graph["nodes"]:
        console.print("  [dim]nothing to draw[/]")
        return

    by_rank: dict[int, list[dict]] = {}
    for node in graph["nodes"]:
        by_rank.setdefault(node["rank"], []).append(node)

    outgoing: dict[str, list[dict]] = {}
    for edge in graph["edges"]:
        outgoing.setdefault(edge["source"], []).append(edge)

    width = title_width(reserved=40)
    for rank in sorted(by_rank):
        console.print(f"  [dim]layer {rank}[/]")
        for node in sorted(by_rank[rank], key=lambda n: n["id"]):
            style = TONE_STYLE[node["tone"]]
            cycle = " [yellow](in cycle)[/]" if node["inCycle"] else ""
            detail = node["meta"].get("what") or node["meta"].get("role") or node["meta"].get("status") or ""
            console.print(f"    [{style}]*[/] [bold]{node['label']}[/]{cycle}  [dim]{one_line(detail, width)}[/]")
            for edge in sorted(outgoing.get(node["id"], []), key=lambda e: e["target"]):
                arrow = "-->" if not edge["dashed"] else "..>"
                mark = " [yellow]<cycle>[/]" if edge.get("inCycle") else ""
                console.print(f"        [dim]{arrow}[/] {edge['target']} [dim]({edge['label']})[/]{mark}")
    if graph["warnings"]:
        console.print()
        print_warnings(graph["warnings"])


def fail_if(condition: bool) -> None:
    if condition:
        raise click.exceptions.Exit(1)
