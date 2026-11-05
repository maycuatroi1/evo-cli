"""The DPI bypass on macOS: SpoofDPI behind the system proxy, run as a LaunchAgent.

GoodbyeDPI has no macOS build. SpoofDPI is a local HTTP proxy that splits the
TLS ClientHello, so the filter never reads the server name in one piece. It only
does that for the hosts `evo route open` named: every other name passes through
untouched and resolves with the system's DNS server (SpoofDPI's own resolver,
which does not read /etc/resolver). The named hosts resolve over DoH instead,
because a name the filter blocks usually has its DNS answer poisoned too, except
on a network where the DoH endpoint does not answer: see `dns_mode`.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import plistlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from evo_cli.commands.route import _dns
from evo_cli.console import info, run_command

LABEL = "com.evo.route.spoofdpi"
DNS_LABEL = "com.evo.route.dns"
LISTEN_HOST = "127.0.0.1"
# Off 8080, where development servers live.
LISTEN_PORT = 18080

# `chunk` cuts the whole ClientHello into small pieces. SpoofDPI's default `sni`
# split, which cuts inside the server name, gets the handshake reset (medium.com,
# 2026-10), and since 2026-11 so does `first-byte`, which only cuts the record
# header off (www.youtube.com, store.steampowered.com). Chunks of 1 to 64 bytes
# got through, 128 did not, so this keeps well under that.
SPLIT_MODE = "chunk"
CHUNK_SIZE = 16

# SpoofDPI asks this one DoH endpoint and has no fallback, so a network that blocks
# it (an office network that keeps DNS to its own resolver, 2026-11) fails every
# opened host with a 502. There the opened hosts use the system's resolver.
DOH_URL = "https://dns.google/dns-query"
# SpoofDPI gives a whole lookup 3 seconds.
DOH_TIMEOUT = 2.5
# Resolves on any network that is up, the way macOS checks for a captive portal.
ONLINE_PROBE = "captive.apple.com"
RESOLVERS = {"https": f"DoH ({DOH_URL})", "system": "the system resolver"}

# macOS rewrites this file on every change of network.
DNS_SETTINGS = "/var/run/resolv.conf"
# Catches a network that was not answering yet when its settings arrived.
DNS_RECHECK_SECONDS = 120


def config(dns_mode):
    """SpoofDPI's config, with the opened hosts resolving by ``dns_mode``: "https" or "system"."""
    return f"""# Written by `evo route open`; the hosts it opened are listed in hosts.txt.
[dns]
mode = "system"

[https]
skip = true

[[rules]]
name = "evo route open"
priority = 50
match = {{ domains = ["file:hosts.txt"] }}
dns = {{ mode = "{dns_mode}", https-url = "{DOH_URL}", cache = true }}
https = {{ skip = false, split-mode = "{SPLIT_MODE}", chunk-size = {CHUNK_SIZE} }}
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


def agent_path(label=LABEL):
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def log_path():
    return Path.home() / "Library" / "Logs" / "evo-route-spoofdpi.log"


def dns_log_path():
    return Path.home() / "Library" / "Logs" / "evo-route-dns.log"


def listen():
    return f"{LISTEN_HOST}:{LISTEN_PORT}"


def opened():
    """The hosts the bypass covers, as `evo route open` named them."""
    try:
        lines = hosts_path().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [line for line in lines if line and not line.startswith(("#", "*"))]


def _write_hosts(hosts):
    home().mkdir(parents=True, exist_ok=True)
    # `*` stands for one label and `**` for any number: a CDN name like store.fastly.example.com needs the second.
    lines = [pattern for host in sorted(set(hosts)) for pattern in (host, f"**.{host}")]
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


def _write_plist(label, job, log):
    """Write a LaunchAgent; True when its content changed."""
    plist = plistlib.dumps({"Label": label, **job, "StandardOutPath": str(log), "StandardErrorPath": str(log)})
    path = agent_path(label)
    if path.exists() and path.read_bytes() == plist:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plist)
    return True


def _write_agent(exe):
    return _write_plist(LABEL, {"ProgramArguments": _program(exe), "RunAtLoad": True, "KeepAlive": True}, log_path())


def _write_dns_agent():
    """The agent that runs `refresh` with this Python whenever the network changes, and every few minutes."""
    job = {
        "ProgramArguments": [sys.executable, "-m", "evo_cli.commands.route._dnswatch"],
        "RunAtLoad": True,
        "WatchPaths": [DNS_SETTINGS],
        "StartInterval": DNS_RECHECK_SECONDS,
    }
    return _write_plist(DNS_LABEL, job, dns_log_path())


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)


def _domain():
    return f"gui/{os.getuid()}"


def _loaded(label=LABEL):
    return _launchctl("print", f"{_domain()}/{label}").returncode == 0


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


def _doh_answers():
    """Whether DOH_URL answers from this network, asked as SpoofDPI asks it: straight, never through a proxy."""
    question = struct.pack(">HHHHHH", 0, 0x0100, 1, 0, 0, 0) + _dns._encode_qname(ONLINE_PROBE)
    question += struct.pack(">HH", 1, 1)
    query = base64.urlsafe_b64encode(question).rstrip(b"=").decode()
    request = urllib.request.Request(f"{DOH_URL}?dns={query}", headers={"accept": "application/dns-message"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=DOH_TIMEOUT) as response:
            reply = response.read()
    except (OSError, ValueError, http.client.HTTPException):
        return False
    # RCODE 0: the endpoint read the question and answered it.
    return len(reply) >= 12 and reply[3] & 0x0F == 0


def _online():
    try:
        socket.getaddrinfo(ONLINE_PROBE, 80, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return True


def dns_mode():
    """How the opened hosts should resolve on the network the Mac is on now.

    Over DoH where its endpoint answers, since a blocked name is often a poisoned one too. With the system
    resolver where it does not, which is the resolver such a network means to be used. None while nothing
    answers at all, offline or before the network is up, when there is nothing to choose between.
    """
    if _doh_answers():
        return "https"
    return "system" if _online() else None


def current_dns_mode():
    """How the opened hosts resolve in the config SpoofDPI runs with; None before evo wrote one."""
    try:
        text = config_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    found = re.search(r'^dns = \{ mode = "(\w+)"', text, re.MULTILINE)
    return found.group(1) if found else None


def _write_config(dns_mode):
    """Write SpoofDPI's config; True when its content changed."""
    text = config(dns_mode)
    path = config_path()
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    home().mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def refresh():
    """Resolve the opened hosts the way the current network allows; restart SpoofDPI only when that changes.

    The DNS agent runs this on every change of network. Returns the mode in use, None while the bypass is off.
    """
    if not opened() or not _loaded():
        return None
    mode = dns_mode()
    if mode is None:
        return current_dns_mode()
    if _write_config(mode):
        _start(reload=False)
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} opened hosts now resolve with {RESOLVERS[mode]}", flush=True)
    return mode


