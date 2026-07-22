from __future__ import annotations

import re
import time
from pathlib import Path

from evo_cli.commands.harness._model import Plan, tone_of
from evo_cli.commands.harness._paths import git, load_repos

SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
DEFAULT_BASES = ("develop", "main", "master")
CACHE_TTL = 20.0

_cache: dict[tuple[str, str, bool], tuple[float, dict]] = {}


def _ref(path: Path, name: str) -> str | None:
    return git(path, "rev-parse", "--verify", "--quiet", name).stdout.strip() or None


def _resolve_ref(path: Path, branch: str) -> tuple[str | None, bool, bool]:
    local = _ref(path, f"refs/heads/{branch}") is not None
    remote = _ref(path, f"refs/remotes/origin/{branch}") is not None
    if remote:
        return f"origin/{branch}", local, remote
    if local:
        return branch, local, remote
    return None, local, remote


def _base_ref(path: Path, entry: dict) -> str | None:
    declared = entry.get("base") or entry.get("merged_into")
    for name in [str(declared)] if declared else list(DEFAULT_BASES):
        ref, _, _ = _resolve_ref(path, name)
        if ref:
            return ref
    return None


def _counts(path: Path, base: str, head: str) -> tuple[int, int] | None:
    result = git(path, "rev-list", "--left-right", "--count", f"{base}...{head}")
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    if len(parts) != 2:
        return None
    return int(parts[0]), int(parts[1])


def _last_fetch(path: Path) -> float | None:
    head = path / ".git" / "FETCH_HEAD"
    return head.stat().st_mtime if head.is_file() else None


