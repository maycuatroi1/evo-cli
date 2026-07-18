from __future__ import annotations

from datetime import datetime

import rich_click as click

from evo_cli.commands.harness._dag import step_key
from evo_cli.commands.harness._model import find_plan, tone_of
from evo_cli.commands.harness._mutate import update_item
from evo_cli.commands.harness._paths import find_manifest, harness_option
from evo_cli.commands.harness._render import interactive, one_line, title_width
from evo_cli.commands.harness.views import step_board
from evo_cli.console import console

STEP_STATUS = ["done", "in_progress", "pending", "blocked"]
DEBT_STATUS = ["fixed", "open"]
QUESTION_STATUS = ["answered", "open"]
REPO_STATUS = ["merged", "done", "in_progress", "pending", "not-needed"]

DATE_KEY = {"done": "done_at", "fixed": "fixed_at", "answered": "answered_at", "merged": "merged_at"}
NEXT_STATUS = {"pending": "in_progress", "blocked": "in_progress", "in_progress": "done", "done": "done"}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _write(manifest_path, plan_id, section, index, status, note, no_date):
    item = find_plan(manifest_path, plan_id)
    entries = item.items(section)
    if index < 0 or index >= len(entries):
        raise click.ClickException(f"{section} holds {len(entries)} items (0..{len(entries) - 1}).")

    updates = {"status": status}
    date_key = DATE_KEY.get(status)
    if date_key and not no_date:
        updates[date_key] = _today()
    if note:
        updates["note"] = note

    result = update_item(item.path, section, index, updates)
    label = entries[index].get("what") or entries[index].get("repo") or entries[index].get("issue") or "?"
    console.print(
        f"[green]DONE[/]  {item.id} / {section}[{index}]: [dim]{result['old'].get('status')}[/] -> [bold]{status}[/]"
    )
    console.print(f"      {' '.join(str(label).split())[:100]}")
    for key, value in updates.items():
        if key != "status":
            console.print(f"      [dim]{key}: {value}[/]")
    console.print(f"      [dim]{item.path}[/]")


def _resolve_step(item, key) -> int:
    entries = item.items("steps")
    wanted = str(key)
    for index, entry in enumerate(entries):
        if step_key(entry, index) == wanted:
            return index
    known = ", ".join(step_key(e, i) for i, e in enumerate(entries))
    raise click.ClickException(f"No step {wanted!r} in {item.id}. Available: {known}")


def _pick(item, key, status):
    """Interactive picker, but only when questionary is installed and we own a real terminal."""
    try:
        import questionary
    except ImportError:
        console.print(f"\n[dim]Set a status:[/] evo harness step {item.id} <id> {'|'.join(STEP_STATUS)}")
        return None, None

    entries = item.items("steps")
    width = title_width(reserved=22)
    if key is None:
        choices = [
            questionary.Choice(
                title=f"{step_key(entry, index):>3}  {str(entry.get('status') or '-'):<12} "
                f"{one_line(entry.get('what') or entry.get('issue'), width)}",
                value=step_key(entry, index),
            )
            for index, entry in enumerate(entries)
        ]
        pending = next((i for i, e in enumerate(entries) if tone_of("steps", e.get("status")) != "ok"), 0)
        key = questionary.select("Pick a step:", choices=choices, default=choices[pending], qmark=">").ask()
        if key is None:
            raise click.Abort()

    if status is None:
        index = _resolve_step(item, key)
        current = str(entries[index].get("status") or "")
        suggested = NEXT_STATUS.get(current, STEP_STATUS[0])
        status = questionary.select(
            f"Step {key} is {current or '-'}. New status:",
            choices=STEP_STATUS,
            default=suggested if suggested in STEP_STATUS else STEP_STATUS[0],
            qmark=">",
        ).ask()
        if status is None:
            raise click.Abort()
    return key, status


@click.command("step", help="Show the step board, or set the status of one step.")
@harness_option
@click.argument("plan_id")
@click.argument("key", required=False)
@click.argument("status", type=click.Choice(STEP_STATUS), required=False)
@click.option("--note", help="Append a note to the step.")
@click.option("--no-date", is_flag=True, help="Do not add done_at.")
@click.option("--no-input", is_flag=True, help="Print the board and stop. For scripts and CI.")
def step(harness_path, plan_id, key, status, note, no_date, no_input):
    """KEY is the step's `id` (or `order` in older plans), the same number depends_on quotes."""
    manifest_path = find_manifest(harness_path)
    item = find_plan(manifest_path, plan_id)

    if key is None or status is None:
        step_board(item, highlight=key)
        if no_input or not interactive() or not item.items("steps"):
            if not no_input:
                console.print(f"\n[dim]Set a status:[/] evo harness step {item.id} <id> {'|'.join(STEP_STATUS)}")
            return
        console.print()
        key, status = _pick(item, key, status)
        if key is None or status is None:
            return

    _write(manifest_path, plan_id, "steps", _resolve_step(item, key), status, note, no_date)
    console.print()
    step_board(find_plan(manifest_path, plan_id), highlight=key)


@click.command("debt", help="Set the status of one tech-debt item (INDEX is its position in `show`).")
@harness_option
@click.argument("plan_id")
@click.argument("index", type=int)
@click.argument("status", type=click.Choice(DEBT_STATUS))
@click.option("--note", help="Append a note.")
@click.option("--no-date", is_flag=True, help="Do not add fixed_at.")
def debt(harness_path, plan_id, index, status, note, no_date):
    _write(find_manifest(harness_path), plan_id, "tech_debt", index, status, note, no_date)


@click.command("question", help="Set the status of one open question (INDEX is its position in `show`).")
@harness_option
@click.argument("plan_id")
@click.argument("index", type=int)
@click.argument("status", type=click.Choice(QUESTION_STATUS))
@click.option("--note", help="Write the answer into note.")
@click.option("--no-date", is_flag=True, help="Do not add answered_at.")
def question(harness_path, plan_id, index, status, note, no_date):
    _write(find_manifest(harness_path), plan_id, "open_questions", index, status, note, no_date)


@click.command("repo", help="Set the status of one repo inside a plan (INDEX is its position in `show`).")
@harness_option
@click.argument("plan_id")
@click.argument("index", type=int)
@click.argument("status", type=click.Choice(REPO_STATUS))
@click.option("--note", help="Append a note.")
@click.option("--no-date", is_flag=True, help="Do not add merged_at.")
def repo(harness_path, plan_id, index, status, note, no_date):
    _write(find_manifest(harness_path), plan_id, "repos", index, status, note, no_date)
