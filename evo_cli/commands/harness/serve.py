from __future__ import annotations

import threading
import webbrowser

import rich_click as click

from evo_cli.commands.harness._model import cluster, load_plans, load_seams
from evo_cli.commands.harness._paths import find_manifest, harness_option
from evo_cli.commands.harness._server import MISSING_BUNDLE, build_server, bundle_ready
from evo_cli.console import console


@click.command("serve", help="Dashboard on localhost: cluster, contract seams and exec-plans as DAGs.")
@harness_option
@click.option("--port", default=8788, show_default=True, type=int)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--open/--no-open", "open_browser", default=True, help="Open a browser once the server is up.")
def serve(harness_path, port, host, open_browser):
    """Inspect the harness and complete plans from the UI. Edit item statuses with
    `evo harness step|debt|question|repo`; the dashboard follows changes within seconds."""
    manifest_path = find_manifest(harness_path)
    if not bundle_ready():
        raise click.ClickException(MISSING_BUNDLE)

    info = cluster(manifest_path)
    plans = load_plans(manifest_path)
    seams = load_seams(manifest_path)
    server = build_server(manifest_path, host, port)
    url = f"http://{host}:{server.server_port}"

    console.print(f"[accent]{info['name']}[/] harness -> [bold]{url}[/]")
    console.print(
        f"[dim]{len(info['repos'])} repos, {len(seams)} seams, {len(plans)} plans from {info['root']}"
        f"  |  plan completion enabled; edit items with `evo harness step ...`[/]"
    )
    console.print("[dim]Ctrl+C to stop.[/]")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/]")
    finally:
        server.server_close()
