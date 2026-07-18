import json
import os
import subprocess
from pathlib import Path

import rich_click as click
import yaml


def read_yaml(path, required=True):
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


def resolve_path(value, base):
    path = Path(os.path.expandvars(str(value))).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def load_repos(manifest_path):
    manifest = read_yaml(manifest_path)
    local = read_yaml(manifest_path.with_name("harness.local.yaml"), required=False)
    root = manifest_path.parent.resolve()
    workspace_value = (
        os.environ.get("EVO_HARNESS_WORKSPACE") or local.get("workspace") or manifest.get("workspace") or root.parent
    )
    workspace = resolve_path(workspace_value, root)
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
        declared = resolve_path(entry["path"], workspace) if entry.get("path") else None
        path = declared if declared and declared.exists() else fallback.resolve()
        present = present_overrides.get(name, entry.get("present", True))
        repos.append(
            {
                "name": name,
                "path": path,
                "present": bool(present) and path.is_dir(),
                "declared_present": bool(present),
                "role": str(entry.get("role") or ""),
                "branch": str(entry.get("branch") or ""),
                "origin": str(entry.get("origin") or ""),
                "note": str(entry.get("note") or ""),
            }
        )
    return manifest, repos


def _contains(parent, child):
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _as_manifest(value):
    path = resolve_path(value, Path.cwd())
    return path / "harness.yaml" if path.is_dir() else path


def find_manifest(value=None):
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

    registry_path = resolve_path(
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
                _, repos = load_repos(candidate)
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


def known_manifests():
    registry_path = resolve_path(
        os.environ.get("EVO_HARNESS_REGISTRY", Path.home() / ".claude" / "harness" / "registry.json"),
        Path.cwd(),
    )
    found = []
    if registry_path.is_file():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return found
        for cluster in registry.get("clusters", []):
            if not isinstance(cluster, dict) or not cluster.get("root"):
                continue
            candidate = _as_manifest(cluster["root"])
            if candidate.is_file():
                found.append(candidate)
    return list(dict.fromkeys(found))


def repo_path(manifest_path, name):
    _, repos = load_repos(manifest_path)
    for entry in repos:
        if entry["name"] == name:
            return entry["path"]
    return manifest_path.parent.resolve().parent / name


def harness_option(func):
    return click.option(
        "--harness",
        "harness_path",
        type=click.Path(path_type=Path),
        help="Harness directory or harness.yaml path. Defaults to auto-discovery.",
    )(func)


def git(path, *args, timeout=20):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
