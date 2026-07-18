import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands.mcp import add_to_claude, resolve_spec, to_opencode_config
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


def test_resolve_spec_playwright_is_local():
    spec = resolve_spec("playwright", None, "http")
    assert spec["transport"] == "stdio"
    assert spec["command"] == ["npx", "-y", "@playwright/mcp@latest"]
    assert to_opencode_config(spec) == {
        "type": "local",
        "command": ["npx", "-y", "@playwright/mcp@latest"],
        "enabled": True,
    }


def test_mcp_list_shows_playwright(runner):
    result = runner.invoke(cli, ["mcp", "list"])
    assert result.exit_code == 0
    assert "playwright" in result.output


def test_resolve_spec_custom_url():
    spec = resolve_spec("anything", "https://x.dev/mcp", "http")
    assert spec["transport"] == "http"
    assert spec["url"] == "https://x.dev/mcp"


def test_resolve_spec_custom_command():
    spec = resolve_spec("my-local", None, "http", command="npx -y some-mcp", env={"K": "V"})
    assert spec["transport"] == "stdio"
    assert spec["command"] == ["npx", "-y", "some-mcp"]
    assert spec["env"] == {"K": "V"}


def test_resolve_spec_url_and_command_conflict():
    with pytest.raises(Exception):
        resolve_spec("x", "https://x.dev/mcp", "http", command="npx -y some-mcp")


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


def test_add_playwright_opencode_only_writes_local(runner, tmp_path, monkeypatch):
    global_path = tmp_path / "global.jsonc"
    monkeypatch.setattr(
        "evo_cli.commands.mcp.get_global_config_path",
        lambda: global_path,
    )
    result = runner.invoke(cli, ["mcp", "add", "playwright", "--opencode-only"])
    assert result.exit_code == 0
    entry = load_jsonc(global_path)["mcp"]["playwright"]
    assert entry["type"] == "local"
    assert entry["command"] == ["npx", "-y", "@playwright/mcp@latest"]


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


def test_claude_add_puts_the_name_before_the_variadic_env_flag(monkeypatch):
    captured = []
    monkeypatch.setattr("evo_cli.commands.mcp.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("evo_cli.commands.mcp.claude_has_server", lambda name: False)
    monkeypatch.setattr("evo_cli.commands.mcp.run_command", lambda cmd, **kw: captured.append(cmd))

    spec = {"transport": "stdio", "command": ["uv", "run", "server.py"], "env": {"A": "1", "B": "2"}}
    add_to_claude("evo-tts", spec, "user")

    cmd = captured[0]
    # `claude mcp add` declares -e as variadic, so a name after it is swallowed as
    # another KEY=value and the real CLI errors out.
    assert cmd.index("evo-tts") < cmd.index("-e")
    assert cmd[cmd.index("--") + 1 :] == ["uv", "run", "server.py"]
    assert "A=1" in cmd and "B=2" in cmd


def test_opencode_add_leaves_an_existing_entry_alone_without_force(runner, tmp_path, monkeypatch):
    global_path = tmp_path / "global.jsonc"
    monkeypatch.setattr("evo_cli.commands.mcp.get_global_config_path", lambda: global_path)

    runner.invoke(cli, ["mcp", "add", "srv", "--opencode-only", "--command", "old cmd"])
    result = runner.invoke(cli, ["mcp", "add", "srv", "--opencode-only", "--command", "new cmd"])

    assert result.exit_code == 0
    assert load_jsonc(global_path)["mcp"]["srv"]["command"] == ["old", "cmd"]


def test_opencode_add_force_replaces_the_entry(runner, tmp_path, monkeypatch):
    global_path = tmp_path / "global.jsonc"
    monkeypatch.setattr("evo_cli.commands.mcp.get_global_config_path", lambda: global_path)

    runner.invoke(cli, ["mcp", "add", "srv", "--opencode-only", "--command", "old cmd"])
    result = runner.invoke(
        cli,
        ["mcp", "add", "srv", "--opencode-only", "--force", "--env", "K=v", "--command", "new cmd"],
    )

    assert result.exit_code == 0
    entry = load_jsonc(global_path)["mcp"]["srv"]
    assert entry["command"] == ["new", "cmd"]
    assert entry["environment"] == {"K": "v"}
