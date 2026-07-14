import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import gh as gh_mod
from evo_cli.commands.gh import check_gh_auth, gh_version, install_command, install_gh


@pytest.fixture
def runner():
    return CliRunner()


def only_which(*available):
    """A shutil.which stub that finds only the named binaries."""
    return lambda name: f"/usr/bin/{name}" if name in available else None


def test_gh_command_is_registered():
    assert "gh" in cli.commands["setup"].commands


def test_gh_help_runs(runner):
    result = runner.invoke(cli, ["setup", "gh", "--help"])
    assert result.exit_code == 0
    assert "GitHub CLI" in result.output


def test_install_command_macos_uses_brew(monkeypatch):
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which("brew"))
    assert install_command() == ["brew", "install", "gh"]


def test_install_command_macos_without_brew(monkeypatch):
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which())
    assert install_command() is None


def test_install_command_apt_adds_github_repo(monkeypatch):
    """gh is not in the default Debian/Ubuntu repos, so the keyring must be set up."""
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which("apt-get"))
    command = install_command()
    assert command[:2] == ["bash", "-c"]
    assert "githubcli-archive-keyring.gpg" in command[2]
    assert "sudo apt install gh -y" in command[2]


def test_install_command_pacman(monkeypatch):
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which("pacman"))
    assert install_command() == ["sudo", "pacman", "-S", "--noconfirm", "github-cli"]


def test_install_command_linux_without_package_manager(monkeypatch):
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which())
    assert install_command() is None


def test_install_command_windows_winget_is_non_interactive(monkeypatch):
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which("winget"))
    command = install_command()
    assert command[:4] == ["winget", "install", "--id", "GitHub.cli"]
    assert "--accept-package-agreements" in command


def test_install_command_windows_falls_back_to_scoop(monkeypatch):
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which("scoop"))
    assert install_command() == ["scoop", "install", "gh"]


def test_gh_version_none_when_missing(monkeypatch):
    monkeypatch.setattr(gh_mod.shutil, "which", only_which())
    assert gh_version() is None


def test_install_gh_skips_when_present(monkeypatch):
    monkeypatch.setattr(gh_mod, "gh_version", lambda: "2.62.0")
    monkeypatch.setattr(gh_mod, "run_command", lambda *a, **kw: pytest.fail("must not install"))
    assert install_gh() is True


def test_install_gh_reinstall_runs_the_installer(monkeypatch):
    calls = []
    monkeypatch.setattr(gh_mod, "gh_version", lambda: "2.62.0")
    monkeypatch.setattr(gh_mod, "install_command", lambda: ["brew", "install", "gh"])
    monkeypatch.setattr(gh_mod, "run_command", lambda cmd, **kw: calls.append(cmd))
    assert install_gh(reinstall=True) is True
    assert calls == [["brew", "install", "gh"]]


def test_install_gh_fails_without_package_manager(monkeypatch):
    monkeypatch.setattr(gh_mod, "gh_version", lambda: None)
    monkeypatch.setattr(gh_mod, "install_command", lambda: None)
    monkeypatch.setattr(gh_mod, "run_command", lambda *a, **kw: pytest.fail("must not install"))
    assert install_gh() is False


def test_install_gh_reports_failed_install(monkeypatch):
    monkeypatch.setattr(gh_mod, "gh_version", lambda: None)
    monkeypatch.setattr(gh_mod, "install_command", lambda: ["brew", "install", "gh"])

    def boom(cmd, **kwargs):
        raise gh_mod.CommandError("brew exploded")

    monkeypatch.setattr(gh_mod, "run_command", boom)
    assert install_gh() is False


def test_install_gh_detects_binary_not_on_path(monkeypatch):
    """A package manager can succeed while the binary is still not reachable."""
    monkeypatch.setattr(gh_mod, "gh_version", lambda: None)
    monkeypatch.setattr(gh_mod, "install_command", lambda: ["brew", "install", "gh"])
    monkeypatch.setattr(gh_mod, "run_command", lambda cmd, **kw: None)
    assert install_gh() is False


class FakeResult:
    def __init__(self, returncode):
        self.returncode = returncode


def test_check_gh_auth_signed_in(monkeypatch):
    monkeypatch.setattr(gh_mod, "run_command", lambda cmd, **kw: FakeResult(0))
    assert check_gh_auth() is True


def test_check_gh_auth_signed_out(monkeypatch):
    """`gh auth status` exits non-zero when logged out; that must not be fatal."""
    monkeypatch.setattr(gh_mod, "run_command", lambda cmd, **kw: FakeResult(1))
    assert check_gh_auth() is False


def test_setup_gh_skip_auth_check(runner, monkeypatch):
    monkeypatch.setattr(gh_mod, "install_gh", lambda reinstall: True)
    monkeypatch.setattr(gh_mod, "gh_version", lambda: "2.62.0")
    monkeypatch.setattr(gh_mod, "check_gh_auth", lambda: pytest.fail("must not check auth"))

    result = runner.invoke(cli, ["setup", "gh", "--skip-auth-check"])
    assert result.exit_code == 0
    assert "Auth check skipped" in result.output
