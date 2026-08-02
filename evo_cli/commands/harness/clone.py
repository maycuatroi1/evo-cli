import re
import shutil

import rich_click as click

from evo_cli.commands.harness._paths import find_manifest, git, harness_option, load_repos
from evo_cli.commands.harness._render import print_summary

SCHEME_RE = re.compile(r"^\w+://")
USER_RE = re.compile(r"^[^@/]+@")


def normalize_remote(url):
    """Reduce a remote URL to host + path so the ssh and https forms of one repo compare equal."""
    text = str(url).strip().rstrip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]
    text = USER_RE.sub("", SCHEME_RE.sub("", text))
    return text.replace(":", "/", 1).lower()


def detail_of(result, fallback):
    lines = ((result.stderr or "") + (result.stdout or "")).strip().splitlines()
    return lines[-1] if lines else fallback


def already_cloned(entry, target):
    """A checkout is already there. Say so, and say if it came from a different remote."""
    declared = entry["origin"]
    actual = git(target, "remote", "get-url", "origin").stdout.strip()
    if declared and actual and normalize_remote(declared) != normalize_remote(actual):
        return f"skipped (already cloned, but origin is {actual})"
    return "skipped (already cloned)"


def checkout_declared_branch(entry, target):
    """Move onto the branch the manifest names. A branch that is not on origin is not an error.

    Cloning with `--branch` would fail the whole clone for a branch that has not been pushed yet,
    which is a normal state for a plan in progress. Clone the default branch, then try to move.
    """
    branch = entry["branch"]
    if not branch or branch.upper() == "TBD":
        return ""
    current = git(target, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if current == branch:
        return ""
    if git(target, "checkout", branch, timeout=120).returncode != 0:
        return f" (branch {branch!r} is not on origin, left on {current or 'HEAD'})"
    return f" (on {branch})"


@click.command("clone", help="Clone the repositories a harness declares into their declared paths.")
@harness_option
@click.option("--repo", "repos", multiple=True, help="Clone only this repo name. Repeat for multiple repos.")
@click.option("--all", "include_absent", is_flag=True, help="Also clone repos the manifest marks present: false.")
@click.option("--depth", type=int, help="Shallow clone with this many commits of history.")
@click.option("--dry-run", is_flag=True, help="Show what would be cloned, and where.")
def clone(harness_path, repos, include_absent, depth, dry_run):
    """Clone every repository the harness manifest declares but this machine does not have.

    Each repo lands on its declared `path`, falling back to `<workspace>/<name>`, so a fresh
    machine ends up with the layout the manifest, the contract seams and the exec-plans already
    assume. Repos already on disk are left alone.
    """
    if shutil.which("git") is None:
        raise click.ClickException("git is not installed or is not on PATH")

    manifest_path = find_manifest(harness_path)
    manifest, entries = load_repos(manifest_path)
    available = {entry["name"] for entry in entries}
    unknown = sorted(set(repos) - available)
    if unknown:
        raise click.ClickException(f"Unknown repo name(s): {', '.join(unknown)}")
    selected = [entry for entry in entries if not repos or entry["name"] in repos]

    click.echo(f"Harness: {manifest.get('name') or manifest_path.parent.name}")
    click.echo(f"Manifest: {manifest_path}")
    results = []
    failures = 0

    for entry in selected:
        name = entry["name"]
        target = entry["declared_path"]

        # `present: false` means the manifest knows about the repo and deliberately does not want
        # it here. Naming it with --repo, or asking for --all, overrides that.
        if not entry["declared_present"] and not include_absent and not repos:
            results.append((name, "skipped (present: false)"))
            continue
        if (target / ".git").is_dir():
            results.append((name, already_cloned(entry, target)))
            continue
        if entry["path"] != target and (entry["path"] / ".git").is_dir():
            results.append((name, f"skipped (already cloned at {entry['path']}, manifest says {target})"))
            continue
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            results.append((name, f"failed (path is taken and is not an empty directory: {target})"))
            failures += 1
            continue
        if not entry["origin"]:
            results.append((name, "failed (no origin declared in the manifest)"))
            failures += 1
            continue
        if dry_run:
            results.append((name, f"would clone {entry['origin']} into {target}"))
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone"]
        if depth:
            args += ["--depth", str(depth)]
        args += [entry["origin"], str(target)]
        result = git(target.parent, *args, timeout=900)
        if result.returncode != 0:
            results.append((name, f"failed ({detail_of(result, 'git clone failed')})"))
            failures += 1
            continue
        results.append((name, f"cloned into {target}{checkout_declared_branch(entry, target)}"))

    print_summary(results)
    if failures:
        raise click.exceptions.Exit(1)
