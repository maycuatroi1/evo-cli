import getpass
import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

CLOUDFLARED_DEB_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"
ETC_DIR = Path("/etc/cloudflared")
SERVICE_UNIT = Path("/etc/systemd/system/cloudflared.service")


class CloudflareSetupError(RuntimeError):
    pass


def _sudo():
    return [] if os.geteuid() == 0 else ["sudo"]


def _run(cmd, capture=False, check=True, input_text=None):
    print("$ " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=capture, text=True, input=input_text)
    if check and result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr.strip())
        raise CloudflareSetupError("command failed: " + " ".join(cmd))
    return result


def _has(binary):
    return shutil.which(binary) is not None


def _deb_arch():
    machine = platform.machine().lower()
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "armhf",
        "armv6l": "arm",
    }
    return mapping.get(machine, "amd64")


def install_cloudflared():
    if _has("cloudflared"):
        result = subprocess.run(["cloudflared", "--version"], capture_output=True, text=True)
        print("cloudflared already installed: " + result.stdout.strip())
        return True

    arch = _deb_arch()
    url = f"{CLOUDFLARED_DEB_BASE}/cloudflared-linux-{arch}.deb"
    print(f"Downloading cloudflared ({arch}) from {url}")
    with tempfile.TemporaryDirectory() as tmp:
        deb_path = os.path.join(tmp, "cloudflared.deb")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "evo-cli"})
            with urllib.request.urlopen(request) as response, open(deb_path, "wb") as out:
                shutil.copyfileobj(response, out)
        except Exception as error:
            print(f"Failed to download cloudflared: {error}")
            return False

        install = _run(_sudo() + ["dpkg", "-i", deb_path], check=False)
        if install.returncode != 0:
            _run(_sudo() + ["apt-get", "install", "-f", "-y"], check=False)

    if not _has("cloudflared"):
        print("cloudflared installation failed.")
        return False
    print("cloudflared installed.")
    return True


def cloudflared_dir():
    return Path.home() / ".cloudflared"


def ensure_login():
    cert = cloudflared_dir() / "cert.pem"
    if cert.exists():
        print(f"Cloudflare login already done ({cert}).")
        return True
    print("Opening Cloudflare login. Pick the domain you want to use in the browser.")
    _run(["cloudflared", "tunnel", "login"], check=False)
    if not cert.exists():
        print("Login did not produce cert.pem. Aborting.")
        return False
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


def ensure_tunnel(name):
    tunnel_id = find_tunnel(name)
    if tunnel_id:
        print(f"Tunnel '{name}' already exists (id {tunnel_id}).")
        return tunnel_id
    print(f"Creating tunnel '{name}'...")
    _run(["cloudflared", "tunnel", "create", name], check=False)
    tunnel_id = find_tunnel(name)
    if not tunnel_id:
        print("Tunnel creation failed.")
        return None
    print(f"Tunnel '{name}' created (id {tunnel_id}).")
    return tunnel_id


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


def write_server_config(tunnel_id, hostname, ssh_port):
    local_cred = cloudflared_dir() / f"{tunnel_id}.json"
    if not local_cred.exists():
        print(f"Credentials file {local_cred} not found.")
        print("This tunnel was likely created on another machine.")
        print(f"Delete it with 'cloudflared tunnel delete {tunnel_id}' and re-run, or")
        print("run this command on the machine that created the tunnel.")
        return None

    etc_cred = ETC_DIR / f"{tunnel_id}.json"
    config_file = ETC_DIR / "config.yml"
    config_text = build_config_yaml(tunnel_id, hostname, ssh_port, str(etc_cred))

    _run(_sudo() + ["mkdir", "-p", str(ETC_DIR)])
    if config_file.exists():
        _run(_sudo() + ["cp", str(config_file), str(config_file) + ".bak"], check=False)
        print(f"Backed up existing config to {config_file}.bak")
    _run(_sudo() + ["cp", str(local_cred), str(etc_cred)])
    _run(_sudo() + ["tee", str(config_file)], capture=True, input_text=config_text)
    print(f"Wrote {config_file}")
    print(config_text.rstrip())
    return config_file


def route_dns(name, hostname):
    print(f"Routing DNS {hostname} to tunnel {name}")
    result = _run(
        ["cloudflared", "tunnel", "route", "dns", "--overwrite-dns", name, hostname],
        capture=True,
        check=False,
    )
    if result.returncode != 0 and "overwrite-dns" in (result.stderr or ""):
        result = _run(
            ["cloudflared", "tunnel", "route", "dns", name, hostname],
            capture=True,
            check=False,
        )

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        lowered = output.lower()
        if "already" in lowered or "exists" in lowered:
            print("DNS record already points to this tunnel.")
            return True
        print(output)
        print("DNS routing failed. Create a proxied CNAME manually in Cloudflare:")
        print(f"  {hostname}  CNAME  <tunnel-id>.cfargotunnel.com  (Proxied, orange cloud)")
        return False
    print("DNS record ready.")
    return True


