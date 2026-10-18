from __future__ import annotations

import rich_click as click
import yaml
from rich.panel import Panel
from rich.text import Text

from evo_cli.commands.harness._dag import resolve_step, step_key
from evo_cli.commands.harness._model import SECTIONS, find_plan, plain, tone_of
from evo_cli.commands.harness._paths import find_manifest, harness_option
from evo_cli.commands.harness._render import TONE_STYLE, as_text, bar, one_line, title_width
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
# Every open question says whether it blocks, even one whose author left the key out.
ALWAYS_SHOWN = {"open_questions": ("blocking",)}

STEP_HEAD = ("id", "title", "status", "repo", "depends_on", "blocking", "done_at")
STEP_BODY = ("what", "verify", "note", "acceptance", "evidence")
INDENT = "    "


def _render_item(section: str, index: int, item: dict, full: bool) -> None:
    status = item.get("status")
    style = TONE_STYLE[tone_of(section, status)]
    authored = item.get("title")
    titled = isinstance(authored, str) and bool(authored.strip())
    if titled:
        head, hidden = authored.strip(), (HIDE_KEYS - {"what"}) | {"title"}
    else:
        head = item.get("what") or item.get("repo") or item.get("issue") or "?"
        hidden = HIDE_KEYS | ({"issue"} if head == item.get("issue") else set())
    label = item.get("id", item.get("order", index))
    badge = f"[{style}]{status}[/]" if status else ""
    if not full:
        head = one_line(head, title_width(reserved=18))
    console.print(f"  [bold]{label:>3}[/] {head}  {badge}")
    fields = {**item, **{key: item.get(key) for key in ALWAYS_SHOWN.get(section, ())}}
    for key, value in fields.items():
        if key in hidden:
            continue
        if isinstance(value, list):
            value = "; ".join(as_text(v) for v in value) or "-"
        text = " ".join(as_text(value).split())
        console.print(f"      [dim]{key}:[/] {text if full else one_line(text, title_width(reserved=18))}")


def _block(value: object) -> list[str]:
    """Lines of a value printed under its key: text keeps its line breaks, a list becomes `- ` items."""
    if value is None or value == "" or value == []:
        return ["-"]
    if isinstance(value, list):
        lines = []
        for entry in value:
            first, *rest = _block(entry)
            lines.append(f"- {first}")
            lines.extend(f"  {line}" if line else "" for line in rest)
        return lines
    if isinstance(value, dict):
        dumped = yaml.safe_dump(plain(value), allow_unicode=True, sort_keys=False, width=1_000_000)
        return dumped.rstrip("\n").split("\n")
    return as_text(value).rstrip("\n").split("\n")


def _is_short(value: object) -> bool:
    nested = isinstance(value, dict) or (isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value))
    text = as_text(value)
    return not nested and "\n" not in text and len(text) <= 80


def _print(text: Text) -> None:
    # Soft wrap: rich never breaks a line itself, so a verify command stays one copyable line.
    console.print(text, soft_wrap=True)


def _print_step(item, key: str) -> None:
    index = resolve_step(item, key)
    entry = item.items("steps")[index]
    key = step_key(entry, index)
    _print(Text.assemble((item.id, "bold cyan"), " step ", (key, "bold"), (f"  ({item.area})", "dim")))

    # Any other key the step carries is printed too: short scalars beside the head, the rest as blocks.
    extra = [k for k in entry if k not in STEP_HEAD and k not in STEP_BODY]
    inline = [k for k in extra if _is_short(entry[k])]
    head = [*STEP_HEAD, *inline]
    width = max(len(k) for k in head) + 1
    for name in head:
        value = as_text(entry.get(name))
        style = TONE_STYLE[tone_of("steps", value)] if name == "status" and value != "-" else ""
        _print(Text.assemble((f"{name + ':':<{width}} ", "dim"), (value, style)))

    for name in [*STEP_BODY, *(k for k in extra if k not in inline)]:
        _print(Text())
        _print(Text(f"{name}:", style="bold"))
        for line in _block(entry.get(name)):
            _print(Text(INDENT + line if line else ""))


@click.command("show", help="Read one exec-plan in the terminal, or one of its steps in full with --step.")
@harness_option
@click.argument("plan_id")
@click.option("--section", "sections", multiple=True, type=click.Choice(SECTIONS), help="Only print some sections.")
@click.option("--full", is_flag=True, help="Print notes verbatim instead of clipping to one line.")
@click.option(
    "--step",
    "step_id",
    metavar="ID",
    help="Print only this step, every field as written: what, verify, note, acceptance and evidence unclipped.",
)
def show(harness_path, plan_id, sections, full, step_id):
    item = find_plan(find_manifest(harness_path), plan_id)
    if step_id is not None:
        if sections:
            raise click.UsageError("--step prints one step; leave out --section.")
        _print_step(item, step_id)
        return

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
