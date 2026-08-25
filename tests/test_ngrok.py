import json
import os
import re
import subprocess
import tarfile
import zipfile

import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import ngrok as ngrok_mod
from evo_cli.commands.ngrok import (
    apply_authtoken,
    asset_url,
    collect_token,
    extract_binary,
    mask,
    package_install_command,
    platform_target,
    resolve_token,
    token_from_config,
    verify_authtoken,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

TOKEN = "2abcDEFghiJKLmnoPQRstu_1vWxYz2AbCdEfGhIjKlM"


def _plain(text):
    return re.sub(r"\s+", " ", _ANSI_RE.sub("", text))


def only_which(*available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(ngrok_mod.ENV_VAR, raising=False)


@pytest.fixture
def store(tmp_path, monkeypatch):
    omelet_dir = tmp_path / ".omelet.d"
    monkeypatch.setenv("OMELET_DIR", str(omelet_dir))
    monkeypatch.setenv("OMELET_CONFIG", str(tmp_path / ".omelet.json"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    (omelet_dir / "credentials").mkdir(parents=True)
    return omelet_dir / "credentials"


@pytest.fixture
def fake_agent(monkeypatch, tmp_path):
    """A stand-in ngrok binary plus a record of how it was called."""
    binary = tmp_path / "ngrok"
    binary.write_text("", encoding="utf-8")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(cmd, 0, "Authtoken saved to configuration file: ngrok.yml", "")

    monkeypatch.setattr(ngrok_mod, "find_ngrok", lambda: binary)
    monkeypatch.setattr(ngrok_mod, "ngrok_version", lambda _binary: "3.30.0")
    monkeypatch.setattr(ngrok_mod.subprocess, "run", fake_run)
    return binary, calls


def test_ngrok_command_is_registered():
    assert "ngrok" in cli.commands["setup"].commands


def test_ngrok_help_runs(runner):
    result = runner.invoke(cli, ["setup", "ngrok", "--help"])
    assert result.exit_code == 0
    assert "ngrok" in _plain(result.output)


def test_asset_url_windows(monkeypatch):
    monkeypatch.setattr(ngrok_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ngrok_mod.platform, "machine", lambda: "AMD64")
    assert asset_url().endswith("/ngrok-v3-stable-windows-amd64.zip")


def test_asset_url_linux_arm64(monkeypatch):
    monkeypatch.setattr(ngrok_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ngrok_mod.platform, "machine", lambda: "aarch64")
    assert asset_url().endswith("/ngrok-v3-stable-linux-arm64.tgz")


def test_asset_url_macos_uses_zip(monkeypatch):
    monkeypatch.setattr(ngrok_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ngrok_mod.platform, "machine", lambda: "arm64")
    assert asset_url().endswith("/ngrok-v3-stable-darwin-arm64.zip")


def test_platform_target_unknown_arch(monkeypatch):
    monkeypatch.setattr(ngrok_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ngrok_mod.platform, "machine", lambda: "sparc64")
    assert platform_target() is None
    assert asset_url() is None


def test_package_install_command_windows_prefers_winget(monkeypatch):
    monkeypatch.setattr(ngrok_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ngrok_mod.shutil, "which", only_which("winget", "choco"))
    assert package_install_command()[:4] == ["winget", "install", "--id", "Ngrok.Ngrok"]


def test_package_install_command_macos_uses_the_ngrok_tap(monkeypatch):
    monkeypatch.setattr(ngrok_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ngrok_mod.shutil, "which", only_which("brew"))
    assert package_install_command() == ["brew", "install", "ngrok/ngrok/ngrok"]


def test_package_install_command_linux_without_privileges(monkeypatch):
    monkeypatch.setattr(ngrok_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ngrok_mod, "is_root", lambda: False)
    monkeypatch.setattr(ngrok_mod.shutil, "which", only_which("apt-get"))
    assert package_install_command() is None


def test_extract_binary_from_zip(tmp_path):
    archive = tmp_path / "ngrok.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("readme.txt", "nope")
        bundle.writestr("ngrok", "#!/bin/sh\n")
    destination = tmp_path / "out" / "ngrok"
    destination.parent.mkdir()
    assert extract_binary(archive, destination) is True
    assert destination.read_text(encoding="utf-8") == "#!/bin/sh\n"
    if os.name != "nt":
        assert os.access(destination, os.X_OK)


def test_extract_binary_from_tarball(tmp_path):
    payload = tmp_path / "ngrok"
    payload.write_text("agent\n", encoding="utf-8")
    archive = tmp_path / "ngrok.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(payload, arcname="ngrok")
    destination = tmp_path / "out" / "ngrok"
    destination.parent.mkdir()
    assert extract_binary(archive, destination) is True
    assert destination.read_text(encoding="utf-8") == "agent\n"


def test_extract_binary_rejects_an_archive_without_the_agent(tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("readme.txt", "nope")
    assert extract_binary(archive, tmp_path / "ngrok") is False


def test_mask_never_shows_the_whole_token():
    masked = mask(TOKEN)
    assert TOKEN not in masked
    assert masked.startswith(TOKEN[:6])


def test_token_from_config_reads_the_agent_yaml(tmp_path, monkeypatch):
    config = tmp_path / "ngrok.yml"
    config.write_text(f"version: 3\nagent:\n  authtoken: {TOKEN}\n", encoding="utf-8")
    monkeypatch.setattr(ngrok_mod, "config_file", lambda: config)
    monkeypatch.setattr(ngrok_mod, "legacy_config_file", lambda: tmp_path / "missing.yml")
    assert token_from_config() == (TOKEN, config)


def test_resolve_token_prefers_the_environment(monkeypatch, store):
    monkeypatch.setenv(ngrok_mod.ENV_VAR, "  from-env  ")
    token, source = resolve_token()
    assert token == "from-env"
    assert source == f"env {ngrok_mod.ENV_VAR}"


def test_collect_token_reuses_a_stored_token(monkeypatch, store):
    monkeypatch.setattr(ngrok_mod, "resolve_token", lambda: (TOKEN, "evo cred ngrok_authtoken"))
    assert collect_token(None, False, force=False) == (TOKEN, "evo cred ngrok_authtoken")


def test_collect_token_without_a_terminal_fails_loudly(monkeypatch, store):
    monkeypatch.setattr(ngrok_mod, "resolve_token", lambda: (None, None))
    monkeypatch.setattr(ngrok_mod.sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(Exception) as excinfo:
        collect_token(None, False, force=False)
    assert "--authtoken" in str(excinfo.value)


def test_apply_authtoken_does_not_echo_the_secret(capsys, monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(cmd, 0, "Authtoken saved", "")

    monkeypatch.setattr(ngrok_mod.subprocess, "run", fake_run)
    assert apply_authtoken(tmp_path / "ngrok", TOKEN) is True
    assert calls[0][1:] == ["config", "add-authtoken", TOKEN]
    printed = capsys.readouterr().out
    assert TOKEN not in printed
    assert "config add-authtoken" in _plain(printed)


def test_setup_ngrok_stores_the_token_and_configures_the_agent(runner, store, fake_agent):
    _binary, calls = fake_agent
    result = runner.invoke(cli, ["setup", "ngrok", "--method", "none", "--authtoken", TOKEN])
    assert result.exit_code == 0, result.output

    stored = json.loads((store / "infra" / "ngrok.json").read_text(encoding="utf-8"))
    assert stored["flat"]["ngrok_authtoken"] == TOKEN
    assert stored["service"] == "ngrok"

    assert calls[0][1:] == ["config", "add-authtoken", TOKEN]
    output = _plain(result.output)
    assert TOKEN not in output
    assert "setup ngrok complete" in output


def test_setup_ngrok_skip_token_leaves_the_store_alone(runner, store, fake_agent):
    _binary, calls = fake_agent
    result = runner.invoke(cli, ["setup", "ngrok", "--method", "none", "--skip-token"])
    assert result.exit_code == 0, result.output
    assert not (store / "infra" / "ngrok.json").exists()
    assert calls == []


def test_setup_ngrok_reads_the_token_from_stdin(runner, store, fake_agent):
    _binary, calls = fake_agent
    result = runner.invoke(cli, ["setup", "ngrok", "--method", "none", "--from-stdin"], input=f"{TOKEN}\n")
    assert result.exit_code == 0, result.output
    assert calls[0][1:] == ["config", "add-authtoken", TOKEN]


def test_setup_ngrok_rejects_an_empty_stdin_token(runner, store, fake_agent):
    result = runner.invoke(cli, ["setup", "ngrok", "--method", "none", "--from-stdin"], input="\n")
    assert result.exit_code != 0
    assert "stdin was empty" in _plain(result.output)


class _FakeProcess:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.terminated = False
        self._returncode = None

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = 0

    def wait(self, timeout=None):
        self._returncode = 0
        return 0

    def kill(self):
        self._returncode = -9


def _fake_popen(lines):
    holder = {}

    def popen(argv, **kwargs):
        holder["process"] = _FakeProcess(lines)
        holder["argv"] = argv
        return holder["process"]

    return popen, holder


def test_verify_authtoken_reports_the_tunnel_url(monkeypatch, tmp_path):
    lines = [
        '{"lvl":"info","msg":"starting web service"}\n',
        '{"lvl":"info","msg":"started tunnel","url":"https://abc123.ngrok-free.app"}\n',
    ]
    popen, holder = _fake_popen(lines)
    monkeypatch.setattr(ngrok_mod.subprocess, "Popen", popen)
    ok, detail = verify_authtoken(tmp_path / "ngrok", timeout=5)
    assert ok is True
    assert detail == "https://abc123.ngrok-free.app"
    assert holder["process"].terminated is True


def test_verify_authtoken_surfaces_an_invalid_token(monkeypatch, tmp_path):
    lines = [
        '{"lvl":"info","msg":"starting web service"}\n',
        '{"lvl":"eror","msg":"failed to auth","err":"authentication failed: ERR_NGROK_105"}\n',
    ]
    popen, _holder = _fake_popen(lines)
    monkeypatch.setattr(ngrok_mod.subprocess, "Popen", popen)
    ok, detail = verify_authtoken(tmp_path / "ngrok", timeout=5)
    assert ok is False
    assert "ERR_NGROK_105" in detail
