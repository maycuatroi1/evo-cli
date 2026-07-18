import json
import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

from evo_cli.credentials.doctor import parse_iso
from evo_cli.credentials.registry import SPECS, credentials_dir, dig, omelet_dir
from evo_cli.credentials.store import CredentialError, write_entry


def misc_spec(key):
    return {
        "path": f"misc/{key}.json",
        "id": key,
        "service": key,
        "category": "misc",
        "type": "unknown",
        "lifetime": "unknown",
        "description": "(auto-migrated, unmapped key - review category/type)",
        "rotate": "",
        "keys": [key],
    }


def build_entry(spec, source, today):
    present = [key for key in spec["keys"] if key in source]
    flat = {key: source[key] for key in present}

    entry = {
        "id": spec["id"],
        "service": spec["service"],
        "category": spec["category"],
        "description": spec.get("description", ""),
        "type": spec.get("type", "api_key"),
        "lifetime": spec.get("lifetime", "stable"),
        "status": spec.get("status", "active"),
        "rotate": spec.get("rotate", ""),
        "added": today,
        "last_rotated": None,
        "expiry": None,
    }

    oauth = spec.get("oauth")
    if oauth:
        entry["oauth"] = oauth
        expiry = dig(flat, oauth["container"] + [oauth["expiry_field"]])
        entry["expiry"] = expiry
        parsed = parse_iso(expiry)
        if parsed is not None and parsed < datetime.now(timezone.utc):
            entry["status"] = "expired"

    entry["flat"] = flat
    return entry, present


def plan(source_path, strict=False, merge=False):
    source_path = Path(os.path.expanduser(str(source_path)))
    if not source_path.exists():
        raise CredentialError(f"source config not found: {source_path}")
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CredentialError(f"invalid JSON in {source_path}: {exc}") from exc
    source = {key: value for key, value in source.items() if not key.startswith("_")}

    today = date.today().isoformat()
    covered = set()
    planned = []
    for spec in SPECS:
        entry, present = build_entry(spec, source, today)
        if present:
            covered.update(present)
            planned.append((spec["path"], entry, present, False))

    unmapped = sorted(set(source.keys()) - covered)
    if unmapped and strict:
        raise CredentialError(
            "keys not mapped in the registry: "
            + ", ".join(unmapped)
            + "\nAdd them to a spec in evo_cli/credentials/registry.py, or drop --strict to route them to misc/."
        )
    for key in unmapped:
        entry, present = build_entry(misc_spec(key), source, today)
        planned.append((f"misc/{key}.json", entry, present, True))

    root = credentials_dir()
    actions = []
    for rel, entry, present, is_misc in planned:
        dest = root / rel
        action = "create"
        new_keys = present
        if merge and dest.exists():
            try:
                existing = json.loads(dest.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {"flat": {}}
            existing_flat = existing.get("flat", {})
            new_keys = [key for key in present if key not in existing_flat]
            if not new_keys:
                continue
            for key in new_keys:
                existing_flat[key] = source[key]
            existing["flat"] = existing_flat
            existing["last_rotated"] = datetime.now(timezone.utc).isoformat()
            entry = existing
            action = "update"
        actions.append(
            {
                "action": action,
                "rel": rel,
                "dest": dest,
                "entry": entry,
                "keys": new_keys,
                "misc": is_misc,
            }
        )

    return {
        "source_path": source_path,
        "source_keys": len(source),
        "actions": actions,
        "unmapped": unmapped,
    }


def folder_has_files():
    root = credentials_dir()
    return root.exists() and any(root.rglob("*.json"))


def apply(result):
    source_path = result["source_path"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path(str(source_path) + f".bak.{stamp}")
    shutil.copy2(source_path, backup)

    for item in result["actions"]:
        write_entry(item["dest"], item["entry"])

    for path in (credentials_dir(), omelet_dir()):
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass

    return backup
