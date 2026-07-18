from datetime import datetime, timedelta, timezone

from evo_cli.credentials.registry import credentials_dir, dig, load_entries

SOON = timedelta(minutes=10)


def parse_iso(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def find_expiry(entry):
    oauth = entry.get("oauth")
    if oauth:
        value = dig(entry.get("flat", {}), oauth["container"] + [oauth["expiry_field"]])
        if value:
            return value
    return entry.get("expiry")


def first_secret_preview(entry):
    for value in entry.get("flat", {}).values():
        if isinstance(value, str):
            text = value
        elif isinstance(value, dict):
            inner = next((item for item in value.values() if isinstance(item, str)), None)
            if inner is None:
                continue
            text = inner
        else:
            continue
        if len(text) <= 12:
            return "***"
        return f"{text[:4]}...{text[-4:]}"
    return "-"


def scan():
    root = credentials_dir()
    now = datetime.now(timezone.utc)
    rows = []
    for path, entry in load_entries():
        status = entry.get("status", "active")
        expiry = find_expiry(entry)
        parsed = parse_iso(expiry)
        health = "ok"
        if status == "deprecated":
            health = "deprecated"
        elif parsed is not None:
            if parsed < now:
                health = "EXPIRED"
            elif parsed - now < SOON:
                health = "expiring"
        rows.append(
            {
                "file": str(path.relative_to(root)),
                "service": entry.get("service", entry.get("id", "?")),
                "type": entry.get("type", "?"),
                "health": health,
                "expiry": expiry or "-",
                "secret": first_secret_preview(entry),
            }
        )
    rows.sort(key=lambda row: (row["health"] != "EXPIRED", row["file"]))
    return rows
