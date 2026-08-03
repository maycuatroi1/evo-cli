import json
import subprocess

import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import opencode
from evo_cli.commands.opencode import (
    DEFAULT_MCP_SERVERS,
    EXA_ENV_VAR,
    configure_opencode_global,
    configure_opencode_project,
    enable_exa_websearch,
    install_mcp_servers,
    install_opencode,
    load_jsonc,
    merge_mcp_config,
    save_jsonc,
)


@pytest.fixture
def runner():
    return CliRunner()


def test_opencode_command_is_registered():
    assert "setup" in cli.commands
    assert "opencode" in cli.commands["setup"].commands


def test_opencode_help_runs(runner):
    result = runner.invoke(cli, ["setup", "opencode", "--help"])
    assert result.exit_code == 0
    assert "Install Node.js" in result.output or "OpenCode" in result.output


def test_load_jsonc_ignores_comments(tmp_path):
    path = tmp_path / "config.jsonc"
    path.write_text('// header\n{"a": 1}\n// footer')
    assert load_jsonc(path) == {"a": 1}


def test_load_jsonc_missing_file(tmp_path):
    assert load_jsonc(tmp_path / "nope.jsonc") == {}


def test_save_and_load_jsonc(tmp_path):
    path = tmp_path / "config.jsonc"
    save_jsonc(path, {"foo": "bar"}, header="// test")
    data = load_jsonc(path)
    assert data == {"foo": "bar"}
    assert path.read_text().startswith("// test")


def test_merge_mcp_config_preserves_existing():
    existing = {"mcp": {"playwright": {"enabled": False}}}
    merged = merge_mcp_config(existing, DEFAULT_MCP_SERVERS)
    assert merged["mcp"]["playwright"]["enabled"] is False


def test_configure_opencode_global(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evo_cli.commands.opencode.get_global_config_path",
        lambda: tmp_path / "opencode" / "opencode.jsonc",
    )
    path = configure_opencode_global()
    data = load_jsonc(path)
    assert "mcp" in data
    assert set(data["mcp"].keys()) == {"playwright"}


def test_configure_opencode_project(tmp_path):
    path = configure_opencode_project(tmp_path)
    data = load_jsonc(path)
    assert "mcp" in data
    assert set(data["mcp"].keys()) == {"playwright"}


def test_enable_exa_websearch_writes_export(tmp_path, monkeypatch):
    rc = tmp_path / ".zshrc"
    monkeypatch.setattr("evo_cli.commands.opencode.is_windows", lambda: False)
    monkeypatch.setattr("evo_cli.commands.opencode.get_shell_rc_path", lambda: rc)

    enable_exa_websearch()
    assert f"export {EXA_ENV_VAR}=1" in rc.read_text()


def test_enable_exa_websearch_is_idempotent(tmp_path, monkeypatch):
    rc = tmp_path / ".zshrc"
    monkeypatch.setattr("evo_cli.commands.opencode.is_windows", lambda: False)
    monkeypatch.setattr("evo_cli.commands.opencode.get_shell_rc_path", lambda: rc)

    enable_exa_websearch()
    enable_exa_websearch()
    assert rc.read_text().count(f"export {EXA_ENV_VAR}=1") == 1


def test_install_mcp_servers_detaches_stdin_and_sets_timeout(monkeypatch):
    """Regression: MCP servers read stdin, so the fetch must not block on a TTY."""
    calls = []

    def fake_run_command(cmd, **kwargs):
        calls.append((cmd, kwargs))

    monkeypatch.setattr("evo_cli.commands.opencode.run_command", fake_run_command)
    install_mcp_servers()

    assert calls, "expected at least one MCP server fetch"
    for cmd, kwargs in calls:
        assert kwargs.get("stdin") is subprocess.DEVNULL
        assert kwargs.get("timeout")
        # A server that ignores --version must not abort the whole setup.
        assert kwargs.get("check") is False


def test_install_opencode_skips_when_present(monkeypatch):
    monkeypatch.setattr("evo_cli.commands.opencode.shutil.which", lambda _: "/usr/bin/opencode")
    monkeypatch.setattr(
        "evo_cli.commands.opencode.subprocess.run",
        lambda *a, **k: type("R", (), {"stdout": "1.2.3\n"})(),
    )

    def fail_run_command(*a, **k):
        raise AssertionError("run_command should not be called when opencode is present")

    monkeypatch.setattr("evo_cli.commands.opencode.run_command", fail_run_command)
    assert install_opencode("npm") is True


def test_install_opencode_runs_npm_when_missing(monkeypatch):
    states = iter([None, "/usr/bin/opencode"])  # missing before install, present after
    monkeypatch.setattr("evo_cli.commands.opencode.shutil.which", lambda _: next(states))
    monkeypatch.setattr(
        "evo_cli.commands.opencode.subprocess.run",
        lambda *a, **k: type("R", (), {"stdout": "1.2.3\n"})(),
    )
    calls = []
    monkeypatch.setattr(
        "evo_cli.commands.opencode.run_command",
        lambda cmd, **k: calls.append(cmd),
    )
    assert install_opencode("npm") is True
    assert calls == [["npm", "install", "-g", "opencode-ai@latest"]]


