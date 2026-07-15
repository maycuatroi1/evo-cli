import os
import platform
import sys
import uuid

import rich_click as click
from rich.text import Text

from evo_cli.console import console, error, info, step, success, warning

# Only the OS-generated machine identifier is rotated here. Firmware / hardware
# identifiers (system UUID, baseboard / BIOS serial, CPU ID) live in SMBIOS and
# are read-only from software, so they are left untouched - see the docstring.
LINUX_MACHINE_ID = "/etc/machine-id"
LINUX_DBUS_MACHINE_ID = "/var/lib/dbus/machine-id"
WIN_CRYPTO_KEY = r"SOFTWARE\Microsoft\Cryptography"

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo hwid-reset[/cyan]                  generate a new machine identifier (asks first)\n"
    "  [cyan]evo hwid-reset --dry-run[/cyan]        preview the change without writing anything\n"
    "  [cyan]evo hwid-reset -y[/cyan]               apply without the confirmation prompt\n"
    "  [cyan]evo hwid-reset --set <guid>[/cyan]     restore a specific value you saved earlier\n\n"
    "[dim]Only the OS-level identifier is rotated: the Windows Machine GUID\n"
    "(HKLM\\SOFTWARE\\Microsoft\\Cryptography) or the Linux machine-id. Firmware\n"
    "identifiers (system UUID, board / BIOS serial, CPU ID) are read-only and stay\n"
    "the same, so 'evo hwid' will change only in its machine_guid component.\n"
    "Needs Administrator (Windows) or root (Linux). Save the old value to restore it.[/dim]"
)


def _normalize_guid(value):
    """Validate/normalise a Windows Machine GUID to lowercase, no braces."""
    try:
        return str(uuid.UUID(value.strip().strip("{}")))
    except (ValueError, AttributeError):
        raise click.BadParameter(f"not a valid GUID: {value}")


def _normalize_machine_id(value):
    """Validate/normalise a Linux machine-id to 32 lowercase hex chars."""
    cleaned = value.strip().lower().replace("-", "")
    try:
        return uuid.UUID(cleaned).hex
    except ValueError:
        raise click.BadParameter(f"not a valid machine-id: {value}")


# --- Windows -----------------------------------------------------------------
def _win_is_admin():
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _win_read_guid():
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WIN_CRYPTO_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        try:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return value
        finally:
            winreg.CloseKey(key)
    except OSError:
        return None


def _win_write_guid(value):
    import winreg

    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WIN_CRYPTO_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
    try:
        winreg.SetValueEx(key, "MachineGuid", 0, winreg.REG_SZ, value)
    finally:
        winreg.CloseKey(key)


def reset_windows(set_value, dry_run, assume_yes):
    old = _win_read_guid()
    new = _normalize_guid(set_value) if set_value else str(uuid.uuid4())

    info(r"Machine GUID - HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid")
    console.print(f"  [dim]old:[/dim] {old or '[dim]unavailable[/dim]'}")
    console.print(f"  [accent]new:[/accent] {new}")

    if dry_run:
        info("dry-run: nothing was changed.")
        return
    if not _win_is_admin():
        error("Administrator privileges are required. Re-run evo from an elevated terminal.")
        sys.exit(1)
    if not assume_yes and not click.confirm("Write the new Machine GUID now?", default=False):
        info("aborted - nothing changed.")
        return

    try:
        _win_write_guid(new)
    except PermissionError:
        error("Access denied writing the registry. Run from an elevated (Administrator) terminal.")
        sys.exit(1)
    success("Machine GUID updated.")
    if old:
        warning(f"To restore the previous value: evo hwid-reset --set {old}")
    info("Run 'evo hwid' to see the new composite HWID.")


# --- Linux -------------------------------------------------------------------
def _read(path):
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return None


def reset_linux(set_value, dry_run, assume_yes):
    old = _read(LINUX_MACHINE_ID)
    new = _normalize_machine_id(set_value) if set_value else uuid.uuid4().hex

    info(f"machine-id - {LINUX_MACHINE_ID} (and {LINUX_DBUS_MACHINE_ID})")
    console.print(f"  [dim]old:[/dim] {old or '[dim]unavailable[/dim]'}")
    console.print(f"  [accent]new:[/accent] {new}")

    if dry_run:
        info("dry-run: nothing was changed.")
        return
    if os.geteuid() != 0:
        error("Root privileges are required. Re-run with sudo.")
        sys.exit(1)
    if not assume_yes and not click.confirm("Write the new machine-id now?", default=False):
        info("aborted - nothing changed.")
        return

    try:
        for path in (LINUX_MACHINE_ID, LINUX_DBUS_MACHINE_ID):
            if path == LINUX_DBUS_MACHINE_ID and not os.path.exists(os.path.dirname(path)):
                continue
            with open(path, "w") as handle:
                handle.write(new + "\n")
    except OSError as exc:
        error(f"Failed to write machine-id: {exc}")
        sys.exit(1)
    success("machine-id updated.")
    if old:
        warning(f"To restore the previous value: evo hwid-reset --set {old}")
    info("A reboot is recommended so all services pick up the new machine-id.")
    info("Run 'evo hwid' to see the new composite HWID.")


# --- macOS -------------------------------------------------------------------
def reset_darwin():
    warning("macOS has no software-writable machine identifier to reset.")
    info("The system UUID and hardware serial are stored in firmware and are read-only.")


@click.command("hwid-reset", epilog=EPILOG)
@click.option("--set", "set_value", default=None, help="Set an explicit value instead of generating one.")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show what would change without modifying anything.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Skip the confirmation prompt.")
def hwid_reset(set_value, dry_run, assume_yes):
    """Reset this machine's OS-level hardware identifier.

    Rotates the only identifier that software can legitimately regenerate: the
    **Windows Machine GUID** (`HKLM\\SOFTWARE\\Microsoft\\Cryptography`) or the
    **Linux machine-id** (`/etc/machine-id`). This is the same operation Windows
    `sysprep` and `systemd-machine-id-setup` perform when a machine is cloned
    from an image.

    Firmware and hardware identifiers used by `evo hwid` - **system UUID**,
    **baseboard / BIOS serial** and **CPU ID** - live in SMBIOS and are read-only
    from software, so they are left unchanged; only the `machine_guid` component
    of the composite HWID changes. The old value is printed so you can restore it
    with `--set`. Requires Administrator (Windows) or root (Linux); use
    `--dry-run` to preview.
    """
    step("evo hwid-reset")
    system = platform.system()
    try:
        if system == "Windows":
            reset_windows(set_value, dry_run, assume_yes)
        elif system == "Linux":
            reset_linux(set_value, dry_run, assume_yes)
        elif system == "Darwin":
            reset_darwin()
        else:
            error(f"Unsupported platform: {system or 'unknown'}")
            sys.exit(1)
    except click.ClickException:
        raise
    except Exception as exc:
        error(str(exc))
        sys.exit(1)
