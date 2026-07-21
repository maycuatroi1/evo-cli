from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import rich_click as click
import yaml

from evo_cli.commands.harness._paths import load_repos, read_yaml

SECTIONS = ("references", "repos", "steps", "decisions", "tech_debt", "open_questions")
AREAS = ("active", "completed")

TITLE_KEYS = ("what", "issue", "repo", "order")
DONE_WORDS = {"done", "merged", "fixed", "verified", "answered", "closed", "resolved", "not-needed"}
ACTIVE_WORDS = {"in_progress", "wip", "doing", "review"}
BLOCKED_WORDS = {"blocked", "stuck"}
WARN_WORDS = {"open", "unknown", "todo", "pending"}


def harness_root(manifest_path: Path) -> Path:
    return manifest_path.parent.resolve()


def plans_dir(manifest_path: Path) -> Path:
    return harness_root(manifest_path) / "plans"


def contracts_file(manifest_path: Path) -> Path:
    return harness_root(manifest_path) / "contracts.yaml"


def deployments_file(manifest_path: Path) -> Path:
    return harness_root(manifest_path) / "deployments.yaml"


def tone_of(section: str, status: str | None) -> str:
    value = (status or "").strip().lower()
    if not value:
        return "idle"
    if value in DONE_WORDS:
        return "ok"
    if value in ACTIVE_WORDS:
        return "active"
    if value in BLOCKED_WORDS:
        return "bad"
    if value in WARN_WORDS:
        return "warn" if section in ("tech_debt", "open_questions", "steps") else "idle"
    return "idle"


def _title_of(section: str, item: dict) -> str:
    if section == "repos":
        return str(item.get("repo") or "?")
    if section == "references":
        return str(item.get("what") or item.get("where") or "?")
    for key in TITLE_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(item.get("what") or "?")


def plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass
class Plan:
    id: str
    path: Path
    area: str
    raw: dict

    @property
    def mtime(self) -> float:
        return self.path.stat().st_mtime

    def items(self, section: str) -> list[dict]:
        """Sections hold mappings, except `decisions`, which authors write as bare strings.

        Dropping non-mappings loses those entirely, so a plain string is lifted into the same
        shape the rest of the pipeline expects rather than being filtered out.
        """
        value = self.raw.get(section) or []
        if not isinstance(value, list):
            return []
        return [x if isinstance(x, dict) else {"what": str(x)} for x in value if x is not None]

    def progress(self) -> dict:
        steps = self.items("steps")
        done = sum(1 for s in steps if tone_of("steps", s.get("status")) == "ok")
        active = sum(1 for s in steps if tone_of("steps", s.get("status")) == "active")
        blocking = sum(1 for s in steps if s.get("blocking") and tone_of("steps", s.get("status")) != "ok")
        repos = self.items("repos")
        return {
            "steps_total": len(steps),
            "steps_done": done,
            "steps_active": active,
            "steps_blocking": blocking,
            "pct": round(done * 100 / len(steps)) if steps else 0,
            "repos_total": len(repos),
            "repos_done": sum(1 for r in repos if tone_of("repos", r.get("status")) == "ok"),
            "debt_open": sum(1 for d in self.items("tech_debt") if tone_of("tech_debt", d.get("status")) != "ok"),
            "debt_total": len(self.items("tech_debt")),
            "questions_open": sum(
                1 for q in self.items("open_questions") if tone_of("open_questions", q.get("status")) != "ok"
            ),
            "questions_total": len(self.items("open_questions")),
        }

    def summary(self) -> dict:
        return {
            "id": self.id,
            "area": self.area,
            "goal": " ".join(str(self.raw.get("goal") or "").split()),
            "created_at": str(self.raw.get("created_at") or ""),
            "path": str(self.path),
            "mtime": self.mtime,
            "progress": self.progress(),
        }

    def detail(self) -> dict:
        data = self.summary()
        data["sections"] = {
            name: [
                {
                    "index": i,
                    "title": _title_of(name, item),
                    "status": item.get("status"),
                    "tone": tone_of(name, item.get("status")),
                    "order": item.get("order"),
                    "raw": plain(item),
                }
                for i, item in enumerate(self.items(name))
            ]
            for name in SECTIONS
        }
        data["extra"] = {
            k: plain(v) for k, v in self.raw.items() if k not in SECTIONS and k not in ("id", "goal", "created_at")
        }
        return data


def load_plan_file(path: Path) -> Plan:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise click.ClickException(f"Broken YAML: {path}\n{exc}") from exc
    if not isinstance(raw, dict):
        raise click.ClickException(f"A plan must be a mapping at the root: {path}")
    return Plan(id=str(raw.get("id") or path.stem), path=path, area=path.parent.name, raw=raw)


def load_plans(manifest_path: Path, area: str | None = None) -> list[Plan]:
    root = plans_dir(manifest_path)
    if not root.is_dir():
        return []
    areas = (area,) if area else AREAS
    found: list[Plan] = []
    for name in areas:
        directory = root / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            found.append(load_plan_file(path))
    return found


