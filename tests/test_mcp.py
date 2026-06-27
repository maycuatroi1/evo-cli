import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands.mcp import resolve_spec, to_opencode_config
from evo_cli.commands.opencode import load_jsonc


@pytest.fixture
def runner():
    return CliRunner()


def test_mcp_command_is_registered():
    assert "mcp" in cli.commands
    assert "add" in cli.commands["mcp"].commands
    assert "list" in cli.commands["mcp"].commands


def test_mcp_list_runs(runner):
    result = runner.invoke(cli, ["mcp", "list"])
    assert result.exit_code == 0
    assert "notion" in result.output


def test_to_opencode_config_remote():
    spec = {"transport": "http", "url": "https://example.com/mcp"}
    assert to_opencode_config(spec) == {
        "type": "remote",
        "url": "https://example.com/mcp",
        "enabled": True,
    }


def test_to_opencode_config_local():
    spec = {"transport": "stdio", "command": ["npx", "-y", "thing"]}
    assert to_opencode_config(spec) == {
        "type": "local",
        "command": ["npx", "-y", "thing"],
        "enabled": True,
    }


def test_resolve_spec_from_registry():
    spec = resolve_spec("notion", None, "http")
    assert spec["url"] == "https://mcp.notion.com/mcp"


def test_resolve_spec_custom_url():
    spec = resolve_spec("anything", "https://x.dev/mcp", "http")
    assert spec["transport"] == "http"
    assert spec["url"] == "https://x.dev/mcp"


def test_resolve_spec_unknown_raises():
    with pytest.raises(Exception):
        resolve_spec("nope", None, "http")


def test_add_opencode_only_writes_configs(runner, tmp_path, monkeypatch):
    global_path = tmp_path / "global.jsonc"
    monkeypatch.setattr(
        "evo_cli.commands.mcp.get_global_config_path",
        lambda: global_path,
    )
    project = tmp_path / "project"
    project.mkdir()

    result = runner.invoke(
        cli,
        ["mcp", "add", "notion", "--opencode-only", "--project", str(project)],
    )
    assert result.exit_code == 0
    assert load_jsonc(global_path)["mcp"]["notion"]["url"] == "https://mcp.notion.com/mcp"
    assert load_jsonc(project / "opencode.json")["mcp"]["notion"]["type"] == "remote"


def test_add_rejects_both_only_flags(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evo_cli.commands.mcp.get_global_config_path",
        lambda: tmp_path / "global.jsonc",
    )
    result = runner.invoke(
        cli,
        ["mcp", "add", "notion", "--claude-only", "--opencode-only"],
    )
    assert result.exit_code == 0
    assert "mutually exclusive" in result.output
