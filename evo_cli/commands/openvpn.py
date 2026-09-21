import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import rich_click as click
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from evo_cli import openvpn as vpn
from evo_cli.commands.cred import _guard
from evo_cli.console import console

DOWN = ("STOPPED", "FAILED", "UNKNOWN")
# Anything else (WAIT, AUTH, GET_CONFIG, RECONNECTING...) is OpenVPN still working on it.
COLORS = {"CONNECTED": "green", "STOPPING": "dim", "STOPPED": "dim", "FAILED": "red", "UNKNOWN": "red"}


def _interactive():
    return sys.stdin.isatty()


def _state_text(state):
    return Text(state, style=COLORS.get(state, "yellow"))


def _table(rows):
    table = Table(header_style="accent", box=None, pad_edge=False)
    for column in ("#", "NAME", "STATE", "TUNNEL IP", "REMOTE", "CREDS", "OTP"):
        table.add_column(column)
    for index, (name, profile, state) in enumerate(rows, 1):
        table.add_row(
            str(index),
            name,
            _state_text(state["state"]),
            state.get("vpn_ip", ""),
            (profile.get("remotes") or [""])[0],
            "yes" if profile.get("password") else "no",
            "yes" if profile.get("totp") else "no",
        )
    return table


def _rows():
    profiles = _guard(vpn.profiles)
    return [(name, profiles[name], _guard(vpn.request, name)) for name in sorted(profiles)]


def _pick(name, prefer_active=False):
    """Resolve NAME or its # from `evo openvpn list`; when omitted, auto-select or prompt."""
    names = sorted(_guard(vpn.profiles))
    if name:
        if name.isdigit() and name not in names:
            if not 1 <= int(name) <= len(names):
                raise click.ClickException(f"No profile #{name}; run evo openvpn list.")
            name = names[int(name) - 1]
        _guard(vpn.get_profile, name)
        return name
    if not names:
        raise click.ClickException("No profiles yet; run evo openvpn import NAME FILE.")
    rows = _rows()
    active = [row[0] for row in rows if row[2]["state"] != "STOPPED"]
    if prefer_active and len(active) == 1:
        name = active[0]
    elif len(names) == 1:
        name = names[0]
    elif not _interactive():
        raise click.ClickException("Several profiles; pass NAME or its # from evo openvpn list.")
    else:
        console.print(_table(rows))
        last = _guard(vpn.last_profile)
        default = names.index(last) + 1 if last in names else 1
        return names[click.prompt("Profile #", type=click.IntRange(1, len(names)), default=default) - 1]
    click.echo(f"Profile: {name}", err=True)
    return name


def _duration(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}"


def _size(count):
    for unit in ("B", "KB", "MB"):
        if count < 1024:
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024
    return f"{count:.2f} GB"


def _panel(name, state, remote):
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim")
    grid.add_column()
    grid.add_row("State", _state_text(state["state"]))
    if state.get("vpn_ip"):
        grid.add_row("Tunnel IP", state["vpn_ip"])
    if remote:
        grid.add_row("Remote", remote)
    if state.get("since"):
        grid.add_row("Uptime", _duration(time.time() - state["since"]))
    if "bytes_in" in state:
        grid.add_row("Traffic", f"down {_size(state['bytes_in'])}  up {_size(state['bytes_out'])}")
    if state.get("reconnects"):
        grid.add_row("Reconnects", Text(f"{state['reconnects']} (last: {state.get('last_reason', '?')})"))
    if state.get("error"):
        grid.add_row("Error", Text(state["error"], style="red"))
    return Panel(grid, title=f"[accent]{name}[/accent]", subtitle="[dim]Ctrl-C to stop watching[/dim]", expand=False)