def test_setup_opencode_skip_install(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evo_cli.commands.opencode.get_global_config_path",
        lambda: tmp_path / "global.jsonc",
    )
    monkeypatch.setattr(
        "evo_cli.commands.opencode.ensure_node_installed",
        lambda: ("node", "npm", "npx"),
    )

    def fail_install_opencode(*a, **k):
        raise AssertionError("install_opencode must not run with --skip-install")

    monkeypatch.setattr("evo_cli.commands.opencode.install_opencode", fail_install_opencode)
    monkeypatch.setattr("evo_cli.commands.opencode.install_mcp_servers", lambda: None)
    monkeypatch.setattr("evo_cli.commands.opencode.install_playwright_browsers", lambda: None)
    monkeypatch.setattr("evo_cli.commands.opencode.verify_mcp_servers", lambda: None)
    monkeypatch.setattr("evo_cli.commands.opencode.enable_exa_websearch", lambda: None)

    result = runner.invoke(cli, ["setup", "opencode", "--global-only", "--skip-install"])
    assert result.exit_code == 0


def test_setup_opencode_writes_global_and_project(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evo_cli.commands.opencode.get_global_config_path",
        lambda: tmp_path / "global.jsonc",
    )
    monkeypatch.setattr(
        "evo_cli.commands.opencode.ensure_node_installed",
        lambda: ("node", "npm", "npx"),
    )
    monkeypatch.setattr("evo_cli.commands.opencode.install_opencode", lambda *a, **k: True)
    monkeypatch.setattr("evo_cli.commands.opencode.install_mcp_servers", lambda: None)
    monkeypatch.setattr("evo_cli.commands.opencode.install_playwright_browsers", lambda: None)
    monkeypatch.setattr("evo_cli.commands.opencode.verify_mcp_servers", lambda: None)
    monkeypatch.setattr("evo_cli.commands.opencode.enable_exa_websearch", lambda: None)

    result = runner.invoke(cli, ["setup", "opencode", "--project", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "opencode.json").exists()
    project_data = json.loads((tmp_path / "opencode.json").read_text().split("\n", 1)[1])
    assert "mcp" in project_data


def test_setup_opencode_global_only(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evo_cli.commands.opencode.get_global_config_path",
        lambda: tmp_path / "global.jsonc",
    )
    monkeypatch.setattr(
        "evo_cli.commands.opencode.ensure_node_installed",
        lambda: ("node", "npm", "npx"),
    )
    monkeypatch.setattr("evo_cli.commands.opencode.install_opencode", lambda *a, **k: True)
    monkeypatch.setattr("evo_cli.commands.opencode.install_mcp_servers", lambda: None)
    monkeypatch.setattr("evo_cli.commands.opencode.install_playwright_browsers", lambda: None)
    monkeypatch.setattr("evo_cli.commands.opencode.verify_mcp_servers", lambda: None)
    monkeypatch.setattr("evo_cli.commands.opencode.enable_exa_websearch", lambda: None)

    project = tmp_path / "project"
    project.mkdir()
    result = runner.invoke(cli, ["setup", "opencode", "--global-only", "--project", str(project)])
    assert result.exit_code == 0
    assert not (project / "opencode.json").exists()


def test_global_config_dir_is_xdg_style_on_every_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode.Path, "home", classmethod(lambda cls: tmp_path))
    for system in ("Windows", "Linux", "Darwin"):
        monkeypatch.setattr(opencode.platform, "system", lambda system=system: system)
        assert opencode.get_global_config_dir() == tmp_path / ".config" / "opencode"


def test_global_config_path_reuses_an_existing_json_file(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode.Path, "home", classmethod(lambda cls: tmp_path))
    directory = tmp_path / ".config" / "opencode"
    directory.mkdir(parents=True)
    existing = directory / "opencode.json"
    existing.write_text("{}", encoding="utf-8")
    assert opencode.get_global_config_path() == existing


def test_global_config_path_prefers_jsonc_when_both_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode.Path, "home", classmethod(lambda cls: tmp_path))
    directory = tmp_path / ".config" / "opencode"
    directory.mkdir(parents=True)
    (directory / "opencode.json").write_text("{}", encoding="utf-8")
    (directory / "opencode.jsonc").write_text("{}", encoding="utf-8")
    assert opencode.get_global_config_path() == directory / "opencode.jsonc"


def test_global_config_path_defaults_to_jsonc_when_nothing_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode.Path, "home", classmethod(lambda cls: tmp_path))
    expected = tmp_path / ".config" / "opencode" / "opencode.jsonc"
    assert opencode.get_global_config_path() == expected


