import shutil

import rich_click as click

from evo_cli.commands.harness._paths import find_manifest, git, harness_option, load_repos


@click.command("pull", help="Fast-forward all available repositories in a harness.")
@harness_option
@click.option("--repo", "repos", multiple=True, help="Pull only this repo name. Repeat for multiple repos.")
@click.option("--prune/--no-prune", default=True, show_default=True, help="Prune deleted remote refs while pulling.")
@click.option("--dry-run", is_flag=True, help="Show repositories that would be pulled.")
def pull(harness_path, repos, prune, dry_run):
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
    incomplete = 0

    for entry in selected:
        name = entry["name"]
        path = entry["path"]
        if not entry["declared_present"]:
            results.append((name, "skipped (present: false)"))
            continue
        if not path.is_dir():
            results.append((name, f"failed (missing: {path})"))
            incomplete += 1
            continue

        status = git(path, "status", "--porcelain")
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
        update = git(path, *args, timeout=180)
        if update.returncode != 0:
            detail = (update.stderr or update.stdout).strip().splitlines()
            results.append((name, f"failed ({detail[-1] if detail else 'git pull failed'})"))
            incomplete += 1
            continue
        head = git(path, "log", "-1", "--oneline")
        results.append((name, head.stdout.strip() or "updated"))

    click.echo()
    click.secho("Summary", bold=True)
    width = max((len(name) for name, _ in results), default=0)
    for name, status in results:
        color = "red" if status.startswith("failed") else "yellow" if status.startswith("skipped") else "green"
        click.secho(f"  {name:<{width}}  {status}", fg=color)
    if incomplete:
        raise click.exceptions.Exit(1)
