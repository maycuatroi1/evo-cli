"""Install and configure OpenCode with common MCP servers."""

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

import rich_click as click
from rich.panel import Panel
from rich.text import Text

from evo_cli.console import console, error, info, resolve_executable, run_command, step, success, warning
from evo_cli.mcp_registry import opencode_servers

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo setup opencode[/cyan]\n"
    "  [cyan]evo setup opencode --global-only[/cyan]\n"
    "  [cyan]evo setup opencode --project .[/cyan]"
)

# Servers bootstrapped by `evo setup opencode`, pulled from the shared library
# (evo_cli/mcp_registry.py). Add more servers anytime with `evo mcp add <name>`.
# Web search is handled by OpenCode's native Exa-backed `websearch` tool (enabled
# via OPENCODE_ENABLE_EXA, no API key), not a scraping MCP server.
DEFAULT_MCP_SERVERS = opencode_servers("playwright")

# OpenCode's built-in `websearch` tool only activates for the OpenCode provider or
# when this env var is truthy, so custom providers need it set explicitly.
EXA_ENV_VAR = "OPENCODE_ENABLE_EXA"


def _local_mcp_commands():
    """Commands for the local (stdio) servers in DEFAULT_MCP_SERVERS."""
    return [(name, list(cfg["command"])) for name, cfg in DEFAULT_MCP_SERVERS.items() if cfg.get("type") == "local"]


def is_windows():
    return platform.system() == "Windows"


def get_global_config_dir():
    # OpenCode reads ~/.config/opencode on every platform, Windows included - it does
    # not follow the %APPDATA% convention. Writing to AppData produced a file that
    # `opencode mcp list` never saw, so every server evo "added" was silently inert.
    return Path.home() / ".config" / "opencode"


