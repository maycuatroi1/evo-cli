"""`evo harness` in a harness whose plans live on the evo-agents hub (harness.yaml `hub.project`).

Writes go through `evo-agents hub plan patch|complete` and `evo-agents hub plan export` (seam hub-cli-v1). The
tests put a fake evo-agents on PATH (tests/fake_evo_agents.py), record every command line it gets and check
each one against the hub's CLI contract: EVO_HUB_CONTRACT names the output of `evo-agents hub contract print`,
else the snapshot in tests/fixtures/hub is used. The copies of the plans (seam hub-plan-mirror-v1) are checked
against a copy the real evo-agents 0.2.0 wrote. No test reaches a hub or touches a real harness.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands.harness import _hub
from evo_cli.commands.harness._mirror import EDITED, INTACT, classify, plan_digest
from evo_cli.commands.harness._mutate import update_item
from evo_cli.commands.harness._paths import yaml_load
from evo_cli.commands.harness._server import build_server

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures" / "hub"
FAKE = HERE / "fake_evo_agents.py"
SNAPSHOT = FIXTURES / "contract-0.2.0.json"
MIRROR_FIXTURE = FIXTURES / "mirror-demo.yaml"
MIRROR_FIXTURE_DIGEST = "sha256:19e5a9b75daecc3c5110371629aafae10d0de2c2087a72e3670570402ad18aad"
PROJECT = "demo"
POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="the fake evo-agents on PATH is a POSIX launcher script")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def flat(text: str) -> str:
    """Output with rich's panel borders and line wrapping taken out, for substring checks."""
    return " ".join(re.sub(r"[─-╿]", " ", text).split())


# The hub's CLI contract


def contract() -> dict:
    given = os.environ.get("EVO_HUB_CONTRACT")
    return json.loads(Path(given or SNAPSHOT).read_text(encoding="utf-8"))


def _check_value(where: str, spec: dict, value: str) -> None:
    if spec["value"] == "integer":
        assert re.fullmatch(r"-?\d+", value), f"{where} takes an integer, got {value!r}"
    if spec.get("choices"):
        assert value in spec["choices"], f"{where} takes one of {spec['choices']}, got {value!r}"


def check_call(argv: list[str], commands: dict) -> str:
    """The contract command `argv` runs; an AssertionError naming what in it the contract does not allow."""
    name = next(
        (" ".join(argv[:n]) for n in range(len(argv), 0, -1) if " ".join(argv[:n]) in commands),
        None,
    )
    assert name, f"no command of the hub contract matches {argv}"
    spec = commands[name]
    rest = argv[len(name.split()) :]
    options = {flag: option for option in spec["options"] for flag in option["flags"]}
    seen: dict[str, int] = {}
    positionals = []
    index = 0
    while index < len(rest):
        token = rest[index]
        index += 1
        if not token.startswith("--"):
            positionals.append(token)
            continue
        flag, equals, inline = token.partition("=")
        assert flag in options, f"`{name}` has no option {flag} in the contract"
        option = options[flag]
        seen[option["flags"][0]] = seen.get(option["flags"][0], 0) + 1
        assert option["repeatable"] or seen[option["flags"][0]] == 1, f"`{name}` takes {flag} once"
        if option["value"] is None:
            assert not equals, f"`{name}` {flag} takes no value"
            continue
        if not equals:
            assert index < len(rest), f"`{name}` {flag} needs a value"
            inline = rest[index]
            index += 1
        _check_value(f"`{name}` {flag}", option, inline)
    specs = spec["positionals"]
    assert len(positionals) <= len(specs), f"`{name}` takes {len(specs)} positionals, got {positionals}"
    for positional, value in zip(specs, positionals):
        _check_value(f"`{name}` {positional['metavar']}", positional, value)
    for positional in specs[len(positionals) :]:
        assert not positional["required"], f"`{name}` needs {positional['metavar']}"
    for option in spec["options"]:
        assert not option["required"] or option["flags"][0] in seen, f"`{name}` needs {option['flags'][0]}"
    assert "--json" in seen, f"evo-cli reads the answer of `{name}`, so it must ask for --json"
    assert spec["json"] is not None, f"`{name}` declares no --json output"
    return name


