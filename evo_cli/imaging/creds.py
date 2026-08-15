import os

from evo_cli.credentials.store import CredentialError, get_value
from evo_cli.imaging.errors import ImagingError


def _stored(key_path):
    try:
        value = get_value(key_path)
    except CredentialError:
        return None
    return value if isinstance(value, str) and value.strip() else None


def gemini_api_key():
    key = os.environ.get("GEMINI_API_KEY") or _stored("gemini_api_key")
    if not key:
        raise ImagingError(
            "missing Gemini credentials: gemini_api_key\n"
            "Get one at https://aistudio.google.com/apikey, then store it with:\n"
            "  evo cred add gemini_api_key --from-stdin"
        )
    return key


def has_gemini_credentials():
    try:
        gemini_api_key()
    except ImagingError:
        return False
    return True
