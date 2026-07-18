from __future__ import annotations

import rich_click as click
from rich.panel import Panel

from evo_cli.commands.harness._model import SECTIONS, find_plan, tone_of
from evo_cli.commands.harness._paths import find_manifest, harness_option
from evo_cli.commands.harness._render import TONE_STYLE, bar, one_line, title_width
from evo_cli.console import console

SECTION_TITLES = {
    "references": "References",
    "repos": "Repos and merge order",
    "steps": "Steps",
    "decisions": "Decisions",
    "tech_debt": "Tech debt",
    "open_questions": "Open questions",
}
HIDE_KEYS = {"status", "order", "what", "repo", "id"}


def _render_item(section: str, index: int, item: dict, full: bool) -> None:
    status = item.get("status")
    style = TONE_STYLE[tone_of(section, status)]
    head = item.get("what") or item.get("repo") or item.get("issue") or "?"
    label = item.get("id", item.get("order", index))
    badge = f"[{style}]{status}[/]" if status else ""
    if not full:
        head = one_line(head, title_width(reserved=18))
    console.print(f"  [bold]{label:>3}[/] {head}  {badge}")
    for key, value in item.items():
        if key in HIDE_KEYS:
            continue
        if isinstance(value, list):
            value = "; ".join(str(v) for v in value) or "-"
        text = " ".join(str(value).split())
        console.print(f"      [dim]{key}:[/] {text if full else one_line(text, title_width(reserved=18))}")


@click.command("show", help="Read one exec-plan in the terminal.")
@harness_option
@click.argument("plan_id")
@click.option("--section", "sections", multiple=True, type=click.Choice(SECTIONS), help="Only print some sections.")
@click.option("--full", is_flag=True, help="Print notes verbatim instead of clipping to one line.")
def show(harness_path, plan_id, sections, full):
    item = find_plan(find_manifest(harness_path), plan_id)
    p = item.progress()

    console.print(
        Panel(
            " ".join(str(item.raw.get("goal") or "").split()),
            title=f"[bold]{item.id}[/] [dim]({item.area})[/]",
            subtitle=f"{bar(p['steps_done'], p['steps_total'])} {p['steps_done']}/{p['steps_total']} steps  "
            f"debt: {p['debt_open']}  questions: {p['questions_open']}",
            border_style="cyan",
        )
    )

    for name in sections or SECTIONS:
        entries = item.items(name)
        if not entries:
            continue
        console.print(f"\n[bold cyan]{SECTION_TITLES[name]}[/] [dim]({len(entries)})[/]")
        for index, entry in enumerate(entries):
            _render_item(name, index, entry, full)
