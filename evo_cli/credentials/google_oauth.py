import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from evo_cli.credentials.registry import dig, load_entries
from evo_cli.credentials.store import TZ, write_entry

TOKEN_URL = "https://oauth2.googleapis.com/token"

SERVICE_ALIASES = {
    "rclone": "rclone",
    "gmail": "gmail",
    "google-drive": "google_drive",
    "google_drive": "google_drive",
    "google-calendar": "google_calendar",
    "google_calendar": "google_calendar",
}


def resolve_creds(entry):
    flat = entry.get("flat", {})
    oauth = entry["oauth"]
    container = dig(flat, oauth["container"])
    if not isinstance(container, dict):
        return None, "token container missing"
    refresh_token = container.get("refresh_token")
    if not refresh_token:
        return None, "refresh_token missing"

    client_src = dig(flat, oauth["client_from"]) or {}
    is_rclone = entry.get("id") == "rclone"
    client_id = (os.environ.get("RCLONE_DRIVE_CLIENT_ID") if is_rclone else None) or client_src.get("client_id")
    client_secret = (os.environ.get("RCLONE_DRIVE_CLIENT_SECRET") if is_rclone else None) or client_src.get(
        "client_secret"
    )
    if not client_id or not client_secret:
        return None, "client_id/client_secret missing (set env for rclone)"

    return {
        "container": container,
        "oauth": oauth,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }, None


def post_refresh(creds):
    payload = urllib.parse.urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(TOKEN_URL, data=payload, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def select_entries(target):
    return [
        (path, entry)
        for path, entry in load_entries()
        if entry.get("oauth") and (target == "*" or entry.get("id") == target)
    ]


def refresh_entry(path, entry, creds):
    body = post_refresh(creds)
    access = body.get("access_token")
    if not access:
        raise ValueError("no access_token in response")

    expires_in = int(body.get("expires_in", 3600))
    expiry = (datetime.now(TZ) + timedelta(seconds=expires_in)).isoformat()

    oauth = creds["oauth"]
    creds["container"][oauth["access_field"]] = access
    creds["container"][oauth["expiry_field"]] = expiry
    if "expires_in" in creds["container"]:
        creds["container"]["expires_in"] = expires_in

    entry["expiry"] = expiry
    entry["status"] = "active"
    entry["last_rotated"] = datetime.now(TZ).isoformat()
    write_entry(path, entry)
    return expiry


def describe_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}"
    if isinstance(exc, urllib.error.URLError):
        return f"network error {exc}"
    return str(exc)
