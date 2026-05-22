import subprocess
import warnings
from pathlib import Path

import rich_click as click
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from evo_cli.console import console, error, info, step, success, warning

warnings.filterwarnings("ignore", category=DeprecationWarning)

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo setupssh[/cyan]\n"
    "  [cyan]evo setupssh -H 42.96.16.233 -u root[/cyan]\n"
    "  [cyan]evo setupssh -H host -u root -P 2222 -i ~/.ssh/id_ed25519[/cyan]"
)


def _import_paramiko():
    try:
        from cryptography.utils import CryptographyDeprecationWarning

        warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)
    except ImportError:
        pass
    import paramiko

    return paramiko


def connect_ssh(hostname, username, password, port=22):
    paramiko = _import_paramiko()
    target = f"{username}@{hostname}" + (f":{port}" if port != 22 else "")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        with console.status(f"[info]Connecting to {target}[/info]", spinner="dots"):
            client.connect(hostname=hostname, username=username, password=password, port=port)
    except Exception as exc:
        error(f"Connection failed: {exc}")
        return None
    success(f"Connected to {target}")
    _, stdout, _ = client.exec_command("hostname")
    remote = stdout.read().decode().strip()
    info(f"Remote hostname: [accent]{remote}[/accent]")
    return client


def ensure_ssh_key_exists():
    ssh_dir = Path.home() / ".ssh"
    private_key = ssh_dir / "id_rsa"
    public_key = ssh_dir / "id_rsa.pub"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    if private_key.exists() and public_key.exists():
        info(f"Using existing key pair at [accent]{private_key}[/accent]")
        return private_key, public_key
    try:
        with console.status("[info]Generating a new 4096-bit RSA key pair[/info]", spinner="dots"):
            subprocess.run(
                ["ssh-keygen", "-t", "rsa", "-b", "4096", "-f", str(private_key), "-N", ""],
                check=True,
                capture_output=True,
            )
    except Exception as exc:
        error(f"Could not generate SSH key: {exc}")
        return None, None
    success(f"Key pair created at [accent]{private_key}[/accent]")
    return private_key, public_key


def upload_ssh_key(client, public_key_path):
    try:
        public_key = Path(public_key_path).read_text().strip()
        commands = [
            "mkdir -p ~/.ssh",
            f"grep -qxF '{public_key}' ~/.ssh/authorized_keys 2>/dev/null "
            f"|| echo '{public_key}' >> ~/.ssh/authorized_keys",
            "chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys",
        ]
        for command in commands:
            _, stdout, stderr = client.exec_command(command)
            stdout.channel.recv_exit_status()
            message = stderr.read().decode().strip()
            if message:
                error(f"Remote command failed: {message}")
                return False
    except Exception as exc:
        error(f"Could not upload public key: {exc}")
        return False
    success("Public key installed in remote authorized_keys")
    return True


def save_to_ssh_config(hostname, username, identity_file, port=22):
    ssh_dir = Path.home() / ".ssh"
    config_path = ssh_dir / "config"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and f"Host {hostname}" in config_path.read_text():
        warning(f"Host [accent]{hostname}[/accent] already in {config_path}, leaving it untouched")
        return
    port_line = f"  Port {port}\n" if port != 22 else ""
    entry = f"\nHost {hostname}\n  HostName {hostname}\n{port_line}  User {username}\n  IdentityFile {identity_file}\n"
    with open(config_path, "a+") as handle:
        handle.write(entry)
    success(f"Added host entry to [accent]{config_path}[/accent]")
    console.print(Panel(entry.strip(), title="ssh config entry", border_style="step", expand=False))


def run_setup_ssh(host, user, password, port, identity):
    host = host or Prompt.ask("[accent]SSH host or IP[/accent]")
    if not host:
        error("Host is required.")
        return
    user = user or Prompt.ask("[accent]SSH username[/accent]", default="root")
    if not password:
        password = Prompt.ask("[accent]SSH password[/accent]", password=True)
    if not password:
        error("Password is required to install the key.")
        return

    if identity:
        private_key = Path(identity)
        public_key = Path(f"{identity}.pub")
        if not private_key.exists() or not public_key.exists():
            error(f"Identity file {identity} or its .pub counterpart was not found.")
            return
        info(f"Using identity file [accent]{private_key}[/accent]")
    else:
        private_key, public_key = ensure_ssh_key_exists()
        if not private_key:
            return

    client = connect_ssh(host, user, password, port)
    if not client:
        return
    try:
        if upload_ssh_key(client, public_key):
            save_to_ssh_config(host, user, str(private_key), port)
            console.print()
            console.print(
                Panel(
                    f"Passwordless login is ready.\nConnect with:  [accent]ssh {host}[/accent]",
                    title="setupssh complete",
                    border_style="success",
                    expand=False,
                )
            )
        else:
            error("Failed to set up passwordless authentication.")
    finally:
        client.close()


@click.command("setupssh", epilog=EPILOG)
@click.option("-H", "--host", help="SSH server hostname or IP address.")
@click.option("-u", "--user", help="SSH username.")
@click.option("-p", "--password", help="SSH password. Prefer the interactive prompt over this flag.")
@click.option("-P", "--port", type=int, default=22, show_default=True, help="SSH port.")
@click.option("-i", "--identity", type=click.Path(), help="Existing private key to install instead of generating one.")
def setupssh(host, user, password, port, identity):
    """Set up SSH key-based (passwordless) authentication to a remote host.

    Generates an SSH key pair if needed, copies the public key into the remote
    `authorized_keys`, and adds a matching `Host` entry to your local SSH config.
    """
    step("evo setupssh")
    run_setup_ssh(host, user, password, port, identity)
