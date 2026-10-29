"""The DPI bypass on macOS: SpoofDPI behind the system proxy, run as a LaunchAgent.

GoodbyeDPI has no macOS build. SpoofDPI is a local HTTP proxy that splits the
TLS ClientHello, so the filter never reads the server name in one piece. It only
does that for the hosts `evo route open` named: every other name passes through
untouched and resolves with the system resolver, so names only a VPN knows keep
working. The named hosts resolve over DoH instead, because a name the filter
blocks usually has its DNS answer poisoned too.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import socket
import subprocess
import time
from pathlib import Path

from evo_cli.console import info, run_command

LABEL = "com.evo.route.spoofdpi"
LISTEN_HOST = "127.0.0.1"
# Off 8080, where development servers live.
LISTEN_PORT = 18080

# `first-byte` cuts the TLS record header off the rest of the ClientHello, and the
# filter does not put the two back together. SpoofDPI's default `sni` split, which
# cuts inside the server name, still gets the handshake reset (medium.com, 2026-10).
SPLIT_MODE = "first-byte"

CONFIG = f"""# Written by `evo route open`; the hosts it opened are listed in hosts.txt.
[dns]
mode = "system"

[https]
skip = true

[[rules]]
name = "evo route open"
priority = 50
match = {{ domains = ["file:hosts.txt"] }}
dns = {{ mode = "https", cache = true }}
https = {{ skip = false, split-mode = "{SPLIT_MODE}" }}
"""

# networksetup names both proxies by these words: -getwebproxy, -setsecurewebproxystate, ...
PROXY_KINDS = ("webproxy", "securewebproxy")

# Chromium browsers, under ~/Library/Application Support; each profile keeps extension-held settings
# in its Secure Preferences.
BROWSERS = ("Google/Chrome", "BraveSoftware/Brave-Browser", "Microsoft Edge", "Chromium")


def home():
    return Path(os.environ.get("EVO_ROUTE_HOME", str(Path.home() / ".evo" / "route")))


def config_path():
    return home() / "spoofdpi.toml"


def hosts_path():
    return home() / "hosts.txt"


def agent_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path():
    return Path.home() / "Library" / "Logs" / "evo-route-spoofdpi.log"


def listen():
    return f"{LISTEN_HOST}:{LISTEN_PORT}"


def opened():
    """The hosts the bypass covers, as `evo route open` named them."""
    try:
        lines = hosts_path().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [line for line in lines if line and not line.startswith(("#", "*."))]


def _write_hosts(hosts):
    home().mkdir(parents=True, exist_ok=True)
    lines = [pattern for host in sorted(set(hosts)) for pattern in (host, f"*.{host}")]
    hosts_path().write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def executable():
    found = shutil.which("spoofdpi")
    if found:
        return found
    # launchd and some shells start without Homebrew on PATH.
    for candidate in ("/opt/homebrew/bin/spoofdpi", "/usr/local/bin/spoofdpi"):
        if Path(candidate).exists():
            return candidate
    return None


def install():
    brew = shutil.which("brew") or next(
        (path for path in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if Path(path).exists()), None
    )
    if not brew:
        raise RuntimeError("SpoofDPI is not installed and neither is Homebrew - install one of them first.")
    info("SpoofDPI is not installed yet, installing it with Homebrew")
    run_command([brew, "install", "spoofdpi"])
    found = executable()
    if not found:
        raise RuntimeError("brew installed spoofdpi, but it is not on PATH.")
    return found


def _program(exe):
    return [exe, "--config", str(config_path()), "--no-tui", "--listen-addr", listen()]


def _write_agent(exe):
    """Write the LaunchAgent; True when its content changed."""
    plist = plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": _program(exe),
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log_path()),
            "StandardErrorPath": str(log_path()),
        }
    )
    path = agent_path()
    if path.exists() and path.read_bytes() == plist:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plist)
    return True


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)


def _domain():
    return f"gui/{os.getuid()}"


def _loaded():
    return _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0


def _listening(timeout=0.5):
    try:
        with socket.create_connection((LISTEN_HOST, LISTEN_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _wait(condition, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return condition()


def _start(reload):
    """Start or restart the agent and wait for the proxy port. SpoofDPI reads hosts.txt only when it starts."""
    if _loaded() and not reload:
        _launchctl("kickstart", "-k", f"{_domain()}/{LABEL}")
    else:
        if _loaded():
            _launchctl("bootout", f"{_domain()}/{LABEL}")
            _wait(lambda: not _listening(), 5)
        if _listening():
            raise RuntimeError(f"Something else already listens on {listen()}.")
        result = _launchctl("bootstrap", _domain(), str(agent_path()))
        if result.returncode != 0:
            raise RuntimeError(f"launchctl bootstrap failed: {(result.stderr or result.stdout).strip()}")
    return _wait(_listening, 10)


def _networksetup(*args):
    return subprocess.run(
        ["networksetup", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False
    )


def network_services():
    """Enabled network services. The first line of the listing explains that `*` marks a disabled one."""
    lines = _networksetup("-listallnetworkservices").stdout.splitlines()[1:]
    return [line for line in lines if line.strip() and not line.startswith("*")]


def _proxy(service, kind):
    fields = {}
    for line in _networksetup(f"-get{kind}", service).stdout.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields.get("Enabled") == "Yes", fields.get("Server", ""), fields.get("Port", "")


def _ours(server, port):
    return server == LISTEN_HOST and port == str(LISTEN_PORT)


def proxied_services():
    """The services whose system proxy points at the bypass."""
    services = []
    for service in network_services():
        proxies = [_proxy(service, kind) for kind in PROXY_KINDS]
        if any(enabled and _ours(server, port) for enabled, server, port in proxies):
            services.append(service)
    return services


def _proxy_conflicts():
    conflicts = []
    for service in network_services():
        for kind in PROXY_KINDS:
            enabled, server, port = _proxy(service, kind)
            if enabled and not _ours(server, port):
                conflicts.append(f"{service} ({server}:{port})")
    return conflicts


def set_system_proxy():
    services = network_services()
    for service in services:
        for kind in PROXY_KINDS:
            result = _networksetup(f"-set{kind}", service, LISTEN_HOST, str(LISTEN_PORT))
            if result.returncode != 0:
                raise RuntimeError(
                    f"networksetup -set{kind} {service} failed: {(result.stderr or result.stdout).strip()}"
                )
    return services


def clear_system_proxy():
    """Turn off only the proxies that point at the bypass; one the user set stays as it was."""
    cleared = []
    for service in network_services():
        for kind in PROXY_KINDS:
            enabled, server, port = _proxy(service, kind)
            if enabled and _ours(server, port):
                _networksetup(f"-set{kind}state", service, "off")
                if service not in cleared:
                    cleared.append(service)
    return cleared


def _extension_name(profile, ext_id):
    for path in sorted((profile / "Extensions" / ext_id).glob("*/manifest.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8-sig"))
            name = manifest.get("name", "")
            if name.startswith("__MSG_"):
                # A localised name: "__MSG_key__" looked up, ignoring case, in the default locale's messages.
                locale = manifest.get("default_locale", "en")
                messages = json.loads(
                    (path.parent / "_locales" / locale / "messages.json").read_text(encoding="utf-8-sig")
                )
                key = name[len("__MSG_") : -len("__")].lower()
                name = next((value["message"] for k, value in messages.items() if k.lower() == key), "")
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if name:
            return name
    return ext_id


def browser_overrides(support=None):
    """Enabled browser extensions that hold the proxy setting, so their browser never sees the system proxy.

    A VPN extension keeps holding it while switched off, set to `direct`.
    """
    support = Path(support or Path.home() / "Library" / "Application Support")
    found = []
    for browser in BROWSERS:
        for prefs in sorted((support / browser).glob("*/Secure Preferences")):
            try:
                settings = json.loads(prefs.read_text(encoding="utf-8"))["extensions"]["settings"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            for ext_id, ext in settings.items():
                enabled = not ext.get("disable_reasons") and ext.get("state", 1) != 0
                if enabled and "proxy" in ext.get("preferences", {}):
                    name = _extension_name(prefs.parent, ext_id)
                    found.append(f"{browser.split('/')[-1]} ({prefs.parent.name}): {name}")
    return found


def service_state():
    exe = executable()
    return {
        "installed": agent_path().exists(),
        "running": _loaded() and _listening(),
        "args": " ".join(_program(exe or "spoofdpi")),
        "reason": None,
        "hosts": opened(),
        "proxy": proxied_services(),
    }


def ensure(host):
    """Put ``host`` through the bypass: install SpoofDPI, (re)start its agent, point the system proxy at it."""
    conflicts = _proxy_conflicts()
    if conflicts:
        raise RuntimeError(f"A proxy is already set on {', '.join(conflicts)}; evo will not replace it.")
    exe = executable() or install()
    home().mkdir(parents=True, exist_ok=True)
    config_path().write_text(CONFIG, encoding="utf-8")
    _write_hosts([*opened(), host])
    if not _start(reload=_write_agent(exe)):
        return service_state()
    set_system_proxy()
    return service_state()


def forget(host):
    """Take ``host`` out of the bypass; the last one out stops it altogether."""
    hosts = [name for name in opened() if name != host]
    if not hosts:
        return remove()
    _write_hosts(hosts)
    _start(reload=False)
    return True


def remove():
    """Clear the system proxy first, so nothing points at a proxy that is gone, then stop and drop the agent."""
    clear_system_proxy()
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    agent_path().unlink(missing_ok=True)
    hosts_path().unlink(missing_ok=True)
    return True