def commands_run(calls: list[list[str]]) -> list[str]:
    """The contract command of every recorded call, each checked against the contract."""
    commands = contract()["commands"]
    return [check_call(argv, commands) for argv in calls]


def option(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, token in enumerate(argv[:-1]) if token == flag]


def test_hub_contract_declares_every_command_and_key_evo_cli_reads():
    commands = contract()["commands"]
    assert contract()["version"] == 1
    for name, keys in _hub.READS.items():
        assert name in commands, f"the hub contract has no `{name}`"
        declared = commands[name]["json"]["keys"]
        assert set(keys) <= set(declared), f"`{name} --json` does not declare {set(keys) - set(declared)}"
    login = {flag for option in commands["hub login"]["options"] for flag in option["flags"]}
    assert "--url" in login  # the sign-in hint evo-cli prints passes --url


@pytest.mark.skipif(not os.environ.get("EVO_HUB_CONTRACT"), reason="EVO_HUB_CONTRACT names no live contract")
def test_hub_contract_snapshot_matches_the_live_contract():
    live = contract()["commands"]
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["commands"]
    for name, spec in snapshot.items():
        assert live.get(name) == spec, (
            f"`{name}` changed in the live hub contract: check evo_cli/commands/harness/_hub.py against it, then "
            f"refresh {SNAPSHOT.name} from `evo-agents hub contract print`"
        )


# A fake hub behind a fake evo-agents


def plan_body(plan_id: str = "demo-plan") -> dict:
    return {
        "id": plan_id,
        "goal": "Ship the demo",
        "repos": [{"repo": "alpha", "status": "pending"}],
        "steps": [
            {"id": 1, "what": "First step", "status": "done", "done_at": "2026-10-01"},
            {"id": 2, "what": "Second step", "status": "pending", "note": "An earlier note.\n"},
            {"id": 3, "what": "Third step", "status": "pending"},
        ],
        "tech_debt": [{"issue": "A known debt", "status": "open"}],
    }


