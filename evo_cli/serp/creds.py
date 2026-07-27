import os
import re
from pathlib import Path

from evo_cli.credentials.store import CredentialError, get_value
from evo_cli.serp.errors import SerpError

CRED_KEY = "serpapi_api_key"
ENV_VAR = "SERPAPI_KEY"

_TOML_KEY = re.compile(r"""^\s*api_key\s*=\s*["']?([^"'\s]+)["']?\s*$""")

MISSING_HINT = (
    "no SerpApi key found.\n"
    "Get one at https://serpapi.com/manage-api-key, then store it with:\n"
    f"  evo cred add {CRED_KEY} --from-stdin\n"
    "or run the guided setup:\n"
    "  evo setup serp"
)


def config_file():
    """Where the official serpapi CLI keeps its key."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "serpapi" / "config.toml"


def _from_store():
    try:
        value = get_value(CRED_KEY)
    except CredentialError:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _from_config_file():
    path = config_file()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        match = _TOML_KEY.match(line)
        if match:
            return match.group(1)
    return None


def resolve():
    """Return (key, source) with the same precedence the serpapi CLI uses."""
    env = os.environ.get(ENV_VAR)
    if env and env.strip():
        return env.strip(), f"env {ENV_VAR}"
    stored = _from_store()
    if stored:
        return stored, f"evo cred {CRED_KEY}"
    from_file = _from_config_file()
    if from_file:
        return from_file, str(config_file())
    return None, None


def api_key():
    key, _ = resolve()
    if not key:
        raise SerpError(MISSING_HINT)
    return key


def has_api_key():
    key, _ = resolve()
    return bool(key)


def write_config_file(key):
    """Write the key where the bare `serpapi` binary can find it, mode 0600."""
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'api_key = "{key}"\n', encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def mask(key):
    if not key:
        return "-"
    if len(key) <= 12:
        return key[:2] + "..." + key[-2:]
    return f"{key[:6]}...{key[-4:]} ({len(key)} chars)"
