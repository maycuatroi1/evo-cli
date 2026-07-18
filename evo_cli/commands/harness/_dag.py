from __future__ import annotations

from pathlib import Path

from evo_cli.commands.harness._model import Plan, cluster, load_seams, tone_of

CLUSTER_REPO = "__cluster__"


def _strongly_connected(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """Tarjan. Returns components of size > 1 plus any self-loop, i.e. every real cycle."""
    adjacency: dict[str, list[str]] = {n: [] for n in nodes}
    for source, target in edges:
        if source in adjacency and target in adjacency:
            adjacency[source].append(target)

    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    counter = [0]
    found: list[list[str]] = []

    def visit(root: str) -> None:
        work = [(root, 0)]
        while work:
            node, child = work[-1]
            if child == 0:
                index_of[node] = low[node] = counter[0]
                counter[0] += 1
                stack.append(node)
                on_stack[node] = True
            recursed = False
            neighbours = adjacency[node]
            while child < len(neighbours):
                nxt = neighbours[child]
                child += 1
                if nxt not in index_of:
                    work[-1] = (node, child)
                    work.append((nxt, 0))
                    recursed = True
                    break
                if on_stack.get(nxt):
                    low[node] = min(low[node], index_of[nxt])
            if recursed:
                continue
            work[-1] = (node, child)
            if low[node] == index_of[node]:
                component = []
                while True:
                    top = stack.pop()
                    on_stack[top] = False
                    component.append(top)
                    if top == node:
                        break
                if len(component) > 1 or (node, node) in edges:
                    found.append(sorted(component))
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])

    for node in nodes:
        if node not in index_of:
            visit(node)
    return found


def _rank(nodes: list[str], edges: list[tuple[str, str]], cyclic: set[str]) -> dict[str, int]:
    """Longest-path layering over the acyclic part. Nodes inside a cycle share their entry rank."""
    live = [(s, t) for s, t in edges if s not in cyclic or t not in cyclic]
    incoming: dict[str, int] = {n: 0 for n in nodes}
    outgoing: dict[str, list[str]] = {n: [] for n in nodes}
    for source, target in live:
        if source in outgoing and target in incoming:
            outgoing[source].append(target)
            incoming[target] += 1
    rank = {n: 0 for n in nodes}
    queue = [n for n in nodes if incoming[n] == 0]
    seen = 0
    while queue:
        node = queue.pop(0)
        seen += 1
        for target in outgoing[node]:
            rank[target] = max(rank[target], rank[node] + 1)
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    return rank


def _finish(nodes: list[dict], edges: list[dict], warnings: list[dict]) -> dict:
    ids = [n["id"] for n in nodes]
    pairs = [(e["source"], e["target"]) for e in edges]
    cycles = _strongly_connected(ids, pairs)
    cyclic = {n for component in cycles for n in component}
    ranks = _rank(ids, pairs, cyclic)

    for node in nodes:
        node["rank"] = ranks.get(node["id"], 0)
        node["inCycle"] = node["id"] in cyclic
    for edge in edges:
        both = edge["source"] in cyclic and edge["target"] in cyclic
        edge["inCycle"] = both and any(
            edge["source"] in component and edge["target"] in component for component in cycles
        )

    if cycles:
        for component in cycles:
            warnings.append(
                {
                    "level": "warn",
                    "text": f"Cycle between {' -> '.join(component)} -> {component[0]}. Merge order is undefined here.",
                }
            )
    return {
        "nodes": nodes,
        "edges": edges,
        "cycles": cycles,
        "acyclic": not cycles,
        "warnings": warnings,
        "depth": (max(ranks.values()) + 1) if ranks else 0,
    }


