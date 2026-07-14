import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import claude_code
from evo_cli.commands.claude_code import (
    DEFAULT_MCP_SERVERS,
    configure_mcp_servers,
    install_attempts,
    install_claude,
    native_install_command,
    npm_install_command,
)
from evo_cli.mcp_registry import MCP_REGISTRY


@pytest.fixture
def runner():
    return CliRunner()


def test_claude_command_is_registered():
    assert "setup" in cli.commands
    assert "claude" in cli.commands["setup"].commands


def test_claude_help_runs(runner):
    result = runner.invoke(cli, ["setup", "claude", "--help"])
    assert result.exit_code == 0
    assert "Claude Code" in result.output


def test_default_mcp_servers_are_in_the_library():
    for name in DEFAULT_MCP_SERVERS:
        assert name in MCP_REGISTRY


def test_native_install_command_posix(monkeypatch):
    monkeypatch.setattr(claude_code, "is_windows", lambda: False)
    assert native_install_command() == ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"]


def test_native_install_command_pins_version(monkeypatch):
    monkeypatch.setattr(claude_code, "is_windows", lambda: False)
    assert native_install_command("2.1.153")[-1].endswith("| bash -s 2.1.153")


def test_native_install_command_windows(monkeypatch):
    monkeypatch.setattr(claude_code, "is_windows", lambda: True)
    command = native_install_command()
    assert command[0] == "powershell"
    assert "install.ps1" in command[-1]


def test_npm_install_command():
    assert npm_install_command() == ["npm", "install", "-g", "@anthropic-ai/claude-code"]
    assert npm_install_command("2.1.153")[-1] == "@anthropic-ai/claude-code@2.1.153"


def test_install_attempts_auto_falls_back_to_npm(monkeypatch):
    """With the native installer unusable, `auto` still has npm to fall back on."""
    monkeypatch.setattr(claude_code, "native_install_available", lambda version=None: False)
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: "/usr/bin/npm")
    labels = [label for label, _ in install_attempts("auto", None)]
    assert labels == ["npm"]


def test_install_attempts_native_only_skips_npm(monkeypatch):
    monkeypatch.setattr(claude_code, "native_install_available", lambda version=None: True)
    monkeypatch.setattr(claude_code, "is_windows", lambda: False)
    labels = [label for label, _ in install_attempts("native", None)]
    assert labels == ["native installer"]


def test_native_install_unavailable_when_pinning_on_windows(monkeypatch):
    """install.ps1 takes no version argument, so a pinned version must go via npm."""
    monkeypatch.setattr(claude_code, "is_windows", lambda: True)
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: "/usr/bin/powershell")
    assert claude_code.native_install_available() is True
    assert claude_code.native_install_available("2.1.153") is False


