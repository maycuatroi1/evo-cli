"""A fake `evo-agents` for tests/test_harness_hub.py: the `hub plan` commands evo-cli runs, answered from a
JSON state file instead of a hub, so no test ever reaches a real hub or a real harness.

FAKE_EVO_HUB names the state file, which holds:

- project: the one hub project the fake knows
- plans: {plan_id: {area, revision, body}}
- calls: the argv of every invocation, appended before anything else happens
- signed_in: false makes every command fail the way evo-agents does without credentials
- unreachable: subcommands (show, patch, complete, export) that fail as if the hub did not answer
- others: changes by another writer, each {on, section, step or index, updates}, applied just before the next
  `on` command (patch or complete) is handled, so that command meets a newer revision
- fail_export: true makes export fail after the change it follows was stored

Answers have the keys `evo-agents hub contract print` declares. A failure is one `error:` line on stderr and
exit status 1, a usage error exit status 2, as argparse gives. The messages are the ones evo-agents 0.2.0
prints for the same refusals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import yaml

UPDATABLE = ("status", "done_at", "fixed_at", "answered_at", "merged_at", "note", "evidence")
STEP_STATUSES = ("done", "in_progress", "pending", "blocked")
SECTIONS = ("steps", "repos", "tech_debt", "open_questions")
AREAS = ("active", "completed")
HEADER = (
    "# Mirror of evo-agents hub plan {plan_id}, revision {revision}. Do not edit; use evo harness step or "
    "evo-agents hub plan.\n"
)
STATE = Path(os.environ["FAKE_EVO_HUB"])


def digest(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def fail(state: dict, message: str, status: int = 1):
    save(state)
    print(f"error: {message}", file=sys.stderr)
    sys.exit(status)


def view(state: dict, plan_id: str) -> dict:
    plan = state["plans"][plan_id]
    return {
        "project": state["project"],
        "plan_id": plan_id,
        "area": plan["area"],
        "revision": plan["revision"],
        "digest": digest(plan["body"]),
        "label": {"level": "internal"},
        "body": plan["body"],
        "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-05T00:00:00+00:00",
        "updated_by": "tester",
    }


def written(state: dict, plan_id: str, changed: bool) -> dict:
    return {**view(state, plan_id), "created": False, "changed": changed, "warnings": []}


def step_key(entry, index: int) -> str:
    if isinstance(entry, dict):
        for name in ("id", "order"):
            if entry.get(name) is not None:
                return str(entry[name])
    return str(index)


def apply(state: dict, plan_id: str, section: str, index, step, updates: dict) -> bool:
    plan = state["plans"][plan_id]
    items = plan["body"].get(section)
    if not isinstance(items, list):
        fail(state, f"plan {plan_id}: this plan has no {section!r} section; nothing was written")
    if step is not None:
        index = next((i for i, entry in enumerate(items) if step_key(entry, i) == str(step)), None)
        if index is None:
            fail(state, f"plan {plan_id}: no step {step!r} in plan {plan_id}; nothing was written")
    if not 0 <= index < len(items) or not isinstance(items[index], dict):
        fail(state, f"plan {plan_id}: section {section!r} has no mapping at {index}; nothing was written")
    for key, value in updates.items():
        if key not in UPDATABLE:
            fail(state, f"plan {plan_id}: {key!r} cannot be set on one item; nothing was written")
        if not isinstance(value, str):
            fail(state, f"plan {plan_id}: {key} must be text; nothing was written")
    if section == "steps" and updates.get("status", "done") not in STEP_STATUSES:
        fail(state, f"plan {plan_id}: a step's status is one of {', '.join(STEP_STATUSES)}; nothing was written")
    item = {**items[index], **updates}
    if item == items[index]:
        return False
    items[index] = item
    plan["revision"] += 1
    return True


def other_writers(state: dict, plan_id: str, on: str) -> None:
    pending = state.get("others") or []
    state["others"] = [change for change in pending if change["on"] != on]
    for change in pending:
        if change["on"] == on:
            apply(state, plan_id, change["section"], change.get("index"), change.get("step"), change["updates"])


def stale(state: dict, plan_id: str, given) -> None:
    held = state["plans"][plan_id]["revision"]
    if given is not None and given != held:
        fail(
            state,
            f"plan {plan_id} is at revision {held} on the hub, not {given}: someone changed it since you read it. "
            "Read it again and retry; nothing was written",
        )


def render(state: dict, plan_id: str) -> str:
    plan = state["plans"][plan_id]
    copy = {**plan["body"], "hub": {"project": state["project"], "revision": plan["revision"]}}
    copy["hub"]["digest"] = digest(plan["body"])
    text = yaml.safe_dump(copy, sort_keys=False, allow_unicode=True, width=110)
    return HEADER.format(plan_id=plan_id, revision=plan["revision"]) + text


def export(state: dict, root: Path) -> dict:
    if state.get("fail_export"):
        fail(state, f"cannot write {root / 'plans'} ([Errno 13] Permission denied); the file is as it was")
    result = {"root": str(root), "project": state["project"], "plans": [], "removed": [], "notes": [], "commit": None}
    for plan_id, plan in sorted(state["plans"].items()):
        path = root / "plans" / plan["area"] / f"{plan_id}.yaml"
        text = render(state, plan_id)
        status = "unchanged" if path.is_file() and path.read_text(encoding="utf-8") == text else "written"
        if status == "written":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        other = root / "plans" / ("completed" if plan["area"] == "active" else "active") / f"{plan_id}.yaml"
        if other.exists():
            other.unlink()
            result["removed"].append(other.relative_to(root).as_posix())
        relative = path.relative_to(root).as_posix()
        result["plans"].append(
            {"plan_id": plan_id, "area": plan["area"], "revision": plan["revision"], "path": relative, "status": status}
        )
    for path in sorted((root / "plans").glob("*/*.yaml")):
        if path.stem not in state["plans"]:
            relative = path.relative_to(root).as_posix()
            result["notes"].append(f"{relative}: not on the hub; push it with `evo-agents hub plan put {relative}`")
    return result


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="evo-agents")
    hub = top.add_subparsers(dest="top", required=True).add_parser("hub")
    plan = hub.add_subparsers(dest="group", required=True).add_parser("plan")
    commands = plan.add_subparsers(dest="command", required=True)

    show = commands.add_parser("show")
    show.add_argument("plan")
    show.add_argument("--revision", type=int)

    patch = commands.add_parser("patch")
    patch.add_argument("plan")
    patch.add_argument("--section", choices=SECTIONS)
    patch.add_argument("--index", type=int)
    patch.add_argument("--step")
    patch.add_argument("--set", action="append", default=[], required=True)
    patch.add_argument("--if-revision", type=int)

    complete = commands.add_parser("complete")
    complete.add_argument("plan")
    complete.add_argument("--if-revision", type=int)

    exported = commands.add_parser("export")
    exported.add_argument("root", nargs="?")
    exported.add_argument("--commit", action="store_true")

    for command in (show, patch, complete, exported):
        command.add_argument("--project")
        command.add_argument("--json", action="store_true")
    return top


def main() -> int:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    state.setdefault("calls", []).append(sys.argv[1:])
    save(state)
    args = parser().parse_args()
    if not state.get("signed_in", True):
        fail(state, "not signed in to a hub: run `evo-agents hub login --url URL`")
    if args.command in (state.get("unreachable") or []):
        fail(state, "cannot reach https://hub.test: [Errno 61] Connection refused")
    if args.project != state["project"]:
        fail(state, f"no project {args.project} on the hub, or no grant on it")

    if args.command == "export":
        answer = export(state, Path(args.root or ".").resolve())
    else:
        if args.plan not in state["plans"]:
            fail(state, f"plan {args.plan} not found in project {args.project}")
        if args.command == "show":
            answer = view(state, args.plan)
        elif args.command == "patch":
            other_writers(state, args.plan, "patch")
            if args.if_revision is None:  # the hub refuses a patch that names no revision
                held = state["plans"][args.plan]["revision"]
                fail(state, f"plan {args.plan} is on the hub at revision {held}: pass if_revision {held} to replace it")
            stale(state, args.plan, args.if_revision)
            updates = {}
            for pair in args.set:
                key, _, value = pair.partition("=")
                updates[key] = value
            if (args.index is None) == (args.step is None):
                fail(state, "name the item with exactly one of --index and --step")
            changed = apply(state, args.plan, args.section or "steps", args.index, args.step, updates)
            answer = written(state, args.plan, changed)
        else:
            other_writers(state, args.plan, "complete")
            stale(state, args.plan, args.if_revision)
            plan = state["plans"][args.plan]
            undone = [
                step_key(s, i) for i, s in enumerate(plan["body"].get("steps") or []) if s.get("status") != "done"
            ]
            if undone:
                fail(
                    state,
                    f"plan {args.plan} has steps that are not done: {', '.join(undone)}. A plan is completed once "
                    "every step is done; nothing was written",
                )
            changed = plan["area"] != "completed"
            if changed:
                plan["area"] = "completed"
                plan["revision"] += 1
            answer = written(state, args.plan, changed)
    save(state)
    print(json.dumps(answer, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
