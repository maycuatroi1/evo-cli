import tarfile
import zipfile

import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import gh as gh_mod
from evo_cli.commands.gh import (
    asset_url,
    check_gh_auth,
    extract_gh_binary,
    gh_version,
    install_command,
    install_gh,
    latest_gh_version,
    release_arch,
)


@pytest.fixture
def runner():
    return CliRunner()


def only_which(*available):
    """A shutil.which stub that finds only the named binaries."""
    return lambda name: f"/usr/bin/{name}" if name in available else None


@pytest.fixture
def unprivileged(monkeypatch):
    """A plain user: not root, no sudo."""
    monkeypatch.setattr(gh_mod, "is_root", lambda: False)


@pytest.fixture
def as_root(monkeypatch):
    monkeypatch.setattr(gh_mod, "is_root", lambda: True)


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


def test_apt_script_keeps_the_signed_by_source_line(monkeypatch, unprivileged):
    """The apt source needs the arch/signed-by block; it must survive intact."""
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which("apt-get", "sudo"))
    script = install_command()[2]
    assert "deb [arch=$(dpkg --print-architecture) signed-by=" in script
    assert script.startswith("SUDO=sudo\n")
    assert "$SUDO apt install gh -y" in script


def test_apt_script_drops_sudo_when_running_as_root(monkeypatch, as_root):
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which("apt-get"))
    script = install_command()[2]
    assert script.startswith("SUDO=\n")
    assert "sudo" not in script.replace("$SUDO", "")


def test_pacman_is_elevated_only_when_not_root(monkeypatch, unprivileged):
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which("pacman", "sudo"))
    assert install_command() == ["sudo", "pacman", "-S", "--noconfirm", "github-cli"]


def test_pacman_as_root_needs_no_sudo(monkeypatch, as_root):
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which("pacman"))
    assert install_command() == ["pacman", "-S", "--noconfirm", "github-cli"]


def test_no_system_install_without_root_or_sudo(monkeypatch, unprivileged):
    """The container case: apt exists, but nothing can elevate to use it."""
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gh_mod.shutil, "which", only_which("apt-get"))
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


def test_release_arch_maps_uname_machines(monkeypatch):
    monkeypatch.setattr(gh_mod.platform, "machine", lambda: "x86_64")
    assert release_arch() == "amd64"
    monkeypatch.setattr(gh_mod.platform, "machine", lambda: "aarch64")
    assert release_arch() == "arm64"
    monkeypatch.setattr(gh_mod.platform, "machine", lambda: "sparc")
    assert release_arch() is None


def test_asset_url_per_platform():
    assert asset_url("2.62.0", "amd64", "Linux").endswith("/v2.62.0/gh_2.62.0_linux_amd64.tar.gz")
    assert asset_url("2.62.0", "arm64", "Darwin").endswith("/v2.62.0/gh_2.62.0_macOS_arm64.zip")


