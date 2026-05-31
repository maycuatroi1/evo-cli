import getpass
import json
import os
import platform
import shutil
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
ETC_DIR = Path("/etc/cloudflared")
SERVICE_UNIT = Path("/etc/systemd/system/cloudflared.service")
ROOT_PATH_DIRS = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo cfssh -H dev.example.com[/cyan]\n"
    "  [cyan]evo cfssh -H box.example.com -n my-box -P 2222[/cyan]\n"
    "  [cyan]evo cfssh -H dev.example.com --no-service[/cyan]"
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


def build_config_yaml(tunnel_id, hostname, ssh_port, credentials_path):
    return (
        f"tunnel: {tunnel_id}\n"
        f"credentials-file: {credentials_path}\n"
        f"\n"
        f"ingress:\n"
        f"  - hostname: {hostname}\n"
        f"    service: ssh://localhost:{ssh_port}\n"
        f"  - service: http_status:404\n"
    )


def install_cloudflared():
    if _has("cloudflared"):
        version = subprocess.run(["cloudflared", "--version"], capture_output=True, text=True).stdout.strip()
        info(f"cloudflared already installed: [accent]{version}[/accent]")
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
    config_file = ETC_DIR / "config.yml"
    if not config_file.exists():
        return None
    try:
        text = config_file.read_text()
    except OSError:
        return None
    tunnel_id = None
    ssh_hostnames = []
    current_host = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("tunnel:"):
            tunnel_id = line.split(":", 1)[1].strip()
        elif line.startswith("- hostname:") or line.startswith("hostname:"):
            current_host = line.split(":", 1)[1].strip()
        elif line.startswith("service:"):
            if current_host and line.split(":", 1)[1].strip().startswith("ssh://"):
                ssh_hostnames.append(current_host)
            current_host = None
    if not tunnel_id:
        return None
    return {"config_file": config_file, "tunnel_id": tunnel_id, "ssh_hostnames": ssh_hostnames}


def cloudflared_service_state():
    if not SERVICE_UNIT.exists():
        return None
    try:
        result = subprocess.run(["systemctl", "is-active", "cloudflared"], capture_output=True, text=True)
    except OSError:
        return "unknown"
    return result.stdout.strip() or "unknown"


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
    table.add_row("ssh hostname", ", ".join(existing["ssh_hostnames"]) or "[dim]none[/dim]")
    table.add_row("service", existing["service"] or "not installed")
    console.print(
        Panel(table, title="SSH tunnel already configured on this machine", border_style="warning", expand=False)
    )


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


def write_server_config(tunnel_id, hostname, ssh_port):
    local_cred = cloudflared_dir() / f"{tunnel_id}.json"
    if not local_cred.exists():
        error(f"Credentials file {local_cred} not found.")
        info("This tunnel was likely created on another machine.")
        info(f"Delete it with 'cloudflared tunnel delete {tunnel_id}' and re-run,")
        info("or run this command on the machine that created the tunnel.")
        return None

    etc_cred = ETC_DIR / f"{tunnel_id}.json"
    config_file = ETC_DIR / "config.yml"
    config_text = build_config_yaml(tunnel_id, hostname, ssh_port, str(etc_cred))

    run_command(sudo_prefix() + ["mkdir", "-p", str(ETC_DIR)])
    if config_file.exists():
        run_command(sudo_prefix() + ["cp", str(config_file), str(config_file) + ".bak"], check=False)
        info(f"Backed up existing config to {config_file}.bak")
    run_command(sudo_prefix() + ["cp", str(local_cred), str(etc_cred)])
    run_command(sudo_prefix() + ["tee", str(config_file)], capture=True, input_text=config_text)
    success(f"Wrote {config_file}")
    console.print(
        Panel(
            Syntax(config_text.rstrip(), "yaml", theme="ansi_dark", background_color="default"),
            title=str(config_file),
            border_style="step",
            expand=False,
        )
    )
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


def print_client_instructions(hostname, ssh_port):
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
    console.print(
        "Manage the tunnel here:  [cmd]sudo systemctl status cloudflared[/cmd]  /  [cmd]cloudflared tunnel list[/cmd]"
    )
    console.print(
        "Optional hardening: create a self-hosted Access application for "
        f"[accent]{hostname}[/accent] in the Cloudflare Zero Trust dashboard."
    )


def run_setup_cloudflare_ssh(hostname, name, ssh_port, no_service):
    if platform.system() != "Linux":
        error("evo cfssh supports Linux (Ubuntu) only.")
        return

    existing = detect_local_tunnel()
    if existing:
        show_local_tunnel(existing)
        existing_host = existing["ssh_hostnames"][0] if existing["ssh_hostnames"] else None
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

    hostname = hostname or Prompt.ask("[accent]Public hostname for SSH (e.g. dev.example.com)[/accent]")
    if not hostname:
        error("Hostname is required.")
        return

    name = name or hostname.split(".")[0]
    ssh_port = ssh_port or 22

    summary = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    summary.add_row("hostname", f"[accent]{hostname}[/accent]")
    summary.add_row("tunnel", f"[accent]{name}[/accent]")
    summary.add_row("ssh port", f"[accent]{ssh_port}[/accent]")
    console.print(Panel(summary, title="Cloudflare SSH tunnel", border_style="step", expand=False))

    try:
        if not install_cloudflared():
            return
        check_sshd(ssh_port)
        if not ensure_login():
            return
        tunnel_id = ensure_tunnel(name)
        if not tunnel_id:
            return
        config_file = write_server_config(tunnel_id, hostname, ssh_port)
        if not config_file:
            return
        run_command(
            ["cloudflared", "--config", str(config_file), "tunnel", "ingress", "validate"],
            check=False,
            status="Validating ingress rules",
        )
        route_dns(name, hostname)
        if not no_service:
            install_service(config_file)
        print_client_instructions(hostname, ssh_port)
    except CommandError as exc:
        error(str(exc))
        error("Cloudflare SSH setup did not finish.")


@click.command("cfssh", epilog=EPILOG)
@click.option("-H", "--hostname", help="Public hostname for SSH access, e.g. `dev.example.com`.")
@click.option("-n", "--name", help="Tunnel name. Defaults to the first label of the hostname.")
@click.option("-P", "--ssh-port", type=int, default=22, show_default=True, help="Local SSH port to forward.")
@click.option("--no-service", is_flag=True, help="Configure only; do not install the systemd service.")
def cfssh(hostname, name, ssh_port, no_service):
    """Expose this machine's SSH server through a Cloudflare named tunnel.

    Installs `cloudflared`, creates a named tunnel, writes
    `/etc/cloudflared/config.yml` with an `ssh://` ingress rule, routes a
    proxied DNS record, and installs the `cloudflared` systemd service. Linux
    (Ubuntu) with a Cloudflare-managed domain is required.
    """
    step("evo cfssh")
    run_setup_cloudflare_ssh(hostname, name, ssh_port, no_service)
