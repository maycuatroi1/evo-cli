"""`evo harness show --step`, the open questions `show` lists, and the depends_on column of the step board."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands.harness._dag import step_depends
from evo_cli.commands.harness._render import key_runs

MIRROR_FIXTURE = Path(__file__).parent / "fixtures" / "hub" / "mirror-demo.yaml"

LONG_WHAT = (
    "A long folded sentence that runs on well past any terminal width, so the step view has to print it whole "
    "rather than cut it at the end of the line, with tiếng Việt có dấu đầy đủ at the very end."
)
VERIFY = (
    "V=$(git -C ~/github/alpha show origin/main:alpha/__init__.py | sed -n 's/x/\\1/p') "
    '&& test "$(curl -fsS https://example.test/health | jq -r .version)" = "$V"'
)
PLAN = textwrap.dedent(
    """\
    id: demo
    goal: A plan to read one step at a time.
    steps:
      - id: 1
        title: First
        repo: alpha
        what: Start.
        status: done
        done_at: '2026-10-01'
        evidence: 'alpha@abc1234: 12 passed'
      - id: 2
        title: Second
        repo: alpha
        what: Two.
        status: done
      - id: 3
        title: Third
        repo: beta
        what: Three.
        depends_on: [1, 2]
        status: done
      - id: 4
        title: Wrap up
        repo: beta
        what: >
          A long folded sentence that runs on well past any terminal width, so the step view has to print it
          whole rather than cut it at the end of the line, with tiếng Việt có dấu đầy đủ at the very end.
        depends_on: [1, 2, 3]
        verify: >
          VERIFY_HERE
        status: pending
        blocking: true
        note: |
          First line of the note.
            Indented second line [bold]not markup[/bold].
        acceptance:
          - The first criterion.
          - |
            A criterion over
            two lines.
        why: Because the board needs it.
        links: {pr: 12, commits: [abc1234]}
      - id: 5
        title: Optional polish
        repo: beta
        what: Polish.
        depends_on: [4]
        status: pending
        blocking: false
    open_questions:
      - issue: Should the board show blocking?
        status: open
        blocking: false
        note: Only when it is the exception.
      - issue: Which width is enough?
        status: answered
    """
).replace("VERIFY_HERE", VERIFY)


def _harness(tmp_path: Path, body: str = PLAN, name: str = "demo", hub: bool = False) -> Path:
    root = tmp_path / "cluster"
    (root / "plans" / "active").mkdir(parents=True)
    project = "hub:\n  project: demo\n" if hub else ""
    (root / "harness.yaml").write_text(f"name: test-cluster\nrepos:\n  - name: alpha\n{project}", encoding="utf-8")
    (root / "plans" / "active" / f"{name}.yaml").write_text(body, encoding="utf-8")
    return root


def _run(*args: str, columns: int = 80, env: dict | None = None):
    return CliRunner().invoke(cli, ["harness", *args], env={"COLUMNS": str(columns), **(env or {})})


def _lines(output: str) -> list[str]:
    """Output lines with rich's colour codes taken out (CI makes rich colour even under CliRunner)."""
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output).splitlines()


def _flat(output: str) -> str:
    return " ".join(re.sub(r"[─-╿]", " ", "\n".join(_lines(output))).split())


# show --step


def test_show_step_prints_every_field_of_one_step_without_clipping_or_wrapping(tmp_path):
    root = _harness(tmp_path)

    result = _run("show", "demo", "--step", "4", "--harness", str(root), columns=60)

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert lines[0].startswith("demo step 4")
    head = [line.split(":", 1)[0] for line in lines[1:9]]
    assert head == ["id", "title", "status", "repo", "depends_on", "blocking", "done_at", "why"]
    assert any(line.split() == ["depends_on:", "1,", "2,", "3"] for line in lines)
    assert any(line.split() == ["blocking:", "true"] for line in lines)
    assert any(line.split() == ["done_at:", "-"] for line in lines)
    assert any(line.split() == ["why:", "Because", "the", "board", "needs", "it."] for line in lines)

    body = [line for line in lines if line.endswith(":") and not line.startswith(" ")]
    assert body == ["what:", "verify:", "note:", "acceptance:", "evidence:", "links:"]
    # Longer than the 60 columns rich was given, yet each sits on one line, whole.
    assert "    " + VERIFY in lines
    assert "    " + LONG_WHAT in lines
    # A literal block keeps its line breaks and indentation, and brackets are not read as markup.
    note = lines.index("note:")
    assert lines[note + 1 : note + 3] == [
        "    First line of the note.",
        "      Indented second line [bold]not markup[/bold].",
    ]
    acceptance = lines.index("acceptance:")
    assert lines[acceptance + 1 : acceptance + 4] == [
        "    - The first criterion.",
        "    - A criterion over",
        "      two lines.",
    ]
    assert lines[lines.index("evidence:") + 1] == "    -"
    # A key outside the schema is printed as well, a mapping as the YAML it is.
    assert lines[lines.index("links:") + 1 :] == ["    pr: 12", "    commits:", "    - abc1234"]


