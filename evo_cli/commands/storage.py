"""
evo storage - see what fills the disk and clear the caches that grow forever.

``audit`` shows free space, swap and how much each known developer cache holds;
``--deep`` also ranks the biggest folders under your home. ``clean`` clears the
caches you pick. Every target is something its tool rebuilds on demand - package
caches, build caches, updater leftovers - never project files or app data.

Lessons from real cleanups, built in:

* The size ``du`` reports is not what you get back. On APFS uv clones cache files
  into each venv, so a 32 GB cache can free 6 GB. ``clean`` measures free space
  before and after every target and reports that number.
* Never pull files out from under a running process: uv tool envs and npx
  packages that show up in ``ps`` are kept, and the Gradle caches are skipped
  while a Gradle daemon is alive.
* Files owned by another user (a past ``sudo uvx``) are reported, not escalated.
"""

import concurrent.futures
import json as jsonlib
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import rich_click as click
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from evo_cli.commands.sysmon import fmt_bytes
from evo_cli.console import console, error, info, step, success, warning

HOME = Path.home()

# Swap this full (fraction of its size) earns a "restart to get it back" hint.
SWAP_HINT_RATIO = 0.5
SWAP_HINT_MIN = 1024**3

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo storage audit[/cyan]                   free space, swap and cache sizes\n"
    "  [cyan]evo storage audit --deep[/cyan]            also rank the biggest folders in your home\n"
    "  [cyan]evo storage clean --all -n[/cyan]          dry run: show what a full clean would remove\n"
    "  [cyan]evo storage clean uv npm gradle[/cyan]     clear only these caches (asks first)\n"
    "  [cyan]evo storage clean --all -y[/cyan]          clear every cache without asking\n\n"
    "[dim]Only caches their tools rebuild on demand are touched. Freed space is measured on the\n"
    "filesystem, so it can be lower than the cache size (APFS clones share blocks with venvs).[/dim]"
)


# --- low-level helpers -------------------------------------------------------
def _run(cmd, timeout=60):
    """Run a command; return the CompletedProcess, or None if it could not start."""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _stdout(cmd, timeout=60):
    """Return a command's stripped stdout on success, else None."""
    res = _run(cmd, timeout)
    if res is None or res.returncode != 0:
        return None
    return res.stdout.strip()


def _err_text(res, default="failed"):
    """Last line of a failed command's output, for a one-line report."""
    if res is None:
        return default
    lines = ((res.stderr or "") + (res.stdout or "")).strip().splitlines()
    return lines[-1] if lines else default


def _run_tool(cmd, failures, timeout=1800):
    """Run a cleanup command, recording a failure message instead of raising."""
    res = _run(cmd, timeout)
    if res is None or res.returncode != 0:
        failures.append(f"`{' '.join(cmd)}`: {_err_text(res)}")


# --- parsing helpers (pure, unit-testable) -----------------------------------
_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT]?)i?B\b", re.IGNORECASE)
_SIZE_SCALE = {"": 1, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4}


def parse_size(text):
    """``4.915GB (56%)`` / ``916.3MB`` / ``63B`` -> bytes, or None."""
    match = _SIZE_RE.search(text or "")
    if not match:
        return None
    return int(float(match.group(1)) * _SIZE_SCALE[match.group(2).upper()])


def parse_du(text):
    """Parse ``du -sk`` output into ``[(bytes, path), ...]``."""
    entries = []
    for line in (text or "").splitlines():
        size, _, path = line.partition("\t")
        if size.strip().isdigit() and path:
            entries.append((int(size) * 1024, path))
    return entries


def parse_docker_df(text):
    """Sum what ``docker system df --format '{{json .}}'`` says image + build-cache pruning frees."""
    total = 0
    for line in (text or "").splitlines():
        try:
            row = jsonlib.loads(line)
        except ValueError:
            continue
        if row.get("Type") in ("Images", "Build Cache"):
            total += parse_size(row.get("Reclaimable")) or 0
    return total


def parse_brew_estimate(text):
    """Pull the size out of ``brew cleanup -n``'s "would free approximately 916.3MB"."""
    match = re.search(r"free approximately\s+(\S+)", text or "")
    return parse_size(match.group(1)) if match else None


