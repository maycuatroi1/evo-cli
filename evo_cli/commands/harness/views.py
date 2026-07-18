from __future__ import annotations

import rich_click as click

from evo_cli.commands.harness._dag import plan_repo_graph, plan_step_graph, seam_graph, step_key
from evo_cli.commands.harness._model import cluster, find_plan, load_plans, load_seams, tone_of
from evo_cli.commands.harness._paths import find_manifest, git, harness_option
from evo_cli.commands.harness._render import (
    TONE_STYLE,
    bar,
    one_line,
    print_graph,
    print_warnings,
    table,
    title_width,
)
from evo_cli.console import console


@click.command("plans", help="Progress table for every exec-plan in the harness.")
@harness_option
@click.option("--area", type=click.Choice(["active", "completed"]), help="Only list one directory.")
def plans(harness_path, area):
    manifest_path = find_manifest(harness_path)
    found = load_plans(manifest_path, area)
    if not found:
        raise click.ClickException(f"No plan under {manifest_path.parent / 'plans'}.")

    grid = table("PLAN", "AREA", "STEPS", "", "REPOS", "DEBT", "QUESTIONS")
    for item in sorted(found, key=lambda p: (p.area != "active", p.id)):
        p = item.progress()
        debt = f"[yellow]{p['debt_open']}[/]/{p['debt_total']}" if p["debt_open"] else f"[dim]0/{p['debt_total']}[/]"
        questions = (
            f"[yellow]{p['questions_open']}[/]/{p['questions_total']}"
            if p["questions_open"]
            else f"[dim]0/{p['questions_total']}[/]"
        )
        grid.add_row(
            f"[bold]{item.id}[/]",
            item.area,
            f"{p['steps_done']}/{p['steps_total']}",
            f"{bar(p['steps_done'], p['steps_total'])} {p['pct']:>3}%",
            f"{p['repos_done']}/{p['repos_total']}",
            debt,
            questions,
        )
    console.print(grid)


@click.command("repos", help="Repositories declared by the harness manifest, with their git state.")
@harness_option
@click.option("--git/--no-git", "with_git", default=True, show_default=True, help="Read branch and dirty state.")
def repos(harness_path, with_git):
    manifest_path = find_manifest(harness_path)
    info = cluster(manifest_path)
    console.print(f"[bold]{info['name']}[/] [dim]{info['root']}[/]")

    grid = table("REPO", "ROLE", "BRANCH", "STATE", "NOTE")
    width = title_width(reserved=62)
    for entry in info["repos"]:
        if not entry["present"]:
            state = "[red]missing[/]"
            branch = entry["branch"] or "-"
        elif with_git:
            branch = git(entry["path"], "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "-"
            dirty = bool(git(entry["path"], "status", "--porcelain").stdout.strip())
            state = "[yellow]dirty[/]" if dirty else "[green]clean[/]"
        else:
            branch = entry["branch"] or "-"
            state = "[dim]-[/]"
        grid.add_row(f"[bold]{entry['name']}[/]", entry["role"] or "-", branch, state, one_line(entry["note"], width))
    console.print(grid)

    if info["proposals_pending"]:
        console.print(f"\n[yellow]{len(info['proposals_pending'])} pending proposal(s)[/] in proposals/_pending")


@click.command("seams", help="Contract seams: who owns what, who consumes it, and how it is verified.")
@harness_option
@click.option("--graph", "as_graph", is_flag=True, help="Print the owner -> consumer DAG instead of the table.")
def seams(harness_path, as_graph):
    manifest_path = find_manifest(harness_path)
    found = load_seams(manifest_path)
    if not found:
        raise click.ClickException(f"No seam declared in {manifest_path.parent / 'contracts.yaml'}.")

    if as_graph:
        print_graph("Seam DAG (owner -> consumer)", seam_graph(manifest_path))
        return

    grid = table("SEAM", "KIND", "OWNER", "CONSUMERS", "BLOCKING", "VERIFY")
    width = title_width(reserved=78)
    for seam in found:
        grid.add_row(
            f"[bold]{seam['name']}[/]",
            seam["kind"],
            seam["owner"],
            ", ".join(seam["consumers"]) or "-",
            "[red]yes[/]" if seam["blocking"] else "[dim]no[/]",
            one_line(seam["verify"] or "[none declared]", width),
        )
    console.print(grid)

    graph = seam_graph(manifest_path)
    if graph["warnings"]:
        console.print()
        print_warnings(graph["warnings"])


@click.command("graph", help="Print a dependency DAG as an adjacency list.")
@harness_option
@click.argument("target", required=False, default="seams")
def graph(harness_path, target):
    """TARGET is `seams` (default), `<plan-id>` for its merge order, or `<plan-id>:steps`."""
    manifest_path = find_manifest(harness_path)
    if target == "seams":
        print_graph("Seam DAG (owner -> consumer)", seam_graph(manifest_path))
        return

    plan_id, _, which = target.partition(":")
    item = find_plan(manifest_path, plan_id)
    if which in ("", "repos"):
        print_graph(f"{item.id} - repo merge order", plan_repo_graph(item))
    elif which == "steps":
        print_graph(f"{item.id} - step order", plan_step_graph(item))
    else:
        raise click.ClickException(f"Unknown graph {which!r}. Use `<plan>`, `<plan>:repos` or `<plan>:steps`.")


def step_board(item, highlight=None) -> None:
    steps = item.items("steps")
    p = item.progress()
    console.print(
        f"[bold]{item.id}[/] [dim]({item.area})[/]  "
        f"{bar(p['steps_done'], p['steps_total'])} {p['steps_done']}/{p['steps_total']} ({p['pct']}%)"
    )
    if not steps:
        console.print("[dim]  This plan has no steps yet.[/]")
        return

    width = title_width()
    grid = table("", "#", "REPO", "STATUS", "STEP")
    for index, entry in enumerate(steps):
        status = entry.get("status")
        style = TONE_STYLE[tone_of("steps", status)]
        key = step_key(entry, index)
        marker = "[bold cyan]>[/]" if highlight is not None and key == str(highlight) else " "
        grid.add_row(
            marker,
            f"[bold]{key}[/]",
            str(entry.get("repo") or "-"),
            f"[{style}]{status or '-'}[/]",
            one_line(entry.get("what") or entry.get("issue"), width),
        )
    console.print(grid)
