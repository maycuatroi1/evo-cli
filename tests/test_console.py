import subprocess
import sys

import pytest

from evo_cli.console import CommandError, console, run_command


def test_run_command_raises_on_timeout():
    # A command that outlives the timeout must not hang; it should raise.
    with pytest.raises(CommandError, match="timed out"):
        run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)


def test_run_command_timeout_without_check_returns_exception():
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout=1,
        check=False,
    )
    assert isinstance(result, subprocess.TimeoutExpired)


def test_run_command_echo_keeps_brackets(capsys):
    # Rich reads [..] as markup, so an unescaped echo would silently drop the
    # bracketed part and show a command that is not the one we ran.
    with console.capture() as capture:
        run_command([sys.executable, "-c", "pass"], timeout=10)
    assert "[arch=amd64]" not in capture.get()  # sanity: no brackets in this command

    with console.capture() as capture:
        run_command(["echo", "deb [arch=amd64 signed-by=/etc/apt/keyrings/gh.gpg] https://x"], timeout=10)
    assert "deb [arch=amd64 signed-by=/etc/apt/keyrings/gh.gpg] https://x" in capture.get()


def test_run_command_detaches_stdin_when_requested():
    # With stdin=DEVNULL a child that reads stdin sees EOF immediately instead
    # of blocking on an inherited terminal.
    result = run_command(
        [sys.executable, "-c", "import sys; sys.exit(0 if sys.stdin.read() == '' else 1)"],
        stdin=subprocess.DEVNULL,
        timeout=10,
    )
    assert result.returncode == 0