def parse_conda_dry_run(text):
    """Bytes ``conda clean --all --dry-run --json`` would remove (tarballs + packages)."""
    try:
        data = jsonlib.loads(text or "")
    except ValueError:
        return None
    return sum((data.get(key) or {}).get("total_size") or 0 for key in ("tarballs", "packages"))


def parse_swapusage(text):
    """``total = 24576.00M  used = 23737.25M ...`` (macOS sysctl) -> (total, used) bytes."""
    match = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M", text or "")
    if not match:
        return None
    return int(float(match.group(1)) * 1024**2), int(float(match.group(2)) * 1024**2)


def parse_meminfo_swap(text):
    """SwapTotal / SwapFree from Linux /proc/meminfo -> (total, used) bytes."""
    values = {}
    for line in (text or "").splitlines():
        key, _, rest = line.partition(":")
        if key in ("SwapTotal", "SwapFree") and rest.split():
            values[key] = int(rest.split()[0]) * 1024
    if "SwapTotal" not in values or "SwapFree" not in values:
        return None
    return values["SwapTotal"], values["SwapTotal"] - values["SwapFree"]


def children_in_use(parent, procs):
    """Names of ``parent``'s direct children that appear in a running command line."""
    prefix = str(parent).rstrip("/") + "/"
    return {match.group(1) for match in re.finditer(re.escape(prefix) + r"([^/\s]+)", procs or "")}


# --- filesystem --------------------------------------------------------------
def is_safe_root(path):
    """Refuse to empty `/`, the home folder or anything above it - a bad env var must not wipe a disk."""
    path = Path(path).expanduser()
    if not path.is_absolute():
        return False
    resolved = path.resolve()
    home = HOME.resolve()
    return resolved != Path(resolved.anchor) and resolved != home and resolved not in home.parents


def remove_path(path):
    """Delete a file or a tree; return True when it is completely gone."""
    path = Path(path)
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return False
        return True
    if not path.exists():
        return True
    failed = []

    def _onerror(_func, failed_path, _exc):
        failed.append(failed_path)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_onerror)
    else:
        shutil.rmtree(path, onerror=_onerror)
    return not failed


def clear_dir(root, failures, keep=()):
    """Remove everything inside ``root`` except the names in ``keep``."""
    root = Path(root)
    if not root.is_dir():
        return
    if not is_safe_root(root):
        failures.append(f"{root}: refusing to empty this folder")
        return
    for child in root.iterdir():
        if child.name not in keep and not remove_path(child):
            failures.append(str(child))


def du_bytes(paths, timeout=900):
    """Total size of the existing ``paths`` via ``du -sk``; None if du could not run."""
    existing = [str(p) for p in paths if Path(p).exists()]
    if not existing:
        return 0
    res = _run(["du", "-sk", *existing], timeout)
    if res is None:
        return None
    return sum(size for size, _ in parse_du(res.stdout))


def free_bytes():
    return shutil.disk_usage(HOME).free


def process_table():
    """Every running command line, one per line (used to spot caches in use)."""
    return _stdout(["ps", "-axo", "args="], timeout=15) or ""


# --- targets -----------------------------------------------------------------
class Target:
    """One cleanable cache: how to find it, size it, tell if it is busy, and clear it."""

    def __init__(self, name, title, measure, clean, detect=None, blocker=None, system=None):
        self.name = name
        self.title = title
        self.measure = measure
        self.clean = clean
        self.detect = detect or (lambda: True)
        self.blocker = blocker or (lambda procs: None)
        self.system = system

    def supported(self):
        return self.system is None or platform.system() == self.system


def _cache_home():
    if platform.system() == "Darwin":
        return HOME / "Library" / "Caches"
    return Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache")


# uv
_UV_META = {"CACHEDIR.TAG", ".gitignore", ".lock"}


def _uv_dir():
    out = _stdout(["uv", "cache", "dir"]) if shutil.which("uv") else None
    return Path(out) if out else Path(os.environ.get("UV_CACHE_DIR") or HOME / ".cache" / "uv")


