import subprocess

from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import update as update_command


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _pypi(versions):
    return {"info": {"version": versions[-1]}, "releases": {version: [{"yanked": False}] for version in versions}}


def _stub_pypi(monkeypatch, versions):
    monkeypatch.setattr(update_command, "fetch_pypi", lambda package, timeout: _pypi(versions))


def test_update_command_is_registered():
    assert "update" in cli.commands


def test_parse_version_orders_releases_and_prereleases():
    assert update_command.parse_version("0.11.10") > update_command.parse_version("0.11.9")
    assert update_command.parse_version("0.12") > update_command.parse_version("0.11.3")
    assert update_command.parse_version("1.0rc1") < update_command.parse_version("1.0")
    assert update_command.parse_version("1.0a2") < update_command.parse_version("1.0b1")


def test_latest_version_skips_prereleases_and_yanked():
    data = {
        "info": {"version": "1.0"},
        "releases": {
            "1.0": [{"yanked": False}],
            "1.1": [{"yanked": True}],
            "1.2rc1": [{"yanked": False}],
            "0.9": [],
        },
    }
    assert update_command.latest_version(data, allow_pre=False) == "1.0"
    assert update_command.latest_version(data, allow_pre=True) == "1.2rc1"


def test_check_reports_available_update_without_installing(monkeypatch):
    _stub_pypi(monkeypatch, ["0.11.3", "0.12.0"])
    monkeypatch.setattr(update_command, "detect_install", lambda: {"mode": "pip", "location": "/venv"})
    monkeypatch.setattr(update_command, "__version__", "0.11.3")
    calls = []
    monkeypatch.setattr(update_command, "execute", lambda commands, dry_run: calls.append(commands))

    result = CliRunner().invoke(cli, ["update", "--check"])

    assert calls == []

    assert result.exit_code == 0
    assert "0.11.3 -> 0.12.0" in result.output


def test_update_is_a_noop_when_already_latest(monkeypatch):
    _stub_pypi(monkeypatch, ["0.11.3"])
    monkeypatch.setattr(update_command, "detect_install", lambda: {"mode": "pip", "location": "/venv"})
    monkeypatch.setattr(update_command, "__version__", "0.11.3")
    calls = []
    monkeypatch.setattr(update_command, "execute", lambda commands, dry_run: calls.append(commands))

    result = CliRunner().invoke(cli, ["update"])

    assert result.exit_code == 0
    assert calls == []
    assert "already the latest release" in result.output


def test_update_runs_pip_upgrade_when_outdated(monkeypatch):
    _stub_pypi(monkeypatch, ["0.11.3", "0.12.0"])
    monkeypatch.setattr(update_command, "detect_install", lambda: {"mode": "pip", "location": "/venv"})
    monkeypatch.setattr(update_command, "__version__", "0.11.3")
    monkeypatch.setattr(update_command, "build_commands", lambda mode, allow_pre: [["pip", "install", "-U", mode]])
    calls = []
    monkeypatch.setattr(update_command, "execute", lambda commands, dry_run: calls.append((commands, dry_run)))

    result = CliRunner().invoke(cli, ["update"])

    assert result.exit_code == 0
    assert calls == [([["pip", "install", "-U", "pip"]], False)]


