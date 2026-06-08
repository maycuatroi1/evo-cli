import getpass
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import rich_click as click
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from evo_cli.console import (
    CommandError,
    console,
    download_file,
    error,
    info,
    run_command,
    step,
    success,
    warning,
)

CLOUDFLARED_RELEASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"

# Linux (systemd) layout - config and credentials live under /etc, owned by root.
ETC_DIR = Path("/etc/cloudflared")
SERVICE_UNIT = Path("/etc/systemd/system/cloudflared.service")
ROOT_PATH_DIRS = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")

# macOS (launchd) layout - cloudflared installs a per-user LaunchAgent that reads
# the config from the user's ~/.cloudflared. No root/sudo involved.
MAC_LABEL = "com.cloudflare.cloudflared"
MAC_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"

IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo cfssh -H dev.example.com[/cyan]                 # SSH into this machine\n"
    "  [cyan]evo cfssh -H box.example.com -n my-box -P 2222[/cyan]\n"
    "  [cyan]evo cfssh -H app.example.com --http 3000[/cyan]     # expose a local web app\n"
    "  [cyan]evo cfssh -H dev.example.com --no-service[/cyan]    # configure only, run manually"
)


def sudo_prefix():
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    return [] if is_root else ["sudo"]


def _has(binary):
    return shutil.which(binary) is not None


def _deb_arch():
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "armhf",
        "armv6l": "arm",
    }
    return mapping.get(platform.machine().lower(), "amd64")


def cloudflared_dir():
    return Path.home() / ".cloudflared"


def server_config_dir():
    """Where the running service reads config.yml from on this OS."""
    return cloudflared_dir() if IS_MACOS else ETC_DIR


def server_config_file():
    return server_config_dir() / "config.yml"


