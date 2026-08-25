"""Install the ngrok agent and wire up its authtoken in one step."""

import getpass
import json as jsonlib
import os
import platform
import queue
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import rich_click as click
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from evo_cli.console import (
    CommandError,
    console,
    download_file,
    error,
    info,
    is_root,
    run_command,
    step,
    success,
    warning,
)
from evo_cli.credentials.store import CredentialError, compile_flat, get_value, relative_to_store, set_value

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo setup ngrok[/cyan]                        install, then ask for the authtoken (no echo)\n"
    "  [cyan]evo setup ngrok -t 2ab...xyz[/cyan]           paste the authtoken up front, no prompt\n"
    "  [cyan]evo setup ngrok --from-stdin < key.txt[/cyan] read the authtoken from stdin\n"
    "  [cyan]evo setup ngrok --method package[/cyan]       use brew/winget/scoop/choco/apt instead\n"
    "  [cyan]evo setup ngrok --skip-token[/cyan]           install the agent only\n"
    "  [cyan]evo setup ngrok --force --verify[/cyan]       re-enter the token, then prove it works\n\n"
    "[dim]The authtoken is stored in the omelet credential store as `ngrok_authtoken`,\n"
    "never in this repository. Push it to your private sync repo with `evo cred sync push`.[/dim]"
)

CRED_KEY = "ngrok_authtoken"
ENV_VAR = "NGROK_AUTHTOKEN"
DASHBOARD_URL = "https://dashboard.ngrok.com/get-started/your-authtoken"
# The equinox channel ngrok's own install docs point at: always the latest v3 stable.
DOWNLOAD_BASE = "https://bin.equinox.io/c/bNyj1mQVY4c"
BINARY_NAME = "ngrok.exe" if os.name == "nt" else "ngrok"
BIN_DIR = Path.home() / ".evo" / "bin"

ARCH_SLUGS = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm",
    "armv6l": "arm",
    "i386": "386",
    "i686": "386",
}

SYSTEM_SLUGS = {
    "Windows": "windows",
    "Darwin": "darwin",
    "Linux": "linux",
    "FreeBSD": "freebsd",
}

_AUTHTOKEN_LINE = re.compile(r"""^\s*authtoken:\s*["']?([^"'\s]+)["']?\s*$""")

# ngrok is not in the distro repositories, so its official Linux instructions add
# ngrok's own apt source first: https://ngrok.com/docs/getting-started/
APT_SCRIPT = """set -e
(type -p curl >/dev/null || ($SUDO apt-get update && $SUDO apt-get install curl -y))
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | $SUDO tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | $SUDO tee /etc/apt/sources.list.d/ngrok.list >/dev/null
$SUDO apt-get update
$SUDO apt-get install ngrok -y
"""


def can_elevate():
    """Whether a privileged package-manager install is possible at all."""
    return is_root() or bool(shutil.which("sudo"))


def elevated(argv):
    return list(argv) if is_root() else ["sudo", *argv]


def shell_script(script):
    sudo = "" if is_root() else "sudo"
    return ["bash", "-c", f"SUDO={sudo}\n{script}"]


def mask(token):
    if not token:
        return "-"
    if len(token) <= 12:
        return f"{token[:2]}...{token[-2:]}"
    return f"{token[:6]}...{token[-4:]} ({len(token)} chars)"


def managed_binary():
    return BIN_DIR / BINARY_NAME


def find_ngrok():
    """ngrok on PATH, else the copy evo installed itself."""
    found = shutil.which("ngrok")
    if found:
        return Path(found)
    managed = managed_binary()
    if managed.is_file() and os.access(managed, os.X_OK):
        return managed
    return None


def ngrok_version(binary):
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [str(binary), "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    match = re.search(r"\d+\.\d+\.\d+", output)
    if match:
        return match.group(0)
    return output.splitlines()[0].strip() if output else "unknown"


def platform_target():
    """(os slug, arch slug, archive extension) for this machine, or None."""
    goos = SYSTEM_SLUGS.get(platform.system())
    arch = ARCH_SLUGS.get(platform.machine().lower())
    if not goos or not arch:
        return None
    return goos, arch, "zip" if goos in ("windows", "darwin") else "tgz"


def asset_url():
    target = platform_target()
    if target is None:
        return None
    goos, arch, ext = target
    return f"{DOWNLOAD_BASE}/ngrok-v3-stable-{goos}-{arch}.{ext}"


def extract_binary(archive, destination):
    """Pull the single ngrok binary out of the release archive.

    Only that one member is read, so a hostile archive cannot write outside the
    destination the way a blanket extractall() could.
    """
    names = ("ngrok", "ngrok.exe")
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            member = next((n for n in bundle.namelist() if Path(n).name in names), None)
            if member is None:
                return False
            with bundle.open(member) as source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target)
    else:
        with tarfile.open(archive, "r:*") as bundle:
            member = next((m for m in bundle.getmembers() if m.isfile() and Path(m.name).name in names), None)
            if member is None:
                return False
            source = bundle.extractfile(member)
            if source is None:
                return False
            with source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target)

    mode = destination.stat().st_mode
    destination.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return True


