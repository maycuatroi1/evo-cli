from __future__ import annotations

import copy
import re
from pathlib import Path

import rich_click as click
import yaml

PLAIN_SAFE = re.compile(r"^[A-Za-z][\w./#@-]*$")


def _fmt(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if PLAIN_SAFE.match(text):
        return text
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
    for key, value in updates.items():
        root = yaml.compose(text)
        item_node = _item_node(root, section, index)
        pairs = _pairs(item_node)
        if key in pairs:
            _, value_node = pairs[key]
            start, end = value_node.start_mark.index, value_node.end_mark.index
            text = text[:start] + _fmt(value) + text[end:]
            continue

        anchor_key = next((k for k in ("status", "order", "severity") if k in pairs), None)
        if anchor_key is None:
            anchor_key = item_node.value[0][0].value
        anchor_value = pairs[anchor_key][1] if anchor_key in pairs else item_node.value[0][1]
        indent = " " * item_node.value[0][0].start_mark.column
        insert_at = _line_end(text, anchor_value.end_mark.index)
        text = text[:insert_at] + f"{indent}{key}: {_fmt(value)}\n" + text[insert_at:]
    return text


def update_item(path: Path, section: str, index: int, updates: dict) -> dict:
    """Rewrite one item in place, then refuse to save unless the reparse matches exactly.

    Editing the text rather than round-tripping through yaml.dump is what keeps comments,
    key order, and block scalars intact. The verify step is what makes that safe.
    """
    original = path.read_text(encoding="utf-8")
    before = yaml.safe_load(original) or {}

    expected = copy.deepcopy(before)
    target = expected[section][index]
    if not isinstance(target, dict):
        raise click.ClickException(
            f"{section}[{index}] is a bare string, not a mapping, so it has no status to set. "
            f"Give it `what:` and `status:` keys first."
        )
    old = {k: target.get(k) for k in updates}
    target.update(updates)

    updated = _apply(original, section, index, updates)
    after = yaml.safe_load(updated) or {}
    if after != expected:
        raise click.ClickException(
            f"Refusing to write {path.name}: the rewrite does not match the expected result. File left untouched."
        )

    path.write_text(updated, encoding="utf-8")
    return {"old": old, "new": updates}
