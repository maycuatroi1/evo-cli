import hashlib
import json as jsonlib
import platform
import re
import subprocess
import sys
import uuid

import rich_click as click
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from evo_cli.console import console, error, info, step

# Each identifier: (key, human label, contributes to the composite HWID hash).
# Only stable, hard-to-change identifiers feed the hash; disk serial and MAC
# are shown for reference but excluded (disks get swapped, MACs are plural).
ID_SPECS = [
    ("machine_guid", "Machine GUID", True),
    ("system_uuid", "System UUID", True),
    ("board_serial", "Baseboard serial", True),
    ("bios_serial", "BIOS serial", True),
    ("cpu_id", "CPU ID", True),
    ("disk_serial", "Disk serial", False),
    ("mac", "MAC address", False),
]

# Placeholder strings firmware vendors leave in serial fields - useless as IDs.
JUNK_VALUES = {
    "",
    "0",
    "none",
    "n/a",
    "na",
    "null",
    "to be filled by o.e.m.",
    "to be filled by o.e.m",
    "default string",
    "system serial number",
    "system product name",
    "system uuid",
    "00000000-0000-0000-0000-000000000000",
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
    "0123456789",
}

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo hwid[/cyan]                  show hardware identifiers and the composite HWID\n"
    "  [cyan]evo hwid --json[/cyan]           machine-readable output\n"
    "  [cyan]evo hwid --raw[/cyan]            also print the pre-hash composite string\n"
    "  [cyan]evo hwid --salt myapp[/cyan]     namespace the HWID for a specific app/license\n\n"
    "[dim]The HWID is a SHA-256 over the stable identifiers that are present\n"
    "(machine GUID, system UUID, baseboard / BIOS serial, CPU ID). It stays\n"
    "constant across reboots and reinstalls on the same physical machine.[/dim]"
)


