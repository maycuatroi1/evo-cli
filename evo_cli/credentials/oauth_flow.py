import json
import secrets
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from evo_cli.credentials.google_oauth import TOKEN_URL
from evo_cli.credentials.registry import dig
from evo_cli.credentials.store import TZ, CredentialError, write_entry

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

SUCCESS_PAGE = b"""<!doctype html><meta charset="utf-8">
<title>evo cred auth</title>
<body style="font-family:system-ui;padding:3rem;max-width:32rem">
<h2>Authorised</h2><p>The token was written to your credential store. You can close this tab.</p>
</body>"""

FAILURE_PAGE = b"""<!doctype html><meta charset="utf-8">
<title>evo cred auth</title>
<body style="font-family:system-ui;padding:3rem;max-width:32rem">
<h2>Authorisation failed</h2><p>Check the terminal for details.</p>
</body>"""


def client_from_secrets_file(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CredentialError(f"cannot read OAuth client file {path}: {exc}") from exc

    block = data.get("installed") or data.get("web") or data
    client_id = block.get("client_id")
    client_secret = block.get("client_secret")
    if not client_id or not client_secret:
        raise CredentialError(
            f"{path} has no client_id/client_secret. Download the JSON from "
            "Console -> APIs & Services -> Credentials -> your OAuth client."
        )
    return client_id, client_secret


def client_from_entry(entry):
    oauth = entry.get("oauth") or {}
    source = dig(entry.get("flat", {}), oauth.get("client_from") or []) or {}
    return source.get("client_id"), source.get("client_secret")


def build_auth_url(client_id, redirect_uri, scopes, state):
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(client_id, client_secret, code, redirect_uri):
    payload = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = urllib.request.Request(TOKEN_URL, data=payload, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


class _Handler(BaseHTTPRequestHandler):
    result = None

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0]
        error = query.get("error", [None])[0]

        ok = bool(code) and not error
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(SUCCESS_PAGE if ok else FAILURE_PAGE)
        type(self).result = {"code": code, "state": state, "error": error}

    def log_message(self, *_args):
        return


def capture_code(timeout=300):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.timeout = timeout
    _Handler.result = None
    redirect_uri = f"http://127.0.0.1:{server.server_port}"

    def serve():
        while _Handler.result is None:
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    return server, redirect_uri, thread


def store_tokens(path, entry, body, scopes):
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    if not access:
        raise CredentialError("no access_token in Google's response")
    if not refresh:
        raise CredentialError(
            "Google returned no refresh_token. Revoke the app at "
            "https://myaccount.google.com/permissions and authorise again."
        )

    oauth = entry["oauth"]
    flat = entry.setdefault("flat", {})
    cursor = flat
    for part in oauth["container"][:-1]:
        cursor = cursor.setdefault(part, {})
    container = cursor.setdefault(oauth["container"][-1], {})

    expiry = (datetime.now(TZ) + timedelta(seconds=int(body.get("expires_in", 3600)))).isoformat()
    container[oauth["access_field"]] = access
    container["refresh_token"] = refresh
    container[oauth["expiry_field"]] = expiry
    container["scopes"] = list(scopes)

    entry["expiry"] = expiry
    entry["status"] = "active"
    entry["last_rotated"] = datetime.now(TZ).isoformat()
    write_entry(path, entry)
    return expiry


def new_state():
    return secrets.token_urlsafe(24)
