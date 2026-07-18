import textwrap

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
