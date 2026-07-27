"""
evo serp - Google search from the terminal through SerpApi.

`evo setup serp` installs the official serpapi CLI and stores the API key in
the omelet credential store. `evo serp search` then runs queries through that
CLI when it is installed, and falls back to the SerpApi HTTPS endpoint when it
is not, so the command works on a machine where only the key is present.
"""

import getpass
import json as jsonlib
import sys
from pathlib import Path

import rich_click as click
from rich.table import Table
from rich.text import Text

from evo_cli.console import console, info, step, success, warning
from evo_cli.credentials.store import CredentialError, compile_flat, relative_to_store, set_value
from evo_cli.serp import client, creds, install, render
from evo_cli.serp.errors import SerpError

SEARCH_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo serp search 'evo cli python'[/cyan]                 top 10 Google results\n"
    "  [cyan]evo serp search coffee -l 'Austin, Texas' -n 5[/cyan]   localized, 5 results\n"
    "  [cyan]evo serp search 'openai' -t news --hl vi --gl vn[/cyan] news vertical, Vietnamese\n"
    "  [cyan]evo serp search 'site:github.com serpapi' --links[/cyan] just the URLs, pipe-friendly\n"
    "  [cyan]evo serp search 'rust async' -p 3 --json > out.json[/cyan] 3 pages of raw JSON\n"
    "  [cyan]evo serp search x -P tbs=qdr:d[/cyan]                   any extra SerpApi parameter"
)

GROUP_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo setup serp[/cyan]                    install the CLI + store the API key\n"
    "  [cyan]evo serp search 'claude code'[/cyan]     Google search\n"
    "  [cyan]evo serp account[/cyan]                  plan and searches left this month\n"
    "  [cyan]evo serp locations Hanoi[/cyan]          canonical names for `--location`\n"
    "  [cyan]evo serp doctor[/cyan]                   binary, key source, quota\n"
    "  [cyan]evo serp raw --jq '.organic_results[0]' engine=google q=coffee[/cyan]"
)

SETUP_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo setup serp[/cyan]                          install + prompt for the key (no echo)\n"
    "  [cyan]evo setup serp --from-stdin < key.txt[/cyan]   read the key from stdin\n"
    "  [cyan]evo setup serp --method binary[/cyan]          skip brew, drop the binary in ~/.evo/bin\n"
    "  [cyan]evo setup serp --method none[/cyan]            key only, use the HTTPS fallback\n"
    "  [cyan]evo setup serp --write-config[/cyan]           also let the bare `serpapi` binary auth\n\n"
    "[dim]The key is stored in the omelet credential store, never in this repo.\n"
    "Push it to your private sync repo afterwards with `evo cred sync push`.[/dim]"
)