def package_install_command():
    """The package-manager install command for this platform, or None."""
    system = platform.system()
    if system == "Darwin":
        return ["brew", "install", "ngrok/ngrok/ngrok"] if shutil.which("brew") else None
    if system == "Windows":
        if shutil.which("winget"):
            return [
                "winget",
                "install",
                "--id",
                "Ngrok.Ngrok",
                "--source",
                "winget",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
        if shutil.which("scoop"):
            return ["scoop", "install", "ngrok"]
        if shutil.which("choco"):
            return ["choco", "install", "ngrok", "-y"]
        return None
    if system == "Linux":
        if not can_elevate():
            return None
        if shutil.which("apt-get"):
            return shell_script(APT_SCRIPT)
        if shutil.which("snap"):
            return elevated(["snap", "install", "ngrok"])
    return None


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def add_to_user_path(directory):
    """Append a directory to the Windows *user* PATH, persistently.

    Only the user scope is touched, so this never folds the machine PATH into
    the user one the way a bare `setx PATH %PATH%;...` would.
    """
    script = (
        f"$dir = {ps_quote(directory)}\n"
        "$current = [Environment]::GetEnvironmentVariable('Path','User')\n"
        "if (($current -split ';') -contains $dir) { exit 0 }\n"
        "$updated = if ([string]::IsNullOrWhiteSpace($current)) { $dir } "
        "else { $current.TrimEnd(';') + ';' + $dir }\n"
        "[Environment]::SetEnvironmentVariable('Path', $updated, 'User')\n"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        warning(f"could not update the user PATH: {exc}")
        return False
    if result.returncode != 0:
        warning((result.stderr or "could not update the user PATH").strip())
        return False
    return True


def on_path(directory):
    entries = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    return any(os.path.normpath(part) == os.path.normpath(str(directory)) for part in entries)


def prepend_to_process_path(directory):
    """Make a just-installed binary visible to this process; PATH is not reloaded yet."""
    if on_path(directory):
        return
    os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"


def announce_bin_dir(add_path):
    """Make ~/.evo/bin usable now and, on Windows, in future shells too."""
    already = on_path(BIN_DIR)
    prepend_to_process_path(BIN_DIR)
    if already:
        return
    if platform.system() == "Windows":
        if not add_path:
            info(f"add [accent]{BIN_DIR}[/accent] to your PATH to run `ngrok` from any shell")
        elif add_to_user_path(BIN_DIR):
            success(f"added [accent]{BIN_DIR}[/accent] to your user PATH (new shells pick it up)")
        return
    info(f'not on PATH yet - add this to your shell rc: [accent]export PATH="{BIN_DIR}:$PATH"[/accent]')


def install_from_release(add_path=True):
    """Download the official ngrok binary into ~/.evo/bin. Needs no privileges."""
    url = asset_url()
    if not url:
        error(f"no prebuilt ngrok binary for {platform.system()}/{platform.machine()}")
        return None

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    destination = managed_binary()
    with tempfile.TemporaryDirectory() as tmpdir:
        archive = Path(tmpdir) / url.rsplit("/", 1)[-1]
        info(f"downloading {url}")
        try:
            download_file(url, archive, description="ngrok agent")
        except OSError as exc:
            error(f"download failed: {exc}")
            return None
        try:
            extracted = extract_binary(archive, destination)
        except OSError as exc:
            error(f"could not write {destination}: {exc}")
            return None
        if not extracted:
            error(f"no ngrok binary inside {archive.name}")
            return None

    announce_bin_dir(add_path)
    success(f"ngrok installed at [accent]{destination}[/accent]")
    return destination


def install_via_package():
    command = package_install_command()
    if command is None:
        warning("no usable package manager for ngrok on this machine")
        return None
    try:
        run_command(command, status="Installing ngrok", stdin=subprocess.DEVNULL, timeout=900)
    except CommandError:
        warning("the package-manager install failed")
        return None
    binary = find_ngrok()
    if binary is None:
        warning("ngrok installed but it is not on PATH yet; open a new terminal and run `ngrok version`")
    return binary


def install_agent(method, reinstall, add_path):
    """Install the ngrok agent. Returns the binary path, or None."""
    step("ngrok agent")

    existing = find_ngrok()
    if existing and not reinstall:
        info(f"ngrok already installed: [accent]{existing}[/accent] ({ngrok_version(existing)})")
        return existing

    if method == "none":
        info("skipping the agent install (--method none)")
        return existing

    if method == "package":
        binary = install_via_package()
        if binary:
            return binary
        info("falling back to the official release binary")
        return install_from_release(add_path)

    return install_from_release(add_path)


def config_file():
    """Where the ngrok agent keeps its own config."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ngrok" / "ngrok.yml"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "ngrok" / "ngrok.yml"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ngrok" / "ngrok.yml"


def legacy_config_file():
    return Path.home() / ".ngrok2" / "ngrok.yml"


def token_from_config():
    for path in (config_file(), legacy_config_file()):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            match = _AUTHTOKEN_LINE.match(line)
            if match:
                return match.group(1), path
    return None, None


def token_from_store():
    try:
        value = get_value(CRED_KEY)
    except CredentialError:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def resolve_token():
    """Return (token, source) using the same precedence the agent itself does."""
    env = os.environ.get(ENV_VAR)
    if env and env.strip():
        return env.strip(), f"env {ENV_VAR}"
    stored = token_from_store()
    if stored:
        return stored, f"evo cred {CRED_KEY}"
    from_config, path = token_from_config()
    if from_config:
        return from_config, str(path)
    return None, None


def collect_token(authtoken, from_stdin, force):
    """Work out which authtoken to use, asking for one only when needed."""
    if from_stdin:
        token = sys.stdin.read().strip()
        if not token:
            raise click.ClickException("stdin was empty, no authtoken to read")
        return token, "stdin"
    if authtoken:
        return authtoken.strip(), "--authtoken"

    existing, source = resolve_token()
    if existing and not force:
        info(f"reusing the authtoken already available from [accent]{source}[/accent] ({mask(existing)})")
        return existing, source

    console.print(f"Get your authtoken at [accent]{DASHBOARD_URL}[/accent]")
    if not sys.stdin.isatty():
        raise click.ClickException(
            "no authtoken available and no terminal to ask on.\n"
            f"Pass --authtoken, pipe it in with --from-stdin, or export {ENV_VAR} before running this again."
        )
    token = getpass.getpass("ngrok authtoken (no echo): ").strip()
    if not token:
        raise click.ClickException("empty authtoken, aborting")
    return token, "prompt"


def store_token(token):
    try:
        path, existed = set_value(CRED_KEY, token)
        count, _, target = compile_flat()
    except CredentialError as exc:
        raise click.ClickException(str(exc)) from exc
    verb = "updated" if existed else "stored"
    success(f"{verb} {CRED_KEY} in {relative_to_store(path)}, recompiled {target} ({count} entries)")


def apply_authtoken(binary, token):
    """Hand the token to the agent so tunnels authenticate with no env var set."""
    console.print(f"[cmd]$ {escape(str(binary))} config add-authtoken {mask(token)}[/cmd]")
    try:
        result = subprocess.run(
            [str(binary), "config", "add-authtoken", token],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        error(f"could not run the agent: {exc}")
        return False
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        error(escape(output) or "ngrok config add-authtoken failed")
        return False
    if output:
        console.print(escape(output))
    return True


def check_config(binary):
    result = run_command(
        [str(binary), "config", "check"],
        status="Checking the ngrok config",
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    return getattr(result, "returncode", None) == 0


def _drain(stream, sink):
    for line in stream:
        sink.put(line)
    sink.put(None)


def _parse_log_line(line):
    try:
        record = jsonlib.loads(line)
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def _log_error(record):
    err = record.get("err")
    if isinstance(err, str) and err.strip() and err.strip() not in ("<nil>", "null"):
        return err.strip()
    if record.get("lvl") in ("eror", "crit"):
        return str(record.get("msg") or "").strip() or None
    return None


def verify_authtoken(binary, timeout=30):
    """Prove the token is live by opening a throwaway tunnel, then tearing it down.

    Nothing listens on the forwarded port, so the URL serves nothing; it exists
    only long enough for the agent to authenticate against ngrok's servers.
    """
    argv = [str(binary), "http", "8080", "--log", "stdout", "--log-format", "json"]
    console.print(f"[cmd]$ {escape(' '.join(argv))}[/cmd]")
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        return False, f"could not start the agent: {exc}"

    lines = queue.Queue()
    threading.Thread(target=_drain, args=(process.stdout, lines), daemon=True).start()

    url = None
    failure = None
    deadline = time.monotonic() + timeout
    with console.status("[info]Opening a throwaway tunnel[/info]", spinner="dots"):
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if line is None:
                break
            record = _parse_log_line(line)
            if record.get("msg") == "started tunnel":
                url = record.get("url") or "(tunnel opened, no url reported)"
                break
            failure = _log_error(record)
            if failure:
                break

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()

    if url:
        return True, url
    return False, failure or f"the agent did not connect within {timeout}s"


def print_summary(binary, token_source, applied, verified):
    console.print()
    if binary is None:
        console.print(
            Panel(
                Text.from_markup(
                    "The ngrok agent is not installed. Grab it from "
                    "[accent]https://ngrok.com/download[/accent] and re-run this command."
                ),
                title="setup ngrok incomplete",
                border_style="warning",
                expand=False,
            )
        )
        return

    lines = [f"ngrok [accent]{ngrok_version(binary)}[/accent] at [accent]{binary}[/accent]"]
    if applied:
        lines.append(f"Authtoken from [accent]{token_source}[/accent], saved to [accent]{config_file()}[/accent]")
        lines.append("Start a tunnel with [accent]ngrok http 3000[/accent]")
    else:
        lines.append("No authtoken configured: run [accent]evo setup ngrok[/accent] again to add one")
    if verified:
        lines.append(f"Verified live at [accent]{verified}[/accent]")
    lines.append("Back the key up with [accent]evo cred sync push[/accent]")

    console.print(
        Panel(
            Text.from_markup("\n".join(lines)),
            title="setup ngrok complete",
            border_style="success",
            expand=False,
        )
    )


@click.command("ngrok", epilog=EPILOG)
@click.option("-t", "--authtoken", help="Paste the authtoken instead of being prompted for it.")
@click.option("--from-stdin", is_flag=True, help="Read the authtoken from stdin.")
@click.option(
    "--method",
    type=click.Choice(["auto", "binary", "package", "none"]),
    default="auto",
    show_default=True,
    help="`auto`/`binary` download the official release into ~/.evo/bin; `package` uses brew/winget/scoop/choco/apt.",
)
@click.option("--reinstall", is_flag=True, help="Install the agent again even if it is already present.")
@click.option("--force", is_flag=True, help="Ask for the authtoken again instead of reusing a stored one.")
@click.option("--skip-token", is_flag=True, help="Install the agent only; do not touch the authtoken.")
@click.option("--verify", is_flag=True, help="Open a throwaway tunnel afterwards to prove the token works.")
@click.option("--no-path", is_flag=True, help="Do not add ~/.evo/bin to the Windows user PATH.")
def setup_ngrok(authtoken, from_stdin, method, reinstall, force, skip_token, verify, no_path):
    """Install the **ngrok** agent and configure its authtoken.

    Downloads the official v3 binary into `~/.evo/bin` - no admin rights needed
    - or installs it through your package manager with `--method package` (brew
    on macOS, winget/scoop/choco on Windows, ngrok's apt repo on Linux).

    The authtoken is asked for during the install unless you paste it up front
    with `--authtoken` or pipe it in with `--from-stdin`. It is saved in the
    omelet credential store as `ngrok_authtoken` and handed to the agent with
    `ngrok config add-authtoken`, so `ngrok http 3000` works straight away. A
    token already in the store, in `NGROK_AUTHTOKEN`, or in ngrok.yml is reused
    unless you pass `--force`.
    """
    step("evo setup ngrok")

    token, source = None, None
    if not skip_token:
        step("Authtoken")
        token, source = collect_token(authtoken, from_stdin, force)
        if source != f"evo cred {CRED_KEY}":
            store_token(token)

    binary = install_agent(method, reinstall, add_path=not no_path)

    applied = False
    verified = None
    if binary and token:
        step("Agent configuration")
        applied = apply_authtoken(binary, token)
        if applied:
            check_config(binary)
    elif binary and skip_token:
        info("authtoken left untouched (--skip-token)")

    if verify:
        step("Verify")
        if binary is None:
            warning("nothing to verify: the agent is not installed")
        else:
            ok, detail = verify_authtoken(binary)
            if ok:
                success(f"the authtoken works - tunnel opened at [accent]{detail}[/accent], then closed")
                verified = detail
            else:
                warning(f"could not open a tunnel: {escape(detail)}")

    print_summary(binary, source, applied, verified)
