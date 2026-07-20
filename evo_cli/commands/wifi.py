"""
evo wifi - control Wi-Fi from the terminal.

A cross-platform Wi-Fi remote: show the current connection, scan for nearby
networks, join / leave a network, toggle the radio, manage saved networks and
reveal a stored password - all from one `evo wifi` group.

Backends, picked automatically:

* macOS - ``networksetup`` (power, join, saved networks) plus
  ``system_profiler SPAirPortDataType`` for the live connection and scan
  results. Modern macOS no longer reports the signal of *nearby* networks and
  hides the current SSID from ``networksetup`` for privacy, so the SSID and
  RSSI come from ``system_profiler`` instead.
* Linux - ``nmcli`` (NetworkManager), which covers every operation cleanly.

Every call degrades gracefully: a missing tool or permission is reported, never
crashes.
"""

import json as jsonlib
import os
import platform
import re
import shutil
import subprocess
import sys
import time

import rich_click as click
from rich.table import Table
from rich.text import Text

from evo_cli.console import console, error, info, step, success, warning

# RSSI (dBm) -> signal-quality buckets. Closer to 0 is stronger.
RSSI_EXCELLENT = -55
RSSI_GOOD = -67
RSSI_FAIR = -75
# nmcli reports signal as a 0-100 percentage instead of dBm.
PCT_EXCELLENT = 75
PCT_GOOD = 55
PCT_FAIR = 35

QUALITY_STYLE = {"excellent": "success", "good": "success", "fair": "warning", "weak": "error"}

# system_profiler stores security as e.g. ``spairport_security_mode_wpa2_personal``.
_SEC_PREFIX = "spairport_security_mode_"
_SEC_UPPER = {"wpa", "wpa2", "wpa3", "wep", "eap", "psk", "tkip", "ccmp"}

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo wifi status[/cyan]                     show the current connection\n"
    "  [cyan]evo wifi scan[/cyan]                       list nearby networks (strongest first)\n"
    "  [cyan]evo wifi connect HomeWifi[/cyan]           join a network (prompts for the password)\n"
    "  [cyan]evo wifi connect Cafe -p hunter2[/cyan]    join with the password inline\n"
    "  [cyan]evo wifi disconnect[/cyan]                 leave the current network\n"
    "  [cyan]evo wifi off[/cyan] / [cyan]evo wifi on[/cyan]            power the radio off / on\n"
    "  [cyan]evo wifi saved[/cyan]                      list saved (preferred) networks\n"
    "  [cyan]evo wifi forget HomeWifi[/cyan]            remove a saved network\n"
    "  [cyan]evo wifi password HomeWifi[/cyan]          reveal a saved password\n"
    "  [cyan]evo wifi status --json[/cyan]              machine-readable output\n\n"
    "[dim]macOS uses networksetup + system_profiler; Linux uses nmcli (NetworkManager).\n"
    "Nearby-network signal strength is only reported on Linux - modern macOS hides it.[/dim]"
)


# --- low-level helpers -------------------------------------------------------
def _run(cmd, timeout=20, input_text=None, capture=True):
    """Run a command; return the CompletedProcess, or None if it could not start."""
    try:
        return subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout, input=input_text)
    except (OSError, subprocess.SubprocessError):
        return None


def _stdout(cmd, timeout=20):
    """Return a command's stdout on success (returncode 0), else None."""
    res = _run(cmd, timeout)
    if res is None or res.returncode != 0:
        return None
    return res.stdout


def _err_text(res, default="failed"):
    """Best-effort human message from a CompletedProcess."""
    if res is None:
        return default
    return ((res.stderr or "") + (res.stdout or "")).strip() or default


# --- parsing helpers (pure, unit-testable) -----------------------------------
def parse_macos_interface(text):
    """Extract the Wi-Fi device from ``networksetup -listallhardwareports`` output."""
    is_wifi = False
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            label = line.split(":", 1)[1].strip().lower()
            is_wifi = "wi-fi" in label or "airport" in label
        elif line.startswith("Device:") and is_wifi:
            return line.split(":", 1)[1].strip()
    return None


