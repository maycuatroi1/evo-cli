import json
import os
from pathlib import Path

META_FIELDS = (
    "id",
    "service",
    "category",
    "description",
    "type",
    "lifetime",
    "expiry",
    "status",
    "rotate",
    "added",
    "last_rotated",
    "oauth",
)

GENERATED_NOTE = "DO NOT EDIT - generated from credentials/ by `evo cred compile`. Source of truth is the folder."

SPECS = [
    {
        "path": "accounts/omelet.json",
        "id": "omelet",
        "service": "Omelet account + n8n webhooks",
        "category": "accounts",
        "type": "account",
        "lifetime": "stable",
        "description": "Omelet account creds, n8n webhooks, GCS upload backend",
        "rotate": "Omelet account settings / edit n8n workflow webhook",
        "keys": [
            "backend_url",
            "public_webhook_url",
            "username",
            "password",
            "use_gcs",
            "gcs_bucket",
        ],
    },
    {
        "path": "cms/ghost.json",
        "id": "ghost",
        "service": "Ghost CMS",
        "category": "cms",
        "type": "api_key",
        "lifetime": "stable",
        "description": "Ghost blog URL + Content/Admin API keys",
        "rotate": "Ghost Admin -> Integrations -> regenerate",
        "keys": ["ghost_api_url", "ghost_content_api_key", "ghost_admin_api_key"],
    },
    {
        "path": "ai/openai.json",
        "id": "openai",
        "service": "OpenAI",
        "category": "ai",
        "type": "api_key",
        "lifetime": "stable",
        "description": "OpenAI project API key (genimg, aicheck)",
        "rotate": "OpenAI dashboard -> API keys -> rotate",
        "keys": ["openai_api_key"],
    },
    {
        "path": "ai/google.json",
        "id": "google_api",
        "service": "Google API key",
        "category": "ai",
        "type": "api_key",
        "lifetime": "stable",
        "description": "Google API key (Gemini, Sheets, etc.)",
        "rotate": "Google Cloud Console -> APIs & Services -> Credentials",
        "keys": ["google_api_key"],
    },
    {
        "path": "ai/kimi.json",
        "id": "kimi",
        "service": "Moonshot Kimi Code",
        "category": "ai",
        "type": "api_key",
        "lifetime": "stable",
        "description": "Moonshot Kimi Code API key",
        "rotate": "Moonshot dashboard",
        "keys": ["kimi_code_api_key"],
    },
    {
        "path": "infra/cloudflare.json",
        "id": "cloudflare",
        "service": "Cloudflare",
        "category": "infra",
        "type": "api_key",
        "lifetime": "stable",
        "description": "Cloudflare API token + zone identifiers (iahl, iahn_zone)",
        "rotate": "Cloudflare dashboard -> API Tokens",
        "keys": ["cloudflare_api_token", "cloudflare"],
    },
    {
        "path": "infra/dokploy.json",
        "id": "dokploy",
        "service": "Dokploy (2 instances)",
        "category": "infra",
        "type": "api_key",
        "lifetime": "stable",
        "description": "Dokploy default + fu instance URLs/API keys",
        "rotate": "Dokploy panel -> profile -> API keys",
        "keys": ["dokploy_url", "dokploy_api_key", "dokploy_fu_url", "dokploy_fu_api_key"],
    },
    {
        "path": "infra/railway.json",
        "id": "railway",
        "service": "Railway",
        "category": "infra",
        "type": "api_key",
        "lifetime": "stable",
        "description": "Railway API token",
        "rotate": "Railway dashboard -> Account -> Tokens",
        "keys": ["railway_api_token"],
    },
    {
        "path": "infra/n8n.json",
        "id": "n8n",
        "service": "n8n",
        "category": "infra",
        "type": "api_key",
        "lifetime": "stable",
        "description": "n8n API URL/key + flutter webhook basic auth",
        "rotate": "n8n -> Settings -> API",
        "keys": ["n8n_api_url", "n8n_api_key", "n8n_flutter_webhook_basic_auth"],
    },
    {
        "path": "infra/observability.json",
        "id": "observability",
        "service": "Logs / Metrics / Grafana",
        "category": "infra",
        "type": "basic_auth",
        "lifetime": "stable",
        "description": "VictoriaLogs password, metrics basic-auth bcrypt, Grafana login",
        "rotate": "Rotate at each service config",
        "keys": ["vlogs_password", "metrics_basicauth_bcrypt_escaped", "grafana"],
    },
    {
        "path": "infra/ialab.json",
        "id": "ialab",
        "service": "iaLab DGX",
        "category": "infra",
        "type": "account",
        "lifetime": "stable",
        "description": "iaLab DGX sudo password",
        "rotate": "passwd on the DGX host",
        "keys": ["ialab_dgx_sudo_password"],
    },
    {
        "path": "google-oauth/rclone.json",
        "id": "rclone",
        "service": "rclone (Google Drive)",
        "category": "google-oauth",
        "type": "oauth_token",
        "lifetime": "access ~1h, refresh until revoked",
        "description": "rclone Google Drive remote: OAuth token + root folder",
        "rotate": "evo cred refresh --service rclone (or rclone config reconnect)",
        "oauth": {
            "container": ["rclone", "token"],
            "access_field": "access_token",
            "expiry_field": "expiry",
            "client_from": ["rclone"],
        },
        "keys": ["rclone"],
    },
    {
        "path": "google-oauth/gmail.json",
        "id": "gmail",
        "service": "Gmail OAuth (primary)",
        "category": "google-oauth",
        "type": "oauth_token",
        "lifetime": "access ~1h, refresh until revoked",
        "description": "Gmail OAuth token used by life mail",
        "rotate": "evo cred refresh --service gmail (or life mail auth)",
        "oauth": {
            "container": ["gmail", "token"],
            "access_field": "token",
            "expiry_field": "expiry",
            "client_from": ["gmail", "token"],
        },
        "keys": ["gmail"],
    },
    {
        "path": "google-oauth/google-drive.json",
        "id": "google_drive",
        "service": "Google Drive OAuth",
        "category": "google-oauth",
        "type": "oauth_token",
        "lifetime": "access ~1h, refresh until revoked",
        "description": "Google Drive OAuth token (evo gdrive)",
        "rotate": "evo cred refresh --service google-drive",
        "oauth": {
            "container": ["google_drive", "token"],
            "access_field": "token",
            "expiry_field": "expiry",
            "client_from": ["google_drive", "token"],
        },
        "keys": ["google_drive"],
    },
    {
        "path": "google-oauth/google-calendar.json",
        "id": "google_calendar",
        "service": "Google Calendar OAuth",
        "category": "google-oauth",
        "type": "oauth_token",
        "lifetime": "access ~1h, refresh until revoked",
        "description": "Google Calendar OAuth client + token (life cal)",
        "rotate": "evo cred refresh --service google-calendar (or life cal auth)",
        "oauth": {
            "container": ["google_calendar", "token"],
            "access_field": "token",
            "expiry_field": "expiry",
            "client_from": ["google_calendar", "token"],
        },
        "keys": ["google_calendar"],
    },
    {
        "path": "google-oauth/sheets-sa.json",
        "id": "sheets_sa",
        "service": "Google Sheets service account",
        "category": "google-oauth",
        "type": "service_account",
        "lifetime": "stable",
        "description": "Path + email of GCP service account JSON for Sheets",
        "rotate": "Generate new SA key in GCP, replace file at the path",
        "keys": ["google_sheets_sa_key_path", "google_sheets_sa_email"],
    },
    {
        "path": "messaging/lark.json",
        "id": "lark",
        "service": "Lark / Feishu",
        "category": "messaging",
        "type": "api_key",
        "lifetime": "stable",
        "description": "Lark app id/secret, base URL, user id, web cookies",
        "rotate": "Lark developer console -> credentials",
        "keys": ["lark"],
    },
    {
        "path": "messaging/facebook.json",
        "id": "facebook",
        "service": "Facebook",
        "category": "messaging",
        "type": "account",
        "lifetime": "stable",
        "description": "Facebook login used by life fb (playwright)",
        "rotate": "Change Facebook account password",
        "keys": ["facebook"],
    },
    {
        "path": "messaging/mailgun.json",
        "id": "mailgun",
        "service": "Mailgun",
        "category": "messaging",
        "type": "api_key",
        "lifetime": "stable",
        "description": "Mailgun API key, region, sandbox domain",
        "rotate": "Mailgun dashboard -> API keys",
        "keys": ["mailgun"],
    },
    {
        "path": "tools/quillbot.json",
        "id": "quillbot",
        "service": "QuillBot",
        "category": "tools",
        "type": "oauth_token",
        "lifetime": "~1h JWT, no programmatic refresh",
        "description": "QuillBot paraphraser Firebase JWT (re-extract from browser)",
        "rotate": "omelet aicheck refresh / re-login QuillBot, capture from browser",
        "keys": ["quillbot_token"],
    },
    {
        "path": "tools/figma.json",
        "id": "figma",
        "service": "Figma",
        "category": "tools",
        "type": "api_key",
        "lifetime": "stable",
        "description": "Figma personal access token",
        "rotate": "Figma -> Settings -> Personal access tokens",
        "keys": ["figma_token"],
    },
    {
        "path": "tools/openclaw.json",
        "id": "openclaw",
        "service": "OpenClaw gateway",
        "category": "tools",
        "type": "api_key",
        "lifetime": "stable",
        "description": "OpenClaw gateway auth token",
        "rotate": "Issued by OpenClaw service",
        "keys": ["openclaw_gateway_token"],
    },
    {
        "path": "tools/cliproxy.json",
        "id": "cliproxy",
        "service": "CLI proxy",
        "category": "tools",
        "type": "api_key",
        "lifetime": "stable",
        "description": "CLI proxy management + public API keys",
        "rotate": "Rotate in cliproxy admin",
        "keys": ["cliproxy"],
    },
    {
        "path": "tools/lms.json",
        "id": "lms",
        "service": "LMS",
        "category": "tools",
        "type": "url",
        "lifetime": "stable",
        "description": "LMS SCORM CDN base URL",
        "rotate": "Edit value directly",
        "keys": ["lms"],
    },
    {
        "path": "firebase/firebase-admin.json",
        "id": "firebase_admin",
        "service": "Firebase Admin SDK",
        "category": "firebase",
        "type": "service_account",
        "lifetime": "stable",
        "description": "Firebase Admin SDK service account (project omelet-f0b89)",
        "rotate": "Firebase console -> Service accounts -> generate new key",
        "keys": ["firebase_admin_sdk"],
    },
    {
        "path": "_legacy/gmail-socrat.json",
        "id": "gmail_socrat",
        "service": "Gmail OAuth (socrat, legacy)",
        "category": "_legacy",
        "type": "oauth_token",
        "lifetime": "legacy",
        "status": "deprecated",
        "description": "Old gmail OAuth style for socrat account, superseded by gmail",
        "rotate": "Deprecated - prefer gmail entry",
        "keys": ["gmail_socrat"],
    },
    {
        "path": "_legacy/gmail-sometimesocrazy.json",
        "id": "gmail_sometimesocrazy",
        "service": "Gmail OAuth (sometimesocrazy, legacy)",
        "category": "_legacy",
        "type": "oauth_token",
        "lifetime": "legacy",
        "status": "deprecated",
        "description": "Old gmail OAuth style for sometimesocrazy account, superseded by gmail",
        "rotate": "Deprecated - prefer gmail entry",
        "keys": ["gmail_sometimesocrazy"],
    },
]


def config_path():
    return Path(os.environ.get("OMELET_CONFIG", str(Path.home() / ".omelet.json")))


def omelet_dir():
    return Path(os.environ.get("OMELET_DIR", str(Path.home() / ".omelet.d")))


def credentials_dir():
    return omelet_dir() / "credentials"


def spec_for_flat_key(top_key):
    for spec in SPECS:
        if top_key in spec["keys"]:
            return spec
    return None


def dig(container, path):
    cur = container
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def deep_merge(base, overlay):
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def iter_credential_files():
    root = credentials_dir()
    if not root.exists():
        return
    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {path}: {exc}") from exc
        yield path, data


def load_entries():
    return list(iter_credential_files())