def seam_graph(manifest_path: Path) -> dict:
    info = cluster(manifest_path)
    seams = load_seams(manifest_path)
    known = {r["name"]: r for r in info["repos"]}

    order: list[str] = []
    for seam in seams:
        for name in [seam["owner"], *seam["consumers"]]:
            if name and name not in order:
                order.append(name)
    for name in known:
        if name not in order:
            order.append(name)

    owned: dict[str, int] = {}
    consumed: dict[str, int] = {}
    for seam in seams:
        owned[seam["owner"]] = owned.get(seam["owner"], 0) + 1
        for name in seam["consumers"]:
            consumed[name] = consumed.get(name, 0) + 1

    nodes = []
    for name in order:
        repo = known.get(name)
        nodes.append(
            {
                "id": name,
                "label": name,
                "kind": "repo" if repo else "external",
                "tone": "idle" if repo and repo["present"] else ("bad" if repo else "warn"),
                "meta": {
                    "role": repo["role"] if repo else "outside the manifest",
                    "path": repo["path"] if repo else "",
                    "present": bool(repo and repo["present"]),
                    "note": repo["note"] if repo else "",
                    "owns": owned.get(name, 0),
                    "consumes": consumed.get(name, 0),
                },
            }
        )

    edges = []
    for seam in seams:
        for consumer in seam["consumers"]:
            if consumer == seam["owner"]:
                continue
            edges.append(
                {
                    "id": f"{seam['name']}:{seam['owner']}->{consumer}",
                    "source": seam["owner"],
                    "target": consumer,
                    "label": seam["name"],
                    "kind": seam["kind"],
                    # Blocking is a stronger contract, not a fault, so it reads as a solid line
                    # rather than a red one. Red stays reserved for something actually wrong.
                    "tone": "idle",
                    "dashed": not seam["blocking"],
                    "meta": {
                        "seam": seam["name"],
                        "blocking": seam["blocking"],
                        "verify": seam["verify"],
                        "source": seam["source"],
                        "mirrors": seam["mirrors"],
                    },
                }
            )

    warnings = []
    for seam in seams:
        if seam["owner"] and seam["owner"] not in known:
            warnings.append(
                {
                    "level": "warn",
                    "text": f"Seam {seam['name']!r} is owned by {seam['owner']!r}, absent from harness.yaml.",
                }
            )
        if not seam["verify"]:
            warnings.append({"level": "warn", "text": f"Seam {seam['name']!r} declares no verify command."})
    return _finish(nodes, edges, warnings)


def plan_repo_graph(plan: Plan) -> dict:
    entries = plan.items("repos")
    names = [str(e.get("repo") or f"repo-{i}") for i, e in enumerate(entries)]

    nodes = []
    for index, entry in enumerate(entries):
        status = str(entry.get("status") or "")
        nodes.append(
            {
                "id": names[index],
                "label": names[index],
                "kind": "repo",
                "tone": tone_of("repos", status),
                "meta": {
                    "index": index,
                    "status": status,
                    "order": entry.get("order"),
                    "branch": str(entry.get("branch") or ""),
                    "note": " ".join(str(entry.get("note") or "").split()),
                },
            }
        )

    edges = []
    warnings = []
    for index, entry in enumerate(entries):
        depends = entry.get("depends_on") or []
        if isinstance(depends, str):
            depends = [depends]
        for dependency in depends:
            dependency = str(dependency)
            if dependency not in names:
                warnings.append(
                    {
                        "level": "warn",
                        "text": f"{names[index]} depends_on {dependency!r}, which this plan never lists.",
                    }
                )
                continue
            edges.append(
                {
                    "id": f"dep:{dependency}->{names[index]}",
                    "source": dependency,
                    "target": names[index],
                    "label": "merges before",
                    "kind": "depends_on",
                    "tone": "ok"
                    if tone_of("repos", str(entries[names.index(dependency)].get("status") or "")) == "ok"
                    else "idle",
                    "dashed": False,
                    "meta": {},
                }
            )

    declared = {name: entries[i].get("order") for i, name in enumerate(names)}
    for edge in edges:
        before, after = declared.get(edge["source"]), declared.get(edge["target"])
        if isinstance(before, int) and isinstance(after, int) and before >= after:
            warnings.append(
                {
                    "level": "error",
                    "text": f"{edge['target']} depends on {edge['source']} but declares order {after} <= {before}. "
                    "The declared order contradicts the dependency.",
                }
            )
    return _finish(nodes, edges, warnings)


