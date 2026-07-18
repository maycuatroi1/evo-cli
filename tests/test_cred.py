import json
from urllib.parse import parse_qs, urlparse

import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import gdrive
from evo_cli.credentials import doctor as doctor_module
from evo_cli.credentials import migrate, oauth_flow, registry
from evo_cli.credentials.store import compile_flat, get_value, set_value


@pytest.fixture
def store(tmp_path, monkeypatch):
    omelet_dir = tmp_path / ".omelet.d"
    config = tmp_path / ".omelet.json"
    monkeypatch.setenv("OMELET_DIR", str(omelet_dir))
    monkeypatch.setenv("OMELET_CONFIG", str(config))
    (omelet_dir / "credentials").mkdir(parents=True)
    return {"dir": omelet_dir / "credentials", "config": config}


def _write(store, rel, entry):
    path = store["dir"] / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    return path


def test_cred_command_is_registered():
    assert "cred" in cli.commands
    for name in ("get", "list", "doctor", "compile", "add", "auth", "refresh", "migrate", "sync", "path"):
        assert name in cli.commands["cred"].commands
    assert set(cli.commands["cred"].commands["sync"].commands) == {"push", "pull"}


def test_paths_follow_env(store):
    assert registry.credentials_dir() == store["dir"]
    assert registry.config_path() == store["config"]


def test_compile_merges_flat_blocks_and_skips_deprecated(store):
    _write(store, "ai/openai.json", {"id": "openai", "flat": {"openai_api_key": "sk-live"}})
    _write(store, "cms/ghost.json", {"id": "ghost", "flat": {"ghost_api_url": "https://blog"}})
    _write(store, "_legacy/old.json", {"id": "gmail_socrat", "status": "deprecated", "flat": {"gmail_socrat": "x"}})

    count, skipped, target = compile_flat()

    assert count == 2
    assert skipped == ["gmail_socrat"]
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["openai_api_key"] == "sk-live"
    assert data["ghost_api_url"] == "https://blog"
    assert "gmail_socrat" not in data
    assert data["_generated"].startswith("DO NOT EDIT")


def test_compile_include_legacy_keeps_deprecated(store):
    _write(store, "_legacy/old.json", {"id": "gmail_socrat", "status": "deprecated", "flat": {"gmail_socrat": "x"}})

    count, skipped, target = compile_flat(include_legacy=True)

    assert (count, skipped) == (1, [])
    assert json.loads(target.read_text(encoding="utf-8"))["gmail_socrat"] == "x"


def test_get_reads_nested_path(store):
    _write(store, "google-oauth/rclone.json", {"id": "rclone", "flat": {"rclone": {"token": {"access_token": "at"}}}})
    compile_flat()

    assert get_value("rclone.token.access_token") == "at"


def test_get_command_writes_raw_value_to_stdout(store):
    _write(store, "ai/openai.json", {"id": "openai", "flat": {"openai_api_key": "sk-live"}})
    compile_flat()

    result = CliRunner().invoke(cli, ["cred", "get", "openai_api_key"])

    assert result.exit_code == 0
    assert result.output == "sk-live"


def test_get_command_export_form(store):
    _write(store, "ai/openai.json", {"id": "openai", "flat": {"openai_api_key": "it's-secret"}})
    compile_flat()

    result = CliRunner().invoke(cli, ["cred", "get", "--export", "OPENAI_API_KEY", "openai_api_key"])

    assert result.exit_code == 0
    assert result.output == "export OPENAI_API_KEY='it'\\''s-secret'\n"


def test_get_command_missing_key_fails(store):
    _write(store, "ai/openai.json", {"id": "openai", "flat": {"openai_api_key": "sk"}})
    compile_flat()

    result = CliRunner().invoke(cli, ["cred", "get", "nope.deeper"])

    assert result.exit_code != 0
    assert "key not found: nope.deeper" in result.output


def test_set_value_routes_known_key_to_its_spec_file(store):
    path, existed = set_value("openai_api_key", "sk-new")

    assert existed is False
    assert path == store["dir"] / "ai" / "openai.json"
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["id"] == "openai"
    assert entry["flat"]["openai_api_key"] == "sk-new"
    assert entry["added"] and entry["last_rotated"]


