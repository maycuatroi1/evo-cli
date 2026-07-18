from __future__ import annotations

import time

import rich_click as click

from evo_cli.commands.harness._dag import plan_repo_graph, plan_step_graph, seam_graph
from evo_cli.commands.harness._git import overlay
from evo_cli.commands.harness._model import find_plan, load_plans
from evo_cli.commands.harness._paths import find_manifest, harness_option
from evo_cli.commands.harness._render import LEVEL_MARK, LEVEL_STYLE, fail_if, print_warnings
from evo_cli.console import console


def _age(timestamp) -> str:
    if not timestamp:
        return "never fetched"
    hours = (time.time() - timestamp) / 3600
    if hours < 1:
        return f"fetched {round(hours * 60)}m ago"
    if hours < 48:
        return f"fetched {round(hours)}h ago"
    return f"fetched {round(hours / 24)}d ago"


def _report(manifest_path, plan_id: str, fetch: bool) -> int:
    item = find_plan(manifest_path, plan_id)
    result = overlay(manifest_path, item, fetch=fetch)

    console.print(f"\n[bold]{item.id}[/] [dim]- {len(result['repos'])} repos checked against git[/]")
    for entry in result["repos"]:
        head = f"  [bold]{entry['repo']}[/] [dim]{entry['branch'] or '-'}[/] status={entry['status'] or '-'}"
        if entry["present"]:
            head += f"  [dim]({_age(entry.get('last_fetch'))})[/]"
        console.print(head)
        for verdict in entry["verdicts"]:
            style = LEVEL_STYLE[verdict["level"]]
            console.print(f"      [{style}]{LEVEL_MARK[verdict['level']]}[/] {verdict['text']}")

    graph_problems = 0
    for title, graph in (("repo merge order", plan_repo_graph(item)), ("step order", plan_step_graph(item))):
        problems = [w for w in graph["warnings"] if w["level"] in ("warn", "error")]
        if problems:
            console.print(f"\n  [bold]{title}[/]")
            print_warnings(problems)
        graph_problems += sum(1 for w in problems if w["level"] == "error")

    console.print(
        f"\n  [red]{result['errors']} wrong[/]  [yellow]{result['warnings']} warnings[/]  "
        f"[dim]{result['unknown']} uncheckable[/]"
    )
    return result["errors"] + graph_problems


@click.command("check", help="Check what a plan claims against the real git state and its DAGs.")
@harness_option
@click.argument("plan_id", required=False)
@click.option("--all", "check_all", is_flag=True, help="Check every plan in plans/active.")
@click.option("--fetch", is_flag=True, help="git fetch first. Slow, needs network, but required to prove absence.")
@click.option("--seams/--no-seams", default=True, show_default=True, help="Also report contract seam problems.")
def check(harness_path, plan_id, check_all, fetch, seams):
    """Catch the ways a plan lies: a branch marked merged that is not in its base, a
    `pushed: true` with no remote branch, a commit named in the plan that does not exist,
    a declared merge order that contradicts depends_on."""
    manifest_path = find_manifest(harness_path)
    if not plan_id and not check_all and not seams:
        raise click.ClickException("Pass a PLAN_ID or --all.")

    errors = 0
    if seams:
        graph = seam_graph(manifest_path)
        problems = [w for w in graph["warnings"] if w["level"] in ("warn", "error")]
        console.print(
            f"[bold]contracts.yaml[/] [dim]- {len(graph['nodes'])} repos, {len(graph['edges'])} seam edges[/]"
        )
        if problems:
            print_warnings(problems)
        else:
            console.print("  [green]OK   [/] Seam graph is acyclic and every seam declares a verify command.")
        errors += sum(1 for w in problems if w["level"] == "error")

    if plan_id or check_all:
        targets = [p.id for p in load_plans(manifest_path, "active")] if check_all else [plan_id]
        errors += sum(_report(manifest_path, target, fetch) for target in targets)

    fail_if(bool(errors))
