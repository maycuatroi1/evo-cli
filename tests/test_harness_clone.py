import importlib
import subprocess

from click.testing import CliRunner

from evo_cli.cli import cli

# harness/__init__ binds the command object to the name `clone`, shadowing the submodule,
# so the module has to be fetched by path rather than imported by name.
clone_command = importlib.import_module("evo_cli.commands.harness.clone")


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _write_harness(root, workspace, extra=""):
    """alpha declares a path outside <workspace>/<name>; beta relies on the fallback."""
    root.mkdir()
    (root / "harness.yaml").write_text(
        f"name: test-cluster\nworkspace: {workspace.as_posix()}\nrepos:\n"
        f"- name: alpha\n  path: {(workspace / 'nested' / 'alpha').as_posix()}\n"
        "  origin: https://github.com/acme/alpha.git\n"
        "- name: beta\n  origin: git@github.com:acme/beta.git\n" + extra,
        encoding="utf-8",
    )


def _checkout(path, origin="https://github.com/acme/alpha.git"):
    (path / ".git").mkdir(parents=True)
    return origin


def test_clone_command_is_registered():
    assert "clone" in cli.commands["harness"].commands


def test_clone_dry_run_targets_the_declared_path_not_the_workspace_fallback(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    calls = []
    monkeypatch.setattr(clone_command, "git", lambda path, *args, **kwargs: calls.append((path, args)) or _result())

    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--dry-run"])

    assert result.exit_code == 0
    assert f"would clone https://github.com/acme/alpha.git into {(workspace / 'nested' / 'alpha').resolve()}" in (
        result.output
    )
    assert f"would clone git@github.com:acme/beta.git into {(workspace / 'beta').resolve()}" in result.output
    assert calls == []


def test_clone_runs_git_clone_from_the_parent_directory(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    calls = []

    def fake_git(path, *args, **kwargs):
        calls.append((path, args))
        return _result()

    monkeypatch.setattr(clone_command, "git", fake_git)
    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--repo", "beta", "--depth", "1"])

    target = (workspace / "beta").resolve()
    assert result.exit_code == 0
    assert calls == [(target.parent, ("clone", "--depth", "1", "git@github.com:acme/beta.git", str(target)))]
    assert f"cloned into {target}" in result.output


def test_clone_creates_the_parent_of_a_nested_declared_path(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    monkeypatch.setattr(clone_command, "git", lambda path, *args, **kwargs: _result())

    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--repo", "alpha"])

    assert result.exit_code == 0
    assert (workspace / "nested").is_dir()


def test_clone_leaves_an_existing_checkout_alone(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    _checkout(workspace / "nested" / "alpha")
    # The ssh form of the same remote must not read as a different repository.
    monkeypatch.setattr(
        clone_command, "git", lambda path, *args, **kwargs: _result(stdout="git@github.com:acme/alpha.git\n")
    )

    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--repo", "alpha"])

    assert result.exit_code == 0
    assert "alpha  skipped (already cloned)" in result.output


def test_clone_reports_a_checkout_whose_origin_is_a_different_repo(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    _checkout(workspace / "nested" / "alpha")
    monkeypatch.setattr(
        clone_command, "git", lambda path, *args, **kwargs: _result(stdout="https://github.com/other/fork.git\n")
    )

    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--repo", "alpha"])

    assert result.exit_code == 0
    assert "skipped (already cloned, but origin is https://github.com/other/fork.git)" in result.output


def test_clone_reports_a_checkout_sitting_somewhere_other_than_the_declared_path(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    _checkout(workspace / "alpha")
    monkeypatch.setattr(clone_command, "git", lambda path, *args, **kwargs: _result())

    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--repo", "alpha"])

    assert result.exit_code == 0
    assert f"already cloned at {(workspace / 'alpha').resolve()}" in result.output
    assert f"manifest says {(workspace / 'nested' / 'alpha').resolve()}" in result.output


def test_clone_refuses_to_write_into_an_occupied_path(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    (workspace / "beta").mkdir()
    (workspace / "beta" / "notes.txt").write_text("mine", encoding="utf-8")
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    monkeypatch.setattr(clone_command, "git", lambda path, *args, **kwargs: _result())

    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--repo", "beta"])

    assert result.exit_code == 1
    assert "failed (path is taken and is not an empty directory" in result.output


def test_clone_reports_the_git_failure_and_exits_nonzero(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)
    monkeypatch.setattr(
        clone_command,
        "git",
        lambda path, *args, **kwargs: _result(returncode=128, stderr="fatal: repository not found\n"),
    )

    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--repo", "beta"])

    assert result.exit_code == 1
    assert "failed (fatal: repository not found)" in result.output


def test_clone_skips_present_false_unless_asked(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace, extra="- name: gamma\n  present: false\n  origin: https://x.test/gamma.git\n")
    monkeypatch.setattr(clone_command, "git", lambda path, *args, **kwargs: _result())

    skipped = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--dry-run"])
    forced = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--dry-run", "--all"])
    named = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--dry-run", "--repo", "gamma"])

    assert "gamma  skipped (present: false)" in skipped.output
    assert f"would clone https://x.test/gamma.git into {(workspace / 'gamma').resolve()}" in forced.output
    assert f"would clone https://x.test/gamma.git into {(workspace / 'gamma').resolve()}" in named.output


def test_clone_fails_a_repo_with_no_origin(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    root.mkdir()
    (root / "harness.yaml").write_text(
        f"name: test-cluster\nworkspace: {workspace.as_posix()}\nrepos:\n- name: alpha\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(clone_command, "git", lambda path, *args, **kwargs: _result())

    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root)])

    assert result.exit_code == 1
    assert "failed (no origin declared in the manifest)" in result.output


def test_clone_moves_onto_the_declared_branch_when_it_exists(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    root.mkdir()
    (root / "harness.yaml").write_text(
        f"name: test-cluster\nworkspace: {workspace.as_posix()}\nrepos:\n"
        "- name: beta\n  origin: git@github.com:acme/beta.git\n  branch: feat/new-intent-flow\n",
        encoding="utf-8",
    )
    calls = []

    def fake_git(path, *args, **kwargs):
        calls.append(args)
        if args[0] == "rev-parse":
            return _result(stdout="main\n")
        return _result()

    monkeypatch.setattr(clone_command, "git", fake_git)
    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root)])

    assert result.exit_code == 0
    assert ("checkout", "feat/new-intent-flow") in calls
    assert "(on feat/new-intent-flow)" in result.output


def test_clone_keeps_a_clone_whose_declared_branch_is_not_pushed_yet(tmp_path, monkeypatch):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    root.mkdir()
    (root / "harness.yaml").write_text(
        f"name: test-cluster\nworkspace: {workspace.as_posix()}\nrepos:\n"
        "- name: beta\n  origin: git@github.com:acme/beta.git\n  branch: feat/unpushed\n",
        encoding="utf-8",
    )

    def fake_git(path, *args, **kwargs):
        if args[0] == "rev-parse":
            return _result(stdout="main\n")
        if args[0] == "checkout":
            return _result(returncode=1, stderr="error: pathspec did not match\n")
        return _result()

    monkeypatch.setattr(clone_command, "git", fake_git)
    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root)])

    assert result.exit_code == 0
    assert "cloned into" in result.output
    assert "branch 'feat/unpushed' is not on origin, left on main" in result.output


def test_clone_rejects_unknown_repo(tmp_path):
    workspace = tmp_path / "repos"
    workspace.mkdir()
    root = tmp_path / "cluster"
    _write_harness(root, workspace)

    result = CliRunner().invoke(cli, ["harness", "clone", "--harness", str(root), "--repo", "nope"])

    assert result.exit_code == 1
    assert "Unknown repo name(s): nope" in result.output


def test_normalize_remote_matches_ssh_and_https_forms():
    assert clone_command.normalize_remote("git@github.com:acme/repo.git") == "github.com/acme/repo"
    assert clone_command.normalize_remote("https://github.com/acme/repo.git") == "github.com/acme/repo"
    assert clone_command.normalize_remote("ssh://git@github.com/acme/repo/") == "github.com/acme/repo"
    assert clone_command.normalize_remote("https://github.com/acme/other") != "github.com/acme/repo"
