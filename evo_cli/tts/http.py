import json
import urllib.error
import urllib.parse
import urllib.request

from evo_cli.tts.errors import TtsError


def _describe(body):
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return body.strip()[:400]
    error = payload.get("error")
    if isinstance(error, dict):
        return f"{error.get('code', '')} {error.get('message', '')}".strip()
    if isinstance(error, str):
        return error
    if payload.get("error_message"):
        return str(payload["error_message"])
    return json.dumps(payload, ensure_ascii=False)[:400]


def request(url, method="GET", headers=None, payload=None, timeout=180):
    body = None
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "evo-cli")
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        detail = _describe(exc.read().decode("utf-8", "replace"))
        raise TtsError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise TtsError(f"{method} {url} -> {exc.reason}") from exc


def request_json(url, method="GET", headers=None, payload=None, timeout=180):
    body, _ = request(url, method=method, headers=headers, payload=payload, timeout=timeout)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise TtsError(f"{method} {url} -> response is not JSON") from exc


def with_query(url, params):
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    if not clean:
        return url
    return f"{url}?{urllib.parse.urlencode(clean)}"
