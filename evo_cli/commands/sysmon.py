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

# Thresholds used to turn raw numbers into a plain-language verdict.
TEMP_WARM = 70.0  # °C - normal under load, worth noting
TEMP_HOT = 90.0  # °C - heavy load / poor cooling, may throttle soon
CPU_BUSY = 85.0  # % overall CPU usage
MEM_HIGH = 85.0  # % memory in use
NOMINAL_PRESSURE = {"nominal", ""}

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo sysmon[/cyan]                    one-shot temperature + performance snapshot\n"
    "  [cyan]evo sysmon -w 2[/cyan]               live dashboard, refresh every 2s (Ctrl-C to stop)\n"
    "  [cyan]evo sysmon -n 10[/cyan]              show the top 10 CPU-hungry processes\n"
    "  [cyan]evo sysmon --json[/cyan]             machine-readable output\n"
    "  [cyan]evo sysmon --install-deps[/cyan]     install the smctemp sensor reader (macOS)\n\n"
    "[dim]On Apple Silicon, real °C values come from the SMC via 'smctemp'\n"
    "(brew install narugit/tap/smctemp). Without it, only battery temperature\n"
    "and macOS thermal-pressure level are available. On Linux it reads\n"
    "/sys/class/thermal sensors. Everything degrades gracefully.[/dim]"
)