def _fake_run(stdout, returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_node_major_version_parses_output(monkeypatch):
    monkeypatch.setattr(opencode.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(opencode.subprocess, "run", lambda *a, **k: _fake_run("v22.11.0\n"))
    assert opencode.node_major_version() == 22


def test_node_major_version_none_without_node(monkeypatch):
    monkeypatch.setattr(opencode.shutil, "which", lambda name: None)
    assert opencode.node_major_version() is None


def test_ensure_node_installed_accepts_recent_node(monkeypatch):
    monkeypatch.setattr(opencode.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(opencode, "node_major_version", lambda: opencode.MIN_NODE_MAJOR)

    def fail_install():
        raise AssertionError("must not reinstall a recent Node")

    monkeypatch.setattr(opencode, "install_node_linux", fail_install)
    assert opencode.ensure_node_installed() == ("/usr/bin/node", "/usr/bin/npm", "/usr/bin/npx")


def test_ensure_node_installed_upgrades_old_node(monkeypatch):
    monkeypatch.setattr(opencode.platform, "system", lambda: "Linux")
    monkeypatch.setattr(opencode.shutil, "which", lambda name: f"/usr/bin/{name}")
    versions = iter([12, 22])
    monkeypatch.setattr(opencode, "node_major_version", lambda: next(versions))
    calls = []
    monkeypatch.setattr(opencode, "install_node_linux", lambda: calls.append("install"))
    opencode.ensure_node_installed()
    assert calls == ["install"]


def test_ensure_node_installed_rejects_still_old_node(monkeypatch):
    monkeypatch.setattr(opencode.platform, "system", lambda: "Linux")
    monkeypatch.setattr(opencode.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(opencode, "node_major_version", lambda: 12)
    monkeypatch.setattr(opencode, "install_node_linux", lambda: None)
    with pytest.raises(RuntimeError):
        opencode.ensure_node_installed()


def test_install_node_linux_uses_nodesource_on_apt(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    monkeypatch.setattr(opencode.tempfile, "gettempdir", lambda: str(tmp_path))
    downloaded = []
    monkeypatch.setattr(opencode, "download_file", lambda url, dest, description=None: downloaded.append(url))
    commands = []
    monkeypatch.setattr(opencode, "run_sudo_command", lambda cmd, **k: commands.append(list(cmd)))
    opencode.install_node_linux()
    assert f"setup_{opencode.NODESOURCE_MAJOR}.x" in downloaded[0]
    assert commands[0][0] == "bash"
    assert commands[1] == ["apt-get", "install", "-y", "nodejs"]


def test_install_node_linux_removes_conflicting_npm(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    monkeypatch.setattr(opencode.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(opencode, "download_file", lambda url, dest, description=None: None)
    commands = []

    def fake_sudo(cmd, **kwargs):
        cmd = list(cmd)
        commands.append(cmd)
        if cmd[:4] == ["apt-get", "install", "-y", "nodejs"] and commands.count(cmd) == 1:
            raise opencode.CommandError("conflict")

    monkeypatch.setattr(opencode, "run_sudo_command", fake_sudo)
    opencode.install_node_linux()
    assert ["apt-get", "remove", "-y", "npm"] in commands
    assert commands[-1] == ["apt-get", "install", "-y", "nodejs"]


def test_npm_global_needs_sudo_when_prefix_not_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode, "is_windows", lambda: False)
    monkeypatch.setattr(opencode.subprocess, "run", lambda *a, **k: _fake_run(f"{tmp_path}\n"))
    monkeypatch.setattr(opencode.os, "access", lambda path, mode: False)
    assert opencode.npm_global_needs_sudo("npm") is True


def test_npm_global_needs_sudo_false_when_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode, "is_windows", lambda: False)
    monkeypatch.setattr(opencode.subprocess, "run", lambda *a, **k: _fake_run(f"{tmp_path}\n"))
    monkeypatch.setattr(opencode.os, "access", lambda path, mode: True)
    assert opencode.npm_global_needs_sudo("npm") is False


def test_install_opencode_uses_sudo_when_prefix_not_writable(monkeypatch):
    monkeypatch.setattr(opencode, "opencode_version", lambda: None)
    monkeypatch.setattr(opencode, "npm_global_needs_sudo", lambda npm_cmd: True)
    sudo_calls = []
    monkeypatch.setattr(opencode, "run_sudo_command", lambda cmd, **k: sudo_calls.append(list(cmd)))

    def fail_plain(*a, **k):
        raise AssertionError("must not run npm install -g without sudo")

    monkeypatch.setattr(opencode, "run_command", fail_plain)
    versions = iter([None, "1.0.0"])
    monkeypatch.setattr(opencode, "opencode_version", lambda: next(versions))
    assert opencode.install_opencode("npm") is True
    assert sudo_calls == [["npm", "install", "-g", "opencode-ai@latest"]]