def test_set_value_routes_unknown_key_to_tools(store):
    path, _ = set_value("whatever_token", "v")

    assert path == store["dir"] / "tools" / "whatever_token.json"
    assert json.loads(path.read_text(encoding="utf-8"))["category"] == "tools"


def test_set_value_updates_existing_leaf_in_place(store):
    _write(store, "ai/openai.json", {"id": "openai", "service": "OpenAI", "flat": {"openai_api_key": "old"}})

    path, existed = set_value("openai_api_key", "new")

    assert existed is True
    assert json.loads(path.read_text(encoding="utf-8"))["flat"]["openai_api_key"] == "new"


def test_add_command_recompiles_flat(store):
    result = CliRunner().invoke(cli, ["cred", "add", "openai_api_key", "--value", "sk-cli"])

    assert result.exit_code == 0
    assert json.loads(store["config"].read_text(encoding="utf-8"))["openai_api_key"] == "sk-cli"


def test_add_command_rejects_two_sources(store):
    result = CliRunner().invoke(cli, ["cred", "add", "k", "--value", "v", "--from-stdin"])

    assert result.exit_code != 0
    assert "only one of" in result.output


def test_add_command_json_flag_parses_value(store):
    result = CliRunner().invoke(cli, ["cred", "add", "use_gcs", "--value", "true", "--json"])

    assert result.exit_code == 0
    assert json.loads(store["config"].read_text(encoding="utf-8"))["use_gcs"] is True


def test_doctor_flags_expired_oauth_and_exits_nonzero(store):
    _write(
        store,
        "google-oauth/gmail.json",
        {
            "id": "gmail",
            "service": "Gmail",
            "type": "oauth_token",
            "oauth": {"container": ["gmail", "token"], "access_field": "token", "expiry_field": "expiry"},
            "flat": {"gmail": {"token": {"token": "abcdefghijklmnop", "expiry": "2020-01-01T00:00:00+00:00"}}},
        },
    )

    rows = doctor_module.scan()
    assert rows[0]["health"] == "EXPIRED"

    result = CliRunner().invoke(cli, ["cred", "doctor"])
    assert result.exit_code == 1
    assert "EXPIRED" in result.output


def test_secret_preview_masks_and_never_shows_full_value(store):
    _write(
        store,
        "ai/openai.json",
        {"id": "openai", "service": "OpenAI", "flat": {"openai_api_key": "sk-abcdefghijklmnop"}},
    )
    _write(store, "tools/short.json", {"id": "short", "service": "Short", "flat": {"short_token": "tiny"}})

    previews = {row["service"]: row["secret"] for row in doctor_module.scan()}

    assert previews["OpenAI"] == "sk-a...mnop"
    assert previews["Short"] == "***"


def test_doctor_passes_when_nothing_expired(store):
    _write(store, "ai/openai.json", {"id": "openai", "service": "OpenAI", "type": "api_key", "flat": {"k": "v"}})

    result = CliRunner().invoke(cli, ["cred", "doctor"])

    assert result.exit_code == 0
    assert "healthy" in result.output