def test_install_claude_skips_when_already_present(monkeypatch):
    calls = []
    monkeypatch.setattr(claude_code, "ensure_claude_on_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "probe_version", lambda: (2, 1, 153))
    monkeypatch.setattr(claude_code, "run_command", lambda *a, **kw: calls.append(a))

    assert install_claude() is True
    assert calls == []


def test_install_claude_reinstall_runs_the_installer(monkeypatch):
    calls = []
    monkeypatch.setattr(claude_code, "ensure_claude_on_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "probe_version", lambda: (2, 1, 153))
    monkeypatch.setattr(claude_code, "install_attempts", lambda m, v: [("npm", ["npm", "install"])])
    monkeypatch.setattr(claude_code, "run_command", lambda cmd, **kw: calls.append(cmd))

    assert install_claude(reinstall=True) is True
    assert calls == [["npm", "install"]]


def test_install_claude_falls_back_after_a_failure(monkeypatch):
    """A failing native install must not abort the run; npm is still tried."""
    calls = []
    monkeypatch.setattr(claude_code, "ensure_claude_on_path", lambda: None if not calls else "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "probe_version", lambda: (2, 1, 153))
    monkeypatch.setattr(
        claude_code,
        "install_attempts",
        lambda m, v: [("native installer", ["bash", "-c", "boom"]), ("npm", ["npm", "install"])],
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "bash":
            raise claude_code.CommandError("native failed")

    monkeypatch.setattr(claude_code, "run_command", fake_run)

    assert install_claude() is True
    assert calls == [["bash", "-c", "boom"], ["npm", "install"]]


def test_install_claude_reports_failure_when_nothing_works(monkeypatch):
    monkeypatch.setattr(claude_code, "ensure_claude_on_path", lambda: None)
    monkeypatch.setattr(claude_code, "install_attempts", lambda m, v: [])
    assert install_claude() is False


def test_configure_mcp_servers_passes_registry_specs(monkeypatch):
    seen = []

    def fake_add(name, spec, scope):
        seen.append((name, spec, scope))
        return True

    monkeypatch.setattr(claude_code, "add_to_claude", fake_add)
    added = configure_mcp_servers(["playwright"], "user")

    assert added == ["playwright"]
    assert seen == [("playwright", MCP_REGISTRY["playwright"], "user")]


def test_configure_mcp_servers_omits_failures(monkeypatch):
    monkeypatch.setattr(claude_code, "add_to_claude", lambda name, spec, scope: name != "context7")
    assert configure_mcp_servers(["playwright", "context7"], "user") == ["playwright"]


def test_unknown_mcp_server_is_rejected(runner, monkeypatch):
    monkeypatch.setattr(claude_code, "run_setup_claude", lambda *a, **kw: pytest.fail("must not run"))
    result = runner.invoke(cli, ["setup", "claude", "--mcp", "nope"])
    assert result.exit_code == 0
    assert "Unknown MCP server" in result.output


def test_skip_install_without_claude_does_nothing(runner, monkeypatch):
    monkeypatch.setattr(claude_code, "ensure_claude_on_path", lambda: None)
    monkeypatch.setattr(claude_code, "configure_mcp_servers", lambda *a: pytest.fail("must not configure"))
    monkeypatch.setattr(claude_code, "run_doctor", lambda: pytest.fail("must not run doctor"))

    result = runner.invoke(cli, ["setup", "claude", "--skip-install", "--no-gh"])
    assert result.exit_code == 0
    assert "not found on PATH" in result.output


def test_no_mcp_skips_registration(runner, monkeypatch):
    monkeypatch.setattr(claude_code, "ensure_claude_on_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "probe_version", lambda: (2, 1, 200))
    monkeypatch.setattr(claude_code, "configure_mcp_servers", lambda *a: pytest.fail("must not configure"))
    monkeypatch.setattr(claude_code, "run_doctor", lambda: None)

    result = runner.invoke(cli, ["setup", "claude", "--skip-install", "--no-mcp", "--no-gh"])
    assert result.exit_code == 0
    assert "Skipping MCP registration" in result.output


def test_setup_installs_gh_by_default(runner, monkeypatch):
    """Claude Code shells out to gh for PRs, so a plain run must set it up too."""
    calls = []
    monkeypatch.setattr(claude_code, "ensure_claude_on_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "probe_version", lambda: (2, 1, 200))
    monkeypatch.setattr(claude_code, "install_gh", lambda: calls.append("install") or True)
    monkeypatch.setattr(claude_code, "check_gh_auth", lambda: calls.append("auth") or True)
    monkeypatch.setattr(claude_code, "run_doctor", lambda: None)

    result = runner.invoke(cli, ["setup", "claude", "--skip-install", "--no-mcp"])
    assert result.exit_code == 0
    assert calls == ["install", "auth"]


def test_no_gh_skips_the_github_cli(runner, monkeypatch):
    monkeypatch.setattr(claude_code, "ensure_claude_on_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "probe_version", lambda: (2, 1, 200))
    monkeypatch.setattr(claude_code, "install_gh", lambda: pytest.fail("must not install gh"))
    monkeypatch.setattr(claude_code, "run_doctor", lambda: None)

    result = runner.invoke(cli, ["setup", "claude", "--skip-install", "--no-mcp", "--no-gh"])
    assert result.exit_code == 0
    assert "Skipping the GitHub CLI" in result.output


def test_setup_github_cli_states(monkeypatch):
    monkeypatch.setattr(claude_code, "install_gh", lambda: False)
    monkeypatch.setattr(claude_code, "check_gh_auth", lambda: pytest.fail("must not check a missing gh"))
    assert claude_code.setup_github_cli() == "missing"

    monkeypatch.setattr(claude_code, "install_gh", lambda: True)
    monkeypatch.setattr(claude_code, "check_gh_auth", lambda: False)
    assert claude_code.setup_github_cli() == "unauthenticated"

    monkeypatch.setattr(claude_code, "check_gh_auth", lambda: True)
    assert claude_code.setup_github_cli() == "ready"


def test_failed_gh_install_does_not_block_claude_setup(runner, monkeypatch):
    """gh is a companion tool: losing it must not fail the Claude Code setup."""
    monkeypatch.setattr(claude_code, "ensure_claude_on_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "probe_version", lambda: (2, 1, 200))
    monkeypatch.setattr(claude_code, "install_gh", lambda: False)
    monkeypatch.setattr(claude_code, "configure_mcp_servers", lambda *a: ["playwright"])
    monkeypatch.setattr(claude_code, "run_doctor", lambda: None)

    result = runner.invoke(cli, ["setup", "claude", "--skip-install"])
    assert result.exit_code == 0
    assert "setup claude complete" in result.output
    assert "evo setup gh" in result.output
