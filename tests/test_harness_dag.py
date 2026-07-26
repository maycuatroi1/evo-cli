import textwrap

from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands.harness._dag import plan_repo_graph, plan_step_graph, seam_graph
from evo_cli.commands.harness._model import load_plan_file, load_seams


def _cluster(tmp_path, contracts, repos=("alpha", "beta", "gamma")):
    root = tmp_path / "cluster"
    (root / "plans" / "active").mkdir(parents=True)
    entries = "".join(f"- name: {name}\n  present: true\n" for name in repos)
    (root / "harness.yaml").write_text(
        f"name: test\nworkspace: {tmp_path.as_posix()}\nrepos:\n{entries}", encoding="utf-8"
    )
    (root / "contracts.yaml").write_text(textwrap.dedent(contracts), encoding="utf-8")
    for name in repos:
        (tmp_path / name).mkdir(exist_ok=True)
    return root / "harness.yaml"


def _plan(tmp_path, body):
    path = tmp_path / "plan.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return load_plan_file(path)


def test_seam_graph_reports_a_cycle_between_two_owners(tmp_path):
    manifest = _cluster(
        tmp_path,
        """
        seams:
          - name: one
            owner: alpha
            consumers: [beta]
            verify: "true"
          - name: two
            owner: beta
            consumers: [alpha]
            verify: "true"
        """,
    )
    graph = seam_graph(manifest)

    assert graph["acyclic"] is False
    assert graph["cycles"] == [["alpha", "beta"]]
    assert all(edge["inCycle"] for edge in graph["edges"])
    assert any("Merge order is undefined" in w["text"] for w in graph["warnings"])


def test_seam_graph_is_acyclic_when_ownership_flows_one_way(tmp_path):
    manifest = _cluster(
        tmp_path,
        """
        seams:
          - name: one
            owner: alpha
            consumers: [beta, gamma]
            verify: "true"
          - name: two
            owner: beta
            consumers: [gamma]
            verify: "true"
        """,
    )
    graph = seam_graph(manifest)

    assert graph["acyclic"] is True
    assert {n["id"]: n["rank"] for n in graph["nodes"]} == {"alpha": 0, "beta": 1, "gamma": 2}
    assert graph["warnings"] == []


def test_seam_without_verify_is_flagged(tmp_path):
    manifest = _cluster(tmp_path, "seams:\n  - name: one\n    owner: alpha\n    consumers: [beta]\n")
    assert any("declares no verify command" in w["text"] for w in seam_graph(manifest)["warnings"])


def test_consumer_outside_the_manifest_becomes_an_external_node(tmp_path):
    manifest = _cluster(
        tmp_path, 'seams:\n  - name: one\n    owner: alpha\n    consumers: [workstation]\n    verify: "true"\n'
    )
    node = next(n for n in seam_graph(manifest)["nodes"] if n["id"] == "workstation")
    assert node["kind"] == "external"


def test_declared_order_contradicting_depends_on_is_an_error(tmp_path):
    plan = _plan(
        tmp_path,
        """
        id: p
        repos:
          - repo: alpha
            order: 2
            depends_on: [beta]
            status: pending
          - repo: beta
            order: 5
            status: pending
        """,
    )
    problems = [w for w in plan_repo_graph(plan)["warnings"] if w["level"] == "error"]
    assert len(problems) == 1
    assert "contradicts the dependency" in problems[0]["text"]


def test_depends_on_naming_a_repo_the_plan_never_lists(tmp_path):
    plan = _plan(tmp_path, "id: p\nrepos:\n  - repo: alpha\n    order: 1\n    depends_on: [ghost]\n")
    assert any("which this plan never lists" in w["text"] for w in plan_repo_graph(plan)["warnings"])


def test_step_graph_reads_every_dependency_spelling(tmp_path):
    plan = _plan(
        tmp_path,
        """
        id: p
        steps:
          - order: 0
            what: first
            status: done
            blocks: [2]
          - order: 1
            what: second
            status: pending
          - order: 2
            what: third
            status: pending
            depends_on_step: [1]
          - order: 3
            what: fourth
            status: pending
            blocked_by: 2
        """,
    )
    graph = plan_step_graph(plan)
    pairs = {(e["source"], e["target"]) for e in graph["edges"]}

    assert pairs == {("step-0", "step-2"), ("step-1", "step-2"), ("step-2", "step-3")}
    assert graph["acyclic"] is True
    # Declared order wins outright: nothing is inferred, so nothing claims to be.
    assert not any(w["level"] == "info" for w in graph["warnings"])


def test_step_graph_says_so_when_it_infers_the_order(tmp_path):
    plan = _plan(
        tmp_path,
        """
        id: p
        repos:
          - repo: alpha
            order: 1
          - repo: beta
            order: 2
            depends_on: [alpha]
        steps:
          - id: 1
            repo: alpha
            what: a
            status: done
          - id: 2
            repo: beta
            what: b
            status: pending
        """,
    )
    graph = plan_step_graph(plan)

    assert {(e["source"], e["target"]) for e in graph["edges"]} == {("step-1", "step-2")}
    assert any("inferred" in w["text"] for w in graph["warnings"])


def test_bare_string_sections_survive_loading(tmp_path):
    plan = _plan(tmp_path, "id: p\ndecisions:\n  - we chose the boring option\n  - and wrote it down\n")
    assert [item["what"] for item in plan.items("decisions")] == [
        "we chose the boring option",
        "and wrote it down",
    ]


