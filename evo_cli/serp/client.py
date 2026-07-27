import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from evo_cli.serp import creds, install
from evo_cli.serp.errors import SerpError

SEARCH_URL = "https://serpapi.com/search.json"
ACCOUNT_URL = "https://serpapi.com/account.json"
LOCATIONS_URL = "https://serpapi.com/locations.json"

DEFAULT_ENGINE = "google"

# Google verticals. `tbm` switches the vertical inside the google engine;
# scholar is a separate engine entirely.
SEARCH_TYPES = {
    "web": {"result_key": "organic_results"},
    "news": {"tbm": "nws", "result_key": "news_results"},
    "images": {"tbm": "isch", "result_key": "images_results"},
    "videos": {"tbm": "vid", "result_key": "video_results"},
    "shopping": {"tbm": "shop", "result_key": "shopping_results"},
    "scholar": {"engine": "google_scholar", "result_key": "organic_results"},
}

RESULT_KEYS = (
    "organic_results",
    "news_results",
    "images_results",
    "video_results",
    "shopping_results",
    "local_results",
    "jobs_results",
)


def build_params(
    query,
    search_type="web",
    num=None,
    start=None,
    location=None,
    hl=None,
    gl=None,
    domain=None,
    device=None,
    safe=None,
    engine=None,
    extra=(),
):
    spec = SEARCH_TYPES.get(search_type)
    if spec is None:
        raise SerpError(f"unknown search type: {search_type}")

    params = {"engine": engine or spec.get("engine") or DEFAULT_ENGINE, "q": query}
    if spec.get("tbm") and not engine:
        params["tbm"] = spec["tbm"]
    for key, value in (
        ("num", num),
        ("start", start),
        ("location", location),
        ("hl", hl),
        ("gl", gl),
        ("google_domain", domain),
        ("device", device),
        ("safe", safe),
    ):
        if value not in (None, ""):
            params[key] = value

    for item in extra:
        if "=" not in item:
            raise SerpError(f"extra parameter must be key=value, got: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise SerpError(f"extra parameter has an empty key: {item}")
        params[key] = value
    return params


def result_key_for(search_type, payload=None):
    spec = SEARCH_TYPES.get(search_type) or {}
    key = spec.get("result_key")
    if payload is not None and key and key not in payload:
        for candidate in RESULT_KEYS:
            if isinstance(payload.get(candidate), list):
                return candidate
    return key or "organic_results"


def _check_payload(payload):
    if not isinstance(payload, dict):
        raise SerpError("SerpApi returned a payload that is not a JSON object")
    error = payload.get("error")
    if error:
        raise SerpError(str(error))
    return payload


def cli_argv(params, fields=None, jq=None):
    argv = ["search"]
    if fields:
        argv += ["--fields", fields]
    if jq:
        argv += ["--jq", jq]
    argv += [f"{key}={value}" for key, value in params.items()]
    return argv


def run_binary(binary, argv, key, timeout=90, capture=True):
    # The key goes through the environment, never argv: process listings are
    # readable by every user on the box.
    env = dict(os.environ)
    env[creds.ENV_VAR] = key
    try:
        return subprocess.run(
            [str(binary), *argv],
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SerpError(f"serpapi CLI timed out after {timeout}s") from exc
    except OSError as exc:
        raise SerpError(f"could not run {binary}: {exc}") from exc


def search_via_cli(binary, params, key, timeout=90, fields=None, jq=None):
    result = run_binary(binary, cli_argv(params, fields=fields, jq=jq), key, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise SerpError(f"serpapi CLI failed: {detail[-1] if detail else f'exit {result.returncode}'}")
    body = (result.stdout or "").strip()
    if not body:
        raise SerpError("serpapi CLI returned no output")
    try:
        return _check_payload(json.loads(body))
    except ValueError as exc:
        raise SerpError(f"serpapi CLI returned non-JSON output: {body[:200]}") from exc


def get_json(url, params, timeout=60):
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(clean)}", headers={"User-Agent": "evo-cli"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(detail).get("error") or detail
        except ValueError:
            message = detail
        raise SerpError(f"HTTP {exc.code} from SerpApi: {str(message).strip()[:300]}") from exc
    except urllib.error.URLError as exc:
        raise SerpError(f"cannot reach SerpApi: {exc.reason}") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise SerpError(f"SerpApi returned non-JSON output: {body[:200]}") from exc


def search_via_http(params, key, timeout=60):
    payload = dict(params)
    payload["api_key"] = key
    return _check_payload(get_json(SEARCH_URL, payload, timeout=timeout))


def resolve_transport(transport, binary=None):
    """Return (transport, binary). 'auto' prefers the official CLI when installed."""
    if transport == "http":
        return "http", None
    binary = binary or install.find_binary()
    if transport == "cli":
        if not binary:
            raise SerpError("serpapi CLI not installed. Run: evo setup serp   (or use --transport http)")
        return "cli", binary
    return ("cli", binary) if binary else ("http", None)


def merge_pages(payloads, result_key):
    """Concatenate the result list across pages, dropping repeats by link."""
    merged = dict(payloads[0])
    combined = []
    seen = set()
    for payload in payloads:
        for item in payload.get(result_key) or []:
            marker = item.get("link") or item.get("original") or json.dumps(item, sort_keys=True)[:200]
            if marker in seen:
                continue
            seen.add(marker)
            combined.append(item)
    if combined or result_key in merged:
        merged[result_key] = combined
    return merged


def search(
    params,
    search_type="web",
    pages=1,
    transport="auto",
    key=None,
    binary=None,
    timeout=90,
    fields=None,
    jq=None,
    on_page=None,
):
    key = key or creds.api_key()
    transport, binary = resolve_transport(transport, binary)
    per_page = int(params.get("num") or 10)
    pages = max(1, int(pages or 1))

    payloads = []
    result_key = None
    for index in range(pages):
        page_params = dict(params)
        if index:
            page_params["start"] = int(params.get("start") or 0) + index * per_page
        if transport == "cli":
            payload = search_via_cli(binary, page_params, key, timeout=timeout, fields=fields, jq=jq)
        else:
            payload = search_via_http(page_params, key, timeout=timeout)
        payloads.append(payload)
        if result_key is None:
            result_key = result_key_for(search_type, payload)
        if on_page:
            on_page(index + 1, payload)
        if pages > 1 and not (payload.get(result_key) or []):
            break

    result_key = result_key or result_key_for(search_type)
    merged = merge_pages(payloads, result_key) if len(payloads) > 1 else payloads[0]
    return merged, result_key, transport


def account(key=None, timeout=30):
    key = key or creds.api_key()
    return _check_payload(get_json(ACCOUNT_URL, {"api_key": key}, timeout=timeout))


def locations(query, limit=10, timeout=30):
    payload = get_json(LOCATIONS_URL, {"q": query, "limit": limit}, timeout=timeout)
    if isinstance(payload, dict):
        _check_payload(payload)
        return []
    return payload