def check_sshd(ssh_port):
    if Path("/usr/sbin/sshd").exists() or _has("sshd"):
        return True
    print(f"WARNING: no SSH server (sshd) found. The tunnel forwards to ssh://localhost:{ssh_port}")
    answer = input("Install openssh-server now? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        _run(_sudo() + ["apt-get", "update"], check=False)
        _run(_sudo() + ["apt-get", "install", "-y", "openssh-server"], check=False)
        _run(_sudo() + ["systemctl", "enable", "--now", "ssh"], check=False)
        return Path("/usr/sbin/sshd").exists()
    print("Continuing without sshd. The tunnel will not be usable until sshd runs.")
    return False


def install_service(config_file):
    if not Path("/run/systemd/system").exists():
        print("systemd not detected. Skipping service install.")
        print("Run the tunnel manually with:")
        print(f"  sudo cloudflared --config {config_file} tunnel run")
        return False

    if SERVICE_UNIT.exists():
        print("cloudflared service already installed. Restarting to apply config...")
        _run(_sudo() + ["systemctl", "restart", "cloudflared"], check=False)
    else:
        _run(_sudo() + ["cloudflared", "service", "install"], check=False)
        _run(_sudo() + ["systemctl", "enable", "--now", "cloudflared"], check=False)

    state = subprocess.run(
        _sudo() + ["systemctl", "is-active", "cloudflared"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    print(f"cloudflared service: {state}")
    return state == "active"


def print_client_instructions(hostname, ssh_port):
    user = getpass.getuser()
    print()
    print("=" * 64)
    print("Server side is ready.")
    print("=" * 64)
    print()
    print("On any CLIENT machine you want to connect FROM:")
    print()
    print("  1. Install cloudflared (https://pkg.cloudflare.com).")
    print("  2. Add this to the client's ~/.ssh/config:")
    print()
    print(f"     Host {hostname}")
    print(f"       User {user}")
    print("       ProxyCommand cloudflared access ssh --hostname %h")
    print()
    print("  3. Connect:")
    print(f"       ssh {hostname}")
    print()
    print("Manage the tunnel on this server with:")
    print("       sudo systemctl status cloudflared")
    print("       cloudflared tunnel list")
    print()
    print("Optional hardening: in the Cloudflare Zero Trust dashboard, create a")
    print(f"self-hosted Access application for {hostname} to control who can reach it.")


def setup_cloudflare_ssh(args):
    if platform.system() != "Linux":
        print("This command supports Linux (Ubuntu) only.")
        return

    hostname = args.hostname or input("Public hostname for SSH (e.g. dev.example.com): ").strip()
    if not hostname:
        print("Hostname is required. Exiting.")
        return

    name = args.name or hostname.split(".")[0]
    ssh_port = getattr(args, "ssh_port", 22) or 22

    print("Setting up Cloudflare SSH tunnel")
    print(f"  hostname : {hostname}")
    print(f"  tunnel   : {name}")
    print(f"  ssh port : {ssh_port}")
    print()

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
        _run(
            ["cloudflared", "--config", str(config_file), "tunnel", "ingress", "validate"],
            check=False,
        )
        route_dns(name, hostname)
        if not getattr(args, "no_service", False):
            install_service(config_file)
        print_client_instructions(hostname, ssh_port)
    except CloudflareSetupError as error:
        print(str(error))
        print("Cloudflare SSH setup did not finish.")


def show_usage():
    print(
        """
Cloudflare SSH Tunnel Setup
===========================

Expose this Ubuntu machine's SSH server through a Cloudflare named tunnel,
so you can reach it from anywhere without opening a public inbound port.

What it does:
  1. Installs cloudflared (if missing).
  2. Logs in to Cloudflare and lets you pick your domain.
  3. Creates a named tunnel.
  4. Writes /etc/cloudflared/config.yml with an ssh:// ingress rule.
  5. Routes a proxied DNS record to the tunnel.
  6. Installs and starts the cloudflared systemd service.

Requirements:
  - A Cloudflare account with a domain managed in Cloudflare.
  - Ubuntu/Linux with systemd.

Usage:
  evo cfssh [options]

Options:
  -H, --hostname HOST   Public hostname for SSH, e.g. dev.example.com
  -n, --name NAME       Tunnel name (default: first label of hostname)
  -P, --ssh-port PORT   Local SSH port to forward (default: 22)
  --no-service          Configure only, do not install the systemd service
  --help-examples       Show this help

Examples:
  evo cfssh -H dev.example.com
  evo cfssh -H box.example.com -n my-box -P 2222
  evo cfssh -H dev.example.com --no-service
"""
    )