def _guard(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except (SerpError, CredentialError) as exc:
        raise click.ClickException(str(exc)) from exc


def _store_key(key):
    path, existed = set_value(creds.CRED_KEY, key)
    count, _, target = compile_flat()
    return path, existed, count, target


def _install_from_release(cli_version):
    binary, version, url = _guard(install.install_binary, cli_version)
    info(f"downloaded {url}")
    success(f"serpapi {version} installed at [accent]{binary}[/accent]")
    return binary


def _install_cli(method, cli_version, trust_tap):
    chosen = install.pick_method(method)
    info(f"installing the serpapi CLI via [accent]{chosen}[/accent]")
    if chosen == "brew":
        try:
            return install.install_brew(trust=trust_tap)
        except install.BrewTrustRequired as exc:
            if method == "brew":
                raise click.ClickException(str(exc)) from exc
            # Same binaries either way: the formula only repackages the GitHub release.
            warning(str(exc))
            info("falling back to the GitHub release binary")
            return _install_from_release(cli_version)
        except SerpError as exc:
            raise click.ClickException(str(exc)) from exc
    if chosen == "go":
        return _guard(install.install_go)
    return _install_from_release(cli_version)


@click.group("serp", epilog=GROUP_EPILOG, context_settings={"help_option_names": ["-h", "--help"]})
def serp_group():
    """**Google search** from the terminal, powered by [SerpApi](https://serpapi.com).

    Runs through the official `serpapi` CLI when it is installed and falls back
    to the SerpApi HTTPS endpoint otherwise. The API key comes from
    `SERPAPI_KEY`, the omelet credential store (`serpapi_api_key`), or
    `~/.config/serpapi/config.toml` - in that order, and is never hardcoded.

    Install everything in one step with `evo setup serp`.
    """


@serp_group.command("search", epilog=SEARCH_EPILOG)
@click.argument("query", nargs=-1, required=True)
@click.option("-n", "--num", type=int, default=10, show_default=True, help="Results per page.")
@click.option("-p", "--pages", type=int, default=1, show_default=True, help="How many pages to fetch and merge.")
@click.option(
    "-t",
    "--type",
    "search_type",
    type=click.Choice(sorted(client.SEARCH_TYPES)),
    default="web",
    show_default=True,
    help="Google vertical to search.",
)
@click.option("-l", "--location", help="Location to search from, e.g. `Austin, Texas`. See `evo serp locations`.")
@click.option("--hl", help="Interface language, e.g. `vi`, `en`.")
@click.option("--gl", help="Country of the search, e.g. `vn`, `us`.")
@click.option("--domain", help="Google domain, e.g. `google.com.vn`.")
@click.option("--device", type=click.Choice(["desktop", "tablet", "mobile"]), help="Device to emulate.")
@click.option("--safe", type=click.Choice(["active", "off"]), help="SafeSearch setting.")
@click.option("--engine", help="Override the SerpApi engine (default `google`).")
@click.option("-P", "--param", "extra", multiple=True, metavar="KEY=VALUE", help="Any extra SerpApi parameter.")
@click.option("--fields", help="Server-side field filter (SerpApi field restrictor syntax).")
@click.option("--jq", "jq_expr", help="jq filter run by the serpapi CLI; prints its raw output.")
@click.option("--json", "as_json", is_flag=True, help="Print the raw JSON payload instead of rendering it.")
@click.option("--links", "links_only", is_flag=True, help="Print only the result URLs, one per line.")
@click.option("-o", "--out", type=click.Path(dir_okay=False), help="Also write the JSON payload to a file.")
@click.option(
    "--transport",
    type=click.Choice(["auto", "cli", "http"]),
    default="auto",
    show_default=True,
    help="`auto` uses the serpapi CLI when installed, else the HTTPS API.",
)
@click.option("--timeout", type=int, default=90, show_default=True, help="Seconds to wait per request.")
def search_cmd(
    query,
    num,
    pages,
    search_type,
    location,
    hl,
    gl,
    domain,
    device,
    safe,
    engine,
    extra,
    fields,
    jq_expr,
    as_json,
    links_only,
    out,
    transport,
    timeout,
):
    """Search Google and print the results.

    `QUERY` is taken verbatim, so operators work: `site:`, `intitle:`, quotes.
    Use `-t news|images|videos|shopping|scholar` for the other verticals and
    `-P key=value` for any SerpApi parameter this command does not wrap.
    """
    text = " ".join(query).strip()
    if not text:
        raise click.UsageError("empty query.")

    params = _guard(
        client.build_params,
        text,
        search_type=search_type,
        num=num,
        location=location,
        hl=hl,
        gl=gl,
        domain=domain,
        device=device,
        safe=safe,
        engine=engine,
        extra=extra,
    )
    key = _guard(creds.api_key)

    if jq_expr:
        # jq output is whatever the expression produces, so hand it over untouched.
        _, binary = _guard(client.resolve_transport, "cli")
        result = _guard(
            client.run_binary, binary, client.cli_argv(params, fields=fields, jq=jq_expr), key, timeout, True
        )
        if result.returncode != 0:
            raise click.ClickException((result.stderr or result.stdout or "serpapi CLI failed").strip())
        sys.stdout.write(result.stdout)
        return

    payload, result_key, used = _guard(
        client.search,
        params,
        search_type=search_type,
        pages=pages,
        transport=transport,
        key=key,
        timeout=timeout,
        fields=fields,
    )

    if out:
        Path(out).write_text(jsonlib.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if as_json:
        sys.stdout.write(jsonlib.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return

    # Google treats `num` as a hint and often returns more, so hold the rendered
    # output to what was actually asked for. `--json`/`-o` still get everything.
    limit = max(1, num) * max(1, pages)

    if links_only:
        for link in render.links(payload, result_key)[:limit]:
            sys.stdout.write(link + "\n")
        return

    console.print()
    render.render_answer_box(payload)
    render.render_knowledge_graph(payload)
    shown = render.render_results(payload, result_key, limit=limit)
    if not shown:
        warning(f"no {result_key.replace('_', ' ')} for [accent]{text}[/accent]")
        return
    render.render_related_questions(payload)
    info(render.summary_line(payload, shown, used))
    if out:
        success(f"payload written to [accent]{out}[/accent]")


@serp_group.command("account")
@click.option("--json", "as_json", is_flag=True, help="Print the raw JSON payload.")
def account_cmd(as_json):
    """Show the SerpApi plan and how many searches are left this month."""
    payload = _guard(client.account)
    if as_json:
        sys.stdout.write(jsonlib.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return
    render.render_account(payload)


@serp_group.command("locations")
@click.argument("query", nargs=-1, required=True)
@click.option("-n", "--limit", type=int, default=10, show_default=True, help="How many matches to show.")
@click.option("--json", "as_json", is_flag=True, help="Print the raw JSON payload.")
def locations_cmd(query, limit, as_json):
    """Look up canonical location names to pass to `search --location`."""
    text = " ".join(query).strip()
    entries = _guard(client.locations, text, limit)
    if as_json:
        sys.stdout.write(jsonlib.dumps(entries, indent=2, ensure_ascii=False) + "\n")
        return
    if not entries:
        warning(f"no location matches [accent]{text}[/accent]")
        return
    render.render_locations(entries, limit)


@serp_group.command(
    "raw",
    context_settings={"ignore_unknown_options": True, "help_option_names": []},
    help="Pass arguments straight to the installed `serpapi` CLI, with the key injected.",
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def raw_cmd(args):
    """Run the official CLI directly, e.g. `evo serp raw search engine=google q=coffee --all-pages`."""
    if not args:
        raise click.UsageError("pass the serpapi CLI arguments, e.g. `search engine=google q=coffee`.")
    _, binary = _guard(client.resolve_transport, "cli")
    key = _guard(creds.api_key)
    result = _guard(client.run_binary, binary, list(args), key, 300, False)
    raise click.exceptions.Exit(result.returncode)


@serp_group.command("doctor")
@click.option("--offline", is_flag=True, help="Skip the account lookup; check only the local setup.")
def doctor_cmd(offline):
    """Report the binary, the key source, and the remaining quota."""
    step("evo serp doctor")
    binary = install.find_binary()
    key, source = creds.resolve()

    table = Table(show_header=True, header_style="accent", box=None, pad_edge=False)
    for column in ("COMPONENT", "STATUS", "DETAIL"):
        table.add_column(column, overflow="fold")
    table.add_row(
        "serpapi CLI",
        "[success]ok[/success]" if binary else "[warning]missing[/warning]",
        f"{binary} ({install.binary_version(binary) or 'unknown version'})"
        if binary
        else "not installed - HTTPS fallback in use (evo setup serp)",
    )
    table.add_row(
        "API key",
        "[success]ok[/success]" if key else "[error]missing[/error]",
        f"{creds.mask(key)} from {source}" if key else "run: evo setup serp",
    )
    config = creds.config_file()
    table.add_row(
        "config.toml",
        "[success]ok[/success]" if config.is_file() else "[dim]absent[/dim]",
        str(config) if config.is_file() else f"{config} (optional: evo setup serp --write-config)",
    )
    console.print(table)

    if not key:
        raise click.exceptions.Exit(1)
    if offline:
        return

    console.print()
    payload = _guard(client.account)
    render.render_account(payload)
    left = payload.get("plan_searches_left")
    if isinstance(left, (int, float)) and left <= 0:
        warning("no searches left on this plan")
        raise click.exceptions.Exit(1)
    success("SerpApi is ready")


@click.command("serp", epilog=SETUP_EPILOG)
@click.option("--api-key", "api_key_opt", help="The key inline. Avoid: it leaks to shell history.")
@click.option("--from-stdin", is_flag=True, help="Read the key from stdin.")
@click.option(
    "--method",
    type=click.Choice(["auto", "brew", "binary", "go", "none"]),
    default="auto",
    show_default=True,
    help="How to install the CLI. `auto` prefers brew, then a release binary. `none` skips it.",
)
@click.option("--cli-version", help="Release version for `--method binary` (default: latest).")
@click.option("-f", "--force", is_flag=True, help="Reinstall the CLI even if it is already present.")
@click.option("--trust-tap", is_flag=True, help=f"Run `{install.BREW_TRUST_COMMAND}` before installing with brew.")
@click.option("--write-config", is_flag=True, help="Also write ~/.config/serpapi/config.toml for the bare binary.")
@click.option("--no-verify", is_flag=True, help="Skip the account lookup at the end.")
def setup_serp(api_key_opt, from_stdin, method, cli_version, force, trust_tap, write_config, no_verify):
    """Install the **SerpApi** CLI and store the API key.

    Installs the official `serpapi` binary (brew, GitHub release, or `go
    install`), saves the key into the omelet credential store as
    `serpapi_api_key`, and verifies it against your SerpApi account. Nothing is
    written into this repository.
    """
    step("evo setup serp")

    if method != "none":
        binary = install.find_binary()
        if binary and not force:
            info(f"serpapi CLI already installed: [accent]{binary}[/accent] ({install.binary_version(binary) or '?'})")
        else:
            binary = _install_cli(method, cli_version, trust_tap)
            hint = install.path_hint()
            if hint and str(binary).startswith(str(install.bin_dir())):
                warning(f'{hint} is not on PATH - add it: export PATH="{hint}:$PATH"')
        if binary:
            success(f"serpapi CLI: [accent]{binary}[/accent] ({install.binary_version(binary) or 'unknown version'})")
    else:
        info("skipping the CLI install (--method none): searches will use the HTTPS API")

    step("API key")
    existing, source = creds.resolve()
    if from_stdin:
        key = sys.stdin.read().strip()
    elif api_key_opt:
        key = api_key_opt.strip()
    elif existing and not force:
        key = existing
        info(f"reusing the key already available from [accent]{source}[/accent] ({creds.mask(key)})")
    else:
        console.print("Get a key at [accent]https://serpapi.com/manage-api-key[/accent]")
        key = getpass.getpass("SerpApi API key (no echo): ").strip()
    if not key:
        raise click.ClickException("empty API key, aborting")

    if key != existing or source != f"evo cred {creds.CRED_KEY}":
        path, existed, count, target = _guard(_store_key, key)
        verb = "updated" if existed else "stored"
        success(f"{verb} {creds.CRED_KEY} in {relative_to_store(path)}, recompiled {target} ({count} entries)")

    if write_config:
        path = creds.write_config_file(key)
        success(f"wrote {path} (mode 600) so the bare `serpapi` binary can authenticate")

    if no_verify:
        return

    step("Verify")
    payload = _guard(client.account)
    render.render_account(payload)
    console.print()
    success("SerpApi is ready: [accent]evo serp search 'hello world'[/accent]")
    info("push the key to your private sync repo with: [accent]evo cred sync push[/accent]")