def test_doctor_without_folder_reports_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("OMELET_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("OMELET_CONFIG", str(tmp_path / ".omelet.json"))

    result = CliRunner().invoke(cli, ["cred", "doctor"])

    assert result.exit_code != 0
    assert "credentials folder not found" in result.output


def test_migrate_splits_flat_file_and_routes_unmapped_to_misc(store, tmp_path):
    source = tmp_path / "old.omelet.json"
    source.write_text(
        json.dumps({"_generated": "note", "openai_api_key": "sk", "ghost_api_url": "https://b", "mystery": "m"}),
        encoding="utf-8",
    )

    result = migrate.plan(source)

    assert result["source_keys"] == 3
    assert result["unmapped"] == ["mystery"]
    by_rel = {item["rel"]: item for item in result["actions"]}
    assert by_rel["ai/openai.json"]["keys"] == ["openai_api_key"]
    assert by_rel["misc/mystery.json"]["misc"] is True


def test_migrate_strict_refuses_unmapped_keys(store, tmp_path):
    source = tmp_path / "old.omelet.json"
    source.write_text(json.dumps({"mystery": "m"}), encoding="utf-8")

    with pytest.raises(Exception) as excinfo:
        migrate.plan(source, strict=True)

    assert "not mapped in the registry" in str(excinfo.value)


def test_migrate_merge_only_adds_missing_keys(store, tmp_path):
    _write(store, "ai/openai.json", {"id": "openai", "flat": {"openai_api_key": "keep-me"}})
    source = tmp_path / "old.omelet.json"
    source.write_text(json.dumps({"openai_api_key": "stale", "ghost_api_url": "https://b"}), encoding="utf-8")

    result = migrate.plan(source, merge=True)

    rels = {item["rel"] for item in result["actions"]}
    assert "ai/openai.json" not in rels
    assert "cms/ghost.json" in rels


def test_migrate_command_refuses_nonempty_folder(store, tmp_path):
    _write(store, "ai/openai.json", {"id": "openai", "flat": {"openai_api_key": "existing"}})
    source = tmp_path / "old.omelet.json"
    source.write_text(json.dumps({"ghost_api_url": "https://b"}), encoding="utf-8")

    result = CliRunner().invoke(cli, ["cred", "migrate", "--source", str(source)])

    assert result.exit_code != 0
    assert "already has files" in result.output


def test_refresh_requires_exactly_one_target(store):
    result = CliRunner().invoke(cli, ["cred", "refresh"])

    assert result.exit_code != 0
    assert "exactly one of --all or --service" in result.output


def test_refresh_writes_new_token_and_recompiles(store, monkeypatch):
    _write(
        store,
        "google-oauth/gmail.json",
        {
            "id": "gmail",
            "service": "Gmail",
            "oauth": {
                "container": ["gmail", "token"],
                "access_field": "token",
                "expiry_field": "expiry",
                "client_from": ["gmail", "token"],
            },
            "flat": {
                "gmail": {
                    "token": {
                        "token": "old",
                        "expiry": "2020-01-01T00:00:00+00:00",
                        "refresh_token": "rt",
                        "client_id": "cid",
                        "client_secret": "cs",
                    }
                }
            },
        },
    )
    monkeypatch.setattr(
        "evo_cli.credentials.google_oauth.post_refresh",
        lambda creds: {"access_token": "fresh", "expires_in": 3600},
    )

    result = CliRunner().invoke(cli, ["cred", "refresh", "--service", "gmail"])

    assert result.exit_code == 0
    assert json.loads(store["config"].read_text(encoding="utf-8"))["gmail"]["token"]["token"] == "fresh"


def test_refresh_dry_run_does_not_write(store, monkeypatch):
    _write(
        store,
        "google-oauth/gmail.json",
        {
            "id": "gmail",
            "oauth": {
                "container": ["gmail", "token"],
                "access_field": "token",
                "expiry_field": "expiry",
                "client_from": ["gmail", "token"],
            },
            "flat": {
                "gmail": {"token": {"token": "old", "refresh_token": "rt", "client_id": "c", "client_secret": "s"}}
            },
        },
    )

    def explode(_creds):
        raise AssertionError("dry-run must not call Google")

    monkeypatch.setattr("evo_cli.credentials.google_oauth.post_refresh", explode)

    result = CliRunner().invoke(cli, ["cred", "refresh", "--service", "gmail", "--dry-run"])

    assert result.exit_code == 0
    assert "would POST" in result.output


def test_refresh_skips_entry_without_refresh_token(store):
    _write(
        store,
        "google-oauth/gmail.json",
        {
            "id": "gmail",
            "oauth": {
                "container": ["gmail", "token"],
                "access_field": "token",
                "expiry_field": "expiry",
                "client_from": ["gmail", "token"],
            },
            "flat": {"gmail": {"token": {"token": "old"}}},
        },
    )

    result = CliRunner().invoke(cli, ["cred", "refresh", "--all"])

    assert result.exit_code == 1
    assert "refresh_token missing" in result.output


def test_sync_requires_repo_env(store, monkeypatch):
    monkeypatch.delenv("OMELET_SYNC_REPO", raising=False)

    result = CliRunner().invoke(cli, ["cred", "sync", "push"])

    assert result.exit_code != 0
    assert "OMELET_SYNC_REPO" in result.output


def test_registry_maps_contract_keys_to_specs():
    for key in ("rclone", "gmail", "google_drive", "google_calendar", "facebook", "openai_api_key"):
        assert registry.spec_for_flat_key(key) is not None


def test_auth_command_is_registered():
    assert "auth" in cli.commands["cred"].commands


def test_build_auth_url_requests_offline_consent():
    url = oauth_flow.build_auth_url("cid", "http://127.0.0.1:9999", ["scope/a", "scope/b"], "st8")

    assert url.startswith(oauth_flow.AUTH_URL + "?")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["cid"]
    assert query["redirect_uri"] == ["http://127.0.0.1:9999"]
    assert query["scope"] == ["scope/a scope/b"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["state"] == ["st8"]


def test_client_from_secrets_file_reads_installed_block(tmp_path):
    path = tmp_path / "client.json"
    path.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "cs"}}), encoding="utf-8")

    assert oauth_flow.client_from_secrets_file(path) == ("cid", "cs")