def find_plan(manifest_path: Path, plan_id: str) -> Plan:
    plans = load_plans(manifest_path)
    matches = [p for p in plans if p.id == plan_id or p.path.stem == plan_id]
    if matches:
        return matches[0]
    partial = [p for p in plans if plan_id.lower() in p.id.lower()]
    if len(partial) == 1:
        return partial[0]
    known = ", ".join(p.id for p in plans) or "(no plan found)"
    raise click.ClickException(f"No plan named {plan_id!r}. Available: {known}")


def load_seams(manifest_path: Path) -> list[dict]:
    path = contracts_file(manifest_path)
    if not path.is_file():
        return []
    raw = read_yaml(path, required=False)
    seams = raw.get("seams") or []
    if not isinstance(seams, list):
        return []
    out = []
    for index, seam in enumerate(seams):
        if not isinstance(seam, dict):
            continue
        consumers = seam.get("consumers") or []
        if isinstance(consumers, str):
            consumers = [consumers]
        out.append(
            {
                "index": index,
                "name": str(seam.get("name") or f"seam-{index}"),
                "kind": str(seam.get("kind") or "unknown"),
                "owner": str(seam.get("owner") or ""),
                "consumers": [str(c) for c in consumers],
                "source": str(seam.get("source") or ""),
                "artifacts": [str(a) for a in (seam.get("artifacts") or [])],
                "mirrors": [str(m) for m in (seam.get("mirrors") or [])],
                "verify": str(seam.get("verify") or ""),
                "blocking": bool(seam.get("blocking", True)),
                "remedy": " ".join(str(seam.get("remedy") or "").split()),
                "notes": " ".join(str(seam.get("notes") or "").split()),
                "keys": plain(seam.get("keys") or []),
            }
        )
    return out


def load_deployments(manifest_path: Path) -> dict:
    path = deployments_file(manifest_path)
    empty = {"version": 0, "configVersion": "", "environments": [], "tenants": [], "deployments": []}
    if not path.is_file():
        return empty
    raw = read_yaml(path, required=False)
    if not isinstance(raw, dict):
        return empty

    environments = [str(e) for e in (raw.get("environments") or []) if e is not None]

    tenants = []
    for tenant in raw.get("tenants") or []:
        if not isinstance(tenant, dict):
            continue
        tenants.append(
            {
                "id": str(tenant.get("id") or ""),
                "code": str(tenant.get("code") or ""),
                "name": str(tenant.get("name") or ""),
                "aliases": [str(a) for a in (tenant.get("aliases") or [])],
            }
        )

    deployments = []
    for index, dep in enumerate(raw.get("deployments") or []):
        if not isinstance(dep, dict):
            continue
        deployments.append(
            {
                "index": index,
                "deploymentId": str(dep.get("deployment_id") or f"deployment-{index}"),
                "product": str(dep.get("product") or ""),
                "tenantId": str(dep.get("tenant_id")) if dep.get("tenant_id") else "",
                "environment": str(dep.get("environment") or ""),
                "kind": str(dep.get("deployment_kind") or ""),
                "webUrl": str(dep.get("web_url")) if dep.get("web_url") else "",
                "apiUrl": str(dep.get("api_url")) if dep.get("api_url") else "",
                "authUrl": str(dep.get("auth_url")) if dep.get("auth_url") else "",
                "capabilities": [str(c) for c in (dep.get("capabilities") or [])],
                "status": str(dep.get("status") or ""),
                "aliases": [str(a) for a in (dep.get("aliases") or [])],
            }
        )

    return {
        "version": raw.get("version") or 0,
        "configVersion": str(raw.get("config_version") or ""),
        "environments": environments,
        "tenants": tenants,
        "deployments": deployments,
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def cluster(manifest_path: Path) -> dict:
    manifest, repos = load_repos(manifest_path)
    root = harness_root(manifest_path)
    principles = [
        {"name": p.name, "path": str(p), "body": _read_text(p)}
        for p in sorted((root / "principles").glob("*.md"))
        if p.is_file()
    ]
    pending = sorted((root / "proposals" / "_pending").glob("*")) if (root / "proposals" / "_pending").is_dir() else []
    return {
        "name": str(manifest.get("name") or root.name),
        "root": str(root),
        "manifest": str(manifest_path),
        "created_at": str(manifest.get("created_at") or ""),
        "workspace": str(manifest.get("workspace") or ""),
        "repos": [
            {
                "name": r["name"],
                "path": str(r["path"]),
                "present": r["present"],
                "role": r["role"],
                "branch": r["branch"],
                "origin": r["origin"],
                "note": " ".join(r["note"].split()),
            }
            for r in repos
        ],
        "principles": principles,
        "proposals_pending": [p.name for p in pending],
        "has_contracts": contracts_file(manifest_path).is_file(),
        "has_plans": plans_dir(manifest_path).is_dir(),
    }


def digest(manifest_path: Path) -> str:
    root = harness_root(manifest_path)
    parts = []
    targets = [manifest_path, contracts_file(manifest_path), deployments_file(manifest_path)]
    targets.extend(sorted(plans_dir(manifest_path).rglob("*.yaml")) if plans_dir(manifest_path).is_dir() else [])
    targets.extend(sorted((root / "principles").glob("*.md")) if (root / "principles").is_dir() else [])
    for path in targets:
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()