def test_editable_update_pulls_and_reports_head(monkeypatch, tmp_path):
    monkeypatch.setattr(update_command, "fetch_pypi", lambda package, timeout: _pypi(["0.11.3"]))
    monkeypatch.setattr(update_command, "detect_install", lambda: {"mode": "editable", "location": tmp_path})
    monkeypatch.setattr(update_command.shutil, "which", lambda name: "/usr/bin/git")
    (tmp_path / "evo_cli").mkdir()
    (tmp_path / "evo_cli" / "VERSION").write_text("0.12.0\n", encoding="utf-8")
    responses = {
        ("status", "--porcelain"): _result(),
        ("fetch", "--prune"): _result(),
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): _result(stdout="origin/main\n"),
        ("rev-list", "--left-right", "--count", "HEAD...@{u}"): _result(stdout="0\t2\n"),
        ("pull", "--ff-only", "--prune"): _result(),
        ("log", "-1", "--oneline"): _result(stdout="abc1234 Add update command\n"),
        ("diff", "--name-only", "old", "new"): _result(stdout="README.md\n"),
    }
    calls = []

    def fake_git(path, *args):
        calls.append(args)
        if args == ("rev-parse", "HEAD"):
            return _result(stdout=("old" if ("pull", "--ff-only", "--prune") not in calls else "new"))
        return responses.get(args, _result())

    monkeypatch.setattr(update_command, "git", fake_git)
    result = CliRunner().invoke(cli, ["update"])

    assert result.exit_code == 0
    assert ("pull", "--ff-only", "--prune") in calls
    assert "abc1234 Add update command" in result.output
    assert "0.12.0" in result.output


def test_editable_update_refuses_dirty_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(update_command, "fetch_pypi", lambda package, timeout: _pypi(["0.11.3"]))
    monkeypatch.setattr(update_command, "detect_install", lambda: {"mode": "editable", "location": tmp_path})
    monkeypatch.setattr(update_command.shutil, "which", lambda name: "/usr/bin/git")
    responses = {
        ("status", "--porcelain"): _result(stdout=" M evo_cli/cli.py\n"),
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): _result(stdout="origin/main\n"),
        ("rev-list", "--left-right", "--count", "HEAD...@{u}"): _result(stdout="0\t1\n"),
    }
    calls = []

    def fake_git(path, *args):
        calls.append(args)
        return responses.get(args, _result())

    monkeypatch.setattr(update_command, "git", fake_git)
    result = CliRunner().invoke(cli, ["update"])

    assert result.exit_code == 1
    assert "uncommitted changes" in result.output
    assert ("pull", "--ff-only", "--prune") not in calls


def test_editable_update_skips_when_up_to_date(monkeypatch, tmp_path):
    monkeypatch.setattr(update_command, "fetch_pypi", lambda package, timeout: _pypi(["0.11.3"]))
    monkeypatch.setattr(update_command, "detect_install", lambda: {"mode": "editable", "location": tmp_path})
    monkeypatch.setattr(update_command.shutil, "which", lambda name: "/usr/bin/git")
    responses = {
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): _result(stdout="origin/main\n"),
        ("rev-list", "--left-right", "--count", "HEAD...@{u}"): _result(stdout="1\t0\n"),
    }
    calls = []

    def fake_git(path, *args):
        calls.append(args)
        return responses.get(args, _result())

    monkeypatch.setattr(update_command, "git", fake_git)
    result = CliRunner().invoke(cli, ["update"])

    assert result.exit_code == 0
    assert "Already up to date" in result.output
    assert "1 local commit(s) not pushed yet." in result.output


def test_dry_run_prints_commands_without_running_them(monkeypatch):
    _stub_pypi(monkeypatch, ["0.11.3", "0.12.0"])
    monkeypatch.setattr(update_command, "detect_install", lambda: {"mode": "pip", "location": "/venv"})
    monkeypatch.setattr(update_command, "__version__", "0.11.3")
    monkeypatch.setattr(update_command, "build_commands", lambda mode, allow_pre: [["pip", "install", "-U", "evo_cli"]])
    monkeypatch.setattr(update_command, "run_command", lambda cmd: (_ for _ in ()).throw(AssertionError("ran")))

    result = CliRunner().invoke(cli, ["update", "--dry-run"])

    assert result.exit_code == 0
    assert "pip install -U evo_cli" in result.output


def test_offline_check_fails_cleanly(monkeypatch):
    def boom(package, timeout):
        raise OSError("network unreachable")

    monkeypatch.setattr(update_command, "fetch_pypi", boom)
    monkeypatch.setattr(update_command, "detect_install", lambda: {"mode": "pip", "location": "/venv"})

    result = CliRunner().invoke(cli, ["update", "--check"])

    assert result.exit_code == 1
    assert "Cannot reach PyPI" in result.output
    assert "Cannot determine the latest version" in result.output