def test_show_step_prints_the_evidence_and_done_at_a_finished_step_carries(tmp_path):
    root = _harness(tmp_path)

    result = _run("show", "demo", "--step", "1", "--harness", str(root))

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert any(line.split() == ["done_at:", "2026-10-01"] for line in lines)
    assert any(line.split() == ["depends_on:", "-"] for line in lines)
    assert lines[lines.index("evidence:") + 1] == "    alpha@abc1234: 12 passed"


def test_show_step_with_an_unknown_id_names_the_ids_the_plan_holds(tmp_path):
    root = _harness(tmp_path)

    result = _run("show", "demo", "--step", "99", "--harness", str(root), columns=2000)

    assert result.exit_code == 1
    assert "No step '99' in demo. Available: 1, 2, 3, 4, 5" in _flat(result.output)


def test_show_step_refuses_to_mix_with_section(tmp_path):
    root = _harness(tmp_path)

    result = _run("show", "demo", "--step", "1", "--section", "steps", "--harness", str(root), columns=2000)

    assert result.exit_code == 2
    assert "--step prints one step; leave out --section." in _flat(result.output)


def test_show_step_reads_the_copy_of_a_hub_plan_without_calling_the_hub(tmp_path):
    # The copy evo-agents wrote, with its hub key and digest, in a harness whose plans live on the hub.
    root = _harness(tmp_path, body=MIRROR_FIXTURE.read_text(encoding="utf-8"), name="mirror-demo", hub=True)
    empty = tmp_path / "no-tools"
    empty.mkdir()

    # Nothing on PATH, so a call out to evo-agents could not succeed.
    result = _run("show", "mirror-demo", "--step", "2", "--harness", str(root), env={"PATH": str(empty)})

    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    assert lines[0].startswith("mirror-demo step 2")
    assert any(line.split() == ["title:", "Ghi", "chú", "nhiều", "dòng"] for line in lines)
    assert any(line.split() == ["depends_on:", "1"] for line in lines)
    note = lines.index("note:")
    assert lines[note + 1 : note + 3] == [
        "    First line of the note.",
        "    Second line: \"quoted\" and 'single'.",
    ]


# show lists the open questions


def test_show_lists_each_open_question_with_its_status_and_blocking_once(tmp_path):
    root = _harness(tmp_path)

    result = _run("show", "demo", "--harness", str(root), columns=2000)

    assert result.exit_code == 0, result.output
    lines = [line.strip() for line in _lines(result.output)]
    start = lines.index("Open questions (2)")
    assert lines[start + 1 :] == [
        "0 Should the board show blocking?  open",
        "blocking: false",
        "note: Only when it is the exception.",
        "1 Which width is enough?  answered",
        "blocking: -",
    ]


def test_show_has_no_open_questions_heading_for_a_plan_without_any(tmp_path):
    root = _harness(
        tmp_path, body="id: bare\ngoal: Nothing open.\nsteps:\n  - id: 1\n    what: Only step.\n", name="bare"
    )

    result = _run("show", "bare", "--harness", str(root), columns=2000)

    assert result.exit_code == 0, result.output
    assert "Open questions" not in result.output


# The step board


def test_step_board_shows_what_each_step_depends_on_and_marks_a_non_blocking_step(tmp_path):
    root = _harness(tmp_path)

    result = _run("step", "demo", "--no-input", "--harness", str(root), columns=200)

    assert result.exit_code == 0, result.output
    rows = {line.split()[0]: line.split() for line in _lines(result.output)[2:] if line.strip()}
    assert _lines(result.output)[1].split() == ["#", "REPO", "STATUS", "DEPENDS", "ON", "STEP"]
    assert rows["1"] == ["1", "alpha", "done", "-", "First"]
    assert rows["3"] == ["3", "beta", "done", "1,", "2", "Third"]
    assert rows["4"] == ["4", "beta", "pending", "1-3", "Wrap", "up"]
    assert rows["5"] == ["5", "beta", "pending", "4", "Optional", "polish", "(non-blocking)"]


def test_key_runs_collapses_only_runs_of_three_or_more_consecutive_integers():
    assert key_runs([]) == "-"
    assert key_runs(["11"]) == "11"
    assert key_runs(["1", "2"]) == "1, 2"
    assert key_runs(["1", "2", "3", "5"]) == "1-3, 5"
    assert key_runs(["5", "6", "8", "9", "10"]) == "5, 6, 8-10"
    assert key_runs(["3", "2", "1"]) == "3, 2, 1"
    assert key_runs(["07", "8", "9"]) == "07, 8, 9"
    assert key_runs(["a", "1", "2", "3", "b"]) == "a, 1-3, b"


def test_step_depends_reads_every_forward_spelling_once_in_written_order():
    assert step_depends({}) == []
    assert step_depends({"depends_on": 3}) == ["3"]
    assert step_depends({"depends_on": [2, 1], "depends_on_step": [1, 4], "blocked_by": "x", "blocks": [9]}) == [
        "2",
        "1",
        "4",
        "x",
    ]