def _commit_facts(path: Path, declared: list, head_ref: str | None, base_ref: str | None) -> list[dict]:
    facts = []
    for line in declared:
        text = str(line)
        match = SHA_RE.search(text)
        sha = match.group(1) if match else None
        fact = {"text": text, "sha": sha, "exists": False, "in_head": False, "in_base": False}
        if sha and git(path, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0:
            fact["exists"] = True
            if head_ref:
                fact["in_head"] = git(path, "merge-base", "--is-ancestor", sha, head_ref).returncode == 0
            if base_ref:
                fact["in_base"] = git(path, "merge-base", "--is-ancestor", sha, base_ref).returncode == 0
        facts.append(fact)
    return facts


def _entry_overlay(entry: dict, path: Path | None, fetch: bool) -> dict:
    name = str(entry.get("repo") or "?")
    branch = str(entry.get("branch") or "").strip()
    status = str(entry.get("status") or "").strip()
    out: dict = {
        "repo": name,
        "branch": branch,
        "status": status,
        "tone": tone_of("repos", status),
        "present": False,
        "path": str(path or ""),
        "verdicts": [],
        "commits": [],
    }

    if branch.lower() in ("none", "n/a") or not path or not path.is_dir() or not (path / ".git").exists():
        reason = (
            "Plan marks this repo as needing no branch."
            if branch.lower() in ("none", "n/a")
            else "Repo is not on this machine, so nothing can be checked against git."
        )
        out["verdicts"].append({"level": "ok" if branch.lower() in ("none", "n/a") else "unknown", "text": reason})
        return out
    out["present"] = True

    if fetch:
        git(path, "fetch", "--quiet", "--prune", "origin", timeout=90)
    out["last_fetch"] = _last_fetch(path)
    out["current_branch"] = git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    out["dirty"] = bool(git(path, "status", "--porcelain").stdout.strip())
    out["head"] = git(path, "log", "-1", "--oneline").stdout.strip()

    base_ref = _base_ref(path, entry)
    out["base"] = str(entry.get("base") or entry.get("merged_into") or "") or (base_ref or "")
    out["base_ref"] = base_ref

    if branch and branch.upper() != "TBD":
        head_ref, local, remote = _resolve_ref(path, branch)
        out["head_ref"] = head_ref
        out["branch_local"] = local
        out["branch_remote"] = remote
        if head_ref and base_ref:
            out["merged"] = git(path, "merge-base", "--is-ancestor", head_ref, base_ref).returncode == 0
            counts = _counts(path, base_ref, head_ref)
            if counts:
                out["behind"], out["ahead"] = counts
        declared = list(entry.get("commits") or ([entry["commit"]] if entry.get("commit") else []))
        out["commits"] = _commit_facts(path, declared, head_ref, base_ref)

    _judge(entry, out)
    return out


def _judge(entry: dict, out: dict) -> None:
    verdicts = out["verdicts"]
    branch = out.get("branch") or ""
    status = out.get("status") or ""
    stale_fetch = out.get("last_fetch") and (time.time() - out["last_fetch"]) > 86400

    if branch and branch.upper() != "TBD" and not out.get("head_ref"):
        hint = (
            " Fetch before concluding - the local copy may be stale."
            if not out.get("last_fetch") or stale_fetch
            else ""
        )
        verdicts.append({"level": "error", "text": f"Branch {branch!r} does not exist, locally or on origin.{hint}"})

    if out.get("head_ref") and out.get("base_ref") and out["head_ref"] != out["base_ref"]:
        if out["tone"] == "ok" and out.get("merged") is False:
            verdicts.append(
                {
                    "level": "error",
                    "text": f"Plan claims status={status!r} but {branch} is not in {out['base_ref']} "
                    f"(ahead by {out.get('ahead', '?')} commits).",
                }
            )
        if out["tone"] != "ok" and out.get("merged") is True:
            verdicts.append(
                {
                    "level": "warn",
                    "text": f"{branch} is already in {out['base_ref']} but the plan still says status={status!r}.",
                }
            )
        if out.get("behind", 0) > 0 and out["tone"] != "ok":
            verdicts.append({"level": "warn", "text": f"{branch} is {out['behind']} commits behind {out['base_ref']}."})

    if entry.get("pushed") is True and out.get("branch_remote") is False:
        verdicts.append({"level": "error", "text": f"Plan says pushed: true but origin/{branch} does not exist."})
    if entry.get("pushed") is False and out.get("branch_remote") is True:
        verdicts.append({"level": "warn", "text": f"origin/{branch} exists but the plan still says pushed: false."})

    missing = [c for c in out.get("commits") or [] if c["sha"] and not c["exists"]]
    if missing:
        shas = ", ".join(c["sha"] for c in missing)
        hint = " Re-run with --fetch before concluding it is gone." if stale_fetch or not out.get("last_fetch") else ""
        verdicts.append({"level": "error", "text": f"Commit named in the plan is not in the repo: {shas}.{hint}"})

    orphan = [c for c in out.get("commits") or [] if c["exists"] and out.get("head_ref") and not c["in_head"]]
    if orphan:
        shas = ", ".join(c["sha"] for c in orphan)
        verdicts.append({"level": "warn", "text": f"Commit exists but is not on {branch}: {shas}."})

    if out.get("dirty") and out.get("current_branch") == branch:
        verdicts.append({"level": "warn", "text": f"Dirty worktree on {branch} - uncommitted changes."})

    if not verdicts:
        verdicts.append({"level": "ok", "text": "Matches git."})


def overlay(manifest_path: Path, plan: Plan, fetch: bool = False) -> dict:
    key = (str(manifest_path), plan.id, fetch)
    cached = _cache.get(key)
    now = time.time()
    if cached and not fetch and now - cached[0] < CACHE_TTL and cached[1].get("mtime") == plan.mtime:
        return cached[1]

    _, entries = load_repos(manifest_path)
    index = {entry["name"]: entry["path"] for entry in entries}
    workspace = manifest_path.parent.resolve().parent

    repos = []
    for entry in plan.items("repos"):
        name = str(entry.get("repo") or "")
        path = index.get(name) or (workspace / name if name and name != "__cluster__" else None)
        repos.append(_entry_overlay(entry, path, fetch))

    levels = [v["level"] for r in repos for v in r["verdicts"]]
    result = {
        "plan": plan.id,
        "mtime": plan.mtime,
        "checked_at": now,
        "fetched": fetch,
        "repos": repos,
        "errors": levels.count("error"),
        "warnings": levels.count("warn"),
        "unknown": levels.count("unknown"),
    }
    _cache[key] = (now, result)
    return result
