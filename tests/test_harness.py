import json
import subprocess

from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import harness as harness_command


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _write_harness(root, workspace):
    root.mkdir()
    (root / "harness.yaml").write_text(
        f"name: test-cluster\nworkspace: {workspace.as_posix()}\nrepos:\n"
        "- name: alpha\n  present: true\n"
        "- name: beta\n  present: true\n",
        encoding="utf-8",
    )


def test_harness_command_is_registered():
    assert "harness" in cli.commands
    assert "pull" in cli.commands["harness"].commands


def test_pull_dry_run_honors_local_present_override(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    (workspace / "alpha").mkdir()
    (workspace / "beta").mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    (root / "harness.local.yaml").write_text("present:\n  beta: false\n", encoding="utf-8")
    calls = []

    def fake_git(path, *args):
        calls.append((path, args))
        return _result()

    monkeypatch.setattr(harness_command, "_git", fake_git)
    result = CliRunner().invoke(cli, ["harness", "pull", "--harness", str(root), "--dry-run"])

    assert result.exit_code == 0
    assert "alpha  would pull" in result.output
    assert "beta   skipped (present: false)" in result.output
    assert calls == [(workspace / "alpha", ("status", "--porcelain"))]


def test_pull_refuses_dirty_repo(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    (workspace / "alpha").mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    (root / "harness.local.yaml").write_text("present:\n  beta: false\n", encoding="utf-8")
    monkeypatch.setattr(harness_command, "_git", lambda path, *args: _result(stdout=" M local.txt\n"))

    result = CliRunner().invoke(cli, ["harness", "pull", "--harness", str(root)])

    assert result.exit_code == 1
    assert "alpha  skipped (uncommitted changes)" in result.output


def test_pull_uses_fast_forward_and_prune(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    (workspace / "alpha").mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    calls = []

    def fake_git(path, *args):
        calls.append((path, args))
        if args[0] == "log":
            return _result(stdout="abc123 Latest change\n")
        return _result()

    monkeypatch.setattr(harness_command, "_git", fake_git)
    result = CliRunner().invoke(cli, ["harness", "pull", "--harness", str(root), "--repo", "alpha"])

    assert result.exit_code == 0
    assert "abc123 Latest change" in result.output
    assert calls == [
        (workspace / "alpha", ("status", "--porcelain")),
        (workspace / "alpha", ("pull", "--ff-only", "--prune")),
        (workspace / "alpha", ("log", "-1", "--oneline")),
    ]


def test_pull_discovers_manifest_from_environment(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    (workspace / "alpha").mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    monkeypatch.setenv("EVO_HARNESS", str(root))
    monkeypatch.setattr(harness_command, "_git", lambda path, *args: _result())

    result = CliRunner().invoke(cli, ["harness", "pull", "--repo", "alpha", "--dry-run"])

    assert result.exit_code == 0
    assert "Harness: test-cluster" in result.output


def test_pull_discovers_registered_member_repo(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    member = workspace / "alpha"
    member.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"clusters": [{"root": str(root)}]}), encoding="utf-8")
    monkeypatch.chdir(member)
    monkeypatch.setenv("EVO_HARNESS_REGISTRY", str(registry))
    monkeypatch.setattr(harness_command, "_git", lambda path, *args: _result())

    result = CliRunner().invoke(cli, ["harness", "pull", "--repo", "alpha", "--dry-run"])

    assert result.exit_code == 0
    assert f"Manifest: {root / 'harness.yaml'}" in result.output


def test_pull_rejects_unknown_repo(tmp_path):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)

    result = CliRunner().invoke(cli, ["harness", "pull", "--harness", str(root), "--repo", "missing"])

    assert result.exit_code == 1
    assert "Unknown repo name(s): missing" in result.output