# --- low-level helpers -------------------------------------------------------
def _run(cmd, timeout=6):
    """Run a command, return its stdout on success, else None (never raises)."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def fmt_bytes(n):
    if n is None:
        return "-"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


# --- temperature: macOS ------------------------------------------------------
def _mac_smc_temp(flag):
    """Read CPU (-c) or GPU (-g) temperature via smctemp; °C float or None."""
    if not shutil.which("smctemp"):
        return None
    out = _run(["smctemp", flag, "-f"])  # -f -> decimal output
    if not out:
        return None
    try:
        value = float(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None
    return value if value > 0 else None


def _mac_battery_temp():
    """Battery temperature from ioreg (AppleSmartBattery), reported in 0.01 °C."""
    out = _run(["ioreg", "-r", "-n", "AppleSmartBattery"])
    if not out:
        return None
    match = re.search(r'"Temperature"\s*=\s*(\d+)', out)
    return int(match.group(1)) / 100.0 if match else None


def _mac_thermal_pressure():
    """macOS thermal-pressure level (Nominal/Moderate/Heavy/...) via powermetrics.

    Needs root; we use ``sudo -n`` so it stays silent (returns None) when sudo
    credentials are not already cached - never prompts for a password.
    """
    out = _run(
        ["sudo", "-n", "powermetrics", "--samplers", "thermal", "-n", "1", "-i", "200"],
        timeout=10,
    )
    if not out:
        return None
    match = re.search(r"pressure level:\s*(\w+)", out)
    return match.group(1) if match else None


# --- temperature: Linux ------------------------------------------------------
def _linux_temps():
    """Read every /sys/class/thermal zone; return list of {label, value}."""
    entries = []
    base = "/sys/class/thermal"
    if not os.path.isdir(base):
        return entries
    for zone in sorted(os.listdir(base)):
        if not zone.startswith("thermal_zone"):
            continue
        try:
            raw = open(f"{base}/{zone}/temp").read().strip()
            label = open(f"{base}/{zone}/type").read().strip()
            entries.append({"label": label, "value": int(raw) / 1000.0})
        except (OSError, ValueError):
            continue
    return entries


def collect_temps():
    system = platform.system()
    data = {
        "platform": system,
        "entries": [],
        "cpu": None,
        "pressure": None,
        "source": None,
        "smctemp_available": None,
    }
    if system == "Darwin":
        cpu = _mac_smc_temp("-c")
        gpu = _mac_smc_temp("-g")
        battery = _mac_battery_temp()
        data["smctemp_available"] = bool(shutil.which("smctemp"))
        data["source"] = "smctemp" if data["smctemp_available"] else "ioreg"
        data["pressure"] = _mac_thermal_pressure()
        for label, value in (("CPU", cpu), ("GPU", gpu), ("Battery", battery)):
            if value is not None:
                data["entries"].append({"label": label, "value": value})
        data["cpu"] = cpu
    elif system == "Linux":
        data["source"] = "sysfs"
        data["entries"] = _linux_temps()
        for entry in data["entries"]:
            low = entry["label"].lower()
            if any(k in low for k in ("cpu", "package", "core", "tctl", "x86_pkg", "soc")):
                data["cpu"] = entry["value"]
                break
        if data["cpu"] is None and data["entries"]:
            data["cpu"] = max(e["value"] for e in data["entries"])
    return data


# --- performance: CPU usage --------------------------------------------------
def _mac_cpu_usage():
    """Overall CPU usage % from two top samples (the first is since boot)."""
    out = _run(["top", "-l", "2", "-n", "0"], timeout=8)
    if not out:
        return None
    samples = re.findall(r"CPU usage:.*?([\d.]+)% idle", out)
    if not samples:
        return None
    return round(100.0 - float(samples[-1]), 1)


def _linux_cpu_usage():
    def snapshot():
        with open("/proc/stat") as handle:
            fields = list(map(int, handle.readline().split()[1:]))
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return idle, sum(fields)

    try:
        idle1, total1 = snapshot()
        time.sleep(0.3)
        idle2, total2 = snapshot()
    except (OSError, ValueError):
        return None
    delta = total2 - total1
    if delta <= 0:
        return None
    return round((1 - (idle2 - idle1) / delta) * 100, 1)


def cpu_usage():
    system = platform.system()
    if system == "Darwin":
        return _mac_cpu_usage()
    if system == "Linux":
        return _linux_cpu_usage()
    return None


# --- performance: memory -----------------------------------------------------
def _mac_memory():
    total_out = _run(["sysctl", "-n", "hw.memsize"])
    vm = _run(["vm_stat"])
    if not total_out or not vm:
        return None
    try:
        total = int(total_out.strip())
    except ValueError:
        return None
    page = 4096
    page_match = re.search(r"page size of (\d+) bytes", vm)
    if page_match:
        page = int(page_match.group(1))

    def pages(label):
        match = re.search(rf"{label}:\s+(\d+)", vm)
        return int(match.group(1)) if match else 0

    # Mirrors Activity Monitor's "Memory Used": app + wired + compressed.
    used = (pages("Pages active") + pages("Pages wired down") + pages("Pages occupied by compressor")) * page
    return {"total": total, "used": used, "percent": round(used / total * 100, 1)}


def _linux_memory():
    info_map = {}
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                info_map[key.strip()] = int(value.split()[0]) * 1024
    except (OSError, ValueError):
        return None
    total = info_map.get("MemTotal")
    if not total:
        return None
    available = info_map.get("MemAvailable", info_map.get("MemFree", 0))
    used = total - available
    return {"total": total, "used": used, "percent": round(used / total * 100, 1)}


def memory():
    system = platform.system()
    if system == "Darwin":
        return _mac_memory()
    if system == "Linux":
        return _linux_memory()
    return None


# --- performance: misc -------------------------------------------------------
def load_average():
    try:
        return list(os.getloadavg())
    except (OSError, AttributeError):
        return None


def uptime_seconds():
    system = platform.system()
    if system == "Linux":
        try:
            return float(open("/proc/uptime").read().split()[0])
        except (OSError, ValueError):
            return None
    if system == "Darwin":
        out = _run(["sysctl", "-n", "kern.boottime"])
        if out:
            match = re.search(r"sec\s*=\s*(\d+)", out)
            if match:
                return time.time() - int(match.group(1))
    return None


def cpu_model():
    system = platform.system()
    if system == "Darwin":
        return (_run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "").strip() or None
    if system == "Linux":
        try:
            for line in open("/proc/cpuinfo"):
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return None


def top_processes(count):
    system = platform.system()
    if system == "Darwin":
        out = _run(["ps", "-Ao", "pid,%cpu,%mem,comm", "-r"])
    else:
        out = _run(["ps", "-Ao", "pid,%cpu,%mem,comm", "--sort=-%cpu"])
    if not out:
        return []
    rows = []
    for line in out.strip().splitlines()[1 : count + 1]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, cpu, mem, comm = parts
        try:
            rows.append({"pid": pid, "cpu": float(cpu), "mem": float(mem), "command": comm.split("/")[-1]})
        except ValueError:
            continue
    return rows


def collect(top_n):
    return {
        "host": platform.node(),
        "system": f"{platform.system()} {platform.release()}",
        "cpu_model": cpu_model(),
        "cpu_count": os.cpu_count(),
        "temps": collect_temps(),
        "cpu_percent": cpu_usage(),
        "memory": memory(),
        "load": load_average(),
        "uptime": uptime_seconds(),
        "processes": top_processes(top_n),
    }


# --- rendering ---------------------------------------------------------------
def _temp_cell(value):
    if value is None:
        return "[dim]-[/dim]"
    style = "success"
    if value >= TEMP_HOT:
        style = "error"
    elif value >= TEMP_WARM:
        style = "warning"
    return f"[{style}]{value:.1f}°C[/{style}]"


def _pct_cell(value, threshold):
    if value is None:
        return "[dim]-[/dim]"
    style = "error" if value >= threshold else "info"
    return f"[{style}]{value:.0f}%[/{style}]"


def _fmt_uptime(seconds):
    if seconds is None:
        return "-"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def render_temps(temps):
    table = Table(title="Temperature", title_style="accent", show_header=True, header_style="accent")
    table.add_column("Sensor", style="info", no_wrap=True)
    table.add_column("Temp", justify="right")
    table.add_column("Status", justify="left")
    if not temps["entries"]:
        table.add_row("[dim]no sensors[/dim]", "-", "-")
    for entry in temps["entries"]:
        value = entry["value"]
        if value >= TEMP_HOT:
            status = "[error]hot[/error]"
        elif value >= TEMP_WARM:
            status = "[warning]warm[/warning]"
        else:
            status = "[success]ok[/success]"
        table.add_row(entry["label"], _temp_cell(value), status)
    if temps.get("pressure"):
        pressure = temps["pressure"]
        hot = pressure.lower() not in NOMINAL_PRESSURE
        cell = f"[{'error' if hot else 'success'}]{pressure}[/]"
        table.add_row("Thermal pressure", cell, "[dim]macOS[/dim]")
    console.print(table)


def render_perf(data):
    table = Table(title="Performance", title_style="accent", show_header=True, header_style="accent")
    table.add_column("Metric", style="info", no_wrap=True)
    table.add_column("Value", justify="right")
    table.add_column("Detail", justify="left")

    ncpu = data["cpu_count"] or 0
    table.add_row("CPU usage", _pct_cell(data["cpu_percent"], CPU_BUSY), f"{ncpu} logical cores")

    load = data["load"]
    if load:
        over = ncpu and load[0] > ncpu
        load_style = "error" if over else "success"
        load_text = f"[{load_style}]{load[0]:.2f}[/] {load[1]:.2f} {load[2]:.2f}"
        table.add_row("Load avg", load_text, "1m 5m 15m")

    mem = data["memory"]
    if mem:
        detail = f"{fmt_bytes(mem['used'])} / {fmt_bytes(mem['total'])}"
        table.add_row("Memory", _pct_cell(mem["percent"], MEM_HIGH), detail)

    table.add_row("Uptime", _fmt_uptime(data["uptime"]), data["cpu_model"] or "")
    console.print(table)


def render_processes(processes):
    if not processes:
        return
    table = Table(title="Top processes (by CPU)", title_style="accent", show_header=True, header_style="accent")
    table.add_column("PID", style="dim", justify="right", no_wrap=True)
    table.add_column("CPU%", justify="right")
    table.add_column("MEM%", justify="right")
    table.add_column("Command", style="info")
    for proc in processes:
        cpu_style = "error" if proc["cpu"] >= 100 else ("warning" if proc["cpu"] >= 25 else "")
        cpu_text = f"[{cpu_style}]{proc['cpu']:.1f}[/]" if cpu_style else f"{proc['cpu']:.1f}"
        table.add_row(proc["pid"], cpu_text, f"{proc['mem']:.1f}", proc["command"])
    console.print(table)


def build_notes(data):
    notes = []
    temps = data["temps"]
    cpu_t = temps.get("cpu")
    if cpu_t is not None:
        if cpu_t >= TEMP_HOT:
            notes.append(("warning", f"CPU is hot ({cpu_t:.0f}°C) - heavy load or limited cooling"))
        elif cpu_t >= TEMP_WARM:
            notes.append(("info", f"CPU is warm ({cpu_t:.0f}°C) - normal while busy"))
    pressure = temps.get("pressure")
    if pressure and pressure.lower() not in NOMINAL_PRESSURE:
        notes.append(("warning", f"Thermal pressure is {pressure} - the system may be throttling"))

    cpu_u = data["cpu_percent"]
    if cpu_u is not None and cpu_u >= CPU_BUSY:
        notes.append(("warning", f"CPU usage is high ({cpu_u:.0f}%)"))

    load = data["load"]
    ncpu = data["cpu_count"]
    if load and ncpu and load[0] > ncpu:
        notes.append(("warning", f"Load average {load[0]:.2f} exceeds {ncpu} cores - tasks are queuing"))

    mem = data["memory"]
    if mem and mem["percent"] >= MEM_HIGH:
        notes.append(("warning", f"Memory usage is high ({mem['percent']:.0f}%)"))

    procs = data["processes"]
    if procs and procs[0]["cpu"] >= 100 and (cpu_u is None or cpu_u >= CPU_BUSY or (cpu_t and cpu_t >= TEMP_WARM)):
        top = procs[0]
        notes.append(("info", f"Biggest CPU consumer: {top['command']} ({top['cpu']:.0f}%, pid {top['pid']})"))

    if temps["platform"] == "Darwin" and temps.get("smctemp_available") is False:
        notes.append(
            (
                "info",
                "Install 'smctemp' for real CPU/GPU °C readings: "
                "evo sysmon --install-deps  (or brew install narugit/tap/smctemp)",
            )
        )
    return notes


def render(data, show_procs):
    render_temps(data["temps"])
    console.print()
    render_perf(data)
    if show_procs:
        console.print()
        render_processes(data["processes"])

    notes = build_notes(data)
    step("Verdict")
    if not notes:
        success("All good - temperatures, CPU, memory and load are within normal range.")
    else:
        dispatch = {"warning": warning, "error": error, "info": info}
        for level, message in notes:
            dispatch.get(level, info)(message)


def install_deps():
    if platform.system() != "Darwin":
        info("Dependency install is only needed/supported on macOS (Apple Silicon).")
        return
    if shutil.which("smctemp"):
        success("smctemp is already installed.")
        return
    if not shutil.which("brew"):
        error("Homebrew not found. Install it from https://brew.sh first.")
        sys.exit(1)
    info("Installing smctemp via Homebrew (narugit/tap/smctemp)...")
    result = subprocess.run(["brew", "install", "narugit/tap/smctemp"])
    if result.returncode == 0 and shutil.which("smctemp"):
        success("smctemp installed - real CPU/GPU temperatures are now available.")
    else:
        error("Install failed. Try manually: brew install narugit/tap/smctemp")
        sys.exit(1)


@click.command("sysmon", epilog=EPILOG)
@click.option("-n", "--top", "top_n", default=5, show_default=True, help="Number of top CPU processes to show.")
@click.option("-w", "--watch", "watch", default=0.0, help="Refresh continuously every N seconds (Ctrl-C to stop).")
@click.option("--no-procs", "no_procs", is_flag=True, help="Hide the top-processes table.")
@click.option("--json", "as_json", is_flag=True, help="Print result as JSON.")
@click.option("--install-deps", "do_install", is_flag=True, help="Install the smctemp sensor reader (macOS) and exit.")
def sysmon(top_n, watch, no_procs, as_json, do_install):
    """Show machine temperature and performance at a glance.

    Reports **CPU / GPU / battery temperature**, overall **CPU usage**,
    **memory** pressure, **load average**, uptime and the **top CPU-hungry
    processes**, then prints a plain-language verdict that flags overheating,
    throttling, an oversubscribed CPU or memory pressure.

    On Apple Silicon real °C values are read from the SMC via `smctemp`
    (`evo sysmon --install-deps` to set it up); without it you still get battery
    temperature and the macOS thermal-pressure level. On Linux it reads
    `/sys/class/thermal`. With `-w/--watch` it becomes a live dashboard.
    """
    if do_install:
        step("evo sysmon - install deps")
        install_deps()
        return

    show_procs = not no_procs

    if as_json:
        console.print_json(jsonlib.dumps(collect(top_n), ensure_ascii=False, default=str))
        return

    if watch and watch > 0:
        try:
            while True:
                console.clear()
                step("evo sysmon")
                with console.status("[info]sampling...[/info]", spinner="dots"):
                    data = collect(top_n)
                render(data, show_procs)
                console.print(f"[dim]refreshing every {watch:g}s - Ctrl-C to stop[/dim]")
                time.sleep(watch)
        except KeyboardInterrupt:
            console.print()
            info("stopped")
        return

    step("evo sysmon")
    try:
        with console.status("[info]sampling...[/info]", spinner="dots"):
            data = collect(top_n)
        render(data, show_procs)
    except click.ClickException:
        raise
    except Exception as exc:
        error(str(exc))
        sys.exit(1)
