from __future__ import annotations

import json
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import rich_click as click

from evo_cli.commands.harness._dag import plan_graphs, seam_graph
from evo_cli.commands.harness._git import overlay
from evo_cli.commands.harness._model import cluster, digest, find_plan, load_plans, load_seams

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX = WEB_DIR / "index.html"

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".map": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
}

MISSING_BUNDLE = (
    "The dashboard bundle is missing. A release wheel ships it prebuilt; a git checkout has to build "
    "it once:\n  cd web && npm install && npm run build"
)


def bundle_ready() -> bool:
    return INDEX.is_file()


def _content_type(path: Path) -> str:
    return TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "evo-harness"
    sys_version = ""
    manifest_path: Path = Path()

    def log_message(self, *args):  # noqa: A003 - silence the default stderr access log
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if route == "/api/stream":
                self._stream()
            elif route.startswith("/api/"):
                self._api(route, query)
            else:
                self._static(route)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:  # a dashboard must not die because one plan has broken YAML
            self._json({"error": str(exc)}, status=500)

    def _send(self, body: bytes, content_type: str, status: int = 200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200):
        self._send(json.dumps(payload, default=str).encode("utf-8"), "application/json; charset=utf-8", status)

    def _static(self, route: str):
        if not bundle_ready():
            self._send(f"<pre>{MISSING_BUNDLE}</pre>".encode(), "text/html; charset=utf-8", status=503)
            return
        relative = route.lstrip("/") or "index.html"
        target = (WEB_DIR / relative).resolve()
        # A single-page app answers every unknown route with index.html, but only for paths that
        # stay inside the bundle. Anything escaping it is a traversal attempt, not a client route.
        if WEB_DIR not in target.parents and target != WEB_DIR:
            self._json({"error": "not found"}, status=404)
            return
        if not target.is_file():
            target = INDEX
        self._send(target.read_bytes(), _content_type(target))

    def _api(self, route: str, query: dict):
        manifest = self.manifest_path
        if route == "/api/state":
            self._json(
                {
                    "digest": digest(manifest),
                    "generatedAt": time.time(),
                    "cluster": cluster(manifest),
                    "seams": load_seams(manifest),
                    "seamGraph": seam_graph(manifest),
                    "plans": [p.summary() for p in load_plans(manifest)],
                }
            )
            return

        parts = route.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "plans":
            try:
                plan = find_plan(manifest, parts[2])
            except click.ClickException as exc:
                self._json({"error": exc.format_message()}, status=404)
                return
            if len(parts) == 3:
                self._json({"plan": plan.detail(), "graphs": plan_graphs(plan)})
                return
            if len(parts) == 4 and parts[3] == "git":
                fetch = query.get("fetch", ["0"])[0] in ("1", "true", "yes")
                self._json(overlay(manifest, plan, fetch=fetch))
                return
        self._json({"error": f"no route {route}"}, status=404)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last = None
        while True:
            current = digest(self.manifest_path)
            if current != last:
                last = current
                self.wfile.write(f"data: {json.dumps({'digest': current})}\n\n".encode())
            else:
                self.wfile.write(b": ping\n\n")
            self.wfile.flush()
            time.sleep(1.5)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # SO_REUSEADDR on Windows lets a second process bind a port that is already served, and
    # requests then go to whichever socket wins. Starting a second `serve` on the same port has
    # to fail loudly instead of appearing to work while the old process answers.
    allow_reuse_address = False


def build_server(manifest_path: Path, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"manifest_path": manifest_path})
    try:
        return Server((host, port), handler)
    except OSError as exc:
        raise click.ClickException(
            f"Cannot bind {host}:{port} ({exc}). Another `evo harness serve` may already own it - "
            f"pass --port to pick another one."
        ) from exc