def _watch_network():
    """(Re)load the DNS agent; launchd runs it once on load, then on every change of network."""
    if _write_dns_agent() or not _loaded(DNS_LABEL):
        _launchctl("bootout", f"{_domain()}/{DNS_LABEL}")
        _launchctl("bootstrap", _domain(), str(agent_path(DNS_LABEL)))


def service_state():
    exe = executable()
    return {
        "installed": agent_path().exists(),
        "running": _loaded() and _listening(),
        "args": " ".join(_program(exe or "spoofdpi")),
        "reason": None,
        "hosts": opened(),
        "proxy": proxied_services(),
        "dns": current_dns_mode(),
    }


def ensure(*hosts):
    """Put ``hosts`` through the bypass: install SpoofDPI, (re)start its agent, point the system proxy at it."""
    conflicts = _proxy_conflicts()
    if conflicts:
        raise RuntimeError(f"A proxy is already set on {', '.join(conflicts)}; evo will not replace it.")
    exe = executable() or install()
    _write_config(dns_mode() or current_dns_mode() or "https")
    _write_hosts([*opened(), *hosts])
    if not _start(reload=_write_agent(exe)):
        return service_state()
    _watch_network()
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
    for label in (DNS_LABEL, LABEL):
        _launchctl("bootout", f"{_domain()}/{label}")
        agent_path(label).unlink(missing_ok=True)
    hosts_path().unlink(missing_ok=True)
    return True