def _uv_clean(procs, failures):
    root = _uv_dir()
    if not (root / "CACHEDIR.TAG").exists():
        failures.append(f"{root}: no CACHEDIR.TAG, does not look like a uv cache")
        return []
    kept = 0
    for bucket in root.iterdir():
        if bucket.name in _UV_META:
            continue
        # uvx runs tools straight from the cache (archive-v0 on older uv,
        # environments-v2 on newer): keep the envs a live process runs from.
        busy = children_in_use(bucket, procs) if bucket.is_dir() else set()
        if busy:
            clear_dir(bucket, failures, keep=busy)
            kept += len(busy)
        elif not remove_path(bucket):
            failures.append(str(bucket))
    return [f"kept {kept} uv env(s) a running process uses"] if kept else []


# npm
def _npm_dir():
    out = _stdout(["npm", "config", "get", "cache"]) if shutil.which("npm") else None
    return Path(out) if out else HOME / ".npm"


def _npm_clean(procs, failures):
    root = _npm_dir()
    if not remove_path(root / "_cacache"):
        failures.append(str(root / "_cacache"))
    npx = root / "_npx"
    busy = children_in_use(npx, procs)
    clear_dir(npx, failures, keep=busy)
    return [f"kept {len(busy)} npx package(s) a running process uses"] if busy else []


# pip
def _pip_dir():
    return Path(os.environ.get("PIP_CACHE_DIR") or _cache_home() / "pip")


# conda
def _conda_exe():
    return os.environ.get("CONDA_EXE") or shutil.which("conda")


def _conda_measure():
    return parse_conda_dry_run(_stdout([_conda_exe(), "clean", "--all", "--dry-run", "--json"], timeout=300))


# gradle
def _gradle_paths():
    home = Path(os.environ.get("GRADLE_USER_HOME") or HOME / ".gradle")
    return [home / "caches", home / "wrapper" / "dists"]


def _gradle_blocker(procs):
    if "GradleDaemon" in procs:
        return "a Gradle daemon is running (`gradle --stop` or quit Android Studio)"
    return None


def _gradle_clean(procs, failures):
    for path in _gradle_paths():
        if not remove_path(path):
            failures.append(str(path))
    return ["the next Android/Gradle build downloads its wrapper and dependencies again"]


# docker
def _docker_blocker(procs):
    if _stdout(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20) is None:
        return "Docker is not running"
    return None


def _docker_measure():
    out = _stdout(["docker", "system", "df", "--format", "{{json .}}"], timeout=60)
    return parse_docker_df(out) if out is not None else None


def _docker_clean(procs, failures):
    _run_tool(["docker", "builder", "prune", "-af"], failures)
    _run_tool(["docker", "image", "prune", "-af"], failures)
    notes = ["images used by any container, running or stopped, are kept"]
    if platform.system() == "Darwin":
        notes.append("Docker Desktop hands space back to macOS gradually - check again in a few minutes")
    return notes


# homebrew
def _brew_cache():
    out = _stdout(["brew", "--cache"])
    return Path(out) if out else None


def _brew_measure():
    # The dry run already counts the download cache; du is only the fallback.
    estimate = parse_brew_estimate(_stdout(["brew", "cleanup", "-n", "--prune=all"], timeout=300))
    if estimate is not None:
        return estimate
    cache = _brew_cache()
    return du_bytes([cache]) if cache else None


def _brew_clean(procs, failures):
    _run_tool(["brew", "cleanup", "--prune=all"], failures)
    cache = _brew_cache()
    if cache:
        clear_dir(cache, failures)


# go
def _go_cache():
    out = _stdout(["go", "env", "GOCACHE"])
    return Path(out) if out else None


# pnpm
def _pnpm_store():
    out = _stdout(["pnpm", "store", "path"])
    return Path(out) if out else None


# xcode (macOS)
def _derived_data():
    return HOME / "Library" / "Developer" / "Xcode" / "DerivedData"


def _xcode_clean(procs, failures):
    clear_dir(_derived_data(), failures)
    if shutil.which("xcrun"):
        _run_tool(["xcrun", "simctl", "delete", "unavailable"], failures)


# app updater leftovers (macOS)
_EDITORS = ("Code", "Code - Insiders", "Cursor", "Windsurf")


def _updater_paths():
    caches = HOME / "Library" / "Caches"
    paths = sorted([*caches.glob("*.ShipIt"), *caches.glob("*-updater")]) if caches.is_dir() else []
    support = HOME / "Library" / "Application Support"
    paths += [p for p in (support / app / "CachedExtensionVSIXs" for app in _EDITORS) if p.is_dir()]
    return paths