def test_seams_are_parsed_with_their_defaults(tmp_path):
    manifest = _cluster(
        tmp_path,
        """
        seams:
          - name: one
            kind: cli-surface
            owner: alpha
            consumers: [beta]
            blocking: false
            verify: "pytest"
        """,
    )
    seam = load_seams(manifest)[0]
    assert seam["blocking"] is False
    assert seam["kind"] == "cli-surface"
    assert seam["consumers"] == ["beta"]


def test_step_graph_titles_each_node_without_losing_the_full_what(tmp_path):
    long_what = "Rewrite the harness dashboard so every step node carries a short readable headline " * 3
    plan = _plan(
        tmp_path,
        f"""
        id: p
        steps:
          - id: 1
            title: Short authored title
            what: {long_what.strip()}
            status: pending
          - id: 2
            what: {long_what.strip()}
            status: pending
          - id: 3
            what: still short
            status: pending
        """,
    )
    metas = {node["meta"]["key"]: node["meta"] for node in plan_step_graph(plan)["nodes"]}

    assert metas["1"]["title"] == "Short authored title"
    assert metas["1"]["what"] == long_what.strip()
    assert metas["1"]["titled"] is True

    assert len(metas["2"]["title"]) <= 60
    assert metas["2"]["title"].endswith("...")
    assert metas["2"]["titled"] is False
    assert long_what.startswith(metas["2"]["title"][:-3])
    assert long_what[len(metas["2"]["title"]) - 3] == " "
    assert metas["2"]["what"] == long_what.strip()

    assert metas["3"]["title"] == "still short"
    assert "..." not in metas["3"]["title"]


def test_step_graph_warns_once_for_the_whole_plan_when_titles_are_missing(tmp_path):
    plan = _plan(
        tmp_path,
        """
        id: p
        steps:
          - id: 1
            what: first thing
            status: pending
          - id: 2
            title: "   "
            what: second thing
            status: pending
            depends_on_step: [1]
          - id: 3
            title: named at last
            what: third thing
            status: pending
            depends_on_step: [2]
        """,
    )
    graph = plan_step_graph(plan)
    missing = [w for w in graph["warnings"] if "declare no title" in w["text"]]

    assert len(missing) == 1
    assert missing[0]["level"] == "warn"
    assert "2 of 3 steps" in missing[0]["text"]
    assert "truncated slice of their what" in missing[0]["text"]
    assert not [w for w in graph["warnings"] if w["level"] == "error"]
    assert graph["acyclic"] is True


def test_step_graph_says_nothing_when_every_step_carries_a_title(tmp_path):
    plan = _plan(
        tmp_path,
        """
        id: p
        steps:
          - id: 1
            title: first
            what: first thing
            status: pending
          - id: 2
            title: second
            what: second thing
            status: pending
            depends_on_step: [1]
          - id: 3
            title: third
            what: third thing
            status: pending
            depends_on_step: [2]
        """,
    )
    graph = plan_step_graph(plan)

    assert not [w for w in graph["warnings"] if "declare no title" in w["text"]]
    assert graph["warnings"] == []
    assert graph["acyclic"] is True


def test_step_graph_survives_a_step_with_neither_title_nor_what(tmp_path):
    plan = _plan(tmp_path, "id: p\nsteps:\n  - id: 1\n    repo: alpha\n    status: pending\n")
    graph = plan_step_graph(plan)

    assert [node["meta"]["title"] for node in graph["nodes"]] == [""]
    assert graph["acyclic"] is True
    assert not [w for w in graph["warnings"] if w["level"] == "error"]


def _titled_cluster(tmp_path):
    root = tmp_path / "cluster"
    (root / "plans" / "active").mkdir(parents=True)
    (root / "harness.yaml").write_text(
        f"name: test\nworkspace: {tmp_path.as_posix()}\nrepos:\n- name: alpha\n  present: true\n",
        encoding="utf-8",
    )
    (root / "contracts.yaml").write_text("seams: []\n", encoding="utf-8")
    (tmp_path / "alpha").mkdir(exist_ok=True)
    long_what = "Rewrite the harness dashboard so every step node carries a short readable headline " * 3
    (root / "plans" / "active" / "p.yaml").write_text(
        textwrap.dedent(
            f"""
            id: p
            steps:
              - id: 1
                repo: alpha
                title: Short authored title
                what: {long_what.strip()}
                status: pending
              - id: 2
                repo: alpha
                what: {long_what.strip()}
                status: pending
            """
        ),
        encoding="utf-8",
    )
    return root


def test_graph_command_prints_the_title_rather_than_the_full_what(tmp_path):
    root = _titled_cluster(tmp_path)
    result = CliRunner().invoke(cli, ["harness", "graph", "p:steps", "--harness", str(root)])

    assert result.exit_code == 0
    assert "Short authored title" in result.output
    assert "Rewrite the harness dashboard so every" not in result.output


def test_show_command_keeps_the_full_what_when_a_step_declares_a_title(tmp_path):
    root = _titled_cluster(tmp_path)
    result = CliRunner().invoke(cli, ["harness", "show", "p", "--full", "--harness", str(root)])

    assert result.exit_code == 0
    assert "Short authored title" in result.output
    assert "what:" in result.output
