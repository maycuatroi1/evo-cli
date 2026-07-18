import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evo_cli.credentials.registry import (
    GENERATED_NOTE,
    META_FIELDS,
    config_path,
    credentials_dir,
    deep_merge,
    load_entries,
    spec_for_flat_key,
)

TZ = timezone(timedelta(hours=7))


class CredentialError(RuntimeError):
    pass


def _chmod(path, mode):
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def require_folder():
    root = credentials_dir()
    if not root.exists():
        raise CredentialError(f"credentials folder not found: {root}\nrun `evo cred migrate` first.")
    return root


def write_entry(path, entry):
    ordered = {key: entry[key] for key in META_FIELDS if key in entry}
    ordered["flat"] = entry.get("flat", {})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _chmod(tmp, 0o600)
    os.replace(tmp, path)


def compile_flat(include_legacy=False):
    require_folder()
    merged = {}
    count = 0
    skipped = []
    for path, entry in load_entries():
        flat = entry.get("flat")
        if not isinstance(flat, dict):
            continue
        if entry.get("status") == "deprecated" and not include_legacy:
            skipped.append(entry.get("id", path.name))
            continue
        deep_merge(merged, flat)
        count += 1

    out = {"_generated": GENERATED_NOTE}
    out.update(merged)

    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", delete=False, dir=str(target.parent), encoding="utf-8")
    try:
        json.dump(out, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.close()
        _chmod(handle.name, 0o600)
        os.replace(handle.name, target)
    except Exception:
        if os.path.exists(handle.name):
            os.unlink(handle.name)
        raise

    return count, skipped, target


def read_flat():
    target = config_path()
    if not target.exists():
        raise CredentialError(f"config not found: {target}\nrun `evo cred compile` to generate it.")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CredentialError(f"invalid JSON in {target}: {exc}") from exc


def get_value(key_path):
    cursor = read_flat()
    for part in key_path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            raise CredentialError(f"key not found: {key_path} (failed at '{part}')")
        cursor = cursor[part]
    return cursor


def find_file_for_top_key(top_key):
    for path, entry in load_entries():
        if top_key in entry.get("flat", {}):
            return path, entry

    root = credentials_dir()
    spec = spec_for_flat_key(top_key)
    if spec:
        path = root / spec["path"]
        if path.exists():
            return path, json.loads(path.read_text(encoding="utf-8"))
        entry = {
            "id": spec["id"],
            "service": spec["service"],
            "category": spec["category"],
            "description": spec.get("description", ""),
            "type": spec.get("type", "api_key"),
            "lifetime": spec.get("lifetime", "stable"),
            "status": spec.get("status", "active"),
            "rotate": spec.get("rotate", ""),
            "added": None,
            "last_rotated": None,
            "expiry": None,
            "flat": {},
        }
        if spec.get("oauth"):
            entry["oauth"] = spec["oauth"]
        return path, entry

    entry = {
        "id": top_key,
        "service": top_key,
        "category": "tools",
        "description": "(added via `evo cred add`)",
        "type": "api_key",
        "lifetime": "stable",
        "status": "active",
        "rotate": "",
        "added": None,
        "last_rotated": None,
        "expiry": None,
        "flat": {},
    }
    return root / "tools" / f"{top_key}.json", entry


def set_value(key_path, value):
    parts = key_path.split(".")
    if not all(parts):
        raise CredentialError(f"invalid key path: {key_path}")

    path, entry = find_file_for_top_key(parts[0])
    flat = entry.setdefault("flat", {})

    cursor = flat
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            cursor[part] = {}
        cursor = cursor[part]
    leaf = parts[-1]
    existed = leaf in cursor
    cursor[leaf] = value

    entry["last_rotated"] = datetime.now(TZ).isoformat()
    if entry.get("added") is None:
        entry["added"] = datetime.now(TZ).date().isoformat()

    write_entry(path, entry)
    return path, existed


def relative_to_store(path):
    root = credentials_dir()
    try:
        return Path(path).relative_to(root)
    except ValueError:
        return Path(path)