def _updater_clean(procs, failures):
    for path in _updater_paths():
        if path.name == "CachedExtensionVSIXs":
            clear_dir(path, failures)
        elif not remove_path(path):
            failures.append(str(path))


def _has(tool):
    return lambda: shutil.which(tool) is not None


TARGETS = {
    target.name: target
    for target in (
        Target(
            "uv",
            "uv package cache",
            measure=lambda: du_bytes([_uv_dir()]),
            clean=_uv_clean,
            detect=lambda: _uv_dir().is_dir(),
        ),
        Target(
            "npm",
            "npm download cache + npx packages",
            measure=lambda: du_bytes([_npm_dir() / "_cacache", _npm_dir() / "_npx"]),
            clean=_npm_clean,
            detect=lambda: _npm_dir().is_dir(),
        ),
        Target(
            "pip",
            "pip wheel/http cache",
            measure=lambda: du_bytes([_pip_dir()]),
            clean=lambda procs, failures: clear_dir(_pip_dir(), failures),
            detect=lambda: _pip_dir().is_dir(),
        ),
        Target(
            "conda",
            "conda tarballs + unused packages",
            measure=_conda_measure,
            clean=lambda procs, failures: _run_tool([_conda_exe(), "clean", "--all", "-y"], failures),
            detect=lambda: _conda_exe() is not None,
        ),
        Target(
            "pnpm",
            "pnpm store (unreferenced packages)",
            measure=lambda: du_bytes([_pnpm_store()]) if _pnpm_store() else None,
            clean=lambda procs, failures: _run_tool(["pnpm", "store", "prune"], failures),
            detect=_has("pnpm"),
        ),
        Target(
            "gradle",
            "Gradle caches + wrapper distributions",
            measure=lambda: du_bytes(_gradle_paths()),
            clean=_gradle_clean,
            detect=lambda: any(p.is_dir() for p in _gradle_paths()),
            blocker=_gradle_blocker,
        ),
        Target(
            "go",
            "Go build cache",
            measure=lambda: du_bytes([_go_cache()]) if _go_cache() else None,
            clean=lambda procs, failures: _run_tool(["go", "clean", "-cache"], failures),
            detect=_has("go"),
        ),
        Target(
            "docker",
            "Docker build cache + unused images",
            measure=_docker_measure,
            clean=_docker_clean,
            detect=_has("docker"),
            blocker=_docker_blocker,
        ),
        Target(
            "brew",
            "Homebrew old versions + download cache",
            measure=_brew_measure,
            clean=_brew_clean,
            detect=_has("brew"),
        ),
        Target(
            "xcode",
            "Xcode DerivedData + unavailable simulators",
            measure=lambda: du_bytes([_derived_data()]),
            clean=_xcode_clean,
            detect=lambda: _derived_data().is_dir() or shutil.which("xcrun") is not None,
            system="Darwin",
        ),
        Target(
            "updaters",
            "app updater leftovers (*.ShipIt, *-updater, editor VSIX cache)",
            measure=lambda: du_bytes(_updater_paths()),
            clean=_updater_clean,
            detect=lambda: bool(_updater_paths()),
            system="Darwin",
        ),
    )
}


_SYSTEM_NAMES = {"Darwin": "macOS"}


def survey_target(target, procs):
    """Size one target and decide whether it can be cleaned right now."""
    entry = {"name": target.name, "title": target.title, "size": None, "status": "ready", "reason": None}
    if not target.supported():
        entry.update(status="skip", reason=f"{_SYSTEM_NAMES.get(target.system, target.system)} only")
        return entry
    if not target.detect():
        entry.update(status="skip", reason="not found")
        return entry
    reason = target.blocker(procs)
    if reason:
        entry.update(status="blocked", reason=reason)
    entry["size"] = target.measure()
    return entry