FORWARD_KEYS = ("depends_on", "depends_on_step", "blocked_by")
BACKWARD_KEYS = ("blocks",)


def step_key(entry: dict, index: int) -> str:
    """Plans key their steps by `id` or by `order`, never both. Dependencies quote that key."""
    for name in ("id", "order"):
        if entry.get(name) is not None:
            return str(entry[name])
    return str(index)


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def plan_step_graph(plan: Plan) -> dict:
    steps = plan.items("steps")
    repo_entries = plan.items("repos")
    repo_names = [str(e.get("repo") or "") for e in repo_entries]
    repo_depends: dict[str, list[str]] = {}
    for index, entry in enumerate(repo_entries):
        depends = [str(d) for d in _as_list(entry.get("depends_on"))]
        repo_depends[repo_names[index]] = [d for d in depends if d in repo_names]

    keys = [step_key(entry, index) for index, entry in enumerate(steps)]
    node_ids = [f"step-{key}" for key in keys]
    by_key = dict(zip(keys, node_ids))

    nodes = []
    lanes: dict[str, list[str]] = {}
    for index, entry in enumerate(steps):
        repo = str(entry.get("repo") or "unassigned")
        status = str(entry.get("status") or "")
        lanes.setdefault(repo, []).append(node_ids[index])
        nodes.append(
            {
                "id": node_ids[index],
                "label": keys[index],
                "kind": "step",
                "tone": tone_of("steps", status),
                "meta": {
                    "index": index,
                    "key": keys[index],
                    "repo": repo,
                    "status": status,
                    "blocking": bool(entry.get("blocking")),
                    "what": " ".join(str(entry.get("what") or entry.get("issue") or "").split()),
                    "verify": str(entry.get("verify") or ""),
                    "note": " ".join(str(entry.get("note") or "").split()),
                },
            }
        )

    edges = []
    warnings = []
    seen: set = set()

    def add(source: str, target: str, label: str, kind: str) -> None:
        if source == target or (source, target) in seen:
            return
        seen.add((source, target))
        edges.append(
            {
                "id": f"{kind}:{source}->{target}",
                "source": source,
                "target": target,
                "label": label,
                "kind": kind,
                "tone": "idle",
                "dashed": kind != "declared",
                "meta": {},
            }
        )

    def link(entry: dict, index: int, raw, forward: bool, key_name: str) -> None:
        target_key = str(raw)
        if target_key not in by_key:
            warnings.append(
                {
                    "level": "warn",
                    "text": f"Step {keys[index]} declares {key_name}: {target_key} - no step carries that id.",
                }
            )
            return
        here, there = node_ids[index], by_key[target_key]
        if forward:
            add(there, here, key_name, "declared")
        else:
            add(here, there, key_name, "declared")

    declared = False
    for index, entry in enumerate(steps):
        for key_name in FORWARD_KEYS:
            for raw in _as_list(entry.get(key_name)):
                declared = True
                link(entry, index, raw, True, key_name)
        for key_name in BACKWARD_KEYS:
            for raw in _as_list(entry.get(key_name)):
                declared = True
                link(entry, index, raw, False, key_name)

    if not declared:
        for lane in lanes.values():
            for first, second in zip(lane, lane[1:]):
                add(first, second, "same repo, in order", "sequence")
        cluster_lane = lanes.get(CLUSTER_REPO, [])
        for repo, lane in lanes.items():
            if repo == CLUSTER_REPO or not lane:
                continue
            for dependency in repo_depends.get(repo, []):
                upstream = lanes.get(dependency) or []
                if upstream:
                    add(upstream[-1], lane[0], f"{dependency} merges first", "repo-order")
            if cluster_lane:
                add(lane[-1], cluster_lane[0], "cluster wrap-up", "repo-order")
        if steps:
            warnings.append(
                {
                    "level": "info",
                    "text": "No step declares depends_on, so these edges are inferred from repo merge order and "
                    "step numbering. Add depends_on to a step to state the real order.",
                }
            )
    return _finish(nodes, edges, warnings)


def plan_graphs(plan: Plan) -> dict:
    return {"repos": plan_repo_graph(plan), "steps": plan_step_graph(plan)}