def _notify(message):
    """Ring the bell and raise a desktop notification, so a drop is noticed with the terminal hidden."""
    console.bell()
    if sys.platform == "darwin" and shutil.which("osascript"):
        script = ["-e", "on run argv", "-e", 'display notification (item 1 of argv) with title "evo openvpn"']
        command = ["osascript", *script, "-e", "end run", message]
    elif shutil.which("notify-send"):
        command = ["notify-send", "evo openvpn", message]
    else:
        return
    try:
        subprocess.run(command, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _event(out, name, state, previous):
    status = state["state"]
    line = Text(f"{datetime.now():%H:%M:%S}  ")
    line.append(status, style=COLORS.get(status, "yellow"))
    detail = state.get("vpn_ip") or (state.get("last_reason") if status == "RECONNECTING" else state.get("error"))
    if detail:
        line.append(f"  {detail}")
    out.print(line)
    if status == "RECONNECTING":
        _notify(f"{name} dropped ({state.get('last_reason', 'unknown')}); reconnecting")
    elif status == "CONNECTED" and previous != "CONNECTED" and state.get("reconnects"):
        _notify(f"{name} reconnected")


def _watch(name):
    """Live status until the tunnel goes down (exit 1) or Ctrl-C (offer to disconnect)."""
    remote = (_guard(vpn.get_profile, name).get("remotes") or [""])[0]
    state, previous, failures = {}, None, 0
    try:
        with Live(console=console, auto_refresh=False) as live:
            while True:
                try:
                    state, failures = vpn.request(name), 0
                except (OSError, ValueError):
                    failures += 1
                    if failures < 5:
                        time.sleep(0.5)
                        continue
                    state = {"profile": name, "state": "UNKNOWN", "error": "Worker is not responding."}
                if previous and (state["state"], state.get("vpn_ip")) != previous:
                    _event(live.console, name, state, previous[0])
                previous = (state["state"], state.get("vpn_ip"))
                live.update(_panel(name, state, remote), refresh=True)
                if state["state"] in DOWN:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        console.print()
        if _interactive() and click.confirm(f"Disconnect {name}?", default=False):
            _disconnect(name)
        else:
            console.print(f"[dim]{name} keeps running in the background.[/dim]")
            console.print(f"[dim]Watch: evo openvpn status {name} -w | Stop: evo openvpn disconnect {name}[/dim]")
        return
    if state["state"] == "STOPPED" and not state.get("error"):
        click.echo(f"{name}: stopped.")
        return
    message = state.get("error") or "tunnel is down."
    _notify(f"{name} disconnected: {message}")
    raise click.ClickException(f"{name} disconnected: {message}")


@click.group("openvpn", help="Manage named OpenVPN profiles with credentials and TOTP in evo cred (macOS/Linux).")
def openvpn_group():
    pass


@openvpn_group.command("list", help="List managed profiles, or inventory the OpenVPN Connect app without changing it.")
@click.option("--app", is_flag=True, help="Read the existing profiles in OpenVPN Connect (macOS).")
def list_profiles(app):
    if app:
        click.echo(json.dumps(_guard(vpn.app_profiles), indent=2))
        return
    rows = _rows()
    if not rows:
        click.echo("No profiles yet; run evo openvpn import NAME FILE.")
        return
    console.print(_table(rows))
    console.print("[dim]Use a NAME or # in connect/status/disconnect, or omit it to pick interactively.[/dim]")


@openvpn_group.command("import", help="Import a trusted self-contained .ovpn (or .ovpn.txt) into evo cred.")
@click.argument("name")
@click.argument("config", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--replace", is_flag=True, help="Explicitly replace only this profile's config; preserve its credentials."
)
def import_profile(name, config, replace):
    _guard(vpn.profile_name, name)
    existing = _guard(vpn.profiles).get(name, {})
    if existing and not replace:
        raise click.ClickException("Profile already exists; use --replace to update only its configuration.")
    if _guard(vpn.request, name)["state"] != "STOPPED":
        raise click.ClickException("Disconnect this profile before replacing its configuration.")
    text = config.read_text(encoding="utf-8-sig")
    remotes = _guard(vpn.validate_config, text)
    _guard(vpn.save_profile, name, {**existing, "config": text, "remotes": remotes})
    click.echo(f"Imported {name} into evo cred: openvpn.profiles.{name}")


@openvpn_group.command("credentials", help="Save username/password and optionally a TOTP enrollment; input is hidden.")
@click.argument("name", required=False)
@click.option("--username", help="VPN account name; password is prompted, never passed as an argument.")
@click.option("--password-stdin", is_flag=True, help="Read VPN password from stdin rather than the hidden prompt.")
@click.option("--otp", is_flag=True, help="Prompt for a base32 secret or otpauth://totp URI (not a one-time code).")
@click.option(
    "--qr", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Read OTP from a local QR image."
)
def credentials(name, username, password_stdin, otp, qr):
    if otp and qr:
        raise click.UsageError("Use either --otp or --qr, not both.")
    name = _pick(name)
    profile = _guard(vpn.get_profile, name)
    username = username or click.prompt("Username", default=profile.get("username", ""))
    password = sys.stdin.readline().rstrip("\n") if password_stdin else getpass.getpass("VPN password (hidden): ")
    if not password:
        raise click.ClickException("Empty password; nothing saved.")
    _guard(vpn.quote, username)
    _guard(vpn.quote, password)
    profile.update(username=username, password=password)
    if qr:
        profile["totp"] = _guard(vpn.qr_totp, qr)
    elif otp:
        profile["totp"] = _guard(vpn.parse_totp, getpass.getpass("OTP enrollment secret/URI (hidden): "))
    _guard(vpn.save_profile, name, profile)
    click.echo(f"Saved credentials for {name}. Sync explicitly with: evo cred sync push")


@openvpn_group.command("otp", help="Print the current OTP for one explicitly selected profile (sensitive output).")
@click.argument("name", required=False)
def otp_code(name):
    name = _pick(name)
    settings = _guard(vpn.get_profile, name).get("totp")
    if not settings:
        raise click.ClickException("No OTP enrollment for this profile.")
    click.echo(_guard(vpn.totp, settings))


@openvpn_group.command("status", help="Query the live worker; stale status files never count as connected.")
@click.argument("name", required=False)
@click.option(
    "-w", "--watch", is_flag=True, help="Live view until Ctrl-C; alerts on drops, exits 1 if the VPN goes down."
)
def status(name, watch):
    name = _pick(name, prefer_active=True)
    if watch:
        _watch(name)
        return
    click.echo(json.dumps(_guard(vpn.request, name), indent=2))


@openvpn_group.command("disconnect", help="Disconnect only the named evo-managed connection, not the GUI profiles.")
@click.argument("name", required=False)
def disconnect(name):
    _disconnect(_pick(name, prefer_active=True))


def _disconnect(name):
    state = _guard(vpn.request, name, "disconnect")
    deadline = time.monotonic() + 20
    while state["state"] != "STOPPED" and time.monotonic() < deadline:
        time.sleep(0.2)
        state = _guard(vpn.request, name)
    if state["state"] != "STOPPED":
        raise click.ClickException("Disconnect not confirmed; inspect evo openvpn status.")
    click.echo(f"{name}: stopped (OpenVPN Connect profiles unchanged).")


@openvpn_group.command(
    "connect", help="Connect in the background (OTP-aware worker), then show live status; Ctrl-C leaves it running."
)
@click.argument("name", required=False)
@click.option("--timeout", type=click.IntRange(10, 300), default=60, show_default=True)
@click.option("--sudo-password-stdin", is_flag=True, help="Read sudo password from stdin instead of a hidden prompt.")
@click.option(
    "--watch/--no-watch", default=None, help="Show live status after connecting [default: when output is a terminal]."
)
def connect(name, timeout, sudo_password_stdin, watch):
    if os.name != "posix":
        raise click.ClickException("Managed connections currently require macOS or Linux.")
    name = _pick(name)
    profile = _guard(vpn.get_profile, name)
    _guard(vpn.validate_config, profile["config"])
    if not shutil.which("openvpn"):
        raise click.ClickException("Install OpenVPN first (macOS: brew install openvpn; Linux: your package manager).")
    if not profile.get("username") or not profile.get("password"):
        raise click.ClickException("Missing credentials; run evo openvpn credentials PROFILE.")
    if _guard(vpn.request, name)["state"] != "STOPPED":
        raise click.ClickException("This profile already has a worker; use status or disconnect first.")
    # Avoid unintentionally stacking two route sets. The external GUI is never controlled here.
    for other in _guard(vpn.profiles):
        if other != name and _guard(vpn.request, other)["state"] != "STOPPED":
            raise click.ClickException(f"Disconnect '{other}' first to avoid conflicting VPN routes.")
    password = ""
    if os.geteuid() != 0:
        if sudo_password_stdin:
            password = sys.stdin.readline().rstrip("\n")
        elif subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
            password = getpass.getpass("sudo password (not stored): ")
    state_path = Path(str(_guard(vpn.session_path, name)) + ".json")
    state_path.unlink(missing_ok=True)
    process = subprocess.Popen(
        [sys.executable, "-m", "evo_cli.openvpn", name, str(timeout)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    process.stdin.write((password + "\n").encode())
    process.stdin.close()
    password = ""
    click.echo(f"Connecting {name}; disconnect any external GUI VPN first. System DNS is not modified.")
    deadline = time.monotonic() + timeout + 20
    try:
        while time.monotonic() < deadline:
            state = _guard(vpn.request, name)
            if state["state"] == "CONNECTED":
                click.echo(f"{name}: connected, tunnel IP {state.get('vpn_ip', 'unknown')}")
                break
            if process.poll() is not None:
                if state_path.exists():
                    last = json.loads(state_path.read_text())
                    raise click.ClickException(last.get("error", "OpenVPN stopped before connecting."))
                raise click.ClickException("OpenVPN worker exited before startup.")
            time.sleep(0.2)
    finally:
        if _guard(vpn.request, name)["state"] != "CONNECTED":
            _guard(vpn.request, name, "disconnect")
    if state["state"] != "CONNECTED":
        raise click.ClickException("Connection not confirmed before timeout; disconnect requested.")
    _guard(vpn.remember, name)
    if sys.stdout.isatty() if watch is None else watch:
        _watch(name)