def survey(targets, procs):
    """Survey targets in parallel (each is mostly a du / tool call) and keep their order."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(lambda target: survey_target(target, procs), targets))


# --- machine-level facts -----------------------------------------------------
def disk_state():
    usage = shutil.disk_usage(HOME)
    return {"path": str(HOME), "total": usage.total, "used": usage.used, "free": usage.free}


def swap_state():
    system = platform.system()
    parsed = None
    if system == "Darwin":
        parsed = parse_swapusage(_stdout(["sysctl", "-n", "vm.swapusage"]))
    elif system == "Linux":
        try:
            parsed = parse_meminfo_swap(Path("/proc/meminfo").read_text())
        except OSError:
            parsed = None
    if not parsed:
        return None
    return {"total": parsed[0], "used": parsed[1]}


def largest_folders(root, limit):
    """The ``limit`` biggest children of ``root`` (one ``du -skx`` pass)."""
    try:
        children = [str(c) for c in Path(root).iterdir() if not c.is_symlink()]
    except OSError:
        return []
    if not children:
        return []
    res = _run(["du", "-skx", *children], timeout=1800)
    entries = parse_du(res.stdout if res else "")
    return sorted(entries, reverse=True)[:limit]


def deep_roots():
    roots = [HOME]
    library = HOME / "Library"
    if platform.system() == "Darwin" and library.is_dir():
        roots.append(library)
    return roots


# --- rendering ---------------------------------------------------------------
def _home_relative(path):
    try:
        return "~/" + str(Path(path).relative_to(HOME))
    except ValueError:
        return str(path)


def render_disk(disk, swap):
    table = Table(title="Disk (home volume)", title_style="accent", show_header=True, header_style="accent")
    table.add_column("Field", style="info", no_wrap=True)
    table.add_column("Value")
    pct = disk["used"] / disk["total"] * 100 if disk["total"] else 0
    style = "error" if pct >= 90 else "warning" if pct >= 80 else "success"
    table.add_row("Size", fmt_bytes(disk["total"]))
    table.add_row("Used", f"[{style}]{fmt_bytes(disk['used'])} ({pct:.0f}%)[/{style}]")
    table.add_row("Free", f"[accent]{fmt_bytes(disk['free'])}[/accent]")
    if swap:
        table.add_row("Swap", f"{fmt_bytes(swap['used'])} of {fmt_bytes(swap['total'])} used")
    console.print(table)


def _status_cell(entry):
    if entry["status"] == "ready":
        return "[success]ready[/success]"
    if entry["status"] == "blocked":
        return f"[warning]blocked[/warning] [dim]{escape(entry['reason'])}[/dim]"
    return f"[dim]{escape(entry['reason'])}[/dim]"


def render_targets(entries, title="Caches"):
    table = Table(title=title, title_style="accent", show_header=True, header_style="accent")
    table.add_column("Target", style="info", no_wrap=True)
    table.add_column("Size", justify="right")
    table.add_column("Status")
    table.add_column("What", style="dim")
    for entry in entries:
        table.add_row(entry["name"], fmt_bytes(entry["size"]), _status_cell(entry), entry["title"])
    console.print(table)


def render_largest(root, entries):
    table = Table(title=f"Largest in {_home_relative(root)}", title_style="accent", header_style="accent")
    table.add_column("Size", justify="right", no_wrap=True)
    table.add_column("Folder", style="info")
    for size, path in entries:
        table.add_row(fmt_bytes(size), escape(_home_relative(path)))
    console.print(table)


def ready_total(entries):
    return sum(entry["size"] or 0 for entry in entries if entry["status"] == "ready")


def swap_hint(swap):
    if swap and swap["total"] >= SWAP_HINT_MIN and swap["used"] >= swap["total"] * SWAP_HINT_RATIO:
        return f"Swap holds {fmt_bytes(swap['used'])} on disk - a restart gives it back."
    return None


def _report_failures(name, failures):
    shown = failures[:3]
    more = f" (and {len(failures) - len(shown)} more)" if len(failures) > len(shown) else ""
    warning(
        f"{name}: could not remove {escape(', '.join(shown))}{more}"
        " - owned by another user? Remove with sudo if needed."
    )


# --- CLI ---------------------------------------------------------------------
@click.group("storage", epilog=EPILOG, context_settings={"help_option_names": ["-h", "--help"]})
def storage():
    """See what fills the **disk** and clear developer caches.

    `audit` reports free space, swap and the size of every cache evo knows
    (uv, npm, pip, conda, pnpm, Gradle, Go, Docker, Homebrew, Xcode, app
    updaters). `clean` clears the ones you pick - only data the tools rebuild on
    demand, never projects or app data.

    Run `evo storage <command> -h` for the options of each subcommand.
    """
    if platform.system() == "Windows":
        error("evo storage supports macOS and Linux only.")
        sys.exit(1)


@storage.command("audit")
@click.option("--deep", is_flag=True, help="Also rank the biggest folders in your home (slow on big disks).")
@click.option("-n", "--limit", default=15, show_default=True, help="Rows per table with --deep.")
@click.option("--json", "as_json", is_flag=True, help="Print result as JSON.")
def audit_cmd(deep, limit, as_json):
    """Show free space, swap and how much each developer cache holds."""
    procs = process_table()
    with console.status("[info]measuring caches...[/info]", spinner="dots"):
        entries = survey(list(TARGETS.values()), procs)
    state = {"disk": disk_state(), "swap": swap_state(), "targets": entries}
    if deep:
        with console.status("[info]ranking folders (du over your home)...[/info]", spinner="dots"):
            state["largest"] = {str(root): largest_folders(root, limit) for root in deep_roots()}

    if as_json:
        console.print_json(jsonlib.dumps(state, ensure_ascii=False))
        return

    step("evo storage audit")
    render_disk(state["disk"], state["swap"])
    render_targets([e for e in entries if e["status"] != "skip"])
    missing = [e["name"] for e in entries if e["status"] == "skip"]
    if missing:
        console.print(f"[dim]not on this machine: {', '.join(missing)}[/dim]")
    for root, rows in state.get("largest", {}).items():
        render_largest(root, rows)

    total = ready_total(entries)
    if total:
        info(f"Up to [accent]{fmt_bytes(total)}[/accent] is cleanable - preview with 'evo storage clean --all -n'.")
    hint = swap_hint(state["swap"])
    if hint:
        warning(hint)


@storage.command("clean")
@click.argument("targets", nargs=-1, type=click.Choice(list(TARGETS), case_sensitive=False))
@click.option("--all", "all_targets", is_flag=True, help="Clean every cache evo knows about.")
@click.option("-n", "--dry-run", is_flag=True, help="Show what would be cleaned; change nothing.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
def clean_cmd(targets, all_targets, dry_run, yes):
    """Clear the chosen caches and report the space actually freed.

    Pass target names (`uv npm gradle ...`) or `--all`. Busy caches are kept:
    uv/npx envs used by a running process stay, and Gradle is skipped while its
    daemon runs.
    """
    names = list(TARGETS) if all_targets else list(dict.fromkeys(t.lower() for t in targets))
    if not names:
        error("Name the caches to clean, or pass --all.")
        info(f"Targets: {', '.join(TARGETS)}")
        sys.exit(2)

    procs = process_table()
    with console.status("[info]measuring caches...[/info]", spinner="dots"):
        entries = survey([TARGETS[name] for name in names], procs)
    step("evo storage clean")
    render_targets(entries, title="Plan")

    ready = [e for e in entries if e["status"] == "ready"]
    if not ready:
        info("Nothing to clean.")
        return
    if dry_run:
        info(f"Dry run - {len(ready)} target(s), up to [accent]{fmt_bytes(ready_total(entries))}[/accent].")
        return
    if not yes and not click.confirm(f"Clean {', '.join(e['name'] for e in ready)}?", default=False):
        info("Aborted - nothing was removed.")
        return

    before_all = free_bytes()
    for entry in ready:
        failures = []
        before = free_bytes()
        with console.status(f"[info]cleaning {entry['name']}...[/info]", spinner="dots"):
            notes = TARGETS[entry["name"]].clean(procs, failures) or []
        freed = max(0, free_bytes() - before)
        was = f" [dim](cache was {fmt_bytes(entry['size'])})[/dim]" if entry["size"] else ""
        success(f"{entry['name']}: freed [accent]{fmt_bytes(freed)}[/accent]{was}")
        for note in notes:
            console.print(f"      [dim]{escape(note)}[/dim]")
        if failures:
            _report_failures(entry["name"], failures)

    total = max(0, free_bytes() - before_all)
    step("Result")
    success(f"Freed [accent]{fmt_bytes(total)}[/accent] - {fmt_bytes(free_bytes())} free now.")
    hint = swap_hint(swap_state())
    if hint:
        warning(hint)