def _run(cmd, timeout=10):
    """Run a command, return stdout on success, else None (never raises)."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _clean(value):
    """Normalise a raw identifier; drop firmware placeholder junk to None."""
    if value is None:
        return None
    value = str(value).strip()
    if value.lower() in JUNK_VALUES:
        return None
    return value or None


def _read_file(path):
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return None


def _mac_address():
    """Primary NIC MAC as AA:BB:CC:DD:EE:FF, or None if unavailable/random."""
    node = uuid.getnode()
    # getnode() sets the multicast bit when it has to invent a random address.
    if (node >> 40) & 0x1:
        return None
    return ":".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))


# --- per-platform collection -------------------------------------------------
_PS_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "[pscustomobject]@{"
    "machine_guid=(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Cryptography' -Name MachineGuid).MachineGuid;"
    "system_uuid=(Get-CimInstance Win32_ComputerSystemProduct).UUID;"
    "board_serial=(Get-CimInstance Win32_BaseBoard).SerialNumber;"
    "bios_serial=(Get-CimInstance Win32_BIOS).SerialNumber;"
    "cpu_id=(Get-CimInstance Win32_Processor | Select-Object -First 1).ProcessorId;"
    "disk_serial=(Get-CimInstance Win32_DiskDrive | Select-Object -First 1).SerialNumber"
    "} | ConvertTo-Json -Compress"
)


def _collect_windows(ids):
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT])
    if out:
        try:
            parsed = jsonlib.loads(out)
        except (ValueError, TypeError):
            parsed = {}
        for key in ("machine_guid", "system_uuid", "board_serial", "bios_serial", "cpu_id", "disk_serial"):
            ids[key] = _clean(parsed.get(key))


def _collect_darwin(ids):
    out = _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
    if out:
        uuid_match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
        serial_match = re.search(r'"IOPlatformSerialNumber"\s*=\s*"([^"]+)"', out)
        ids["system_uuid"] = _clean(uuid_match.group(1) if uuid_match else None)
        ids["board_serial"] = _clean(serial_match.group(1) if serial_match else None)


def _collect_linux(ids):
    ids["machine_guid"] = _clean(_read_file("/etc/machine-id") or _read_file("/var/lib/dbus/machine-id"))
    # DMI fields under /sys; product_uuid and board_serial usually need root.
    ids["system_uuid"] = _clean(_read_file("/sys/class/dmi/id/product_uuid"))
    ids["board_serial"] = _clean(_read_file("/sys/class/dmi/id/board_serial"))
    ids["bios_serial"] = _clean(_read_file("/sys/class/dmi/id/product_serial"))


def collect():
    system = platform.system()
    ids = {key: None for key, _, _ in ID_SPECS}
    if system == "Windows":
        _collect_windows(ids)
    elif system == "Darwin":
        _collect_darwin(ids)
    elif system == "Linux":
        _collect_linux(ids)
    ids["mac"] = _mac_address()
    return system, ids


# --- composite HWID ----------------------------------------------------------
def compute_hwid(ids, salt=None):
    used = [key for key, _, in_hash in ID_SPECS if in_hash and ids.get(key)]
    if not used:
        return None, None, None, used
    composite = "|".join(ids[key] for key in used)
    raw = f"{salt}|{composite}" if salt else composite
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
    return digest, digest[:16], composite, used


# --- rendering ---------------------------------------------------------------
def render(system, ids, hwid, short, composite, used, show_raw):
    table = Table(title="Hardware identifiers", title_style="accent", show_header=True, header_style="accent")
    table.add_column("Identifier", style="info", no_wrap=True)
    table.add_column("Value", style="default", overflow="fold")
    table.add_column("In HWID", justify="center", no_wrap=True)

    for key, label, in_hash in ID_SPECS:
        value = ids.get(key)
        value_cell = value if value else "[dim]unavailable[/dim]"
        if in_hash:
            mark = "[success]yes[/success]" if value else "[dim]-[/dim]"
        else:
            mark = "[dim]no[/dim]"
        table.add_row(label, value_cell, mark)
    console.print(table)
    console.print(f"[dim]platform: {system or 'unknown'}[/dim]")

    step("HWID")
    if not hwid:
        error("No stable hardware identifier could be read on this machine.")
        info("On Linux, system UUID and board serial usually need root (try with sudo).")
        return

    if show_raw:
        console.print(f"[dim]composite:[/dim] {composite}")
    console.print(
        Panel(
            f"[bold accent]{hwid}[/bold accent]\n[dim]short:[/dim] [accent]{short}[/accent]",
            title="HWID (SHA-256)",
            border_style="accent",
            expand=False,
        )
    )
    console.print(f"[dim]derived from: {', '.join(used)}[/dim]")


@click.command("hwid", epilog=EPILOG)
@click.option("--salt", "salt", default=None, help="Namespace the HWID with an app/license string before hashing.")
@click.option("--raw", "show_raw", is_flag=True, help="Also print the pre-hash composite string.")
@click.option("--json", "as_json", is_flag=True, help="Print result as JSON.")
def hwid(salt, show_raw, as_json):
    """Show this machine's hardware ID (HWID).

    Reads the stable hardware identifiers available on the current platform -
    **machine GUID**, **system UUID**, **baseboard / BIOS serial** and **CPU
    ID** - then folds them into a single **SHA-256 composite HWID** that stays
    constant across reboots and OS reinstalls on the same physical machine.

    Disk serial and MAC address are shown for reference but kept out of the hash
    (disks get replaced, machines have several MACs). Use `--salt` to derive a
    per-application HWID, and `--json` for machine-readable output.
    """
    try:
        system, ids = collect()
        hwid_value, short, composite, used = compute_hwid(ids, salt)

        if as_json:
            payload = {
                "platform": system,
                "identifiers": ids,
                "salt": salt,
                "components": used,
                "hwid": hwid_value,
                "hwid_short": short,
            }
            console.print_json(jsonlib.dumps(payload, ensure_ascii=False))
            return

        step("evo hwid")
        render(system, ids, hwid_value, short, composite, used, show_raw)
    except click.ClickException:
        raise
    except Exception as exc:
        error(str(exc))
        sys.exit(1)