def test_latest_gh_version_reads_the_redirect(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return "https://github.com/cli/cli/releases/tag/v2.63.1"

    monkeypatch.setattr(gh_mod.urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
    assert latest_gh_version() == "2.63.1"


def test_latest_gh_version_survives_a_network_error(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(gh_mod.urllib.request, "urlopen", boom)
    assert latest_gh_version() is None


def test_extract_gh_binary_from_tarball(tmp_path):
    payload = tmp_path / "gh"
    payload.write_bytes(b"#!/bin/sh\necho gh\n")
    archive = tmp_path / "gh_2.62.0_linux_amd64.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(payload, arcname="gh_2.62.0_linux_amd64/bin/gh")

    destination = tmp_path / "out" / "gh"
    destination.parent.mkdir()
    assert extract_gh_binary(archive, destination) is True
    assert destination.read_bytes() == b"#!/bin/sh\necho gh\n"
    assert destination.stat().st_mode & 0o111  # executable


def test_extract_gh_binary_from_zip(tmp_path):
    archive = tmp_path / "gh_2.62.0_macOS_arm64.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("gh_2.62.0_macOS_arm64/bin/gh", "binary")

    destination = tmp_path / "gh"
    assert extract_gh_binary(archive, destination) is True
    assert destination.read_text() == "binary"


def test_extract_gh_binary_without_the_binary(tmp_path):
    archive = tmp_path / "empty.tar.gz"
    with tarfile.open(archive, "w:gz"):
        pass
    assert extract_gh_binary(archive, tmp_path / "gh") is False


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


def test_install_gh_falls_back_to_release_without_a_package_manager(monkeypatch):
    """No root, no sudo: the userspace install is the only way through."""
    monkeypatch.setattr(gh_mod, "gh_version", lambda: None)
    monkeypatch.setattr(gh_mod, "install_command", lambda: None)
    monkeypatch.setattr(gh_mod, "can_elevate", lambda: False)
    monkeypatch.setattr(gh_mod, "install_from_release", lambda: True)
    monkeypatch.setattr(gh_mod, "run_command", lambda *a, **kw: pytest.fail("must not install"))
    assert install_gh() is True


def test_install_gh_falls_back_to_release_after_a_failed_install(monkeypatch):
    monkeypatch.setattr(gh_mod, "gh_version", lambda: None)
    monkeypatch.setattr(gh_mod, "install_command", lambda: ["brew", "install", "gh"])
    monkeypatch.setattr(gh_mod, "install_from_release", lambda: True)

    def boom(cmd, **kwargs):
        raise gh_mod.CommandError("brew exploded")

    monkeypatch.setattr(gh_mod, "run_command", boom)
    assert install_gh() is True


def test_user_flag_never_touches_system_packages(monkeypatch):
    monkeypatch.setattr(gh_mod, "gh_version", lambda: None)
    monkeypatch.setattr(gh_mod, "install_command", lambda: pytest.fail("must not use a package manager"))
    monkeypatch.setattr(gh_mod, "install_from_release", lambda: True)
    assert install_gh(user=True) is True


def test_install_from_release_writes_the_binary(monkeypatch, tmp_path):
    user_bin = tmp_path / ".local" / "bin"
    monkeypatch.setattr(gh_mod, "USER_BIN", user_bin)
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gh_mod, "release_arch", lambda: "amd64")
    monkeypatch.setattr(gh_mod, "latest_gh_version", lambda: "2.62.0")

    downloaded = []

    def fake_download(url, destination, description=""):
        downloaded.append(url)
        with tarfile.open(destination, "w:gz") as bundle:
            payload = tmp_path / "gh"
            payload.write_bytes(b"gh-binary")
            bundle.add(payload, arcname="gh_2.62.0_linux_amd64/bin/gh")

    monkeypatch.setattr(gh_mod, "download_file", fake_download)

    assert gh_mod.install_from_release() is True
    assert downloaded == ["https://github.com/cli/cli/releases/download/v2.62.0/gh_2.62.0_linux_amd64.tar.gz"]
    assert (user_bin / "gh").read_bytes() == b"gh-binary"


def test_install_from_release_reports_an_unknown_arch(monkeypatch):
    monkeypatch.setattr(gh_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gh_mod, "release_arch", lambda: None)
    monkeypatch.setattr(gh_mod, "download_file", lambda *a, **kw: pytest.fail("must not download"))
    assert gh_mod.install_from_release() is False


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
    monkeypatch.setattr(gh_mod, "install_gh", lambda reinstall, user: True)
    monkeypatch.setattr(gh_mod, "gh_version", lambda: "2.62.0")
    monkeypatch.setattr(gh_mod, "check_gh_auth", lambda: pytest.fail("must not check auth"))

    result = runner.invoke(cli, ["setup", "gh", "--skip-auth-check"])
    assert result.exit_code == 0
    assert "Auth check skipped" in result.output