class FakeHub:
    def __init__(self, state_path: Path, root: Path, env: dict):
        self.state_path = state_path
        self.root = root
        self.env = env

    @property
    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def change(self, **values) -> None:
        state = self.state
        state.update(values)
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    @property
    def calls(self) -> list[list[str]]:
        return self.state["calls"]

    def setup(self, *bodies: dict, revision: int = 3) -> None:
        """A harness with hub.project whose plans are `bodies` on the fake hub, with their copies exported."""
        self.root.mkdir(exist_ok=True)
        (self.root / "harness.yaml").write_text(
            f"name: hub-cluster\nrepos:\n  - name: alpha\nhub:\n  project: {PROJECT}\n", encoding="utf-8"
        )
        plans = {body["id"]: {"area": "active", "revision": revision, "body": body} for body in bodies}
        self.state_path.write_text(json.dumps({"project": PROJECT, "plans": plans, "calls": []}), encoding="utf-8")
        exported = subprocess.run(
            [sys.executable, str(FAKE), "hub", "plan", "export", str(self.root), "--project", PROJECT, "--json"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert exported.returncode == 0, exported.stderr
        self.change(calls=[])

    def copy(self, plan_id: str = "demo-plan", area: str = "active") -> Path:
        return self.root / "plans" / area / f"{plan_id}.yaml"

    def hub_step(self, key: int, plan_id: str = "demo-plan") -> dict:
        steps = self.state["plans"][plan_id]["body"]["steps"]
        return next(step for step in steps if step["id"] == key)

    def copy_step(self, key: int, plan_id: str = "demo-plan") -> dict:
        data = yaml_load(self.copy(plan_id).read_bytes())
        return next(step for step in data["steps"] if step["id"] == key)


@pytest.fixture
def hub(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "evo-agents"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    state_path = tmp_path / "hub-state.json"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_EVO_HUB", str(state_path))
    return FakeHub(state_path, tmp_path / "cluster", dict(os.environ))


def run(*args: str):
    # Wide enough that rich never wraps a long path in the middle of a word.
    return CliRunner().invoke(cli, ["harness", *args], env={"COLUMNS": "2000"})


# Writes in a hub harness


@POSIX_ONLY
def test_step_in_hub_harness_patches_with_if_revision_then_exports_the_copy(hub):
    hub.setup(plan_body())

    result = run(
        "step", "--harness", str(hub.root), "demo-plan", "2", "done", "--note", "Second note.", "--evidence", "a@1: ok"
    )

    assert result.exit_code == 0, result.output
    assert commands_run(hub.calls) == ["hub plan show", "hub plan patch", "hub plan export"]
    patch = hub.calls[1]
    assert option(patch, "--project") == [PROJECT]
    assert option(patch, "--section") == ["steps"]
    assert option(patch, "--step") == ["2"]
    assert option(patch, "--if-revision") == ["3"]
    assert set(option(patch, "--set")) == {
        "status=done",
        f"done_at={today()}",
        "note=An earlier note.\nSecond note.",
        "evidence=a@1: ok",
    }
    assert hub.calls[2][3:] == [str(hub.root), "--project", PROJECT, "--json"]
    step = hub.copy_step(2)
    assert step["status"] == "done"
    assert step["note"] == "An earlier note.\nSecond note."
    assert step["evidence"] == "a@1: ok"
    found = classify(hub.root, PROJECT, hub.copy())
    assert (found.state, found.revision) == (INTACT, 4)
    assert "hub demo, revision 4" in flat(result.output)


@POSIX_ONLY
def test_step_in_hub_harness_retries_a_409_without_losing_the_other_writers_update(hub):
    hub.setup(plan_body())
    hub.change(
        others=[
            {
                "on": "patch",
                "section": "steps",
                "step": "2",
                "updates": {"note": "An earlier note.\nThe other writer's note.", "evidence": "theirs"},
            },
            {"on": "patch", "section": "steps", "step": "3", "updates": {"status": "in_progress"}},
        ]
    )

    result = run(
        "step", "--harness", str(hub.root), "demo-plan", "2", "done", "--note", "My note.", "--evidence", "mine"
    )

    assert result.exit_code == 0, result.output
    assert commands_run(hub.calls) == [
        "hub plan show",
        "hub plan patch",
        "hub plan show",
        "hub plan patch",
        "hub plan export",
    ]
    assert [option(hub.calls[i], "--if-revision") for i in (1, 3)] == [["3"], ["5"]]
    for step in (hub.hub_step(2), hub.copy_step(2)):
        assert step["status"] == "done"
        assert step["note"] == "An earlier note.\nThe other writer's note.\nMy note."
        assert step["evidence"] == "theirs\nmine"
    assert hub.hub_step(3)["status"] == "in_progress"
    assert hub.copy_step(3)["status"] == "in_progress"
    assert hub.state["plans"]["demo-plan"]["revision"] == 6


@POSIX_ONLY
def test_step_in_hub_harness_does_not_overwrite_a_status_another_writer_set(hub):
    hub.setup(plan_body())
    hub.change(others=[{"on": "patch", "section": "steps", "step": "2", "updates": {"status": "blocked"}}])
    before = hub.copy().read_bytes()

    result = run("step", "--harness", str(hub.root), "demo-plan", "2", "done")

    assert result.exit_code == 1
    message = flat(result.output)
    assert "status of step 2 in plan demo-plan changed on the hub (revision 4)" in message
    assert "so it was not overwritten" in message
    assert commands_run(hub.calls) == ["hub plan show", "hub plan patch", "hub plan show"]
    assert hub.hub_step(2)["status"] == "blocked"
    assert hub.copy().read_bytes() == before


@POSIX_ONLY
def test_step_in_hub_harness_sends_nothing_when_the_hub_already_holds_it(hub):
    hub.setup(plan_body())
    args = ("step", "--harness", str(hub.root), "demo-plan", "2", "in_progress", "--note", "Once.")

    assert run(*args).exit_code == 0
    hub.change(calls=[])
    again = run(*args)

    assert again.exit_code == 0, again.output
    assert commands_run(hub.calls) == ["hub plan show", "hub plan export"]
    assert hub.hub_step(2)["note"] == "An earlier note.\nOnce."


@POSIX_ONLY
def test_debt_in_hub_harness_patches_by_index(hub):
    hub.setup(plan_body())

    result = run("debt", "--harness", str(hub.root), "demo-plan", "0", "fixed", "--note", "Paid back.")

    assert result.exit_code == 0, result.output
    patch = hub.calls[1]
    assert commands_run(hub.calls) == ["hub plan show", "hub plan patch", "hub plan export"]
    assert (option(patch, "--section"), option(patch, "--index"), option(patch, "--step")) == (["tech_debt"], ["0"], [])
    assert set(option(patch, "--set")) == {"status=fixed", f"fixed_at={today()}", "note=Paid back."}
    assert yaml_load(hub.copy().read_bytes())["tech_debt"][0]["status"] == "fixed"


@POSIX_ONLY
def test_debt_in_hub_harness_refuses_when_the_item_at_that_index_is_another_one(hub):
    hub.setup(plan_body())
    state = hub.state
    state["plans"]["demo-plan"]["body"]["tech_debt"].insert(0, {"issue": "A newer debt", "status": "open"})
    state["plans"]["demo-plan"]["revision"] = 4
    hub.change(plans=state["plans"])
    before = hub.copy().read_bytes()

    result = run("debt", "--harness", str(hub.root), "demo-plan", "0", "fixed")

    assert result.exit_code == 1
    assert "tech_debt[0] of plan demo-plan on the hub (revision 4) is not the item" in flat(result.output)
    assert commands_run(hub.calls) == ["hub plan show"]
    assert hub.copy().read_bytes() == before


@POSIX_ONLY
def test_step_in_hub_harness_fails_loudly_when_the_hub_cannot_be_reached(hub):
    hub.setup(plan_body())
    hub.change(unreachable=["patch"])
    before = hub.copy().read_bytes()

    result = run("step", "--harness", str(hub.root), "demo-plan", "2", "done", "--evidence", "x")

    assert result.exit_code == 1
    message = flat(result.output)
    assert "`evo-agents hub plan patch` failed: cannot reach https://hub.test" in message
    assert "plans/active/demo-plan.yaml was not changed." in message
    assert commands_run(hub.calls) == ["hub plan show", "hub plan patch", "hub plan show"]
    assert hub.copy().read_bytes() == before
    assert hub.hub_step(2)["status"] == "pending"


@POSIX_ONLY
def test_step_in_hub_harness_fails_loudly_when_no_command_reaches_the_hub(hub):
    hub.setup(plan_body())
    hub.change(unreachable=["show", "patch", "complete", "export"])
    before = hub.copy().read_bytes()

    result = run("step", "--harness", str(hub.root), "demo-plan", "2", "done")

    assert result.exit_code == 1
    assert "cannot reach https://hub.test" in flat(result.output)
    assert commands_run(hub.calls) == ["hub plan show"]
    assert hub.copy().read_bytes() == before


@POSIX_ONLY
def test_step_in_hub_harness_without_evo_agents_says_how_to_install_and_sign_in(hub, tmp_path, monkeypatch):
    hub.setup(plan_body())
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    before = hub.copy().read_bytes()

    result = run("step", "--harness", str(hub.root), "demo-plan", "2", "done")

    assert result.exit_code == 1
    message = flat(result.output)
    assert "`evo-agents` is not on PATH" in message
    assert "uv tool install 'evo-ak[graphify]>=0.2.0'" in message
    assert "evo-agents hub login --url https://agents.omelet.tech" in message
    assert hub.calls == []
    assert hub.copy().read_bytes() == before


@POSIX_ONLY
def test_step_in_hub_harness_when_not_signed_in_says_how_to_sign_in(hub):
    hub.setup(plan_body())
    hub.change(signed_in=False)
    before = hub.copy().read_bytes()

    result = run("step", "--harness", str(hub.root), "demo-plan", "2", "done")

    assert result.exit_code == 1
    message = flat(result.output)
    assert "not signed in to a hub" in message
    assert "Sign in with `evo-agents hub login --url https://agents.omelet.tech`" in message
    assert commands_run(hub.calls) == ["hub plan show"]
    assert hub.copy().read_bytes() == before


@POSIX_ONLY
def test_step_in_hub_harness_refuses_before_the_hub_when_a_mirror_copy_was_edited(hub):
    hub.setup(plan_body(), plan_body("other-plan"))
    edited = hub.copy("other-plan")
    edited.write_text(edited.read_text(encoding="utf-8").replace("Ship the demo", "Ship it by hand"), encoding="utf-8")
    before = hub.copy().read_bytes()

    result = run("step", "--harness", str(hub.root), "demo-plan", "2", "done")

    assert result.exit_code == 1
    message = flat(result.output)
    assert "plans/active/other-plan.yaml was edited outside the hub (digest mismatch)" in message
    assert "Nothing was sent to the hub, and no file was changed." in message
    assert hub.calls == []
    assert hub.copy().read_bytes() == before


@POSIX_ONLY
def test_step_in_hub_harness_refuses_a_plan_file_that_is_not_a_mirror_copy(hub):
    hub.setup(plan_body())
    draft = hub.copy()
    data = yaml_load(draft.read_bytes())
    del data["hub"]
    draft.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    result = run("step", "--harness", str(hub.root), "demo-plan", "2", "done")

    assert result.exit_code == 1
    assert "plans/active/demo-plan.yaml has no hub key, so it is not a copy of a hub plan" in flat(result.output)
    assert hub.calls == []


@POSIX_ONLY
def test_step_in_hub_harness_keeps_the_hub_change_and_says_so_when_export_fails(hub):
    hub.setup(plan_body())
    hub.change(fail_export=True)
    before = hub.copy().read_bytes()

    result = run("step", "--harness", str(hub.root), "demo-plan", "2", "done")

    assert result.exit_code == 1
    message = flat(result.output)
    assert "The hub holds the change (plan demo-plan, revision 4), but writing its copy failed" in message
    assert f"Run `evo-agents hub plan export {hub.root}` to write it." in message
    assert hub.hub_step(2)["status"] == "done"
    assert hub.copy().read_bytes() == before


def _post_complete(manifest: Path, plan_id: str):
    server = build_server(manifest, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/plans/{plan_id}/complete"
    request = Request(url, method="POST", headers={"X-Evo-Harness-Write": "1"})
    try:
        with urlopen(request) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@POSIX_ONLY
def test_serve_complete_in_hub_harness_goes_through_the_hub_and_retries_a_409(hub):
    body = plan_body()
    for step in body["steps"]:
        step.update(status="done", done_at="2026-10-02")
    hub.setup(body)
    hub.change(others=[{"on": "complete", "section": "steps", "step": "1", "updates": {"evidence": "theirs"}}])

    status, payload = _post_complete(hub.root / "harness.yaml", "demo-plan")

    assert status == 200, payload
    assert payload["plan"]["area"] == "completed"
    assert commands_run(hub.calls) == [
        "hub plan show",
        "hub plan complete",
        "hub plan show",
        "hub plan complete",
        "hub plan export",
    ]
    assert [option(hub.calls[i], "--if-revision") for i in (1, 3)] == [["3"], ["4"]]
    assert not hub.copy().exists()
    completed = hub.copy(area="completed")
    assert classify(hub.root, PROJECT, completed).state == INTACT
    assert yaml_load(completed.read_bytes())["steps"][0]["evidence"] == "theirs"


@POSIX_ONLY
def test_serve_complete_in_hub_harness_reports_what_the_hub_refused(hub):
    hub.setup(plan_body())
    before = hub.copy().read_bytes()

    status, payload = _post_complete(hub.root / "harness.yaml", "demo-plan")

    assert status == 409
    assert "has steps that are not done: 2, 3" in payload["error"]
    assert "plans/active/demo-plan.yaml was not changed." in payload["error"]
    assert commands_run(hub.calls) == ["hub plan show", "hub plan complete", "hub plan show"]
    assert hub.copy().read_bytes() == before


# A harness without hub.project: the file is edited in place, evo-agents is never run


LOCAL_PLAN = """\
id: local-plan
goal: Local only
steps:
  - id: 1
    what: First
    status: pending
    note: >
      An existing folded note
      that spans lines.

  - id: 2
    what: Second
    status: pending
"""


def _local_harness(root: Path) -> Path:
    (root / "plans" / "active").mkdir(parents=True)
    (root / "harness.yaml").write_text("name: local-cluster\nrepos: []\n", encoding="utf-8")
    plan = root / "plans" / "active" / "local-plan.yaml"
    plan.write_text(LOCAL_PLAN, encoding="utf-8")
    return plan


@POSIX_ONLY
def test_step_without_hub_appends_note_and_evidence_in_the_file_and_never_runs_evo_agents(hub, tmp_path):
    hub.state_path.write_text(json.dumps({"project": PROJECT, "plans": {}, "calls": []}), encoding="utf-8")
    root = tmp_path / "local"
    plan = _local_harness(root)

    result = run("step", "--harness", str(root), "local-plan", "1", "done", "--note", "New note.", "--evidence", "e1")

    assert result.exit_code == 0, result.output
    step = yaml_load(plan.read_bytes())["steps"][0]
    assert step["status"] == "done"
    assert step["note"] == "An existing folded note that spans lines.\nNew note."
    assert step["evidence"] == "e1"
    assert yaml_load(plan.read_bytes())["steps"][1] == {"id": 2, "what": "Second", "status": "pending"}
    assert hub.calls == []


def test_step_note_appends_and_is_not_repeated_when_the_same_command_runs_twice(tmp_path):
    root = tmp_path / "local"
    plan = _local_harness(root)
    args = ("step", "--harness", str(root), "local-plan", "2", "in_progress", "--note", "Same note.")

    assert run(*args).exit_code == 0
    first = plan.read_bytes()
    again = run(*args)

    assert again.exit_code == 0, again.output
    assert plan.read_bytes() == first
    assert yaml_load(first)["steps"][1]["note"] == "Same note."
    assert run("step", "--harness", str(root), "local-plan", "2", "in_progress", "--note", "Next.").exit_code == 0
    assert yaml_load(plan.read_bytes())["steps"][1]["note"] == "Same note.\nNext."


def test_question_note_appends_the_answer_and_keeps_the_question(tmp_path):
    root = tmp_path / "local"
    _local_harness(root)
    plan = root / "plans" / "active" / "q-plan.yaml"
    plan.write_text(
        "id: q-plan\nopen_questions:\n  - issue: Why?\n    status: open\n    note: Asked by A.\n", encoding="utf-8"
    )

    result = run("question", "--harness", str(root), "q-plan", "0", "answered", "--note", "Because.")

    assert result.exit_code == 0, result.output
    assert yaml_load(plan.read_bytes())["open_questions"][0]["note"] == "Asked by A.\nBecause."


def test_local_update_keeps_text_with_line_breaks_quotes_and_tabs_exact(tmp_path):
    root = tmp_path / "local"
    plan = _local_harness(root)
    text = "Line one: 'single' and \"double\".\n\tTabbed line # not a comment\nUnicode: tiếng Việt."

    result = update_item(plan, "steps", 1, {"status": "done"}, append={"evidence": text})

    assert result["hub"] is None
    step = yaml_load(plan.read_bytes())["steps"][1]
    assert step["evidence"] == text.strip()
    assert step["status"] == "done"
    assert yaml_load(plan.read_bytes())["steps"][0]["note"] == "An existing folded note that spans lines.\n"


# The copies of hub plans (seam hub-plan-mirror-v1)


def test_mirror_digest_matches_the_copy_evo_agents_wrote():
    data = yaml_load(MIRROR_FIXTURE.read_bytes())

    assert data["hub"]["digest"] == MIRROR_FIXTURE_DIGEST
    assert plan_digest(data) == MIRROR_FIXTURE_DIGEST
    unquoted = MIRROR_FIXTURE.read_text(encoding="utf-8").replace("created_at: '2026-10-04'", "created_at: 2026-10-04")
    assert "created_at: 2026-10-04\n" in unquoted
    assert plan_digest(yaml_load(unquoted)) == MIRROR_FIXTURE_DIGEST  # a YAML date counts as its ISO text
    edited = MIRROR_FIXTURE.read_text(encoding="utf-8").replace("Read the plan.", "Read the plan twice.")
    assert plan_digest(yaml_load(edited)) != MIRROR_FIXTURE_DIGEST


def _mirror_harness(root: Path) -> Path:
    (root / "plans" / "active").mkdir(parents=True)
    (root / "harness.yaml").write_text(
        f"name: mirror-cluster\nrepos:\n  - name: alpha\nhub:\n  project: {PROJECT}\n", encoding="utf-8"
    )
    copy = root / "plans" / "active" / "mirror-demo.yaml"
    shutil.copyfile(MIRROR_FIXTURE, copy)
    return copy


def test_check_passes_on_mirror_copies_evo_agents_wrote(tmp_path):
    root = tmp_path / "cluster"
    _mirror_harness(root)

    result = run("check", "--harness", str(root))

    assert result.exit_code == 0, result.output
    assert "every digest matches" in flat(result.output)


def test_check_reports_a_mirror_copy_whose_digest_does_not_match(tmp_path):
    root = tmp_path / "cluster"
    copy = _mirror_harness(root)
    copy.write_text(copy.read_text(encoding="utf-8").replace("Read the plan.", "Read the plan twice."), "utf-8")

    result = run("check", "--harness", str(root))

    assert result.exit_code == 1
    assert classify(root, PROJECT, copy).state == EDITED
    assert (
        "plans/active/mirror-demo.yaml was edited outside the hub (digest mismatch). Push it with "
        "`evo-agents hub plan put plans/active/mirror-demo.yaml --if-revision 7` or restore it with "
        "`evo-agents hub plan export .`"
    ) in flat(result.output)


def test_check_reports_mirror_copies_of_another_project_and_plan_files_without_a_hub_key(tmp_path):
    root = tmp_path / "cluster"
    copy = _mirror_harness(root)
    foreign = root / "plans" / "active" / "foreign.yaml"
    foreign.write_text(copy.read_text(encoding="utf-8").replace("project: demo", "project: elsewhere"), "utf-8")
    (root / "plans" / "active" / "draft.yaml").write_text("id: draft\ngoal: Not pushed yet\n", encoding="utf-8")

    result = run("check", "--harness", str(root))

    assert result.exit_code == 1
    message = flat(result.output)
    assert "plans/active/draft.yaml has no hub key, so it is not a copy of a hub plan" in message
    assert "plans/active/foreign.yaml is a copy of a plan of hub project 'elsewhere'" in message
    assert "plans/active/mirror-demo.yaml" not in message


def test_check_says_nothing_about_copies_in_a_harness_without_hub_project(tmp_path):
    root = tmp_path / "local"
    _local_harness(root)

    result = run("check", "--harness", str(root))

    assert result.exit_code == 0, result.output
    assert "plan copies" not in result.output


def _real_evo_agents() -> str | None:
    found = shutil.which("evo-agents")
    if found is None:
        return None
    probe = subprocess.run([found, "harness", "validate", "--help"], capture_output=True, text=True, check=False)
    return found if probe.returncode == 0 else None


@pytest.mark.skipif(_real_evo_agents() is None, reason="evo-agents is not installed")
def test_mirror_check_agrees_with_evo_agents_harness_validate(tmp_path):
    root = tmp_path / "cluster"
    copy = _mirror_harness(root)
    verdicts = []
    for text in (copy.read_text(encoding="utf-8"), copy.read_text(encoding="utf-8").replace("Read the", "Read a")):
        copy.write_text(text, encoding="utf-8")
        validated = subprocess.run(
            [_real_evo_agents(), "harness", "validate", "--json", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        report = json.loads(validated.stdout)
        verdicts.append((report["ok"], run("check", "--harness", str(root)).exit_code == 0))
    assert verdicts == [(True, True), (False, False)]
