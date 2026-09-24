import json
import sys

import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import storage

# The command exits on Windows, and the uv test builds POSIX command lines.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="evo storage supports macOS and Linux only")


def test_storage_registered():
    assert "storage" in cli.commands


def test_storage_help_lists_subcommands():
    result = CliRunner().invoke(cli, ["storage", "--help"])
    assert result.exit_code == 0
    for sub in ("audit", "clean"):
        assert sub in result.output


def test_parse_size():
    assert storage.parse_size("4.915GB (56%)") == 4_915_000_000
    assert storage.parse_size("916.3MB") == 916_300_000
    assert storage.parse_size("187.1kB") == 187_100
    assert storage.parse_size("63B") == 63
    assert storage.parse_size("0B (0%)") == 0
    assert storage.parse_size("") is None
    assert storage.parse_size(None) is None


def test_parse_du():
    text = "32505856\t/Users/me/.cache/uv\n128\t/Users/me/a b\ndu: /x: Permission denied\n"
    assert storage.parse_du(text) == [(32505856 * 1024, "/Users/me/.cache/uv"), (128 * 1024, "/Users/me/a b")]
    assert storage.parse_du("") == []


def test_parse_docker_df_counts_images_and_build_cache_only():
    text = (
        '{"Reclaimable":"4.915GB (56%)","Size":"8.645GB","Type":"Images"}\n'
        '{"Reclaimable":"63B (0%)","Size":"187.1kB","Type":"Containers"}\n'
        '{"Reclaimable":"655.2MB (41%)","Size":"1.576GB","Type":"Local Volumes"}\n'
        '{"Reclaimable":"11.02GB","Size":"14.29GB","Type":"Build Cache"}\n'
        "not json\n"
    )
    assert storage.parse_docker_df(text) == 4_915_000_000 + 11_020_000_000
    assert storage.parse_docker_df("") == 0


def test_parse_brew_estimate():
    text = (
        "Would remove: /opt/homebrew/foo (1 file)\n==> This operation would free approximately 916.3MB of disk space."
    )
    assert storage.parse_brew_estimate(text) == 916_300_000
    assert storage.parse_brew_estimate("nothing to do") is None


def test_parse_conda_dry_run():
    text = json.dumps({"tarballs": {"total_size": 1000}, "packages": {"total_size": 250}, "index_cache": {}})
    assert storage.parse_conda_dry_run(text) == 1250
    assert storage.parse_conda_dry_run('{"tarballs": {}, "packages": null}') == 0
    assert storage.parse_conda_dry_run("oops") is None
    assert storage.parse_conda_dry_run(None) is None


def test_parse_swapusage():
    text = "total = 24576.00M  used = 23737.25M  free = 838.75M  (encrypted)"
    total, used = storage.parse_swapusage(text)
    assert total == 24576 * 1024**2
    assert used == int(23737.25 * 1024**2)
    assert storage.parse_swapusage("") is None


def test_parse_meminfo_swap():
    text = "MemTotal:  16000000 kB\nSwapTotal:  2097148 kB\nSwapFree:   1048576 kB\n"
    assert storage.parse_meminfo_swap(text) == (2097148 * 1024, (2097148 - 1048576) * 1024)
    assert storage.parse_meminfo_swap("MemTotal: 1 kB") is None


def test_children_in_use():
    procs = (
        "/Users/me/.cache/uv/archive-v0/abc123/bin/python /Users/me/.cache/uv/archive-v0/abc123/bin/tool\n"
        "node /Users/me/.npm/_npx/9f8e/node_modules/.bin/mcp\n"
    )
    assert storage.children_in_use("/Users/me/.cache/uv/archive-v0", procs) == {"abc123"}
    assert storage.children_in_use("/Users/me/.npm/_npx/", procs) == {"9f8e"}
    assert storage.children_in_use("/Users/me/.gradle", procs) == set()


def test_is_safe_root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "HOME", tmp_path / "home")
    assert storage.is_safe_root("/") is False
    assert storage.is_safe_root(tmp_path / "home") is False
    assert storage.is_safe_root(tmp_path) is False
    assert storage.is_safe_root("relative/cache") is False
    assert storage.is_safe_root(tmp_path / "home" / ".cache" / "pip") is True


def test_clear_dir_keeps_named_children(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "HOME", tmp_path / "home")
    root = tmp_path / "cache"
    (root / "keep").mkdir(parents=True)
    (root / "drop").mkdir()
    (root / "drop" / "f.bin").write_text("x")
    (root / "file.txt").write_text("x")
    failures = []
    storage.clear_dir(root, failures, keep={"keep"})
    assert sorted(p.name for p in root.iterdir()) == ["keep"]
    assert failures == []


def test_clear_dir_refuses_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "project").mkdir(parents=True)
    monkeypatch.setattr(storage, "HOME", home)
    failures = []
    storage.clear_dir(home, failures)
    assert (home / "project").exists()
    assert failures and "refusing" in failures[0]


