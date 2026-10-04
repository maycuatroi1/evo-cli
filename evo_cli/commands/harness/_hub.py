"""Plan writes in a harness whose plans live on the evo-agents hub (harness.yaml `hub.project`).

Such a harness keeps read-only copies of its plans in git (`_mirror`). `evo harness step`, `debt`,
`question`, `repo` and the dashboard's complete button never edit those files: they run
`evo-agents hub plan patch` or `evo-agents hub plan complete` (seam hub-cli-v1, whose contract is
`evo-agents hub contract print`), then `evo-agents hub plan export` to write the copies back.

Every write names the revision it was computed from (`--if-revision`). When someone else wrote the
plan in between, the hub refuses it with a 409 and changes nothing; this module reads the plan again,
recomputes the one item on the new revision and sends it again, at most ATTEMPTS times, after a random pause
that grows with each retry so that writers racing for one plan spread out instead of colliding again:

- text that `--note` and `--evidence` append is appended to the other writer's text, never in place of it;
- a key set outright (status and its date) that the other writer changed to a different value stops the
  command with an error instead of overwriting it, the way `evo-agents hub plan patch` treats it;
- an item other than a step must still be the same item at its index, or nothing is written.

Before asking the hub anything, a write checks the copies in the harness: the export after it rewrites
them, so a copy someone edited by hand stops the command first. When evo-agents is missing, not signed
in, or the hub cannot be reached, the command fails with what to do and the YAML stays as it was.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rich_click as click

from evo_cli.commands.harness._dag import step_key
from evo_cli.commands.harness._mirror import EDITED, FOREIGN, INTACT, classify, copies, normalized
from evo_cli.commands.harness._model import AREAS, Plan, harness_root, join_text

EXECUTABLE = "evo-agents"
INSTALL = "uv tool install 'evo-ak[graphify]>=0.2.0'"
LOGIN = "evo-agents hub login --url https://agents.omelet.tech"
ATTEMPTS = 12  # writes sent for one item before a run of revision conflicts is reported
PAUSE = 0.25  # seconds; the pause before a retry is random, up to PAUSE doubled per retry and at most MAX_PAUSE
MAX_PAUSE = 3.0
TIMEOUT = 180  # seconds for one evo-agents command; its own HTTP calls time out sooner
UPDATABLE = ("status", "done_at", "fixed_at", "answered_at", "merged_at", "note", "evidence")

# The keys of each `--json` answer read below. tests/test_harness_hub.py checks them, and every
# command line sent, against `evo-agents hub contract print`.
READS = {
    "hub plan show": ("area", "revision", "body"),
    "hub plan patch": ("revision", "changed"),
    "hub plan complete": ("revision", "changed"),
    "hub plan export": ("notes",),
}


class HubError(click.ClickException):
    """An `evo-agents hub ...` command failed. `unavailable` when no command can work right now
    (evo-agents missing, too old, or not signed in), so reading the plan again is pointless."""

    def __init__(self, message: str, *, unavailable: bool = False):
        super().__init__(message)
        self.unavailable = unavailable

    def adding(self, text: str) -> HubError:
        return HubError(f"{self.message} {text}", unavailable=self.unavailable)


def _error_text(stderr: str, returncode: int) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    errors = [line[len("error:") :].strip() for line in lines if line.startswith("error:")]
    text = " ".join(errors) or " ".join(lines[-5:]) or f"exit status {returncode}"
    return text if text.endswith((".", "!", "?")) else text + "."


def call(args: list[str], cwd: Path) -> Any:
    """`evo-agents hub ARGS --json`, decoded; HubError saying what went wrong otherwise."""
    executable = shutil.which(EXECUTABLE)
    if executable is None:
        raise HubError(
            "This harness keeps its plans on the evo-agents hub (hub.project in harness.yaml), and `evo-agents` "
            f"is not on PATH. Install it with `{INSTALL}`, then sign in with `{LOGIN}`.",
            unavailable=True,
        )
    shown = "evo-agents hub " + " ".join(args[:2])
    try:
        done = subprocess.run(
            [executable, "hub", *args, "--json"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HubError(f"`{shown}` gave no answer within {TIMEOUT}s.") from None
    except OSError as exc:
        raise HubError(f"Cannot run {executable}: {exc}.", unavailable=True) from None

    if done.returncode != 0:
        text = _error_text(done.stderr, done.returncode)
        lowered = done.stderr.lower()
        if "not signed in" in lowered or "hub login" in lowered:
            raise HubError(f"`{shown}` failed: {text} Sign in with `{LOGIN}`.", unavailable=True)
        if done.returncode == 2 and ("invalid choice" in lowered or "unrecognized arguments" in lowered):
            raise HubError(
                f"This evo-agents does not understand `{shown}`: {text} Install a version that does with `{INSTALL}`.",
                unavailable=True,
            )
        raise HubError(f"`{shown}` failed: {text}")
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise HubError(f"`{shown}` did not print the JSON it promises with --json.") from None


@dataclass(frozen=True)
class Target:
    manifest_path: Path
    root: Path
    project: str
    plan: Plan

    @property
    def copy(self) -> str:
        try:
            return self.plan.path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(self.plan.path)

    @property
    def untouched(self) -> str:
        return f"{self.copy} was not changed."


def _preflight(target: Target) -> None:
    """Refuse before the hub hears anything when the export after the write would overwrite work."""
    problems = []
    for found in copies(target.manifest_path, target.project):
        mine = found.path.resolve() == target.plan.path.resolve()
        if found.state in (EDITED, FOREIGN) or (mine and found.state != INTACT):
            problems.append(f"  {found.message}")
    if problems:
        raise click.ClickException(
            "The plans of this harness live on the evo-agents hub, and `evo-agents hub plan export` rewrites "
            "their copies after every change, so it would overwrite this:\n"
            + "\n".join(problems)
            + "\nNothing was sent to the hub, and no file was changed."
        )


def _show(target: Target) -> dict:
    try:
        plan = call(["plan", "show", target.plan.id, "--project", target.project], target.root)
    except HubError as exc:
        raise exc.adding(target.untouched) from None
    if (
        not isinstance(plan, dict)
        or type(plan.get("revision")) is not int
        or not isinstance(plan.get("body"), dict)
        or plan.get("area") not in AREAS
    ):
        raise HubError(f"`evo-agents hub plan show {target.plan.id}` answered without a revision, area and body.")
    return plan


def _reread(target: Target, failure: HubError, sent_on: dict, retry: int) -> dict:
    """After a refused write: the plan again when a newer revision explains the refusal (a 409), else the
    original error. Telling a conflict by the revision, not by the wording of the message, keeps this
    independent of how the hub phrases it. The pause comes before the read, so the retry goes out on
    a revision as fresh as it can be."""
    if failure.unavailable:
        raise failure.adding(target.untouched) from None
    _pause(retry)
    try:
        latest = _show(target)
    except click.ClickException:
        raise failure.adding(target.untouched) from None
    if latest["revision"] == sent_on["revision"]:
        raise failure.adding(target.untouched) from None
    return latest


def _pause(retry: int) -> None:
    time.sleep(random.uniform(0, min(MAX_PAUSE, PAUSE * 2**retry)))


def _identity(item: dict) -> dict:
    return {key: value for key, value in item.items() if key not in UPDATABLE}


def _hub_item(target: Target, plan: dict, section: str, index: int, key: str | None, local: dict) -> dict:
    items = plan["body"].get(section)
    items = items if isinstance(items, list) else []
    stale = (
        f"{target.copy} may be older than the hub's plan: `evo-agents hub plan export {target.root}` refreshes it. "
        f"Nothing was written."
    )
    if key is not None:
        position = next(
            (i for i, entry in enumerate(items) if step_key(entry if isinstance(entry, dict) else {}, i) == key),
            None,
        )
        if position is None:
            raise click.ClickException(
                f"Plan {target.plan.id} on the hub (revision {plan['revision']}) has no step {key}. {stale}"
            )
    else:
        position = index
        if not 0 <= index < len(items) or not isinstance(items[index], dict) or _identity(items[index]) != local:
            raise click.ClickException(
                f"{section}[{index}] of plan {target.plan.id} on the hub (revision {plan['revision']}) is not the item "
                f"{target.copy} shows there. {stale}"
            )
    item = items[position]
    if not isinstance(item, dict):
        raise click.ClickException(f"Step {key} of plan {target.plan.id} on the hub is not a mapping. {stale}")
    return item


def _target(manifest_path: Path, plan: Plan, project: str) -> Target:
    return Target(manifest_path, harness_root(manifest_path), project, plan)


def _export(target: Target, written: dict, area: str | None = None) -> tuple[Path, list[str]]:
    """Write the copies, then confirm the copy of this plan holds at least the revision just written."""
    held = f"The hub holds the change (plan {target.plan.id}, revision {written['revision']}), but"
    retry = f"Run `evo-agents hub plan export {target.root}` to write it."
    try:
        result = call(["plan", "export", str(target.root), "--project", target.project], target.root)
    except HubError as exc:
        raise click.ClickException(f"{held} writing its copy failed: {exc.message} {retry}") from None

    areas = (area,) if area else AREAS
    for name in areas:
        path = target.root / "plans" / name / f"{target.plan.id}.yaml"
        if not path.is_file():
            continue
        found = classify(target.root, target.project, path)
        if found.state == INTACT and (found.revision or 0) >= written["revision"]:
            notes = result.get("notes") if isinstance(result, dict) else None
            return path, [str(note) for note in notes or []]
    where = " or ".join(f"plans/{name}/{target.plan.id}.yaml" for name in areas)
    raise click.ClickException(f"{held} `evo-agents hub plan export` left no copy of that revision at {where}. {retry}")


def patch_item(
    manifest_path: Path, plan: Plan, project: str, section: str, index: int, updates: dict, append: dict
) -> dict:
    """Set `updates` and append `append` on one item of `plan` on the hub, then write the copies."""
    target = _target(manifest_path, plan, project)
    _preflight(target)

    entries = plan.raw.get(section)
    local = entries[index] if isinstance(entries, list) and 0 <= index < len(entries) else None
    if not isinstance(local, dict):
        raise click.ClickException(
            f"{section}[{index}] of {target.copy} is not a mapping, so it has no status to set. Nothing was written."
        )
    local = normalized(local)
    key = step_key(local, index) if section == "steps" else None
    where = f"step {key}" if key is not None else f"{section}[{index}]"
    selector = ["--step", key] if key is not None else ["--index", str(index)]

    current = _show(target)
    first = previous = None
    for attempt in range(ATTEMPTS):
        item = _hub_item(target, current, section, index, key, _identity(local))
        if first is None:
            first = item
        else:
            clashes = [k for k in updates if previous.get(k) != item.get(k) and item.get(k) != updates[k]]
            if clashes:
                raise click.ClickException(
                    f"{', '.join(clashes)} of {where} in plan {plan.id} changed on the hub (revision "
                    f"{current['revision']}) while this ran, so it was not overwritten. Look at it with "
                    f"`evo-agents hub plan show {plan.id}` and run the command again to set it anyway. "
                    f"{target.untouched}"
                )
        values = dict(updates)
        for name, addition in append.items():
            values[name] = join_text(item.get(name), addition, f"{where} {name}")
        sending = {name: value for name, value in values.items() if item.get(name) != value}
        if not sending:  # the hub already holds all of it, a retry of a write that landed included
            written = {"revision": current["revision"], "changed": False}
            break
        sets = [part for name, value in sending.items() for part in ("--set", f"{name}={value}")]
        args = ["plan", "patch", plan.id, "--project", project, "--section", section, *selector, *sets]
        try:
            written = call([*args, "--if-revision", str(current["revision"])], target.root)
            break
        except HubError as exc:
            previous, current = item, _reread(target, exc, current, attempt)
    else:
        raise click.ClickException(
            f"Plan {plan.id} changed on the hub {ATTEMPTS} times while {where} was being written; nothing was "
            f"written. Run the command again. {target.untouched}"
        )

    path, notes = _export(target, written)
    return {
        "old": {name: first.get(name) for name in values},
        "new": values,
        "revision": written["revision"],
        "changed": bool(written.get("changed")),
        "path": path,
        "notes": notes,
    }


def complete(manifest_path: Path, plan: Plan, project: str) -> Path:
    """Move `plan` to completed on the hub, then write the copies; the path of the completed copy."""
    target = _target(manifest_path, plan, project)
    _preflight(target)
    current = _show(target)
    for attempt in range(ATTEMPTS):
        if current["area"] == "completed":
            written = {"revision": current["revision"], "area": "completed", "changed": False}
            break
        args = ["plan", "complete", plan.id, "--project", project, "--if-revision", str(current["revision"])]
        try:
            written = call(args, target.root)
            break
        except HubError as exc:
            current = _reread(target, exc, current, attempt)
    else:
        raise click.ClickException(
            f"Plan {plan.id} changed on the hub {ATTEMPTS} times while it was being completed; nothing was "
            f"written. Run it again. {target.untouched}"
        )
    path, _ = _export(target, written, "completed")
    return path
