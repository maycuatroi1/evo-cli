import json

import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands.opencode import (
    DEFAULT_MCP_SERVERS,
    configure_opencode_global,
    configure_opencode_project,
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
    path.write_text("// header\n{\"a\": 1}\n// footer")
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
    assert merged["mcp"]["google-search"]["enabled"] is True


def test_configure_opencode_global(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evo_cli.commands.opencode.get_global_config_path",
        lambda: tmp_path / "opencode" / "opencode.jsonc",
    )
    path = configure_opencode_global()
    data = load_jsonc(path)
    assert "mcp" in data
    assert set(data["mcp"].keys()) == {"google-search", "playwright"}


def test_configure_opencode_project(tmp_path):
    path = configure_opencode_project(tmp_path)
    data = load_jsonc(path)
    assert "mcp" in data
    assert set(data["mcp"].keys()) == {"google-search", "playwright"}


def test_setup_opencode_writes_global_and_project(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evo_cli.commands.opencode.get_global_config_path",
        lambda: tmp_path / "global.jsonc",
    )
    monkeypatch.setattr(
        "evo_cli.commands.opencode.ensure_node_installed",
        lambda: ("node", "npm", "npx"),
    )
    monkeypatch.setattr("evo_cli.commands.opencode.install_mcp_servers", lambda: None)
    monkeypatch.setattr(
        "evo_cli.commands.opencode.install_playwright_browsers", lambda: None
    )
    monkeypatch.setattr("evo_cli.commands.opencode.verify_mcp_servers", lambda: None)

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
    monkeypatch.setattr("evo_cli.commands.opencode.install_mcp_servers", lambda: None)
    monkeypatch.setattr(
        "evo_cli.commands.opencode.install_playwright_browsers", lambda: None
    )
    monkeypatch.setattr("evo_cli.commands.opencode.verify_mcp_servers", lambda: None)

    project = tmp_path / "project"
    project.mkdir()
    result = runner.invoke(cli, ["setup", "opencode", "--global-only", "--project", str(project)])
    assert result.exit_code == 0
    assert not (project / "opencode.json").exists()