def _port_listening(port):
    """True if something is accepting TCP connections on localhost:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def build_config_yaml(tunnel_id, hostname, service, credentials_path):
    return (
        f"tunnel: {tunnel_id}\n"
        f"credentials-file: {credentials_path}\n"
        f"\n"
        f"ingress:\n"
        f"  - hostname: {hostname}\n"
        f"    service: {service}\n"
        f"  - service: http_status:404\n"
    )


def install_cloudflared():
    if _has("cloudflared"):
        version = subprocess.run(["cloudflared", "--version"], capture_output=True, text=True).stdout.strip()
        info(f"cloudflared already installed: [accent]{version}[/accent]")
        return True

    if IS_MACOS:
        if not _has("brew"):
            error("cloudflared not found and Homebrew is unavailable.")
            info(
                "Install it manually: https://github.com/cloudflare/cloudflared/releases (or `brew install cloudflared`)."
            )
            return False
        run_command(["brew", "install", "cloudflared"], check=False, status="Installing cloudflared (brew)")
        if not _has("cloudflared"):
            error("cloudflared installation failed.")
            return False
        success("cloudflared installed.")
        return True

    arch = _deb_arch()
    url = f"{CLOUDFLARED_RELEASE}/cloudflared-linux-{arch}.deb"
    with tempfile.TemporaryDirectory() as tmp:
        deb_path = os.path.join(tmp, "cloudflared.deb")
        try:
            download_file(url, deb_path, f"cloudflared ({arch})")
        except Exception as exc:
            error(f"Failed to download cloudflared: {exc}")
            return False
        install = run_command(sudo_prefix() + ["dpkg", "-i", deb_path], check=False, status="Installing cloudflared")
        if install.returncode != 0:
            run_command(
                sudo_prefix() + ["apt-get", "install", "-f", "-y"],
                check=False,
                status="Resolving dependencies",
            )

    if not _has("cloudflared"):
        error("cloudflared installation failed.")
        return False
    success("cloudflared installed.")
    return True


def ensure_login():
    cert = cloudflared_dir() / "cert.pem"
    if cert.exists():
        info(f"Cloudflare login already done ([accent]{cert}[/accent]).")
        return True
    info("Opening Cloudflare login. Pick the domain you want to use in the browser.")
    run_command(["cloudflared", "tunnel", "login"], check=False)
    if not cert.exists():
        error("Login did not produce cert.pem. Aborting.")
        return False
    success("Cloudflare login complete.")
    return True


def list_tunnels():
    result = subprocess.run(
        ["cloudflared", "tunnel", "list", "--output", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []


def find_tunnel(name):
    for tunnel in list_tunnels():
        if tunnel.get("name") == name:
            return tunnel.get("id")
    return None


def read_local_config():
    config_file = server_config_file()
    if not config_file.exists():
        return None
    try:
        text = config_file.read_text()
    except OSError:
        return None
    tunnel_id = None
    routes = []  # list of (hostname, service)
    current_host = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("tunnel:"):
            tunnel_id = line.split(":", 1)[1].strip()
        elif line.startswith("- hostname:") or line.startswith("hostname:"):
            current_host = line.split(":", 1)[1].strip()
        elif line.startswith("service:"):
            if current_host:
                routes.append((current_host, line.split(":", 1)[1].strip()))
            current_host = None
    if not tunnel_id:
        return None
    return {"config_file": config_file, "tunnel_id": tunnel_id, "routes": routes}


def cloudflared_service_state():
    if IS_MACOS:
        return mac_service_state() if MAC_PLIST.exists() else None
    if not SERVICE_UNIT.exists():
        return None
    try:
        result = subprocess.run(["systemctl", "is-active", "cloudflared"], capture_output=True, text=True)
    except OSError:
        return "unknown"
    return result.stdout.strip() or "unknown"


def mac_service_state():
    """Read the launchd state of the cloudflared user agent."""
    if not MAC_PLIST.exists():
        return None
    try:
        result = subprocess.run(["launchctl", "list", MAC_LABEL], capture_output=True, text=True)
    except OSError:
        return "unknown"
    if result.returncode != 0:
        return "stopped"
    return "running" if '"PID"' in result.stdout else "loaded"


def detect_local_tunnel():
    config = read_local_config()
    if not config:
        return None
    name = None
    if _has("cloudflared"):
        for tunnel in list_tunnels():
            if tunnel.get("id") == config["tunnel_id"]:
                name = tunnel.get("name")
                break
    config["name"] = name
    config["service"] = cloudflared_service_state()
    return config


def show_local_tunnel(existing):
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("config", str(existing["config_file"]))
    name = existing["name"] or "[dim]not found in account[/dim]"
    table.add_row("tunnel", f"[accent]{name}[/accent] ({existing['tunnel_id']})")
    routes = ", ".join(f"{host} -> {svc}" for host, svc in existing["routes"]) or "[dim]none[/dim]"
    table.add_row("routes", routes)
    table.add_row("service", existing["service"] or "not installed")
    console.print(Panel(table, title="Tunnel already configured on this machine", border_style="warning", expand=False))


def ensure_tunnel(name):
    tunnel_id = find_tunnel(name)
    if tunnel_id:
        info(f"Tunnel [accent]{name}[/accent] already exists (id {tunnel_id}).")
        return tunnel_id
    run_command(["cloudflared", "tunnel", "create", name], check=False, status=f"Creating tunnel {name}")
    tunnel_id = find_tunnel(name)
    if not tunnel_id:
        error("Tunnel creation failed.")
        return None
    success(f"Tunnel [accent]{name}[/accent] created (id {tunnel_id}).")
    return tunnel_id


def _print_config_panel(config_file, config_text):
    success(f"Wrote {config_file}")
    console.print(
        Panel(
            Syntax(config_text.rstrip(), "yaml", theme="ansi_dark", background_color="default"),
            title=str(config_file),
            border_style="step",
            expand=False,
        )
    )


def write_server_config(tunnel_id, hostname, service):
    local_cred = cloudflared_dir() / f"{tunnel_id}.json"
    if not local_cred.exists():
        error(f"Credentials file {local_cred} not found.")
        info("This tunnel was likely created on another machine.")
        info(f"Delete it with 'cloudflared tunnel delete {tunnel_id}' and re-run,")
        info("or run this command on the machine that created the tunnel.")
        return None

    if IS_MACOS:
        return _write_server_config_macos(tunnel_id, hostname, service, local_cred)
    return _write_server_config_linux(tunnel_id, hostname, service, local_cred)


def _write_server_config_macos(tunnel_id, hostname, service, local_cred):
    # The LaunchAgent runs as this user, so it reads ~/.cloudflared directly -
    # no copy into /etc and no sudo needed.
    config_file = cloudflared_dir() / "config.yml"
    config_text = build_config_yaml(tunnel_id, hostname, service, str(local_cred))
    if config_file.exists():
        shutil.copy2(config_file, str(config_file) + ".bak")
        info(f"Backed up existing config to {config_file}.bak")
    config_file.write_text(config_text)
    _print_config_panel(config_file, config_text)
    return config_file


def _write_server_config_linux(tunnel_id, hostname, service, local_cred):
    etc_cred = ETC_DIR / f"{tunnel_id}.json"
    config_file = ETC_DIR / "config.yml"
    config_text = build_config_yaml(tunnel_id, hostname, service, str(etc_cred))

    run_command(sudo_prefix() + ["mkdir", "-p", str(ETC_DIR)])
    if config_file.exists():
        run_command(sudo_prefix() + ["cp", str(config_file), str(config_file) + ".bak"], check=False)
        info(f"Backed up existing config to {config_file}.bak")
    run_command(sudo_prefix() + ["cp", str(local_cred), str(etc_cred)])
    run_command(sudo_prefix() + ["tee", str(config_file)], capture=True, input_text=config_text)
    _print_config_panel(config_file, config_text)
    return config_file


def route_dns(name, hostname):
    info(f"Routing DNS [accent]{hostname}[/accent] to tunnel [accent]{name}[/accent]")
    result = run_command(
        ["cloudflared", "tunnel", "route", "dns", "--overwrite-dns", name, hostname],
        capture=True,
        check=False,
    )
    if result.returncode != 0 and "overwrite-dns" in (result.stderr or ""):
        result = run_command(
            ["cloudflared", "tunnel", "route", "dns", name, hostname],
            capture=True,
            check=False,
        )

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        lowered = output.lower()
        if "already" in lowered or "exists" in lowered:
            info("DNS record already points to this tunnel.")
            return True
        error("DNS routing failed. Create a proxied CNAME manually in Cloudflare:")
        console.print(f"  {hostname}  CNAME  <tunnel-id>.cfargotunnel.com  (Proxied, orange cloud)")
        return False
    success("DNS record ready.")
    return True


def check_sshd(ssh_port):
    if IS_MACOS:
        return _check_sshd_macos(ssh_port)
    return _check_sshd_linux(ssh_port)


def _check_sshd_macos(ssh_port):
    if _port_listening(ssh_port):
        info(f"SSH server is listening on localhost:{ssh_port}.")
        return True
    warning(f"No SSH server is listening on localhost:{ssh_port}.")
    info("On macOS this is the 'Remote Login' service.")
    info("Enable it in System Settings > General > Sharing > Remote Login, or run:")
    console.print("  [cmd]sudo systemsetup -setremotelogin on[/cmd]")
    if Confirm.ask("[accent]Enable Remote Login now (needs sudo)?[/accent]", default=True):
        run_command(sudo_prefix() + ["systemsetup", "-setremotelogin", "on"], check=False)
        if _port_listening(ssh_port):
            success("Remote Login enabled.")
            return True
    warning("Continuing without sshd. The tunnel will not be usable for SSH until it runs.")
    return False


def _check_sshd_linux(ssh_port):
    if Path("/usr/sbin/sshd").exists() or _has("sshd"):
        return True
    warning(f"No SSH server (sshd) found. The tunnel forwards to ssh://localhost:{ssh_port}")
    if Confirm.ask("[accent]Install openssh-server now?[/accent]", default=True):
        run_command(sudo_prefix() + ["apt-get", "update"], check=False, status="apt-get update")
        run_command(
            sudo_prefix() + ["apt-get", "install", "-y", "openssh-server"],
            check=False,
            status="Installing openssh-server",
        )
        run_command(sudo_prefix() + ["systemctl", "enable", "--now", "ssh"], check=False)
        return Path("/usr/sbin/sshd").exists()
    warning("Continuing without sshd. The tunnel will not be usable until sshd runs.")
    return False


def cloudflared_bin_for_root():
    path = shutil.which("cloudflared")
    if not path:
        return None
    resolved = os.path.realpath(path)
    if os.path.dirname(resolved) in ROOT_PATH_DIRS:
        return resolved
    target = "/usr/local/bin/cloudflared"
    info(f"cloudflared is at [accent]{resolved}[/accent]; copying to {target} so the service can run it.")
    run_command(sudo_prefix() + ["install", "-m", "0755", resolved, target], check=False)
    return target


def install_service(config_file):
    if IS_MACOS:
        return _install_service_macos()
    return _install_service_linux(config_file)


def _install_service_macos():
    uid = os.getuid()
    if MAC_PLIST.exists():
        info("cloudflared launch agent already installed. Restarting to apply config.")
        run_command(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/{MAC_LABEL}"],
            check=False,
            status="Restarting launch agent",
        )
    else:
        run_command(
            ["cloudflared", "service", "install"],
            check=False,
            status="Installing cloudflared launch agent",
        )

    state = mac_service_state()
    if state == "running":
        success("cloudflared launch agent: running")
    else:
        warning(f"cloudflared launch agent: {state or 'unknown'}")
        info("Check logs with:  log show --predicate 'process == \"cloudflared\"' --last 5m")
    return state == "running"


def _install_service_linux(config_file):
    if not Path("/run/systemd/system").exists():
        warning("systemd not detected. Skipping service install.")
        info(f"Run the tunnel manually:  sudo cloudflared --config {config_file} tunnel run")
        return False

    if SERVICE_UNIT.exists():
        info("cloudflared service already installed. Restarting to apply config.")
        run_command(sudo_prefix() + ["systemctl", "restart", "cloudflared"], check=False, status="Restarting service")
    else:
        cf_bin = cloudflared_bin_for_root()
        if not cf_bin:
            error("cloudflared binary not found; cannot install the service.")
            return False
        run_command(
            sudo_prefix() + [cf_bin, "service", "install"],
            check=False,
            status="Installing cloudflared service",
        )
        run_command(sudo_prefix() + ["systemctl", "enable", "--now", "cloudflared"], check=False)

    state = subprocess.run(
        sudo_prefix() + ["systemctl", "is-active", "cloudflared"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if state == "active":
        success(f"cloudflared service: {state}")
    else:
        warning(f"cloudflared service: {state or 'unknown'}")
    return state == "active"


def manage_hint():
    if IS_MACOS:
        return f"Manage the tunnel here:  [cmd]launchctl list {MAC_LABEL}[/cmd]  /  [cmd]cloudflared tunnel list[/cmd]"
    return (
        "Manage the tunnel here:  [cmd]sudo systemctl status cloudflared[/cmd]  /  [cmd]cloudflared tunnel list[/cmd]"
    )


def run_manually_hint(config_file):
    return f"Run it manually:  cloudflared --config {config_file} tunnel run"


def print_ssh_instructions(hostname, ssh_port, config_file, service_installed):
    user = getpass.getuser()
    ssh_config = f"Host {hostname}\n  User {user}\n  ProxyCommand cloudflared access ssh --hostname %h"
    console.print()
    console.print(
        Panel(
            "Server side is ready.\n\n"
            "On any [accent]client[/accent] machine you want to connect FROM:\n"
            "  1. Install cloudflared (https://pkg.cloudflare.com).\n"
            "  2. Add the block below to the client's ~/.ssh/config.\n"
            f"  3. Connect with:  [accent]ssh {hostname}[/accent]",
            title="cfssh complete",
            border_style="success",
            expand=False,
        )
    )
    console.print(Panel(ssh_config, title="client ~/.ssh/config", border_style="step", expand=False))
    if not service_installed:
        console.print(run_manually_hint(config_file))
    console.print(manage_hint())
    console.print(
        "Optional hardening: create a self-hosted Access application for "
        f"[accent]{hostname}[/accent] in the Cloudflare Zero Trust dashboard."
    )


def print_http_instructions(hostname, http_port, config_file, service_installed):
    console.print()
    console.print(
        Panel(
            "Server side is ready.\n\n"
            f"Local service:  [accent]http://localhost:{http_port}[/accent]\n"
            f"Public URL:     [accent]https://{hostname}[/accent]\n\n"
            "Open the public URL in a browser (DNS may take a few seconds to propagate).",
            title="cfssh complete (http)",
            border_style="success",
            expand=False,
        )
    )
    if not service_installed:
        console.print(run_manually_hint(config_file))
    console.print(manage_hint())
    console.print(
        "Anything reachable at the public URL is public. Make sure the service has its "
        "own auth, or protect it with a Cloudflare Access application."
    )


def run_setup_cloudflare_tunnel(hostname, name, ssh_port, http_port, no_service):
    if not (IS_MACOS or IS_LINUX):
        error("evo cfssh supports macOS and Linux only.")
        return

    is_http = http_port is not None
    service_kind = "http" if is_http else "ssh"
    target_port = http_port if is_http else ssh_port
    service = f"http://localhost:{http_port}" if is_http else f"ssh://localhost:{ssh_port}"

    existing = detect_local_tunnel()
    if existing:
        show_local_tunnel(existing)
        existing_host = existing["routes"][0][0] if existing["routes"] else None
        if existing_host:
            if not hostname:
                if Confirm.ask(f"[accent]Reuse this tunnel for {existing_host}?[/accent]", default=True):
                    hostname = existing_host
                    name = name or existing["name"] or existing_host.split(".")[0]
                else:
                    info("Setting up a new tunnel; it will replace the config shown above.")
            elif hostname == existing_host:
                info(f"Reconfiguring the existing tunnel for {hostname}.")
            elif not Confirm.ask(
                f"[warning]{existing_host} is already served from this machine. Replace it with {hostname}?[/warning]",
                default=False,
            ):
                info("Keeping the existing tunnel. Nothing changed.")
                return

    hostname = hostname or Prompt.ask("[accent]Public hostname (e.g. dev.example.com)[/accent]")
    if not hostname:
        error("Hostname is required.")
        return

    name = name or hostname.split(".")[0]

    summary = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    summary.add_row("hostname", f"[accent]{hostname}[/accent]")
    summary.add_row("tunnel", f"[accent]{name}[/accent]")
    summary.add_row("service", f"[accent]{service}[/accent]")
    summary.add_row("platform", f"[accent]{platform.system()}[/accent]")
    console.print(Panel(summary, title="Cloudflare tunnel", border_style="step", expand=False))

    try:
        if not install_cloudflared():
            return
        if service_kind == "ssh":
            check_sshd(target_port)
        if not ensure_login():
            return
        tunnel_id = ensure_tunnel(name)
        if not tunnel_id:
            return
        config_file = write_server_config(tunnel_id, hostname, service)
        if not config_file:
            return
        run_command(
            ["cloudflared", "--config", str(config_file), "tunnel", "ingress", "validate"],
            check=False,
            status="Validating ingress rules",
        )
        route_dns(name, hostname)
        service_installed = False
        if not no_service:
            service_installed = install_service(config_file)
        if service_kind == "http":
            print_http_instructions(hostname, target_port, config_file, service_installed)
        else:
            print_ssh_instructions(hostname, target_port, config_file, service_installed)
    except CommandError as exc:
        error(str(exc))
        error("Cloudflare tunnel setup did not finish.")


@click.command("cfssh", epilog=EPILOG)
@click.option("-H", "--hostname", help="Public hostname for the tunnel, e.g. `dev.example.com`.")
@click.option("-n", "--name", help="Tunnel name. Defaults to the first label of the hostname.")
@click.option("-P", "--ssh-port", type=int, default=22, show_default=True, help="Local SSH port to forward (ssh mode).")
@click.option(
    "--http",
    "http_port",
    type=int,
    default=None,
    metavar="PORT",
    help="Expose a local HTTP service on this port instead of SSH.",
)
@click.option("--no-service", is_flag=True, help="Configure only; do not install the launchd/systemd service.")
def cfssh(hostname, name, ssh_port, http_port, no_service):
    """Expose this machine through a Cloudflare named tunnel.

    Installs `cloudflared`, creates a named tunnel, writes a `config.yml` with an
    ingress rule, routes a proxied DNS record, and installs the background
    service (launchd on macOS, systemd on Linux). Defaults to forwarding SSH
    (`ssh://localhost:22`); pass `--http PORT` to expose a local web service
    instead. A Cloudflare-managed domain is required.
    """
    step("evo cfssh")
    run_setup_cloudflare_tunnel(hostname, name, ssh_port, http_port, no_service)
