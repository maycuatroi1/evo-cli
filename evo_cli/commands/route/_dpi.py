from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

from evo_cli.console import download_file, info, resolve_executable

RELEASE_URL = "https://github.com/ValdikSS/GoodbyeDPI/releases/download/0.2.2/goodbyedpi-0.2.2.zip"
RELEASE_SIZE = 635551
RELEASE_SHA256 = "00a2f8b99cd817f8c7fc4c449033015f039d18af213de78cb66bf202277c0628"

SERVICE_NAME = "GoodbyeDPI"

# Deliberately NOT the bundled `-5` preset. `-5` is these flags plus
# `--max-payload`, whose 1200-byte ceiling skips the very packet that matters:
# a modern Chrome ClientHello carries a post-quantum key share and runs past
# 1200 bytes, so it sails through untouched while curl - whose ClientHello is
# small - looks like it works. Without --max-payload both are handled.
DEFAULT_ARGS = ["-f", "2", "-e", "2", "--auto-ttl", "--reverse-frag"]


def is_windows():
    return os.name == "nt"


def is_admin():
    if not is_windows():
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def install_dir():
    root = os.environ.get("ProgramFiles", r"C:\Program Files")
    return Path(root) / "GoodbyeDPI"


def executable():
    return install_dir() / "x86_64" / "goodbyedpi.exe"


def _sc(*args, check=False):
    cmd = resolve_executable(["sc.exe", *args])
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def service_state():
    if not is_windows():
        return {"installed": False, "running": False, "args": None, "reason": "windows only"}
    result = _sc("qc", SERVICE_NAME)
    if result.returncode != 0:
        return {"installed": False, "running": False, "args": None, "reason": None}

    binary = None
    for line in (result.stdout or "").splitlines():
        if "BINARY_PATH_NAME" in line:
            binary = line.split(":", 1)[1].strip()
            break

    query = _sc("query", SERVICE_NAME)
    running = "RUNNING" in (query.stdout or "")
    return {"installed": True, "running": running, "args": binary, "reason": None}


def verify_download(path):
    size = Path(path).stat().st_size
    if size != RELEASE_SIZE:
        return False, f"size mismatch: got {size}, expected {RELEASE_SIZE}"
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest != RELEASE_SHA256:
        return False, f"sha256 mismatch: got {digest}"
    return True, None


def fetch(destination=None):
    destination = Path(destination or (Path(tempfile.gettempdir()) / "goodbyedpi-0.2.2.zip"))
    download_file(RELEASE_URL, destination, description="GoodbyeDPI 0.2.2")
    ok, reason = verify_download(destination)
    if not ok:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"refusing to install a tampered download - {reason}")
    return destination


def unpack(archive, target=None):
    target = Path(target or install_dir())
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
        root = next(Path(tmp).glob("goodbyedpi-*"))
        source = root / "x86_64"
        dest = target / "x86_64"
        dest.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            dest.joinpath(item.name).write_bytes(item.read_bytes())
    return target


def install_service(args=None):
    args = list(args or DEFAULT_ARGS)
    binary = f'"{executable()}" ' + " ".join(args)

    existing = service_state()
    if existing["installed"]:
        _sc("stop", SERVICE_NAME)
        _sc("delete", SERVICE_NAME)

    # sc.exe wants "binPath=" and its value as two separate argv entries. Passing
    # an explicit list keeps that shape; a shell would glue them and sc would
    # silently print its usage instead of creating anything.
    created = _sc("create", SERVICE_NAME, "binPath=", binary, "start=", "auto")
    if created.returncode != 0:
        raise RuntimeError((created.stdout or created.stderr or "sc create failed").strip())
    _sc("description", SERVICE_NAME, "Passive DPI blocker and Active DPI circumvention utility")
    return binary


def start():
    result = _sc("start", SERVICE_NAME)
    return result.returncode == 0 or "ALREADY_RUNNING" in (result.stdout or "")


def stop():
    result = _sc("stop", SERVICE_NAME)
    return result.returncode == 0 or "NOT_ACTIVE" in (result.stdout or "")


def remove():
    stop()
    result = _sc("delete", SERVICE_NAME)
    return result.returncode == 0


def ensure(args=None):
    state = service_state()
    if not executable().exists():
        info("GoodbyeDPI is not installed yet, fetching it")
        archive = fetch()
        unpack(archive)
        Path(archive).unlink(missing_ok=True)
    if not state["installed"]:
        install_service(args)
    start()
    return service_state()
