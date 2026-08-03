import subprocess
import sys

import pytest

from evo_cli import console as evo_console
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
    original_width = console.width
    console.width = 200
    try:
        with console.capture() as capture:
            run_command([sys.executable, "-c", "pass"], timeout=10)
        assert "[arch=amd64]" not in capture.get()  # sanity: no brackets in this command

        bracketed = "deb [arch=amd64 signed-by=/etc/apt/keyrings/gh.gpg] https://x"
        with console.capture() as capture:
            run_command([sys.executable, "-c", f"print({bracketed!r})"], timeout=10)
        assert bracketed in capture.get()
    finally:
        console.width = original_width


def test_run_command_detaches_stdin_when_requested():
    # With stdin=DEVNULL a child that reads stdin sees EOF immediately instead
    # of blocking on an inherited terminal.
    result = run_command(
        [sys.executable, "-c", "import sys; sys.exit(0 if sys.stdin.read() == '' else 1)"],
        stdin=subprocess.DEVNULL,
        timeout=10,
    )
    assert result.returncode == 0


@pytest.fixture(autouse=True)
def reset_sudo_cache(monkeypatch):
    monkeypatch.setattr(evo_console, "_SUDO_PRIMED", False, raising=False)


def test_ensure_sudo_noop_as_root(monkeypatch):
    monkeypatch.setattr(evo_console, "is_root", lambda: True)
    assert evo_console.ensure_sudo() is True


def test_ensure_sudo_uses_cached_credentials(monkeypatch):
    monkeypatch.setattr(evo_console, "is_root", lambda: False)
    monkeypatch.setattr(evo_console.shutil, "which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(evo_console, "_sudo_credentials_cached", lambda: True)

    def fail_run(*a, **k):
        raise AssertionError("must not re-authenticate when sudo is already primed")

    monkeypatch.setattr(evo_console.subprocess, "run", fail_run)
    assert evo_console.ensure_sudo() is True


def test_ensure_sudo_raises_instead_of_hanging_without_tty(monkeypatch):
    monkeypatch.setattr(evo_console, "is_root", lambda: False)
    monkeypatch.setattr(evo_console.shutil, "which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(evo_console, "_sudo_credentials_cached", lambda: False)
    monkeypatch.delenv(evo_console.SUDO_PASSWORD_ENV, raising=False)
    monkeypatch.setattr(evo_console.sys, "stdin", type("S", (), {"isatty": staticmethod(lambda: False)})())
    with pytest.raises(CommandError, match="sudo needs a password"):
        evo_console.ensure_sudo()


def test_ensure_sudo_authenticates_from_env(monkeypatch):
    monkeypatch.setattr(evo_console, "is_root", lambda: False)
    monkeypatch.setattr(evo_console.shutil, "which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(evo_console, "_sudo_credentials_cached", lambda: False)
    monkeypatch.setenv(evo_console.SUDO_PASSWORD_ENV, "hunter2")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(evo_console.subprocess, "run", fake_run)
    assert evo_console.ensure_sudo() is True
    assert seen["cmd"][-1] == "-v"
    assert seen["input"] == "hunter2\n"


def test_ensure_sudo_raises_on_wrong_password(monkeypatch):
    monkeypatch.setattr(evo_console, "is_root", lambda: False)
    monkeypatch.setattr(evo_console.shutil, "which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(evo_console, "_sudo_credentials_cached", lambda: False)
    monkeypatch.setenv(evo_console.SUDO_PASSWORD_ENV, "nope")
    monkeypatch.setattr(
        evo_console.subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=""),
    )
    with pytest.raises(CommandError, match="authentication failed"):
        evo_console.ensure_sudo()


def test_run_sudo_command_prefixes_sudo(monkeypatch):
    monkeypatch.setattr(evo_console, "is_root", lambda: False)
    monkeypatch.setattr(evo_console, "ensure_sudo", lambda: True)
    seen = {}
    monkeypatch.setattr(evo_console, "run_command", lambda cmd, **k: seen.setdefault("cmd", list(cmd)))
    evo_console.run_sudo_command(["apt-get", "install", "-y", "nodejs"])
    assert seen["cmd"] == ["sudo", "-n", "apt-get", "install", "-y", "nodejs"]


def test_run_sudo_command_skips_sudo_as_root(monkeypatch):
    monkeypatch.setattr(evo_console, "is_root", lambda: True)
    seen = {}
    monkeypatch.setattr(evo_console, "run_command", lambda cmd, **k: seen.setdefault("cmd", list(cmd)))
    evo_console.run_sudo_command(["apt-get", "update"])
    assert seen["cmd"] == ["apt-get", "update"]
