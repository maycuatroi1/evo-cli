import json
import os
import shutil
import subprocess
from pathlib import Path

import rich_click as click
import yaml


def _read_yaml(path, required=True):
    if not path.is_file():
        if required:
            raise click.ClickException(f"Harness manifest not found: {path}")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise click.ClickException(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise click.ClickException(f"Expected a YAML mapping in {path}")
    return data


def _resolve_path(value, base):
    path = Path(os.path.expandvars(str(value))).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _load_repos(manifest_path):
    manifest = _read_yaml(manifest_path)
    local = _read_yaml(manifest_path.with_name("harness.local.yaml"), required=False)
    root = manifest_path.parent.resolve()
    workspace_value = (
        os.environ.get("EVO_HARNESS_WORKSPACE") or local.get("workspace") or manifest.get("workspace") or root.parent
    )
    workspace = _resolve_path(workspace_value, root)
    entries = manifest.get("repos", [])
    if not isinstance(entries, list):
        raise click.ClickException(f"Expected 'repos' to be a list in {manifest_path}")
    present_overrides = local.get("present", {}) or {}
    if not isinstance(present_overrides, dict):
        raise click.ClickException(
            f"Expected 'present' to be a mapping in {manifest_path.with_name('harness.local.yaml')}"
        )

    repos = []
    names = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise click.ClickException(f"Expected repos[{index}] to be a mapping in {manifest_path}")
        name = str(entry.get("name", "")).strip()
        if not name:
            raise click.ClickException(f"Expected repos[{index}].name in {manifest_path}")
        if name in names:
            raise click.ClickException(f"Duplicate repo name '{name}' in {manifest_path}")
        names.add(name)
        fallback = workspace / name
        declared = _resolve_path(entry["path"], workspace) if entry.get("path") else None
        path = declared if declared and declared.exists() else fallback.resolve()
        present = present_overrides.get(name, entry.get("present", True))
        repos.append({"name": name, "path": path, "present": bool(present)})
    return manifest, repos


def _contains(parent, child):
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _as_manifest(value):
    path = _resolve_path(value, Path.cwd())
    return path / "harness.yaml" if path.is_dir() else path


def _find_manifest(value=None):
    configured = value or os.environ.get("EVO_HARNESS")
    if configured:
        manifest_path = _as_manifest(configured)
        if not manifest_path.is_file():
            raise click.ClickException(f"Harness manifest not found: {manifest_path}")
        return manifest_path

    cwd = Path.cwd().resolve()
    for base in (cwd, *cwd.parents):
        candidate = base / "harness.yaml"
        if candidate.is_file():
            return candidate

    registry_path = _resolve_path(
        os.environ.get("EVO_HARNESS_REGISTRY", Path.home() / ".claude" / "harness" / "registry.json"),
        Path.cwd(),
    )
    matches = []
    if registry_path.is_file():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise click.ClickException(f"Cannot read {registry_path}: {exc}") from exc
        for cluster in registry.get("clusters", []):
            if not isinstance(cluster, dict) or not cluster.get("root"):
                continue
            candidate = _as_manifest(cluster["root"])
            if not candidate.is_file():
                continue
            try:
                _, repos = _load_repos(candidate)
            except click.ClickException:
                continue
            root = candidate.parent.resolve()
            if _contains(root, cwd) or any(_contains(repo["path"], cwd) for repo in repos):
                matches.append(candidate)

    matches = list(dict.fromkeys(matches))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        choices = ", ".join(str(path.parent) for path in matches)
        raise click.ClickException(f"Current directory belongs to multiple harnesses: {choices}. Use --harness PATH.")
    raise click.ClickException("Cannot find harness.yaml. Run inside a harness repo or use --harness PATH.")


def _git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@click.group("harness", help="Manage repositories declared by a harness manifest.")
def harness_group():
    pass


@harness_group.command("pull", help="Fast-forward all available repositories in a harness.")
@click.option(
    "--harness",
    "harness_path",
    type=click.Path(path_type=Path),
    help="Harness directory or harness.yaml path. Defaults to auto-discovery.",
)
@click.option("--repo", "repos", multiple=True, help="Pull only this repo name. Repeat for multiple repos.")
@click.option("--prune/--no-prune", default=True, show_default=True, help="Prune deleted remote refs while pulling.")
@click.option("--dry-run", is_flag=True, help="Show repositories that would be pulled.")
def pull(harness_path, repos, prune, dry_run):
    if shutil.which("git") is None:
        raise click.ClickException("git is not installed or is not on PATH")

    manifest_path = _find_manifest(harness_path)
    manifest, entries = _load_repos(manifest_path)
    available = {entry["name"] for entry in entries}
    unknown = sorted(set(repos) - available)
    if unknown:
        raise click.ClickException(f"Unknown repo name(s): {', '.join(unknown)}")
    selected = [entry for entry in entries if not repos or entry["name"] in repos]

    click.echo(f"Harness: {manifest.get('name') or manifest_path.parent.name}")
    click.echo(f"Manifest: {manifest_path}")
    results = []
    incomplete = 0

    for entry in selected:
        name = entry["name"]
        path = entry["path"]
        if not entry["present"]:
            results.append((name, "skipped (present: false)"))
            continue
        if not path.is_dir():
            results.append((name, f"failed (missing: {path})"))
            incomplete += 1
            continue

        status = _git(path, "status", "--porcelain")
        if status.returncode != 0:
            detail = (status.stderr or status.stdout).strip().splitlines()
            results.append((name, f"failed ({detail[-1] if detail else 'not a git repository'})"))
            incomplete += 1
            continue
        if status.stdout.strip():
            results.append((name, "skipped (uncommitted changes)"))
            incomplete += 1
            continue
        if dry_run:
            results.append((name, "would pull"))
            continue

        args = ["pull", "--ff-only"]
        if prune:
            args.append("--prune")
        update = _git(path, *args)
        if update.returncode != 0:
            detail = (update.stderr or update.stdout).strip().splitlines()
            results.append((name, f"failed ({detail[-1] if detail else 'git pull failed'})"))
            incomplete += 1
            continue
        head = _git(path, "log", "-1", "--oneline")
        results.append((name, head.stdout.strip() or "updated"))

    click.echo()
    click.secho("Summary", bold=True)
    width = max((len(name) for name, _ in results), default=0)
    for name, status in results:
        color = "red" if status.startswith("failed") else "yellow" if status.startswith("skipped") else "green"
        click.secho(f"  {name:<{width}}  {status}", fg=color)
    if incomplete:
        raise click.exceptions.Exit(1)
