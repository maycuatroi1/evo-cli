"""Install and configure OpenCode with common MCP servers."""

import json
import platform
import shutil
import subprocess
from pathlib import Path

import rich_click as click
from rich.panel import Panel
from rich.text import Text

from evo_cli.console import console, error, info, run_command, step, success, warning

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo setup opencode[/cyan]\n"
    "  [cyan]evo setup opencode --global-only[/cyan]\n"
    "  [cyan]evo setup opencode --project .[/cyan]"
)

DEFAULT_MCP_SERVERS = {
    "google-search": {
        "type": "local",
        "command": ["npx", "-y", "@mcp-server/google-search-mcp@latest"],
        "enabled": True,
    },
    "playwright": {
        "type": "local",
        "command": ["npx", "-y", "@playwright/mcp@latest", "--headless"],
        "enabled": True,
    },
}


def is_windows():
    return platform.system() == "Windows"


def get_global_config_dir():
    home = Path.home()
    if is_windows():
        return home / "AppData" / "Roaming" / "opencode"
    return home / ".config" / "opencode"


def get_global_config_path():
    return get_global_config_dir() / "opencode.jsonc"


def ensure_node_installed():
    """Ensure Node.js and npm are available; return their paths."""
    node_cmd = shutil.which("node")
    npm_cmd = shutil.which("npm")
    npx_cmd = shutil.which("npx")

    if node_cmd and npm_cmd and npx_cmd:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, check=True)
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
    servers = [
        "@mcp-server/google-search-mcp@latest",
        "@playwright/mcp@latest",
    ]
    for server in servers:
        info(f"Fetching {server}")
        run_command(
            ["npx", "-y", server, "--version"],
            status=f"Fetching {server}",
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

    header = "// OpenCode global configuration generated by evo setup opencode"
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

    header = "// OpenCode project configuration generated by evo setup opencode"
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
    checks = [
        ("playwright", ["npx", "-y", "@playwright/mcp@latest", "--headless"]),
        ("google-search", ["npx", "-y", "@mcp-server/google-search-mcp@latest"]),
    ]
    for name, cmd in checks:
        try:
            result = subprocess.run(
                cmd,
                input=init_message,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and '"jsonrpc":"2.0"' in result.stdout:
                success(f"MCP server [accent]{name}[/accent] responded to initialize")
            else:
                warning(f"MCP server [accent]{name}[/accent] did not respond as expected")
        except Exception as exc:
            warning(f"Could not verify MCP server [accent]{name}[/accent]: {exc}")


def run_setup_opencode(global_only, project):
    step("evo setup opencode")

    try:
        ensure_node_installed()
    except Exception as exc:
        error(str(exc))
        return

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

    console.print(
        Panel(
            f"OpenCode configured with google-search and playwright MCP servers.\n\n{paths_text}\n\n"
            "Restart OpenCode to load the new MCP tools.",
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
    "--project",
    type=click.Path(file_okay=False, dir_okay=True, writable=True),
    help="Project directory to write opencode.json into. Defaults to the current directory.",
)
def setup_opencode(global_only, project):
    """Install Node.js (if needed), OpenCode MCP servers, and write config files.

    This command bootstraps a fresh machine with the same OpenCode + MCP setup
    used across devices. It installs the google-search and playwright MCP servers,
    downloads the Playwright Chromium browser, and writes both global
    (~/.config/opencode/opencode.jsonc) and project-level (opencode.json) configs.

    No credentials are bundled; only public MCP server references are added.
    """
    run_setup_opencode(global_only, project)
