import base64
import io
import json
import time
import urllib.error
import urllib.request

from evo_cli.imaging import load_pillow
from evo_cli.imaging.creds import gemini_api_key
from evo_cli.imaging.errors import ImagingError

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DEFAULT_MODEL = "gemini-3-pro-image"
SIZES = ("1K", "2K", "4K")
DEFAULT_SIZE = "4K"
UPLOAD_EDGE = 2048
REQUEST_TIMEOUT = 900
RETRY_STATUSES = (429, 500, 503)
RETRIES = 3
RETRY_BACKOFF = 5.0

PROMPT = (
    "Upscale and restore this graphic asset to maximum sharpness. Keep the composition, colors, "
    "shapes, materials, ornament and every design element exactly identical - do not add, remove, "
    "move or redraw anything. Only recover fine detail and crisp edges that blur destroyed."
)


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
    return json.dumps(payload, ensure_ascii=False)[:400]


def _request_json(url, headers, payload, timeout):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = dict(headers)
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("User-Agent", "evo-cli")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = _describe(exc.read().decode("utf-8", "replace"))
        failure = ImagingError(f"POST {url} -> HTTP {exc.code}: {detail}")
        failure.status = exc.code
        raise failure from exc
    except urllib.error.URLError as exc:
        raise ImagingError(f"POST {url} -> {exc.reason}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ImagingError(f"POST {url} -> response is not JSON") from exc


def _post(payload, model, timeout):
    url = API_URL.format(model=model)
    headers = {"x-goog-api-key": gemini_api_key()}
    last = None
    for attempt in range(RETRIES):
        try:
            return _request_json(url, headers, payload, timeout)
        except ImagingError as exc:
            if getattr(exc, "status", None) not in RETRY_STATUSES:
                raise
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise last


def prepare_upload(image, edge=UPLOAD_EDGE):
    Image = load_pillow()
    frame = image.copy()
    frame.thumbnail((edge, edge), Image.LANCZOS)
    buffer = io.BytesIO()
    frame.save(buffer, format="PNG")
    return buffer.getvalue()


def build_payload(png_bytes, size=DEFAULT_SIZE, prompt=None):
    if size not in SIZES:
        raise ImagingError(f"gemini does not support size '{size}' (supported: {', '.join(SIZES)})")
    return {
        "contents": [
            {
                "parts": [
                    {"text": prompt or PROMPT},
                    {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png_bytes).decode()}},
                ]
            }
        ],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"imageSize": size}},
    }


def _extract_image(payload):
    candidates = payload.get("candidates") or []
    for candidate in candidates:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                return base64.b64decode(inline["data"])
    blocked = (payload.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise ImagingError(f"Gemini refused the prompt: {blocked}")
    reason = candidates[0].get("finishReason") if candidates else None
    raise ImagingError(f"Gemini returned no image (finishReason={reason or 'unknown'})")


def upscale(image, size=DEFAULT_SIZE, model=DEFAULT_MODEL, prompt=None, timeout=REQUEST_TIMEOUT, poster=None):
    payload = build_payload(prepare_upload(image), size=size, prompt=prompt)
    body = (poster or _post)(payload, model or DEFAULT_MODEL, timeout)
    return _extract_image(body)
