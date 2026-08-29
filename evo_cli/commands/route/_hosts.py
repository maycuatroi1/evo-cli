from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

MARK_START = "# >>> evo route >>>"
MARK_END = "# <<< evo route <<<"

BLOCK_RE = re.compile(
    re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\r?\n?",
    re.DOTALL,
)


def hosts_path():
    if os.name == "nt":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return Path(root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def read_block(path=None):
    path = Path(path) if path else hosts_path()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    match = BLOCK_RE.search(raw)
    if not match:
        return {}
    entries = {}
    for line in match.group(0).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            entries[parts[1]] = parts[0]
    return entries


def backup(path=None):
    path = Path(path) if path else hosts_path()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(path.name + f".evo-route.{stamp}")
    shutil.copy2(path, target)
    return target


def write_block(entries, path=None, make_backup=True):
    path = Path(path) if path else hosts_path()
    raw = path.read_text(encoding="utf-8", errors="replace")
    if make_backup:
        backup(path)

    raw = BLOCK_RE.sub("", raw)
    if raw and not raw.endswith("\n"):
        raw += "\n"

    if entries:
        lines = [MARK_START]
        for host in sorted(entries):
            lines.append(f"{entries[host]}  {host}")
        lines.append(MARK_END)
        raw += "\n".join(lines) + "\n"

    path.write_text(raw, encoding="utf-8")
    return path


def pin(host, ip, path=None, make_backup=True):
    entries = read_block(path)
    entries[host] = ip
    write_block(entries, path=path, make_backup=make_backup)
    return entries


def unpin(host, path=None, make_backup=True):
    entries = read_block(path)
    if host not in entries:
        return entries, False
    entries.pop(host)
    write_block(entries, path=path, make_backup=make_backup)
    return entries, True


def clear(path=None, make_backup=True):
    write_block({}, path=path, make_backup=make_backup)
