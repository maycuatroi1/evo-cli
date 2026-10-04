"""Read-only copies of hub plans (seam hub-plan-mirror-v1, owned by evo-agents).

When harness.yaml has `hub.project`, the evo-agents hub owns the plans of the harness, and every
plans/<area>/<id>.yaml is a copy the hub wrote with `evo-agents hub plan export`: a header line,
the plan, and a `hub: {project, revision, digest}` key. The digest is "sha256:" plus the SHA-256 of
the canonical JSON of the plan without the hub key (keys sorted, no whitespace, UTF-8 with non-ASCII
characters left as they are), read the way YAML loads it, with dates turned into ISO text.

This module recomputes that digest so `evo harness check` tells a copy the hub wrote from one someone
edited by hand, and so a write refuses to run an export that would overwrite such an edit. The
messages are the ones `evo-agents harness validate` prints, so both tools point at the same fix.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from evo_cli.commands.harness._model import harness_root
from evo_cli.commands.harness._paths import read_yaml, yaml_load

HUB_KEY = "hub"

INTACT = "intact"  # a copy of a plan of this project, exactly as the hub wrote it
EDITED = "edited"  # a copy of a plan of this project whose content no longer has its digest
FOREIGN = "foreign"  # a copy of a plan of another hub project
DRAFT = "draft"  # a plan file without a hub key: never a copy, or a copy whose key was removed
BROKEN = "broken"  # not YAML holding a mapping


def hub_project(manifest_path: Path) -> str | None:
    """The project harness.yaml names under `hub`; when set, the hub owns the plans and git keeps copies."""
    hub = read_yaml(manifest_path).get(HUB_KEY)
    project = hub.get("project") if isinstance(hub, dict) else None
    return project if isinstance(project, str) and project.strip() else None


def normalized(value):
    """A loaded YAML value as the hub sees it: dates as ISO text, every mapping key as text."""
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): normalized(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


def plan_body(data: dict) -> dict:
    """The plan without the hub key: what the hub stores and what its digest covers."""
    return {key: value for key, value in normalized(data).items() if key != HUB_KEY}


def plan_digest(data: dict) -> str:
    canonical = json.dumps(plan_body(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Copy:
    path: Path
    relative: str  # the path from the harness root, as messages name it
    state: str
    revision: int | None
    message: str


def classify(root: Path, project: str, path: Path) -> Copy:
    try:
        relative = path.resolve().relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    try:
        data = yaml_load(path.read_bytes())
    except (OSError, yaml.YAMLError) as exc:
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        message = f"{relative} cannot be read as YAML ({reason}): restore it with `evo-agents hub plan export .`"
        return Copy(path, relative, BROKEN, None, message)
    if not isinstance(data, dict):
        message = f"{relative} does not hold a plan (a YAML mapping): restore it with `evo-agents hub plan export .`"
        return Copy(path, relative, BROKEN, None, message)

    hub = data.get(HUB_KEY)
    if not isinstance(hub, dict):
        message = (
            f"{relative} has no hub key, so it is not a copy of a hub plan. Push it with "
            f"`evo-agents hub plan put {relative}` or restore the hub's copy with `evo-agents hub plan export .`"
        )
        return Copy(path, relative, DRAFT, None, message)

    revision = hub.get("revision") if type(hub.get("revision")) is int else None
    if hub.get("project") != project:
        message = (
            f"{relative} is a copy of a plan of hub project {hub.get('project')!r}, but harness.yaml says "
            f"hub.project {project!r}: restore it with `evo-agents hub plan export .`"
        )
        return Copy(path, relative, FOREIGN, revision, message)
    if hub.get("digest") != plan_digest(data):
        message = (
            f"{relative} was edited outside the hub (digest mismatch). Push it with "
            f"`evo-agents hub plan put {relative} --if-revision {revision or 'N'}` or restore it with "
            "`evo-agents hub plan export .`"
        )
        return Copy(path, relative, EDITED, revision, message)
    return Copy(path, relative, INTACT, revision, "")


def copies(manifest_path: Path, project: str) -> list[Copy]:
    """Every plans/*/*.yaml of the harness, classified; the same files `evo-agents harness validate` reads."""
    root = harness_root(manifest_path)
    plans = root / "plans"
    if not plans.is_dir():
        return []
    return [classify(root, project, path) for path in sorted(plans.glob("*/*.yaml"))]