def get_global_config_path():
    directory = get_global_config_dir()
    for name in ("opencode.jsonc", "opencode.json"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    return directory / "opencode.jsonc"


def ensure_node_installed():
    """Ensure Node.js and npm are available; return their paths."""
    node_cmd = shutil.which("node")
    npm_cmd = shutil.which("npm")
    npx_cmd = shutil.which("npx")

    if node_cmd and npm_cmd and npx_cmd:
        result = subprocess.run(
            resolve_executable(["node", "--version"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        info(f"Node.js found: {result.stdout.strip()}")
        return node_cmd, npm_cmd, npx_cmd

    info("Node.js/npm not found. Installing via package manager...")
    system = platform.system()
    if system == "Linux":
        try:
            if shutil.which("apt-get"):
                run_command(
                    ["sudo", "apt-get", "update"],
                    status="Updating apt package lists",
                )
                run_command(
                    ["sudo", "apt-get", "install", "-y", "nodejs", "npm"],
                    status="Installing nodejs and npm",
                )
            elif shutil.which("dnf"):
                run_command(
                    ["sudo", "dnf", "install", "-y", "nodejs", "npm"],
                    status="Installing nodejs and npm",
                )
            elif shutil.which("pacman"):
                run_command(
                    ["sudo", "pacman", "-Sy", "--noconfirm", "nodejs", "npm"],
                    status="Installing nodejs and npm",
                )
            elif shutil.which("apk"):
                run_command(
                    ["sudo", "apk", "add", "nodejs", "npm"],
                    status="Installing nodejs and npm",
                )
            else:
                raise RuntimeError("No supported package manager found (apt-get, dnf, pacman, apk)")
        except Exception as exc:
            error(f"Could not install Node.js automatically: {exc}")
            info("Please install Node.js 18+ manually from https://nodejs.org/")
            raise
    elif system == "Darwin":
        if shutil.which("brew"):
            run_command(["brew", "install", "node"], status="Installing Node.js via Homebrew")
        else:
            raise RuntimeError("Homebrew not found. Please install Node.js 18+ manually.")
    elif system == "Windows":
        raise RuntimeError("Please install Node.js 18+ manually from https://nodejs.org/")
    else:
        raise RuntimeError(f"Unsupported platform: {system}")

    node_cmd = shutil.which("node")
    npm_cmd = shutil.which("npm")
    npx_cmd = shutil.which("npx")
    if not (node_cmd and npm_cmd and npx_cmd):
        raise RuntimeError("Node.js installation succeeded but binaries are not on PATH")
    return node_cmd, npm_cmd, npx_cmd


def install_mcp_servers():
    """Pre-fetch MCP server npm packages into the npx cache.

    MCP servers speak JSON-RPC over stdio, so most do not implement a real
    ``--version`` flag - they simply start up and block reading stdin. Run with
    stdin detached (DEVNULL) so a server that ignores ``--version`` sees EOF and
    exits right away instead of hanging on the terminal, and cap each call with a
    timeout as a safety net. ``check=False`` keeps a non-zero exit from aborting
    the whole setup; the verify step confirms the servers actually work.
    """
    step("Installing MCP servers")
    for name, command in _local_mcp_commands():
        info(f"Fetching {name}")
        run_command(
            [*command, "--version"],
            status=f"Fetching {name}",
            stdin=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
    success("MCP server packages ready")


def install_playwright_browsers():
    """Install Playwright Chromium browser binaries."""
    step("Installing Playwright browsers")
    try:
        run_command(
            ["npx", "playwright", "install", "chromium"],
            status="Downloading Chromium for Playwright",
            stdin=subprocess.DEVNULL,
            timeout=600,
        )
        success("Playwright Chromium installed")
    except Exception as exc:
        warning(f"Playwright browser install failed: {exc}")
        info("You can retry later with: npx playwright install chromium")


def opencode_version():
    """Return the installed OpenCode version string, or None if not on PATH."""
    if not shutil.which("opencode"):
        return None
    try:
        result = subprocess.run(
            resolve_executable(["opencode", "--version"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def install_opencode(npm_cmd="npm"):
    """Install the OpenCode CLI globally via npm if it is not already present."""
    step("Installing OpenCode")
    version = opencode_version()
    if version is not None:
        info(f"OpenCode already installed ({version}); skipping")
        return True

    try:
        run_command(
            [npm_cmd, "install", "-g", "opencode-ai@latest"],
            status="Installing OpenCode via npm",
            timeout=300,
        )
    except Exception as exc:
        warning(f"Could not install OpenCode automatically: {exc}")
        info("Install manually: npm i -g opencode-ai@latest (or curl -fsSL https://opencode.ai/install | bash)")
        return False

    version = opencode_version()
    if version is None:
        warning("OpenCode installed but `opencode` is not on PATH yet")
        info("Open a new shell, or add your npm global bin directory to PATH")
        return False
    success(f"OpenCode installed ({version})")
    return True


def load_jsonc(path):
    """Load a JSONC file, stripping // comments.

    Be careful not to truncate URLs that contain '://' inside a JSON string.
    """
    path = Path(path)
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    cleaned_lines = []
    for line in text.splitlines():
        new_line = []
        in_string = False
        escape = False
        for idx, ch in enumerate(line):
            if escape:
                new_line.append(ch)
                escape = False
                continue
            if ch == "\\":
                new_line.append(ch)
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                new_line.append(ch)
                continue
            if not in_string and ch == "/" and idx + 1 < len(line) and line[idx + 1] == "/":
                break
            new_line.append(ch)
        stripped = "".join(new_line).strip()
        if stripped.startswith("//"):
            continue
        cleaned_lines.append("".join(new_line))
    cleaned = "\n".join(cleaned_lines)
    if not cleaned.strip():
        return {}
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def save_jsonc(path, data, header=None):
    """Save data as pretty-printed JSONC."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False)
    if header:
        content = header + "\n" + content
    path.write_text(content + "\n", encoding="utf-8")


def merge_mcp_config(existing, new_servers):
    """Merge new MCP servers into existing config, preserving user values."""
    result = dict(existing)
    mcp = result.setdefault("mcp", {})
    for name, config in new_servers.items():
        if name in mcp:
            info(f"MCP server [accent]{name}[/accent] already configured; skipping")
            continue
        mcp[name] = dict(config)
        success(f"Added MCP server [accent]{name}[/accent]")
    return result


def configure_opencode_global():
    """Write/update the global OpenCode config with MCP servers."""
    step("Configuring global OpenCode")
    config_path = get_global_config_path()
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    existing = load_jsonc(config_path) if config_path.exists() else {}
    merged = merge_mcp_config(existing, DEFAULT_MCP_SERVERS)

    header = "// OpenCode global configuration generated by evo"
    save_jsonc(config_path, merged, header=header)
    success(f"Global config written to [accent]{config_path}[/accent]")
    return config_path


def configure_opencode_project(project_path):
    """Write/update a project-level OpenCode config with MCP servers."""
    step("Configuring project OpenCode")
    project_dir = Path(project_path).resolve()
    config_path = project_dir / "opencode.json"

    existing = load_jsonc(config_path) if config_path.exists() else {}
    merged = merge_mcp_config(existing, DEFAULT_MCP_SERVERS)

    header = "// OpenCode project configuration generated by evo"
    save_jsonc(config_path, merged, header=header)
    success(f"Project config written to [accent]{config_path}[/accent]")
    return config_path


def verify_mcp_servers():
    """Run a basic JSON-RPC initialize check against installed MCP servers."""
    step("Verifying MCP servers")
    init_message = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize",'
        '"params":{"protocolVersion":"2024-11-05","capabilities":{},'
        '"clientInfo":{"name":"evo-cli","version":"1.0"}}}'
    )
    for name, cmd in _local_mcp_commands():
        try:
            result = subprocess.run(
                resolve_executable(cmd),
                input=init_message,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if result.returncode == 0 and '"jsonrpc":"2.0"' in result.stdout:
                success(f"MCP server [accent]{name}[/accent] responded to initialize")
            else:
                warning(f"MCP server [accent]{name}[/accent] did not respond as expected")
        except Exception as exc:
            warning(f"Could not verify MCP server [accent]{name}[/accent]: {exc}")


def get_shell_rc_path():
    """Return the shell rc file to wire env vars into, based on the active shell."""
    shell = os.environ.get("SHELL", "")
    home = Path.home()
    if shell.endswith("zsh"):
        return home / ".zshrc"
    if shell.endswith("bash"):
        # macOS login shells read .bash_profile; Linux reads .bashrc.
        return home / (".bash_profile" if platform.system() == "Darwin" else ".bashrc")
    if shell.endswith("fish"):
        return home / ".config" / "fish" / "config.fish"
    return home / ".profile"


def enable_exa_websearch():
    """Enable OpenCode's built-in Exa web search via the OPENCODE_ENABLE_EXA env var.

    OpenCode ships a native ``websearch`` tool backed by Exa's hosted MCP service
    (no API key needed), but it is only active when using the OpenCode provider or
    when ``OPENCODE_ENABLE_EXA`` is truthy. Custom providers therefore need the env
    var, so wire it into the shell rc idempotently.
    """
    step("Enabling Exa web search")
    if is_windows():
        info(f"Set the {EXA_ENV_VAR}=1 environment variable to enable web search.")
        info("PowerShell: [accent]setx OPENCODE_ENABLE_EXA 1[/accent]")
        return

    rc_path = get_shell_rc_path()
    if rc_path.exists() and EXA_ENV_VAR in rc_path.read_text(encoding="utf-8"):
        info(f"{EXA_ENV_VAR} already set in [accent]{rc_path}[/accent]; skipping")
        success("Exa web search already enabled")
        return

    rc_path.parent.mkdir(parents=True, exist_ok=True)
    block = f"\n# opencode web search (Exa native, no API key) - added by evo setup opencode\nexport {EXA_ENV_VAR}=1\n"
    with rc_path.open("a", encoding="utf-8") as fh:
        fh.write(block)
    success(f"Enabled Exa web search in [accent]{rc_path}[/accent]")
    info(f"Run [accent]source {rc_path}[/accent] or open a new terminal to apply.")


def run_setup_opencode(global_only, project, skip_install=False):
    step("evo setup opencode")

    try:
        _, npm_cmd, _ = ensure_node_installed()
    except Exception as exc:
        error(str(exc))
        return

    if not skip_install:
        install_opencode(npm_cmd)
    enable_exa_websearch()
    install_mcp_servers()
    install_playwright_browsers()

    global_path = configure_opencode_global()
    project_path = None
    if not global_only:
        target = project or Path.cwd()
        project_path = configure_opencode_project(target)

    verify_mcp_servers()

    console.print()
    paths_text = f"Global: [accent]{global_path}[/accent]"
    if project_path:
        paths_text += f"\nProject: [accent]{project_path}[/accent]"

    opencode_note = (
        "Run `opencode` to start."
        if opencode_version() is not None
        else "Install OpenCode with: npm i -g opencode-ai@latest"
    )
    console.print(
        Panel(
            "OpenCode web search enabled via Exa (native, no API key); "
            f"playwright MCP server configured for browser automation.\n\n{paths_text}\n\n"
            f"{opencode_note} Open a new terminal (or `source` your shell rc), then restart OpenCode.",
            title="setup opencode complete",
            border_style="success",
            expand=False,
        )
    )


@click.command("opencode", epilog=EPILOG)
@click.option(
    "--global-only",
    is_flag=True,
    help="Only update the global OpenCode config; skip project-level config.",
)
@click.option(
    "--skip-install",
    is_flag=True,
    help="Skip installing the OpenCode CLI; only set up MCP servers and config.",
)
@click.option(
    "--project",
    type=click.Path(file_okay=False, dir_okay=True, writable=True),
    help="Project directory to write opencode.json into. Defaults to the current directory.",
)
def setup_opencode(global_only, skip_install, project):
    """Install OpenCode and Node.js (if needed), its MCP servers, and write config files.

    This command bootstraps a fresh machine with the same OpenCode + MCP setup
    used across devices. It installs the OpenCode CLI (via npm, unless
    --skip-install), enables OpenCode's native Exa-backed web search (via the
    OPENCODE_ENABLE_EXA env var, no API key required), installs the playwright MCP
    server for browser automation, downloads the Playwright Chromium browser, and
    writes both global (~/.config/opencode/opencode.jsonc) and project-level
    (opencode.json) configs.

    No credentials are bundled; only public MCP server references are added.
    """
    run_setup_opencode(global_only, project, skip_install=skip_install)
