from click.testing import CliRunner

from evo_cli.cli import cli


def test_all_commands_are_registered():
    for name in ("setupssh", "miniconda", "cfssh", "f-claude"):
        assert name in cli.commands


def test_group_help_runs():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0


def test_fix_claude_help_runs():
    result = CliRunner().invoke(cli, ["f-claude", "--help"])
    assert result.exit_code == 0
