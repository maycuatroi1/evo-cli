import importlib
import json
import socket
import struct
import subprocess
import threading
from urllib.request import Request, urlopen

from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands.harness._server import Handler, Server, build_server

# harness/__init__ binds the command object to the name `pull`, shadowing the submodule,
# so the module has to be fetched by path rather than imported by name.
pull_command = importlib.import_module("evo_cli.commands.harness.pull")


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

    def fake_git(path, *args, **kwargs):
        calls.append((path, args))
        return _result()

    monkeypatch.setattr(pull_command, "git", fake_git)
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
    monkeypatch.setattr(pull_command, "git", lambda path, *args, **kwargs: _result(stdout=" M local.txt\n"))

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

    def fake_git(path, *args, **kwargs):
        calls.append((path, args))
        if args[0] == "log":
            return _result(stdout="abc123 Latest change\n")
        return _result()

    monkeypatch.setattr(pull_command, "git", fake_git)
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
    monkeypatch.setattr(pull_command, "git", lambda path, *args, **kwargs: _result())

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
    monkeypatch.setattr(pull_command, "git", lambda path, *args, **kwargs: _result())

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


def test_serve_complete_plan_moves_file_and_is_idempotent(tmp_path):
    root = tmp_path / "cluster"
    active = root / "plans" / "active"
    active.mkdir(parents=True)
    manifest = root / "harness.yaml"
    manifest.write_text("name: test-cluster\nrepos: []\n", encoding="utf-8")
    plan = active / "finish-me.yaml"
    plan.write_text("id: finish-me\ngoal: Finish this plan\nsteps: []\n", encoding="utf-8")

    server = build_server(manifest, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/plans/finish-me/complete"
    request = Request(url, method="POST", headers={"X-Evo-Harness-Write": "1"})

    try:
        with urlopen(request) as response:
            payload = json.load(response)
        with urlopen(request) as response:
            repeated = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    completed = root / "plans" / "completed" / plan.name
    assert not plan.exists()
    assert completed.read_text(encoding="utf-8") == "id: finish-me\ngoal: Finish this plan\nsteps: []\n"
    assert payload["plan"]["area"] == "completed"
    assert repeated["plan"]["area"] == "completed"


def test_serve_stays_quiet_when_a_client_drops_a_keep_alive_connection(tmp_path, capsys):
    root = tmp_path / "cluster"
    root.mkdir()
    manifest = root / "harness.yaml"
    manifest.write_text("name: test-cluster\nrepos: []\n", encoding="utf-8")

    served = threading.Event()

    class ProbeHandler(Handler):
        manifest_path = manifest

    class ProbeServer(Server):
        def shutdown_request(self, request):
            super().shutdown_request(request)
            served.set()

    server = ProbeServer(("127.0.0.1", 0), ProbeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    client = socket.create_connection(("127.0.0.1", server.server_port))
    try:
        client.sendall(b"GET /api/state HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert client.recv(4096)
        # RST instead of FIN: the server is left reading a socket that is already gone, the way a
        # closed browser tab leaves it.
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    finally:
        client.close()

    try:
        assert served.wait(5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert "Traceback" not in capsys.readouterr().err
