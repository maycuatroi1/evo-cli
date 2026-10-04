from __future__ import annotations

import copy
import re
from pathlib import Path

import rich_click as click
import yaml

from evo_cli.commands.harness import _hub
from evo_cli.commands.harness._mirror import hub_project
from evo_cli.commands.harness._model import Plan, join_text, load_plan_file
from evo_cli.commands.harness._paths import yaml_load

PLAIN_SAFE = re.compile(r"^[A-Za-z][\w./#@-]*$")


def _fmt(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if PLAIN_SAFE.match(text):
        return text
    if not text.isprintable():
        # A line break inside single quotes folds into a space when read back, so text holding one
        # (an appended note) or any other control character goes out double-quoted with escapes.
        quoted = yaml.safe_dump(text, default_style='"', allow_unicode=True, width=1_000_000)
        return quoted.removesuffix("\n...\n").rstrip("\n")
    return "'" + text.replace("'", "''") + "'"


def _item_node(root, section: str, index: int):
    section_node = None
    for key_node, value_node in root.value:
        if key_node.value == section:
            section_node = value_node
            break
    if section_node is None or not hasattr(section_node, "value"):
        raise click.ClickException(f"This plan has no {section!r} section.")
    items = list(section_node.value)
    if index < 0 or index >= len(items):
        raise click.ClickException(f"Section {section!r} holds {len(items)} items, so index {index} does not exist.")
    return items[index]


def _pairs(item_node) -> dict:
    return {key_node.value: (key_node, value_node) for key_node, value_node in item_node.value}


def _line_end(text: str, position: int) -> int:
    newline = text.find("\n", position)
    return len(text) if newline == -1 else newline + 1


def _apply(text: str, section: str, index: int, updates: dict) -> str:
    # Stays on the pure-python loader on purpose. libyaml drops a leading BOM before it
    # starts counting, so its node marks come back one lower than the offsets into `text`
    # that the splices below rely on.
    for key, value in updates.items():
        root = yaml.compose(text)
        item_node = _item_node(root, section, index)
        pairs = _pairs(item_node)
        if key in pairs:
            _, value_node = pairs[key]
            start, end = value_node.start_mark.index, value_node.end_mark.index
            replacement = _fmt(value)
            if getattr(value_node, "style", None) in ("|", ">"):
                # A block scalar's span runs through the line breaks after its last line, so they go
                # back after the new value or the next key would land on the same line.
                old = text[start:end]
                tail = old[len(old.rstrip(" \t\r\n")) :]
                replacement += tail[tail.find("\n") :] if "\n" in tail else ""
            text = text[:start] + replacement + text[end:]
            continue

        anchor_key = next((k for k in ("status", "order", "severity") if k in pairs), None)
        if anchor_key is None:
            anchor_key = item_node.value[0][0].value
        anchor_value = pairs[anchor_key][1] if anchor_key in pairs else item_node.value[0][1]
        indent = " " * item_node.value[0][0].start_mark.column
        anchor_end = anchor_value.end_mark.index
        # After a block scalar the span already ends at the start of a line: insert right there.
        insert_at = anchor_end if text[anchor_end - 1 : anchor_end] == "\n" else _line_end(text, anchor_end)
        text = text[:insert_at] + f"{indent}{key}: {_fmt(value)}\n" + text[insert_at:]
    return text


def _manifest_beside(path: Path) -> Path:
    """harness.yaml of the harness holding plans/<area>/<plan>.yaml."""
    parents = path.resolve().parents
    return (parents[2] if len(parents) > 2 else parents[-1]) / "harness.yaml"


def _hub_of(manifest_path: Path) -> str | None:
    return hub_project(manifest_path) if manifest_path.is_file() else None


def update_item(
    path: Path, section: str, index: int, updates: dict, append: dict | None = None, manifest_path: Path | None = None
) -> dict:
    """Set `updates` and append the text in `append` (a line of its own) on one item of the plan at `path`.

    When harness.yaml has hub.project the plan lives on the evo-agents hub and the file is its read-only
    copy: the change goes through `evo-agents hub plan patch` and the copy is rewritten by an export
    (`_hub.patch_item`). Otherwise the file is edited in place.
    """
    append = {key: value for key, value in (append or {}).items() if value and value.strip()}
    manifest_path = manifest_path or _manifest_beside(path)
    project = _hub_of(manifest_path)
    if project is not None:
        result = _hub.patch_item(manifest_path, load_plan_file(path), project, section, index, updates, append)
        return {**result, "hub": project}
    return {**_update_file(path, section, index, updates, append), "hub": None, "path": path}


def _update_file(path: Path, section: str, index: int, updates: dict, append: dict) -> dict:
    """Rewrite one item in place, then refuse to save unless the reparse matches exactly.

    Editing the text rather than round-tripping through yaml.dump is what keeps comments,
    key order, and block scalars intact. The verify step is what makes that safe.
    """
    original = path.read_text(encoding="utf-8")
    before = yaml_load(original) or {}

    expected = copy.deepcopy(before)
    items = expected.get(section) if isinstance(expected, dict) else None
    if not isinstance(items, list) or not 0 <= index < len(items):
        raise click.ClickException(f"{path.name} has no item {index} in {section!r}.")
    target = items[index]
    if not isinstance(target, dict):
        raise click.ClickException(
            f"{section}[{index}] is a bare string, not a mapping, so it has no status to set. "
            f"Give it `what:` and `status:` keys first."
        )
    values = dict(updates)
    for key, addition in append.items():
        joined = join_text(target.get(key), addition, f"{section}[{index}].{key}")
        if joined != target.get(key):
            values[key] = joined
    old = {k: target.get(k) for k in values}
    target.update(values)

    try:
        updated = _apply(original, section, index, values)
        after = yaml_load(updated) or {}
    except yaml.YAMLError:
        updated, after = original, None
    if after != expected:
        raise click.ClickException(
            f"Refusing to write {path.name}: the rewrite does not match the expected result. File left untouched."
        )

    if updated != original:
        path.write_text(updated, encoding="utf-8")
    return {"old": old, "new": values, "changed": updated != original}


def complete_plan(plan: Plan, manifest_path: Path | None = None) -> Plan:
    """Move an active plan into the completed area: on the hub when harness.yaml has hub.project (the
    hub refuses while a step is not done, then the export moves the copy), else by moving the file
    without rewriting its YAML."""
    if plan.area == "completed":
        return plan
    if plan.area != "active":
        raise click.ClickException(f"Plan {plan.id!r} is in an unsupported area: {plan.area!r}.")

    manifest_path = manifest_path or _manifest_beside(plan.path)
    project = _hub_of(manifest_path)
    if project is not None:
        return load_plan_file(_hub.complete(manifest_path, plan, project))

    destination_dir = plan.path.parent.parent / "completed"
    destination = destination_dir / plan.path.name
    if destination.exists():
        raise click.ClickException(f"Cannot complete {plan.id!r}: {destination} already exists.")

    destination_dir.mkdir(parents=True, exist_ok=True)
    plan.path.rename(destination)
    return load_plan_file(destination)
