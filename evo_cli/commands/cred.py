import getpass
import json
import os
import sys
import webbrowser
from pathlib import Path

import rich_click as click
from rich.table import Table

from evo_cli.console import console, info, success, warning
from evo_cli.credentials import doctor as doctor_module
from evo_cli.credentials import google_oauth, migrate, oauth_flow, sync
from evo_cli.credentials.registry import config_path, credentials_dir
from evo_cli.credentials.store import (
    CredentialError,
    compile_flat,
    get_value,
    read_flat,
    relative_to_store,
    require_folder,
    set_value,
)

HEALTH_STYLE = {
    "ok": "green",
    "expiring": "yellow",
    "EXPIRED": "bold red",
    "deprecated": "dim",
}


def _guard(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except CredentialError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _render(rows):
    table = Table(show_header=True, header_style="accent", box=None, pad_edge=False)
    for column in ("FILE", "SERVICE", "TYPE", "HEALTH", "SECRET", "EXPIRY"):
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(
            row["file"],
            row["service"],
            row["type"],
            f"[{HEALTH_STYLE.get(row['health'], 'white')}]{row['health']}[/]",
            row["secret"],
            row["expiry"],
        )
    console.print(table)


@click.group("cred", help="Read, refresh, and sync the omelet credential store.")
def cred_group():
    pass


@cred_group.command("get", help="Print one credential by dotted key path.")
@click.argument("key_path")
@click.option("--export", "export_var", metavar="VAR", help="Print a shell export statement instead of the raw value.")
def get(key_path, export_var):
    value = _guard(get_value, key_path)

    if isinstance(value, (dict, list)):
        sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
        return

    text = str(value)
    if export_var:
        escaped = text.replace("'", "'\\''")
        sys.stdout.write(f"export {export_var}='{escaped}'\n")
        return

    sys.stdout.write(text)
    if sys.stdout.isatty():
        sys.stdout.write("\n")


@cred_group.command("list", help="List every credential in the store with its health.")
def list_credentials():
    _guard(require_folder)
    rows = _guard(doctor_module.scan)
    _render(rows)
    console.print(f"\n{len(rows)} entries in {credentials_dir()}")


@cred_group.command("doctor", help="Report health and expiry; exit 1 if anything is expired.")
def doctor():
    _guard(require_folder)
    rows = _guard(doctor_module.scan)
    _render(rows)
    expired = [row for row in rows if row["health"] == "EXPIRED"]
    console.print(f"\n{len(rows)} entries in {credentials_dir()}")
    if expired:
        warning(f"{len(expired)} EXPIRED - run: evo cred refresh --all")
        raise click.exceptions.Exit(1)
    success("all credentials healthy")


@cred_group.command("compile", help="Rebuild the flat config from the credentials folder.")
@click.option("--include-legacy", is_flag=True, help="Also merge entries marked deprecated.")
def compile_command(include_legacy):
    count, skipped, target = _guard(compile_flat, include_legacy=include_legacy)
    message = f"compiled {count} entries -> {target}"
    if skipped:
        message += f" (skipped {len(skipped)} deprecated: {', '.join(skipped)})"
    success(message)


@cred_group.command("add", help="Add or update a credential, then recompile the flat config.")
@click.argument("key_path")
@click.option("--from-stdin", is_flag=True, help="Read the value from stdin.")
@click.option("--from-env", metavar="VAR", help="Read the value from an environment variable.")
@click.option("--value", "inline_value", help="Inline value. Avoid: leaks to shell history.")
@click.option("--json", "parse_json", is_flag=True, help="Parse the value as JSON (bool/number/object/array).")
def add(key_path, from_stdin, from_env, inline_value, parse_json):
    sources = [bool(from_stdin), bool(from_env), inline_value is not None]
    if sum(sources) > 1:
        raise click.UsageError("Use only one of --from-stdin, --from-env, --value.")

    if from_stdin:
        value = sys.stdin.read().rstrip("\n")
    elif from_env:
        if from_env not in os.environ:
            raise click.ClickException(f"env var {from_env} not set")
        value = os.environ[from_env]
    elif inline_value is not None:
        value = inline_value
    else:
        value = getpass.getpass(f"value for {key_path} (no echo): ")
        if not value:
            raise click.ClickException("empty value, aborting")

    if parse_json:
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise click.ClickException(f"--json: failed to parse value: {exc}") from exc

    path, existed = _guard(set_value, key_path, value)
    count, _, target = _guard(compile_flat)
    verb = "updated" if existed else "added"
    success(f"{verb} {key_path} in {relative_to_store(path)}, recompiled {target} ({count} entries)")


@cred_group.command("refresh", help="Refresh Google OAuth access tokens and recompile.")
@click.option("--all", "refresh_all", is_flag=True, help="Refresh every refreshable OAuth entry.")
@click.option(
    "--service",
    type=click.Choice(sorted(google_oauth.SERVICE_ALIASES)),
    help="Refresh a single service.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be refreshed without calling Google.")
def refresh(refresh_all, service, dry_run):
    if refresh_all == bool(service):
        raise click.UsageError("Pass exactly one of --all or --service.")

    _guard(require_folder)
    target = "*" if refresh_all else google_oauth.SERVICE_ALIASES[service]
    entries = _guard(google_oauth.select_entries, target)
    if not entries:
        raise click.ClickException("no matching refreshable oauth entries")

    failures = 0
    refreshed = 0
    for path, entry in entries:
        name = entry.get("id", path.name)
        creds, err = google_oauth.resolve_creds(entry)
        if err:
            warning(f"{name}: skip ({err})")
            failures += 1
            continue

        if dry_run:
            info(
                f"{name}: would POST {google_oauth.TOKEN_URL} "
                f"refresh_token={creds['refresh_token'][:10]}... client_id={creds['client_id'][:12]}..."
            )
            continue

        try:
            expiry = google_oauth.refresh_entry(path, entry, creds)
        except Exception as exc:
            warning(f"{name}: {google_oauth.describe_error(exc)}")
            failures += 1
            continue

        refreshed += 1
        success(f"{name}: refreshed, valid until {expiry}")

    if refreshed:
        count, _, target_path = _guard(compile_flat)
        info(f"recompiled {target_path} ({count} entries)")

    if failures:
        raise click.exceptions.Exit(1)


@cred_group.command("auth", help="Run the first-time Google OAuth consent flow and store the refresh token.")
@click.option(
    "--service",
    required=True,
    type=click.Choice(sorted(google_oauth.SERVICE_ALIASES)),
    help="Which service to authorise.",
)
@click.option(
    "--client-secrets",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="OAuth client JSON downloaded from the Cloud Console. Omit to reuse the stored client.",
)
@click.option("--scope", "scopes", multiple=True, help="Override the scopes. Repeat for several.")
@click.option("--no-browser", is_flag=True, help="Print the URL instead of opening a browser.")
def auth(service, client_secrets, scopes, no_browser):
    target = google_oauth.SERVICE_ALIASES[service]
    _guard(require_folder)

    entries = _guard(google_oauth.select_entries, target)
    if not entries:
        raise click.ClickException(f"no oauth entry for '{service}' in the store")
    path, entry = entries[0]

    resolved_scopes = list(scopes) or (entry.get("oauth") or {}).get("scopes")
    if not resolved_scopes:
        raise click.ClickException(
            f"no scopes known for '{service}'. Pass --scope, or add them to the oauth descriptor "
            f"in evo_cli/credentials/registry.py."
        )

    if client_secrets:
        client_id, client_secret = _guard(oauth_flow.client_from_secrets_file, client_secrets)
    else:
        client_id, client_secret = oauth_flow.client_from_entry(entry)
    if not client_id or not client_secret:
        raise click.ClickException(
            "no OAuth client available. Create one in the Cloud Console "
            "(APIs & Services -> Credentials -> OAuth client ID -> Desktop app), download the JSON, "
            "and pass it with --client-secrets."
        )

    server, redirect_uri, thread = oauth_flow.capture_code()
    state = oauth_flow.new_state()
    url = oauth_flow.build_auth_url(client_id, redirect_uri, resolved_scopes, state)

    info(f"scopes: {', '.join(resolved_scopes)}")
    info(f"listening on {redirect_uri}")
    if no_browser:
        console.print(f"\nOpen this URL to authorise:\n\n{url}\n")
    else:
        console.print("\nOpening your browser to authorise. Approve the consent screen.\n")
        webbrowser.open(url)

    thread.start()
    thread.join(timeout=300)
    result = oauth_flow._Handler.result
    server.server_close()

    if result is None:
        raise click.ClickException("timed out waiting for the consent redirect")
    if result.get("error"):
        raise click.ClickException(f"Google returned an error: {result['error']}")
    if result.get("state") != state:
        raise click.ClickException("state mismatch on the redirect; aborting rather than trusting it")

    body = _guard(oauth_flow.exchange_code, client_id, client_secret, result["code"], redirect_uri)

    oauth = entry.get("oauth") or {}
    container_path = oauth.get("client_from") or oauth.get("container") or []
    if container_path:
        cursor = entry.setdefault("flat", {})
        for part in container_path[:-1]:
            cursor = cursor.setdefault(part, {})
        leaf = cursor.setdefault(container_path[-1], {})
        leaf["client_id"] = client_id
        leaf["client_secret"] = client_secret

    expiry = _guard(oauth_flow.store_tokens, path, entry, body, resolved_scopes)
    count, _, target_path = _guard(compile_flat)
    success(f"{target}: authorised, valid until {expiry}")
    success(f"compiled {count} entries -> {target_path}")


@cred_group.command("migrate", help="Split a flat omelet.json into the per-service credentials folder.")
@click.option("--source", "source", type=click.Path(), help="Flat file to migrate. Defaults to the compiled config.")
@click.option("--dry-run", is_flag=True, help="Show the plan, write nothing.")
@click.option("--merge", is_flag=True, help="Migrate into an existing folder, adding only keys not already present.")
@click.option("--strict", is_flag=True, help="Abort on keys not mapped in the registry instead of routing to misc/.")
@click.option("--force", is_flag=True, help="Overwrite an existing non-empty folder.")
def migrate_command(source, dry_run, merge, strict, force):
    result = _guard(migrate.plan, source or config_path(), strict=strict, merge=merge)

    console.print(f"source: {result['source_path']} ({result['source_keys']} top-level keys)")
    console.print(f"target: {credentials_dir()}{'  [MERGE]' if merge else ''}\n")

    for item in result["actions"]:
        flags = " [unmapped->misc]" if item["misc"] else ""
        if item["entry"].get("status") == "deprecated":
            flags += " [DEPRECATED]"
        if item["entry"].get("expiry"):
            flags += f" expiry={item['entry']['expiry']}"
        console.print(f"  {item['action']:6s} {item['rel']:40s} <- {', '.join(item['keys'])}{flags}")

    if result["unmapped"]:
        warning(f"{len(result['unmapped'])} unmapped key(s) routed to misc/: {', '.join(result['unmapped'])}")

    if dry_run:
        info(f"dry-run: would write {len(result['actions'])} file(s)")
        return

    if not result["actions"]:
        info("nothing to do (folder already up to date)")
        return

    if migrate.folder_has_files() and not (merge or force):
        raise click.ClickException(
            f"refusing: {credentials_dir()} already has files.\n"
            "Use --merge (add missing keys) or --force (overwrite), or point OMELET_DIR elsewhere."
        )

    backup = _guard(migrate.apply, result)
    info(f"backed up source -> {backup}")
    success(f"wrote {len(result['actions'])} file(s) to {credentials_dir()}")

    count, _, target_path = _guard(compile_flat)
    success(f"compiled {count} entries -> {target_path}")


@cred_group.group("sync", help="Sync the credentials folder with a private GitHub repo.")
def sync_group():
    pass


@sync_group.command("push", help="Push the local credentials folder to the sync repo.")
def sync_push():
    host = _guard(sync.push)
    if host is None:
        info("no changes to push")
        return
    success(f"pushed {sync.dir_in_repo()}/ to {sync.sync_repo()} (from {host})")


@sync_group.command("pull", help="Pull the credentials folder from the sync repo, then recompile.")
def sync_pull():
    backup = _guard(sync.pull)
    if backup:
        info(f"backed up existing folder -> {backup}")
    success(f"pulled {sync.sync_repo()}:{sync.dir_in_repo()}/ -> {credentials_dir()}")
    count, _, target_path = _guard(compile_flat)
    success(f"compiled {count} entries -> {target_path}")


@cred_group.command("path", help="Print the store locations this machine resolves to.")
def show_path():
    console.print(f"folder: {credentials_dir()}")
    console.print(f"flat:   {config_path()}")
    try:
        keys = [key for key in read_flat() if not key.startswith("_")]
    except CredentialError:
        keys = []
    console.print(f"keys:   {len(keys)}")