def _fake_uv_cache(tmp_path):
    root = tmp_path / "uv"
    (root / "archive-v0" / "busy" / "bin").mkdir(parents=True)
    (root / "archive-v0" / "idle").mkdir()
    (root / "environments-v2" / "tool-abc").mkdir(parents=True)
    (root / "sdists-v9" / "pkg").mkdir(parents=True)
    (root / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55")
    return root


@posix_only
def test_uv_clean_keeps_envs_a_process_runs_from(tmp_path, monkeypatch):
    root = _fake_uv_cache(tmp_path)
    monkeypatch.setattr(storage, "HOME", tmp_path / "home")
    monkeypatch.setattr(storage, "_uv_dir", lambda: root)
    procs = f"{root}/archive-v0/busy/bin/python {root}/archive-v0/busy/bin/cisco-pt-mcp\n"
    failures = []
    notes = storage._uv_clean(procs, failures)
    assert (root / "archive-v0" / "busy").exists()
    assert not (root / "archive-v0" / "idle").exists()
    assert not (root / "environments-v2").exists()
    assert not (root / "sdists-v9").exists()
    assert (root / "CACHEDIR.TAG").exists()
    assert failures == []
    assert notes == ["kept 1 uv env(s) a running process uses"]


def test_uv_clean_refuses_folder_without_cachedir_tag(tmp_path, monkeypatch):
    root = tmp_path / "not-uv"
    (root / "important").mkdir(parents=True)
    monkeypatch.setattr(storage, "_uv_dir", lambda: root)
    failures = []
    storage._uv_clean("", failures)
    assert (root / "important").exists()
    assert "CACHEDIR.TAG" in failures[0]


def test_gradle_blocked_while_daemon_runs():
    procs = "/usr/bin/java -Xmx2g org.gradle.launcher.daemon.bootstrap.GradleDaemon 8.10.2\n"
    assert "Gradle daemon" in storage._gradle_blocker(procs)
    assert storage._gradle_blocker("/bin/zsh\n") is None


def test_swap_hint():
    gb = 1024**3
    assert "restart" in storage.swap_hint({"total": 24 * gb, "used": 23 * gb})
    assert storage.swap_hint({"total": 24 * gb, "used": 2 * gb}) is None
    assert storage.swap_hint({"total": 512 * 1024**2, "used": 500 * 1024**2}) is None
    assert storage.swap_hint(None) is None


def _fake_target(name, size=1000, calls=None, **kwargs):
    def clean(procs, failures):
        calls.append(name)
        return ["a note"]

    return storage.Target(name, f"fake {name}", measure=lambda: size, clean=clean, **kwargs)


def test_survey_target_statuses():
    other = "Plan9"
    assert storage.survey_target(_fake_target("a", calls=[], system=other), "")["status"] == "skip"
    missing = storage.survey_target(_fake_target("b", calls=[], detect=lambda: False), "")
    assert (missing["status"], missing["reason"]) == ("skip", "not found")
    blocked = storage.survey_target(_fake_target("c", calls=[], blocker=lambda procs: "busy"), "")
    assert (blocked["status"], blocked["size"]) == ("blocked", 1000)
    assert storage.survey_target(_fake_target("d", calls=[]), "")["status"] == "ready"


@posix_only
def test_clean_requires_targets():
    result = CliRunner().invoke(cli, ["storage", "clean"])
    assert result.exit_code == 2
    assert "--all" in result.output


def _patch_targets(monkeypatch, calls):
    monkeypatch.setitem(storage.TARGETS, "uv", _fake_target("uv", calls=calls))
    monkeypatch.setitem(storage.TARGETS, "npm", _fake_target("npm", calls=calls, blocker=lambda procs: "busy"))
    monkeypatch.setattr(storage, "process_table", lambda: "")
    monkeypatch.setattr(storage, "swap_state", lambda: None)


@posix_only
def test_clean_dry_run_changes_nothing(monkeypatch):
    calls = []
    _patch_targets(monkeypatch, calls)
    result = CliRunner().invoke(cli, ["storage", "clean", "uv", "npm", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert calls == []
    assert "Dry run" in result.output


@posix_only
def test_clean_runs_ready_targets_only(monkeypatch):
    calls = []
    _patch_targets(monkeypatch, calls)
    result = CliRunner().invoke(cli, ["storage", "clean", "uv", "npm", "-y"])
    assert result.exit_code == 0, result.output
    assert calls == ["uv"]
    assert "freed" in result.output
    assert "a note" in result.output


@posix_only
def test_clean_aborts_when_not_confirmed(monkeypatch):
    calls = []
    _patch_targets(monkeypatch, calls)
    result = CliRunner().invoke(cli, ["storage", "clean", "uv"], input="n\n")
    assert result.exit_code == 0, result.output
    assert calls == []
    assert "Aborted" in result.output


@posix_only
def test_audit_json(monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "TARGETS", {"uv": _fake_target("uv", size=4096, calls=calls)})
    monkeypatch.setattr(storage, "process_table", lambda: "")
    monkeypatch.setattr(storage, "swap_state", lambda: {"total": 10, "used": 1})
    result = CliRunner().invoke(cli, ["storage", "audit", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["targets"][0]["name"] == "uv"
    assert data["targets"][0]["size"] == 4096
    assert data["disk"]["free"] > 0
    assert calls == []
