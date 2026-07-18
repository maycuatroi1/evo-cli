import os

from evo_cli.credentials.store import CredentialError, get_value
from evo_cli.tts.errors import TtsError


def _stored(key_path):
    try:
        value = get_value(key_path)
    except CredentialError:
        return None
    return value if isinstance(value, str) and value.strip() else None


def _resolve(env_var, key_path):
    return os.environ.get(env_var) or _stored(key_path)


def vbee_credentials():
    app_id = _resolve("VBEE_APP_ID", "vbee.app_id")
    token = _resolve("VBEE_TOKEN", "vbee.token")
    missing = [key for key, value in (("vbee.app_id", app_id), ("vbee.token", token)) if not value]
    if missing:
        raise TtsError(
            "missing Vbee credentials: "
            + ", ".join(missing)
            + "\nGet them at https://studio.vbee.vn/apps, then store with:"
            + "".join(f"\n  evo cred add {key} --from-stdin" for key in missing)
        )
    return app_id, token


def has_vbee_credentials():
    try:
        vbee_credentials()
    except TtsError:
        return False
    return True


def openai_api_key():
    key = _resolve("OPENAI_API_KEY", "openai_api_key")
    if not key:
        raise TtsError(
            "missing OpenAI credentials: openai_api_key\nStore it with:\n  evo cred add openai_api_key --from-stdin"
        )
    return key


def has_openai_credentials():
    try:
        openai_api_key()
    except TtsError:
        return False
    return True


def vbee_webhook_url():
    return _resolve("VBEE_WEBHOOK_URL", "vbee.webhook_url")