def test_client_from_secrets_file_rejects_a_file_without_a_client(tmp_path):
    path = tmp_path / "client.json"
    path.write_text(json.dumps({"nothing": True}), encoding="utf-8")

    with pytest.raises(Exception) as excinfo:
        oauth_flow.client_from_secrets_file(path)

    assert "no client_id/client_secret" in str(excinfo.value)


def test_store_tokens_refuses_a_response_without_refresh_token(store):
    entry = {
        "id": "google_drive",
        "oauth": {"container": ["google_drive", "token"], "access_field": "token", "expiry_field": "expiry"},
        "flat": {},
    }

    with pytest.raises(Exception) as excinfo:
        oauth_flow.store_tokens(
            store["dir"] / "google-oauth" / "google-drive.json",
            entry,
            {"access_token": "at", "expires_in": 3600},
            ["s"],
        )

    assert "no refresh_token" in str(excinfo.value)


def test_store_tokens_writes_token_refresh_and_scopes(store):
    path = store["dir"] / "google-oauth" / "google-drive.json"
    entry = {
        "id": "google_drive",
        "oauth": {"container": ["google_drive", "token"], "access_field": "token", "expiry_field": "expiry"},
        "flat": {},
    }

    oauth_flow.store_tokens(
        path,
        entry,
        {"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
        ["https://www.googleapis.com/auth/drive.readonly"],
    )

    written = json.loads(path.read_text(encoding="utf-8"))["flat"]["google_drive"]["token"]
    assert written["token"] == "at"
    assert written["refresh_token"] == "rt"
    assert written["scopes"] == ["https://www.googleapis.com/auth/drive.readonly"]
    assert written["expiry"]


def test_gdrive_reads_the_google_drive_entry_not_rclone(store):
    _write(
        store,
        "google-oauth/rclone.json",
        {
            "id": "rclone",
            "oauth": {
                "container": ["rclone", "token"],
                "access_field": "access_token",
                "expiry_field": "expiry",
                "client_from": ["rclone"],
            },
            "flat": {"rclone": {"token": {"access_token": "rclone-token", "expiry": "2099-01-01T00:00:00+00:00"}}},
        },
    )
    _write(
        store,
        "google-oauth/google-drive.json",
        {
            "id": "google_drive",
            "oauth": {
                "container": ["google_drive", "token"],
                "access_field": "token",
                "expiry_field": "expiry",
                "client_from": ["google_drive", "token"],
            },
            "flat": {"google_drive": {"token": {"token": "drive-token", "expiry": "2099-01-01T00:00:00+00:00"}}},
        },
    )

    assert gdrive.access_token() == "drive-token"


def test_gdrive_without_a_drive_entry_points_at_the_auth_command(store):
    with pytest.raises(Exception) as excinfo:
        gdrive.access_token()

    assert "evo cred auth --service google-drive" in str(excinfo.value)


def test_registry_drive_scope_is_readonly_not_full_drive():
    spec = registry.spec_for_flat_key("google_drive")

    assert spec["oauth"]["scopes"] == ["https://www.googleapis.com/auth/drive.readonly"]


def test_rclone_is_not_deprecated_while_red_life_still_reads_it():
    spec = registry.spec_for_flat_key("rclone")

    assert spec.get("status") != "deprecated"
