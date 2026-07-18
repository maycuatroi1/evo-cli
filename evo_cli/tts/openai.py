from evo_cli.tts.creds import openai_api_key
from evo_cli.tts.errors import TtsError
from evo_cli.tts.http import request

SPEECH_URL = "https://api.openai.com/v1/audio/speech"

DEFAULT_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "alloy"
TEXT_LIMIT = 4000
FORMATS = ("mp3", "wav", "opus", "aac", "flac", "pcm")
VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
)


def synthesize(
    text,
    voice=None,
    output_format="mp3",
    model=DEFAULT_MODEL,
    instructions=None,
    speed=None,
):
    if len(text) > TEXT_LIMIT:
        raise TtsError(f"OpenAI speech accepts at most {TEXT_LIMIT} characters, got {len(text)}")
    payload = {
        "model": model,
        "input": text,
        "voice": voice or DEFAULT_VOICE,
        "response_format": output_format,
    }
    if instructions:
        payload["instructions"] = instructions
    if speed is not None and abs(speed - 1.0) > 1e-6:
        payload["speed"] = speed
    body, content_type = request(
        SPEECH_URL,
        method="POST",
        headers={"Authorization": f"Bearer {openai_api_key()}"},
        payload=payload,
    )
    if "json" in content_type:
        raise TtsError(f"OpenAI returned JSON instead of audio: {body.decode('utf-8', 'replace')[:300]}")
    return body


def list_voices():
    return [{"code": voice, "name": voice, "language_code": "multi", "gender": ""} for voice in VOICES]
