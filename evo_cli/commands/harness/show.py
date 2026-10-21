from __future__ import annotations

import re

import rich_click as click
import yaml
from rich.panel import Panel
from rich.text import Text

from evo_cli.commands.harness._dag import resolve_step, step_key
from evo_cli.commands.harness._model import SECTIONS, find_plan, plain, tone_of
from evo_cli.commands.harness._paths import find_manifest, harness_option
from evo_cli.commands.harness._render import TONE_STYLE, as_text, bar, key_runs, one_line, title_width
from evo_cli.console import console

SECTION_TITLES = {
    "references": "References",
    "repos": "Repos and merge order",
    "steps": "Steps",
    "decisions": "Decisions",
    "tech_debt": "Tech debt",
    "open_questions": "Open questions",
    "acceptance": "Acceptance",
    "non_goals": "Non-goals",
}
# Plan-level lists `show` prints whole. They stay out of SECTIONS, which also shapes the
# dashboard's plan detail, where both are still read from `extra`.
PLAN_LISTS = ("acceptance", "non_goals")
SHOW_SECTIONS = (*SECTIONS, *PLAN_LISTS)
HIDE_KEYS = {"status", "order", "what", "repo", "id"}
# Every open question says whether it blocks, even one whose author left the key out.
ALWAYS_SHOWN = {"open_questions": ("blocking",)}

STEP_HEAD = ("id", "title", "status", "repo", "depends_on", "blocking", "done_at")
STEP_BODY = ("what", "verify", "note", "acceptance", "evidence")
INDENT = "    "
FIELD_INDENT = "      "
# A step's `acceptance` names criteria by id, as a list or as text like "a1; a9"; prose around them is not an id.
CRITERION_ID = re.compile(r"^a\d+$")
CRITERION_TEXT = ("criterion", "what")


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


def _plan_list(item, name: str) -> list:
    """A plan-level list as written: `acceptance` holds mappings or plain strings, `non_goals` mostly strings."""
    value = item.raw.get(name)
    if value is None or value == "" or value == []:
        return []
    return [entry for entry in (value if isinstance(value, list) else [value]) if entry is not None]


def criterion_refs(value: object) -> list[str]:
    """The criterion ids a step's `acceptance` names, once each, in written order."""
    found: list[str] = []
    for entry in value if isinstance(value, list) else [value]:
        if entry is None or isinstance(entry, (dict, list)):
            continue
        for token in re.split(r"[\s,;]+", as_text(entry)):
            if CRITERION_ID.match(token) and token not in found:
                found.append(token)
    return found


def _steps_by_criterion(item) -> dict[str, list[str]]:
    named: dict[str, list[str]] = {}
    for index, entry in enumerate(item.items("steps")):
        for ref in criterion_refs(entry.get("acceptance")):
            named.setdefault(ref, []).append(step_key(entry, index))
    return named


def _print_whole(label: str, value: object, fields: list[tuple[str, object]], badge: Text | None = None) -> None:
    """One entry laid out as the board lays it out, label then text then `key: value` lines, but nothing clipped."""
    first, *rest = _block(value)
    _print(Text.assemble("  ", (f"{label:>3}", "bold"), " ", first, *(("  ", badge) if badge else ())))
    for line in rest:
        _print(Text(FIELD_INDENT + line if line else ""))
    for key, field in fields:
        lines = _block(field)
        if len(lines) == 1:
            _print(Text.assemble(FIELD_INDENT, (f"{key}:", "dim"), " ", lines[0]))
            continue
        _print(Text.assemble(FIELD_INDENT, (f"{key}:", "dim")))
        for line in lines:
            _print(Text(FIELD_INDENT + INDENT + line if line else ""))


def _render_acceptance(item, asked: bool) -> None:
    criteria = _plan_list(item, "acceptance")
    named = _steps_by_criterion(item)
    if not criteria and not asked:
        return
    count = f"({len(criteria)})" if criteria else "(none in this plan)"
    _print(Text.assemble("\n", ("Acceptance", "bold cyan"), " ", (count, "dim")))
    ids: set[str] = set()
    for index, entry in enumerate(criteria):
        if not isinstance(entry, dict):
            _print_whole(str(index), entry, [])
            continue
        text_key = next((key for key in CRITERION_TEXT if key in entry), None)
        status = entry.get("status")
        badge = Text(as_text(status), style=TONE_STYLE[tone_of("acceptance", as_text(status))]) if status else None
        rest = [(key, value) for key, value in entry.items() if key not in ("id", "status", text_key)]
        # verify first, the way a reader checks a criterion; any other key after it, as written.
        fields = sorted(rest, key=lambda pair: pair[0] != "verify")
        label = str(index) if entry.get("id") is None else str(entry["id"])
        if entry.get("id") is not None:
            ids.add(label)
            fields.append(("steps", key_runs(named.get(label, []))))
        _print_whole(label, entry.get(text_key) if text_key else "?", fields, badge)
    unknown = [f"{ref} (step {key_runs(keys)})" for ref, keys in named.items() if ref not in ids]
    if unknown:
        _print(Text.assemble(("  steps name criteria this plan does not have: ", "yellow"), "; ".join(unknown)))


def _render_non_goals(item, asked: bool) -> None:
    goals = _plan_list(item, "non_goals")
    if not goals and not asked:
        return
    count = f"({len(goals)})" if goals else "(none in this plan)"
    _print(Text.assemble("\n", ("Non-goals", "bold cyan"), " ", (count, "dim")))
    for index, entry in enumerate(goals):
        _print_whole(str(index), entry, [])


def _count_plan_lists(item) -> None:
    """The board stays short: one line per plan-level list, saying how to print it whole."""
    for name in PLAN_LISTS:
        entries = _plan_list(item, name)
        if entries:
            _print(
                Text.assemble(
                    "\n", (SECTION_TITLES[name], "bold cyan"), (f" ({len(entries)})  in full: --section {name}", "dim")
                )
            )


@click.command(
    "show",
    help="Read one exec-plan in the terminal, or one of its steps in full with --step. "
    "The board counts the plan's acceptance criteria and non-goals; --section acceptance or "
    "--section non_goals prints them in full.",
)
@harness_option
@click.argument("plan_id")
@click.option(
    "--section",
    "sections",
    multiple=True,
    type=click.Choice(SHOW_SECTIONS),
    help="Only print some sections. acceptance prints each criterion whole (id, criterion, verify, the steps "
    "naming it); non_goals prints each non-goal whole.",
)
@click.option(
    "--full",
    is_flag=True,
    help="Print notes verbatim instead of clipping to one line, and acceptance and non_goals in full.",
)
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

    for name in sections or (SHOW_SECTIONS if full else SECTIONS):
        if name == "acceptance":
            _render_acceptance(item, asked=bool(sections))
            continue
        if name == "non_goals":
            _render_non_goals(item, asked=bool(sections))
            continue
        entries = item.items(name)
        if not entries:
            continue
        console.print(f"\n[bold cyan]{SECTION_TITLES[name]}[/] [dim]({len(entries)})[/]")
        for index, entry in enumerate(entries):
            _render_item(name, index, entry, full)
    if not sections and not full:
        _count_plan_lists(item)