def parse_preferred(text):
    """Parse ``networksetup -listpreferredwirelessnetworks`` into a list of SSIDs."""
    networks = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("preferred networks"):
            continue
        networks.append(stripped)
    return networks


def parse_rssi(value):
    """Pull the RSSI (dBm) out of ``-76 dBm / -93 dBm``; return int or None."""
    if not value:
        return None
    match = re.search(r"(-?\d+)\s*dBm", value)
    return int(match.group(1)) if match else None


def clean_security(value):
    """Turn ``spairport_security_mode_wpa2_personal_mixed`` into ``WPA2 Personal Mixed``."""
    if not value:
        return None
    token = value.replace(_SEC_PREFIX, "").strip().lower()
    if token in ("", "none", "open"):
        return "Open"
    parts = [p.upper() if p in _SEC_UPPER else p.capitalize() for p in token.split("_")]
    return " ".join(parts)


def is_open(security):
    """True when a network has no encryption."""
    if not security:
        return True
    return security.strip().lower() in ("", "open", "none", "--")


def band_of(channel):
    """Map a channel string like ``149 (5GHz, 80MHz)`` to a friendly band label."""
    if not channel:
        return None
    low = channel.lower()
    if "6ghz" in low:
        return "6 GHz"
    if "5ghz" in low:
        return "5 GHz"
    if "2ghz" in low or "2.4" in low:
        return "2.4 GHz"
    return None


def nmcli_split(line):
    """Split an ``nmcli -t`` line on unescaped colons (fields may contain ``\\:``)."""
    fields, current, index = [], "", 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            current += line[index + 1]
            index += 2
            continue
        if char == ":":
            fields.append(current)
            current = ""
        else:
            current += char
        index += 1
    fields.append(current)
    return fields


# --- signal quality ----------------------------------------------------------
def quality_from_rssi(rssi):
    if rssi is None:
        return None
    if rssi >= RSSI_EXCELLENT:
        return "excellent"
    if rssi >= RSSI_GOOD:
        return "good"
    if rssi >= RSSI_FAIR:
        return "fair"
    return "weak"


def quality_from_pct(pct):
    if pct is None:
        return None
    if pct >= PCT_EXCELLENT:
        return "excellent"
    if pct >= PCT_GOOD:
        return "good"
    if pct >= PCT_FAIR:
        return "fair"
    return "weak"


def entry_quality(entry):
    """Quality bucket for a network dict (prefers dBm, falls back to percent)."""
    if entry.get("rssi") is not None:
        return quality_from_rssi(entry["rssi"])
    return quality_from_pct(entry.get("signal"))


def signal_display(entry):
    """Rich-markup signal cell for a network dict."""
    quality = entry_quality(entry)
    style = QUALITY_STYLE.get(quality, "info")
    if entry.get("rssi") is not None:
        return f"[{style}]{entry['rssi']} dBm ({quality})[/{style}]"
    if entry.get("signal") is not None:
        return f"[{style}]{entry['signal']}% ({quality})[/{style}]"
    return "[dim]-[/dim]"


# --- interface detection -----------------------------------------------------
def macos_interface():
    return parse_macos_interface(_stdout(["networksetup", "-listallhardwareports"]))


