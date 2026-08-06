import base64
import io
import shutil
import subprocess
import time
import wave

from evo_cli.tts.creds import gemini_api_key
from evo_cli.tts.errors import TtsError
from evo_cli.tts.http import request_json

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_VOICE = "Achird"
TEXT_LIMIT = 2000
FORMATS = ("wav", "pcm", "mp3")
DEFAULT_FORMAT = "wav"
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1
REQUEST_TIMEOUT = 300
RETRY_STATUSES = (429, 500, 502, 503, 504)
RETRIES = 3
RETRY_BACKOFF = 2.0

VOICES = (
    ("Zephyr", "Bright"),
    ("Puck", "Upbeat"),
    ("Charon", "Informative"),
    ("Kore", "Firm"),
    ("Fenrir", "Excitable"),
    ("Leda", "Youthful"),
    ("Orus", "Firm"),
    ("Aoede", "Breezy"),
    ("Callirrhoe", "Easy-going"),
    ("Autonoe", "Bright"),
    ("Enceladus", "Breathy"),
    ("Iapetus", "Clear"),
    ("Umbriel", "Easy-going"),
    ("Algieba", "Smooth"),
    ("Despina", "Smooth"),
    ("Erinome", "Clear"),
    ("Algenib", "Gravelly"),
    ("Rasalgethi", "Informative"),
    ("Laomedeia", "Upbeat"),
    ("Achernar", "Soft"),
    ("Alnilam", "Firm"),
    ("Schedar", "Even"),
    ("Gacrux", "Mature"),
    ("Pulcherrima", "Forward"),
    ("Achird", "Friendly"),
    ("Zubenelgenubi", "Casual"),
    ("Vindemiatrix", "Gentle"),
    ("Sadachbia", "Lively"),
    ("Sadaltager", "Knowledgeable"),
    ("Sulafat", "Warm"),
)

_BY_LOWER_CODE = {code.lower(): code for code, _ in VOICES}


def normalise_voice(voice):
    # The catalog is capitalised (Achird, Kore); accept whatever case the caller typed
    # and let anything unknown through so a newly shipped voice still works.
    if not voice:
        return DEFAULT_VOICE
    return _BY_LOWER_CODE.get(voice.strip().lower(), voice.strip())


def _prompt(text, instructions):
    if not instructions:
        return text
    return f"{instructions.strip().rstrip(':')}:\n{text}"


def _sample_rate(mime_type):
    for field in (mime_type or "").split(";"):
        field = field.strip()
        if field.startswith("rate="):
            try:
                return int(field.split("=", 1)[1])
            except ValueError:
                return SAMPLE_RATE
    return SAMPLE_RATE


def _to_wav(pcm, sample_rate):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(CHANNELS)
        writer.setsampwidth(SAMPLE_WIDTH)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)
    return buffer.getvalue()


def _to_mp3(pcm, sample_rate, bitrate):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TtsError(
            "Gemini returns raw PCM and mp3 encoding needs ffmpeg on PATH. "
            "Install it (`winget install Gyan.FFmpeg` / `brew install ffmpeg`) or use --format wav."
        )
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(CHANNELS),
            "-i",
            "pipe:0",
            "-f",
            "mp3",
            "-b:a",
            f"{bitrate or 128}k",
            "pipe:1",
        ],
        input=pcm,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or not result.stdout:
        raise TtsError(f"ffmpeg failed to encode mp3: {result.stderr.decode('utf-8', 'replace')[:300]}")
    return result.stdout


def _extract_audio(payload):
    candidates = payload.get("candidates") or []
    for candidate in candidates:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                mime_type = inline.get("mimeType") or inline.get("mime_type") or ""
                return base64.b64decode(inline["data"]), mime_type
    blocked = (payload.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise TtsError(f"Gemini refused the prompt: {blocked}")
    reason = candidates[0].get("finishReason") if candidates else None
    raise TtsError(f"Gemini returned no audio (finishReason={reason or 'unknown'})")


def _post(payload, model, timeout):
    url = API_URL.format(model=model)
    headers = {"x-goog-api-key": gemini_api_key()}
    last = None
    for attempt in range(RETRIES):
        try:
            return request_json(url, method="POST", headers=headers, payload=payload, timeout=timeout)
        except TtsError as exc:
            # The model sometimes emits text tokens instead of audio and the server 500s on it;
            # Google's own guidance is to retry rather than surface it.
            if getattr(exc, "status", None) not in RETRY_STATUSES:
                raise
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise last


def build_payload(text, voice=None, instructions=None):
    return {
        "contents": [{"parts": [{"text": _prompt(text, instructions)}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": normalise_voice(voice)}}},
        },
    }


def synthesize(
    text,
    voice=None,
    output_format=DEFAULT_FORMAT,
    model=DEFAULT_MODEL,
    instructions=None,
    bitrate=128,
    timeout=REQUEST_TIMEOUT,
):
    if len(text) > TEXT_LIMIT:
        raise TtsError(f"Gemini TTS accepts at most {TEXT_LIMIT} characters per request, got {len(text)}")
    if output_format not in FORMATS:
        raise TtsError(f"gemini does not support format '{output_format}' (supported: {', '.join(FORMATS)})")
    payload = build_payload(text, voice=voice, instructions=instructions)
    body = _post(payload, model or DEFAULT_MODEL, timeout)
    pcm, mime_type = _extract_audio(body)
    sample_rate = _sample_rate(mime_type)
    if output_format == "pcm":
        return pcm
    if output_format == "mp3":
        return _to_mp3(pcm, sample_rate, bitrate)
    return _to_wav(pcm, sample_rate)


def list_voices():
    return [{"code": code, "name": description, "language_code": "multi", "gender": ""} for code, description in VOICES]
