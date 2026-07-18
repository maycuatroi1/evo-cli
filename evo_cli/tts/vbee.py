import time

from evo_cli.tts.creds import vbee_credentials, vbee_webhook_url
from evo_cli.tts.errors import TtsError
from evo_cli.tts.http import request, request_json, with_query

TTS_URL = "https://api.vbee.vn/v1/tts"
REQUEST_URL = "https://api.vbee.vn/v1/tts/requests/{request_id}"
VOICES_URL = "https://vbee.vn/api/public/v1/voices"

REALTIME_LIMIT = 300
BATCH_LIMIT = 100000
DEFAULT_VOICE = "hn_female_ngochuyen_full_48k-fhg"
DEFAULT_WEBHOOK = "https://example.com/evo-tts-noop"
FORMATS = ("mp3", "wav", "pcm")
BITRATES = (8, 16, 32, 64, 128)
SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)
TERMINAL_STATUSES = ("COMPLETED", "SUCCESS", "FAILED", "ERROR")


def _headers():
    app_id, token = vbee_credentials()
    return {"Authorization": f"Bearer {token}", "App-Id": app_id, "Content-Type": "application/json"}


def _body(text, mode, voice, output_format, bitrate, speed, sample_rate):
    payload = {
        "text": text,
        "mode": mode,
        "voiceCode": voice or DEFAULT_VOICE,
        "outputFormat": output_format,
    }
    if bitrate:
        payload["bitrate"] = bitrate
    if speed is not None:
        payload["speed"] = speed
    if sample_rate:
        payload["sampleRate"] = sample_rate
    return payload


def synthesize(text, voice=None, output_format="mp3", bitrate=128, speed=1.0, sample_rate=None):
    if len(text) > REALTIME_LIMIT:
        raise TtsError(f"Vbee realtime accepts at most {REALTIME_LIMIT} characters, got {len(text)}")
    body, content_type = request(
        TTS_URL,
        method="POST",
        headers=_headers(),
        payload=_body(text, "sync", voice, output_format, bitrate, speed, sample_rate),
    )
    if "json" in content_type:
        raise TtsError(f"Vbee returned JSON instead of audio: {body.decode('utf-8', 'replace')[:300]}")
    return body


def submit(text, voice=None, output_format="mp3", bitrate=128, speed=1.0, sample_rate=None, webhook_url=None):
    if len(text) > BATCH_LIMIT:
        raise TtsError(f"Vbee batch accepts at most {BATCH_LIMIT} characters, got {len(text)}")
    payload = _body(text, "async", voice, output_format, bitrate, speed, sample_rate)
    payload["webhookUrl"] = webhook_url or vbee_webhook_url() or DEFAULT_WEBHOOK
    result = request_json(TTS_URL, method="POST", headers=_headers(), payload=payload)
    request_id = result.get("requestId")
    if not request_id:
        raise TtsError(f"Vbee did not return a requestId: {result}")
    return request_id


def get_request(request_id):
    return request_json(REQUEST_URL.format(request_id=request_id), headers=_headers())


def wait_for_audio(request_id, timeout=900, interval=3.0, on_poll=None):
    deadline = time.time() + timeout
    while True:
        result = get_request(request_id)
        status = str(result.get("status", "")).upper()
        if on_poll:
            on_poll(request_id, status)
        if result.get("audioLink"):
            return result["audioLink"]
        if status in ("FAILED", "ERROR"):
            raise TtsError(f"Vbee request {request_id} failed: {result}")
        if time.time() >= deadline:
            raise TtsError(f"Vbee request {request_id} still {status or 'PENDING'} after {timeout}s")
        time.sleep(interval)


def download(audio_link):
    body, _ = request(audio_link)
    return body


def synthesize_async(
    text,
    voice=None,
    output_format="mp3",
    bitrate=128,
    speed=1.0,
    sample_rate=None,
    webhook_url=None,
    timeout=900,
    interval=3.0,
    on_poll=None,
):
    request_id = submit(
        text,
        voice=voice,
        output_format=output_format,
        bitrate=bitrate,
        speed=speed,
        sample_rate=sample_rate,
        webhook_url=webhook_url,
    )
    audio_link = wait_for_audio(request_id, timeout=timeout, interval=interval, on_poll=on_poll)
    return download(audio_link), request_id


def list_voices(ownership="VBEE", language_code=None, gender=None, code=None, limit=100, cursor=None):
    app_id, token = vbee_credentials()
    url = with_query(
        VOICES_URL,
        {
            "voiceOwnership": ownership,
            "languageCode": language_code,
            "gender": gender,
            "code": code,
            "limit": limit,
            "cursor": cursor,
        },
    )
    payload = request_json(url, headers={"Authorization": f"Bearer {token}", "App-Id": app_id})
    result = payload.get("result") or {}
    return result.get("voices") or [], (result.get("pagination") or {})