def linux_interface():
    out = _stdout(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"])
    if out:
        for line in out.splitlines():
            parts = nmcli_split(line)
            if len(parts) >= 2 and parts[1] == "wifi":
                return parts[0]
    base = "/sys/class/net"
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if os.path.isdir(f"{base}/{name}/wireless"):
                return name
    return None


def interface():
    system = platform.system()
    if system == "Darwin":
        return macos_interface()
    if system == "Linux":
        return linux_interface()
    return None


# --- macOS: system_profiler --------------------------------------------------
def _macos_profiler_iface():
    out = _stdout(["system_profiler", "-json", "SPAirPortDataType"], timeout=30)
    if not out:
        return None
    try:
        data = jsonlib.loads(out)
    except (ValueError, TypeError):
        return None
    for block in data.get("SPAirPortDataType", []) or []:
        ifaces = block.get("spairport_airport_interfaces") or []
        if ifaces:
            return ifaces[0]
    return None


def _macos_network_entry(node, connected=False):
    """Normalise one system_profiler network node into our common dict shape."""
    return {
        "ssid": node.get("_name"),
        "rssi": parse_rssi(node.get("spairport_signal_noise")),
        "signal": None,
        "security": clean_security(node.get("spairport_security_mode")) or "Open",
        "channel": node.get("spairport_network_channel"),
        "band": band_of(node.get("spairport_network_channel")),
        "phymode": node.get("spairport_network_phymode"),
        "rate": node.get("spairport_network_rate"),
        "connected": connected,
    }


def macos_status(iface):
    state = {
        "platform": "Darwin",
        "interface": iface,
        "power": power_status(iface),
        "connected": False,
        "ssid": None,
    }
    prof = _macos_profiler_iface()
    current = (prof or {}).get("spairport_current_network_information")
    if current:
        entry = _macos_network_entry(current, connected=True)
        state.update(
            connected=True, **{k: entry[k] for k in ("ssid", "rssi", "security", "channel", "band", "phymode", "rate")}
        )
    if state["connected"]:
        ip = _stdout(["ipconfig", "getifaddr", iface])
        state["ip"] = ip.strip() if ip else None
    return state


def macos_scan(iface):
    prof = _macos_profiler_iface()
    if not prof:
        return []
    networks, seen = [], set()
    current = prof.get("spairport_current_network_information")
    nodes = []
    if current:
        nodes.append((current, True))
    for node in prof.get("spairport_airport_other_local_wireless_networks") or []:
        nodes.append((node, False))
    for node, connected in nodes:
        entry = _macos_network_entry(node, connected=connected)
        if not entry["ssid"] or entry["ssid"] in seen:
            continue
        seen.add(entry["ssid"])
        networks.append(entry)
    return sort_networks(networks)


def macos_saved(iface):
    return parse_preferred(_stdout(["networksetup", "-listpreferredwirelessnetworks", iface]))


# --- Linux: nmcli ------------------------------------------------------------
def _nmcli_active_field(out, predicate, slot):
    for line in (out or "").splitlines():
        parts = nmcli_split(line)
        if predicate(parts):
            return parts[slot] if slot < len(parts) else None
    return None


def linux_status(iface):
    state = {
        "platform": "Linux",
        "interface": iface,
        "power": power_status(iface),
        "connected": False,
        "ssid": None,
    }
    out = _stdout(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY,CHAN,RATE", "device", "wifi"])
    for line in (out or "").splitlines():
        parts = nmcli_split(line)
        if parts and parts[0] == "yes":
            state["connected"] = True
            state["ssid"] = parts[1] or None
            state["signal"] = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            state["rssi"] = None
            state["security"] = (parts[3] if len(parts) > 3 else "") or "Open"
            state["channel"] = parts[4] if len(parts) > 4 else None
            state["band"] = None
            state["rate"] = parts[5] if len(parts) > 5 else None
            break
    if state["connected"] and iface:
        ip_out = _stdout(["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", iface])
        match = re.search(r"IP4\.ADDRESS\[1\]:\s*(\S+)", ip_out or "")
        if match:
            state["ip"] = match.group(1).split("/")[0]
    return state


def linux_scan(iface):
    fields = ["IN-USE", "SSID", "SIGNAL", "SECURITY", "CHAN"]
    cmd = ["nmcli", "-t", "-f", ",".join(fields), "device", "wifi", "list", "--rescan", "yes"]
    out = _stdout(cmd, timeout=30)
    if out is None:  # --rescan may be denied without privileges; reuse the cache
        out = _stdout(["nmcli", "-t", "-f", ",".join(fields), "device", "wifi", "list"], timeout=30)
    networks, seen = [], set()
    for line in (out or "").splitlines():
        parts = nmcli_split(line)
        if len(parts) < 5 or not parts[1]:
            continue
        ssid = parts[1]
        if ssid in seen:
            continue
        seen.add(ssid)
        networks.append(
            {
                "ssid": ssid,
                "rssi": None,
                "signal": int(parts[2]) if parts[2].isdigit() else None,
                "security": parts[3] or "Open",
                "channel": parts[4],
                "band": None,
                "phymode": None,
                "rate": None,
                "connected": parts[0].strip() == "*",
            }
        )
    return sort_networks(networks)


def linux_saved(iface):
    out = _stdout(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    saved = []
    for line in (out or "").splitlines():
        parts = nmcli_split(line)
        if len(parts) >= 2 and parts[1] in ("wifi", "802-11-wireless"):
            saved.append(parts[0])
    return saved


def sort_networks(networks):
    """Strongest signal first; networks without a reading sink to the bottom."""

    def key(entry):
        value = entry.get("rssi")
        if value is None:
            value = entry.get("signal")
        return (value is None, -(value if value is not None else -9999))

    return sorted(networks, key=key)


# --- power -------------------------------------------------------------------
def power_status(iface):
    """True/False if the radio is on/off, or None if it cannot be determined."""
    system = platform.system()
    if system == "Darwin":
        out = _stdout(["networksetup", "-getairportpower", iface])
        if out and ":" in out:
            return out.split(":", 1)[1].strip().lower() == "on"
    elif system == "Linux":
        out = _stdout(["nmcli", "radio", "wifi"])
        if out:
            return out.strip().lower() == "enabled"
    return None


def set_power(iface, on):
    system = platform.system()
    if system == "Darwin":
        res = _run(["networksetup", "-setairportpower", iface, "on" if on else "off"])
    elif system == "Linux":
        res = _run(["nmcli", "radio", "wifi", "on" if on else "off"])
    else:
        return False, "unsupported platform"
    if res is not None and res.returncode == 0:
        return True, None
    return False, _err_text(res, "could not change Wi-Fi power")


# --- connect / disconnect ----------------------------------------------------
def do_connect(iface, ssid, password):
    system = platform.system()
    if system == "Darwin":
        cmd = ["networksetup", "-setairportnetwork", iface, ssid]
        if password:
            cmd.append(password)
        res = _run(cmd, timeout=45)
        if res is None:
            return False, "could not run networksetup"
        # networksetup is silent on success and prints an error line on failure
        # (often still exiting 0), so any output means it did not join.
        message = _err_text(res, "")
        if res.returncode != 0 or message:
            return False, message or "failed to join network"
        return True, None
    if system == "Linux":
        cmd = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        if iface:
            cmd += ["ifname", iface]
        res = _run(cmd, timeout=45)
        if res is not None and res.returncode == 0:
            return True, (res.stdout or "").strip() or None
        return False, _err_text(res, "failed to join network")
    return False, "unsupported platform"


def do_disconnect(iface):
    system = platform.system()
    if system == "Linux":
        res = _run(["nmcli", "device", "disconnect", iface], timeout=20)
        if res is not None and res.returncode == 0:
            return True, None
        return False, _err_text(res, "could not disconnect")
    if system == "Darwin":
        # The old `airport -z` disassociate tool was removed from macOS, and
        # networksetup has no "leave without powering off" verb. Surface that
        # honestly instead of silently toggling the radio.
        return False, (
            "macOS no longer ships a CLI to leave a network while keeping the radio on. "
            "Use 'evo wifi off' to disconnect, or 'evo wifi forget <ssid>' to stop auto-joining."
        )
    return False, "unsupported platform"


def do_forget(iface, ssid):
    system = platform.system()
    if system == "Darwin":
        cmd = ["networksetup", "-removepreferredwirelessnetwork", iface, ssid]
        res = _run(cmd, timeout=20)
        if res is not None and res.returncode == 0 and "not found" not in _err_text(res, "").lower():
            return True, None
        # Editing the preferred list needs admin rights on modern macOS.
        info("Removing a saved network needs admin rights - sudo may prompt for your password.")
        res = _run(["sudo", *cmd], timeout=60, capture=False)
        if res is not None and res.returncode == 0:
            return True, None
        return False, _err_text(res, "could not remove the saved network")
    if system == "Linux":
        res = _run(["nmcli", "connection", "delete", ssid], timeout=20)
        if res is not None and res.returncode == 0:
            return True, None
        return False, _err_text(res, "could not delete the saved connection")
    return False, "unsupported platform"


def saved_password(iface, ssid):
    """Return (password, error). The password is a stored secret - handle with care."""
    system = platform.system()
    if system == "Darwin":
        info("Reading the Wi-Fi password from the keychain - macOS may prompt for permission.")
        res = _run(["security", "find-generic-password", "-wa", ssid], timeout=30, capture=True)
        if res is not None and res.returncode == 0:
            value = (res.stdout or "").strip()
            if value:
                return value, None
        return None, _err_text(res, "no saved password found (or access was denied)")
    if system == "Linux":
        res = _run(
            ["nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", ssid],
            timeout=20,
        )
        if res is not None and res.returncode == 0:
            value = (res.stdout or "").strip()
            if value:
                return value, None
            return None, "this saved network has no stored PSK (open or enterprise network)"
        return None, _err_text(res, "no saved connection with that name")
    return None, "unsupported platform"


# --- platform dispatch -------------------------------------------------------
def collect_status(iface):
    return macos_status(iface) if platform.system() == "Darwin" else linux_status(iface)


def collect_scan(iface):
    return macos_scan(iface) if platform.system() == "Darwin" else linux_scan(iface)


def collect_saved(iface):
    return macos_saved(iface) if platform.system() == "Darwin" else linux_saved(iface)


def prepare():
    """Validate platform + backend and return (system, interface), or exit."""
    system = platform.system()
    if system not in ("Darwin", "Linux"):
        error(f"evo wifi supports macOS and Linux, not {system}.")
        sys.exit(1)
    if system == "Linux" and not shutil.which("nmcli"):
        error("'nmcli' (NetworkManager) is required on Linux. Install the 'network-manager' package.")
        sys.exit(1)
    iface = interface()
    if not iface:
        error("No Wi-Fi interface found on this machine.")
        sys.exit(1)
    return system, iface


# --- rendering ---------------------------------------------------------------
def render_status(state):
    table = Table(title="Wi-Fi", title_style="accent", show_header=True, header_style="accent")
    table.add_column("Field", style="info", no_wrap=True)
    table.add_column("Value")

    power = state.get("power")
    power_cell = "[dim]unknown[/dim]"
    if power is True:
        power_cell = "[success]on[/success]"
    elif power is False:
        power_cell = "[error]off[/error]"
    table.add_row("Radio", power_cell)
    table.add_row("Interface", state.get("interface") or "-")

    if state.get("connected"):
        table.add_row("Network", f"[accent]{state.get('ssid') or '-'}[/accent]")
        table.add_row("Signal", signal_display(state))
        table.add_row("Security", _security_cell(state.get("security")))
        if state.get("channel"):
            band = f" - {state['band']}" if state.get("band") else ""
            table.add_row("Channel", f"{state['channel']}{band}")
        if state.get("rate"):
            table.add_row(
                "Tx rate", f"{state['rate']} Mbps" if isinstance(state["rate"], (int, float)) else str(state["rate"])
            )
        if state.get("phymode"):
            table.add_row("PHY mode", state["phymode"])
        if state.get("ip"):
            table.add_row("IP address", state["ip"])
    else:
        table.add_row("Network", "[dim]not connected[/dim]")
    console.print(table)


def _security_cell(security):
    if is_open(security):
        return "[warning]Open (no encryption)[/warning]"
    return security or "-"


def render_scan(networks):
    table = Table(show_header=True, header_style="accent", expand=False)
    table.add_column("", width=1, no_wrap=True)
    table.add_column("SSID", style="info", no_wrap=True)
    table.add_column("Signal", justify="right")
    table.add_column("Security")
    table.add_column("Channel", justify="right")
    table.add_column("Mode", style="dim")

    for net in networks:
        marker = "[success]•[/success]" if net.get("connected") else ""
        ssid = f"[accent]{net['ssid']}[/accent]" if net.get("connected") else net["ssid"]
        band = net.get("band")
        channel = net.get("channel") or "-"
        if band and band not in str(channel):
            channel = f"{channel}"
        table.add_row(
            marker,
            ssid,
            signal_display(net),
            _security_cell(net.get("security")),
            str(channel),
            net.get("phymode") or "",
        )
    console.print(table)


def render_saved(networks):
    if not networks:
        info("No saved networks.")
        return
    table = Table(title="Saved networks", title_style="accent", show_header=True, header_style="accent")
    table.add_column("#", style="dim", justify="right", no_wrap=True)
    table.add_column("SSID", style="info")
    for index, ssid in enumerate(networks, 1):
        table.add_row(str(index), ssid)
    console.print(table)


def status_notes(state):
    notes = []
    if state.get("power") is False:
        notes.append(("warning", "Wi-Fi radio is off - run 'evo wifi on' to enable it."))
        return notes
    if not state.get("connected"):
        notes.append(("info", "Not connected to any network - 'evo wifi scan' to see what is nearby."))
        return notes
    quality = entry_quality(state)
    if quality == "weak":
        notes.append(("warning", "Signal is weak - move closer to the access point or pick a better band."))
    elif quality == "fair":
        notes.append(("info", "Signal is only fair - fine for browsing, may stutter on calls."))
    if is_open(state.get("security")):
        notes.append(("warning", f"'{state.get('ssid')}' is an open network - traffic is unencrypted."))
    return notes


# --- CLI ---------------------------------------------------------------------
@click.group("wifi", epilog=EPILOG, context_settings={"help_option_names": ["-h", "--help"]})
def wifi():
    """Control **Wi-Fi** from the terminal.

    Show the current connection, scan for nearby networks, join or leave a
    network, toggle the radio and manage saved networks. Uses `networksetup` +
    `system_profiler` on macOS and `nmcli` on Linux - no extra dependencies.

    Run `evo wifi <command> -h` for the options of each subcommand.
    """


@wifi.command("status")
@click.option("--json", "as_json", is_flag=True, help="Print result as JSON.")
def status_cmd(as_json):
    """Show the current Wi-Fi connection (SSID, signal, security, channel, IP)."""
    _, iface = prepare()
    with console.status("[info]reading Wi-Fi state...[/info]", spinner="dots"):
        state = collect_status(iface)
    if as_json:
        console.print_json(jsonlib.dumps(state, ensure_ascii=False))
        return
    step("evo wifi status")
    render_status(state)
    notes = status_notes(state)
    for level, message in notes:
        {"warning": warning, "error": error}.get(level, info)(message)


@wifi.command("scan")
@click.option("-n", "--limit", default=0, help="Show only the N strongest networks (0 = all).")
@click.option("--json", "as_json", is_flag=True, help="Print result as JSON.")
def scan_cmd(limit, as_json):
    """Scan for nearby Wi-Fi networks, strongest signal first.

    On Linux each network's signal strength is shown; modern macOS only reports
    the signal of the network you are *connected* to, so nearby entries show
    `-` there.
    """
    _, iface = prepare()
    with console.status("[info]scanning for networks...[/info]", spinner="dots"):
        networks = collect_scan(iface)
    if limit and limit > 0:
        networks = networks[:limit]
    if as_json:
        console.print_json(jsonlib.dumps(networks, ensure_ascii=False))
        return
    step("evo wifi scan")
    if not networks:
        warning("No networks found. Is the radio on? Try 'evo wifi on'.")
        return
    render_scan(networks)
    console.print(f"[dim]{len(networks)} network(s)[/dim]")


@wifi.command("saved")
@click.option("--json", "as_json", is_flag=True, help="Print result as JSON.")
def saved_cmd(as_json):
    """List saved / preferred networks this machine will auto-join."""
    _, iface = prepare()
    networks = collect_saved(iface)
    if as_json:
        console.print_json(jsonlib.dumps(networks, ensure_ascii=False))
        return
    step("evo wifi saved")
    render_saved(networks)


@wifi.command("connect")
@click.argument("ssid")
@click.option("-p", "--password", default=None, help="Network password (omit to be prompted when needed).")
@click.option("-a", "--ask-password", is_flag=True, help="Always prompt for a password (hidden input).")
def connect_cmd(ssid, password, ask_password):
    """Join the Wi-Fi network `SSID`.

    If no password is given, the network is looked up in a quick scan: secured
    networks prompt for a password (hidden), open networks join straight away.
    Use `-a/--ask-password` to force a prompt regardless.
    """
    _, iface = prepare()
    if not password:
        if ask_password:
            password = click.prompt(f"Password for {ssid}", hide_input=True)
        else:
            match = next((n for n in collect_scan(iface) if n["ssid"] == ssid), None)
            if match and not is_open(match.get("security")):
                password = click.prompt(f"Password for {ssid}", hide_input=True)

    step("evo wifi connect")
    with console.status(f"[info]joining {ssid}...[/info]", spinner="dots"):
        ok, message = do_connect(iface, ssid, password)
    if not ok:
        error(f"Could not join '{ssid}': {message}")
        sys.exit(1)

    time.sleep(2)  # give DHCP a moment before we read back the connection
    state = collect_status(iface)
    if state.get("connected") and state.get("ssid") == ssid:
        ip = f" ({state['ip']})" if state.get("ip") else ""
        success(f"Connected to [accent]{ssid}[/accent]{ip}.")
    else:
        success(f"Join command accepted for [accent]{ssid}[/accent].")
        info("Run 'evo wifi status' to confirm the connection.")


@wifi.command("disconnect")
def disconnect_cmd():
    """Leave the current Wi-Fi network (keeps the radio on, Linux only)."""
    _, iface = prepare()
    step("evo wifi disconnect")
    ok, message = do_disconnect(iface)
    if ok:
        success("Disconnected.")
    else:
        warning(message)


@wifi.command("on")
def on_cmd():
    """Turn the Wi-Fi radio on."""
    _, iface = prepare()
    step("evo wifi on")
    ok, message = set_power(iface, True)
    if ok:
        success("Wi-Fi radio is on.")
    else:
        error(message)
        sys.exit(1)


@wifi.command("off")
def off_cmd():
    """Turn the Wi-Fi radio off (also disconnects)."""
    _, iface = prepare()
    step("evo wifi off")
    ok, message = set_power(iface, False)
    if ok:
        success("Wi-Fi radio is off.")
    else:
        error(message)
        sys.exit(1)


@wifi.command("forget")
@click.argument("ssid")
def forget_cmd(ssid):
    """Remove `SSID` from the saved networks so it is no longer auto-joined."""
    _, iface = prepare()
    step("evo wifi forget")
    ok, message = do_forget(iface, ssid)
    if ok:
        success(f"Removed '{ssid}' from saved networks.")
    else:
        error(f"Could not forget '{ssid}': {message}")
        sys.exit(1)


@wifi.command("password")
@click.argument("ssid", required=False)
def password_cmd(ssid):
    """Reveal the saved password for `SSID` (defaults to the current network).

    The password is read from the system keychain (macOS) or NetworkManager
    (Linux) and printed to the terminal - run this only where it is safe to do
    so.
    """
    _, iface = prepare()
    if not ssid:
        state = collect_status(iface)
        ssid = state.get("ssid")
        if not ssid:
            error("Not connected to a network - pass an SSID: 'evo wifi password <ssid>'.")
            sys.exit(1)
    step("evo wifi password")
    value, message = saved_password(iface, ssid)
    if value is None:
        error(f"Could not read the password for '{ssid}': {message}")
        sys.exit(1)
    console.print(f"[info]{ssid}[/info]  [accent]{value}[/accent]")
    warning("That is a secret - clear your terminal if others can see it.")
