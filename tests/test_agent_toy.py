import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import agent_toy as toy
from evo_cli.commands.agent_toy import (
    FAILED,
    INSTALLED,
    PARTIAL,
    TOYS,
    install_agent_skills,
    install_context_mode,
    install_plannotator,
    parse_entries,
    plannotator_install_command,
)

PLUGIN_LIST = """Installed plugins:

  ❯ context-mode@context-mode
    Version: 1.0.169
    Scope: user
    Status: ✔ enabled

  ❯ warp@claude-code-warp
    Version: 2.2.0
    Scope: user
    Status: ✔ enabled
"""

MARKETPLACE_LIST = """Configured marketplaces:

  ❯ context-mode
    Source: Git (https://github.com/mksglu/context-mode.git)

  ❯ plannotator
    Source: GitHub (backnotprop/plannotator)
"""


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def options():
    return {"scope": "user", "minimal": False, "claude": True}


def test_agent_toy_is_registered():
    assert "agent-toy" in cli.commands["setup"].commands


def test_agent_toy_help_runs(runner):
    result = runner.invoke(cli, ["setup", "agent-toy", "--help"])
    assert result.exit_code == 0
    assert "plannotator" in result.output


def test_list_shows_every_toy(runner):
    result = runner.invoke(cli, ["setup", "agent-toy", "--list"])
    assert result.exit_code == 0
    for name in TOYS:
        assert name in result.output


def test_unknown_toy_is_rejected(runner, monkeypatch):
    monkeypatch.setattr(toy, "ensure_claude_on_path", lambda: "/usr/bin/claude")
    result = runner.invoke(cli, ["setup", "agent-toy", "nope"])
    assert "Unknown toy" in result.output


def test_parse_entries_reads_plugin_names():
    assert parse_entries(PLUGIN_LIST) == ["context-mode@context-mode", "warp@claude-code-warp"]


def test_parse_entries_reads_marketplace_names():
    assert parse_entries(MARKETPLACE_LIST) == ["context-mode", "plannotator"]


def test_plannotator_install_command_posix(monkeypatch):
    monkeypatch.setattr(toy, "is_windows", lambda: False)
    monkeypatch.setattr(toy.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert plannotator_install_command(False) == [
        "bash",
        "-c",
        "curl -fsSL https://plannotator.ai/install.sh | bash",
    ]
    assert plannotator_install_command(True)[-1].endswith("| bash -s -- --minimal")


def test_plannotator_install_command_windows(monkeypatch):
    monkeypatch.setattr(toy, "is_windows", lambda: True)
    monkeypatch.setattr(toy.shutil, "which", lambda name: f"C:\\{name}.exe")
    command = plannotator_install_command(True)
    assert command[0] == "powershell"
    assert "PLANNOTATOR_MINIMAL=1" in command[-1]
    assert "install.ps1" in command[-1]


def test_plannotator_install_command_without_a_shell(monkeypatch):
    monkeypatch.setattr(toy, "is_windows", lambda: False)
    monkeypatch.setattr(toy.shutil, "which", lambda name: None)
    assert plannotator_install_command(False) is None


def test_plannotator_is_partial_when_the_installer_fails(monkeypatch, options):
    monkeypatch.setattr(toy, "ensure_marketplace", lambda *args: True)
    monkeypatch.setattr(toy, "ensure_plugin", lambda *args: True)
    monkeypatch.setattr(toy, "run_plannotator_installer", lambda minimal: False)
    monkeypatch.setattr(toy, "plannotator_binary", lambda: "/usr/local/bin/plannotator")
    assert install_plannotator(options) == PARTIAL


def test_plannotator_fails_when_no_binary_landed(monkeypatch, options):
    monkeypatch.setattr(toy, "ensure_marketplace", lambda *args: True)
    monkeypatch.setattr(toy, "ensure_plugin", lambda *args: True)
    monkeypatch.setattr(toy, "run_plannotator_installer", lambda minimal: False)
    monkeypatch.setattr(toy, "plannotator_binary", lambda: None)
    assert install_plannotator(options) == FAILED


def test_plannotator_minimal_skips_the_plugin(monkeypatch, options):
    calls = []
    monkeypatch.setattr(toy, "ensure_marketplace", lambda *args: calls.append(args) or True)
    monkeypatch.setattr(toy, "run_plannotator_installer", lambda minimal: True)
    options["minimal"] = True
    assert install_plannotator(options) == INSTALLED
    assert calls == []


def test_context_mode_needs_claude(options):
    options["claude"] = False
    assert install_context_mode(options) == FAILED


def test_context_mode_installs_the_plugin(monkeypatch, options):
    monkeypatch.setattr(toy, "ensure_marketplace", lambda *args: True)
    monkeypatch.setattr(toy, "ensure_plugin", lambda *args: True)
    assert install_context_mode(options) == INSTALLED


def test_agent_skills_installs_globally(monkeypatch, options):
    captured = {}
    monkeypatch.setattr(toy.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(toy, "run_command", lambda command, **kwargs: captured.setdefault("command", command))
    assert install_agent_skills(options) == INSTALLED
    assert captured["command"][-1] == "--global"
    assert "maycuatroi1/agent-skills" in captured["command"]


def test_agent_skills_scopes_to_the_project(monkeypatch, options):
    captured = {}
    monkeypatch.setattr(toy.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(toy, "run_command", lambda command, **kwargs: captured.setdefault("command", command))
    options["scope"] = "project"
    install_agent_skills(options)
    assert captured["command"][-1] == "--project"


def test_agent_skills_needs_npx(monkeypatch, options):
    monkeypatch.setattr(toy.shutil, "which", lambda name: None)
    assert install_agent_skills(options) == FAILED
