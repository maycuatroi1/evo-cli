import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from evo_cli.serp.errors import SerpError

REPO = "serpapi/serpapi-cli"
GITHUB_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_URL = "https://github.com/{repo}/releases/download/v{ver}/serpapi_{ver}_{goos}_{arch}.tar.gz"
FALLBACK_VERSION = "0.2.3"
GO_PACKAGE = f"github.com/{REPO}/cmd/serpapi@latest"
BREW_TAP = "serpapi/homebrew-tap"
BREW_FORMULA = "serpapi-cli"
# Homebrew refers to `serpapi/homebrew-tap` as `serpapi/tap` once tapped, and
# refuses to build from it until the tap is explicitly trusted.
BREW_QUALIFIED = "serpapi/tap/serpapi-cli"
BREW_TRUST_COMMAND = f"brew trust --formula {BREW_QUALIFIED}"

BINARY_NAME = "serpapi.exe" if os.name == "nt" else "serpapi"


def bin_dir():
    return Path.home() / ".evo" / "bin"


def managed_binary():
    return bin_dir() / BINARY_NAME


def find_binary():
    """The serpapi binary on PATH, else the one evo installed itself."""
    found = shutil.which("serpapi")
    if found:
        return Path(found)
    managed = managed_binary()
    if managed.is_file() and os.access(managed, os.X_OK):
        return managed
    return None


def binary_version(binary):
    for args in (["--version"], ["version"]):
        try:
            result = subprocess.run(
                [str(binary), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            continue
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode == 0 and out:
            return out.splitlines()[0].strip()
    return None


def go_bin_dir():
    gopath = os.environ.get("GOPATH")
    if gopath:
        return Path(gopath) / "bin"
    return Path.home() / "go" / "bin"


def platform_target():
    system = platform.system().lower()
    goos = {"darwin": "darwin", "linux": "linux", "windows": "windows"}.get(system)
    machine = platform.machine().lower()
    arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine)
    if not goos or not arch:
        raise SerpError(f"no prebuilt serpapi binary for {system}/{machine}; try --method go or --method brew")
    return goos, arch


def latest_version():
    request = urllib.request.Request(GITHUB_LATEST, headers={"User-Agent": "evo-cli"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SerpError(f"GitHub release lookup failed: {exc}")
    tag = (data.get("tag_name") or "").lstrip("v")
    if not tag:
        raise SerpError("GitHub release lookup returned no tag_name")
    return tag


def asset_url(version):
    goos, arch = platform_target()
    return ASSET_URL.format(repo=REPO, ver=version, goos=goos, arch=arch)


def _extract_binary(archive, destination):
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            names = [name for name in bundle.namelist() if Path(name).name in ("serpapi", "serpapi.exe")]
            if not names:
                raise SerpError(f"no serpapi binary inside {archive.name}")
            with bundle.open(names[0]) as source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target)
        return
    with tarfile.open(archive, "r:*") as bundle:
        members = [m for m in bundle.getmembers() if m.isfile() and Path(m.name).name in ("serpapi", "serpapi.exe")]
        if not members:
            raise SerpError(f"no serpapi binary inside {archive.name}")
        source = bundle.extractfile(members[0])
        if source is None:
            raise SerpError(f"could not read serpapi out of {archive.name}")
        with source, open(destination, "wb") as target:
            shutil.copyfileobj(source, target)


def install_binary(version=None, downloader=None):
    """Download the release tarball and drop the binary in ~/.evo/bin."""
    version = version or latest_version()
    url = asset_url(version)
    target_dir = bin_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / BINARY_NAME

    if downloader is None:
        from evo_cli.console import download_file as downloader

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / Path(url).name
        try:
            downloader(url, str(archive), f"serpapi {version}")
        except (urllib.error.URLError, OSError) as exc:
            raise SerpError(f"download failed: {url} ({exc})")
        staged = Path(tmp) / BINARY_NAME
        _extract_binary(archive, staged)
        shutil.move(str(staged), str(target))

    try:
        os.chmod(target, 0o755)
    except OSError:
        pass
    return target, version, url


class BrewTrustRequired(SerpError):
    """Homebrew refuses to build a formula from a tap that is not trusted yet."""


def _brew(args):
    from evo_cli.console import console

    cmd = ["brew", *args]
    console.print(f"[cmd]$ {' '.join(cmd)}[/cmd]")
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if output:
        console.print(f"[dim]{output.splitlines()[-1]}[/dim]")
    return result.returncode, output


def _tail(output):
    lines = [line for line in (output or "").strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no output"


def install_brew(trust=False, runner=None):
    if shutil.which("brew") is None:
        raise SerpError("brew not found; use --method binary or --method go")
    runner = runner or _brew

    code, output = runner(["tap", BREW_TAP])
    if code != 0 and "already tapped" not in output.lower():
        raise SerpError(f"brew tap {BREW_TAP} failed: {_tail(output)}")

    if trust:
        code, output = runner(["trust", "--formula", BREW_QUALIFIED])
        if code != 0:
            raise SerpError(f"{BREW_TRUST_COMMAND} failed: {_tail(output)}")

    code, output = runner(["install", BREW_FORMULA])
    if code != 0:
        if "untrusted tap" in output.lower() or "brew trust" in output.lower():
            raise BrewTrustRequired(
                f"Homebrew will not build {BREW_QUALIFIED} until you trust the tap.\n"
                f"Trust it and retry with:  evo setup serp --trust-tap\n"
                f"(that runs `{BREW_TRUST_COMMAND}`)"
            )
        raise SerpError(f"brew install {BREW_FORMULA} failed: {_tail(output)}")

    binary = find_binary()
    if not binary:
        raise SerpError("brew reported success but serpapi is still not on PATH")
    return binary


def install_go(runner=None):
    if shutil.which("go") is None:
        raise SerpError("go toolchain not found; use --method brew or --method binary")
    runner = runner or _run
    runner(["go", "install", GO_PACKAGE])
    binary = find_binary()
    if binary:
        return binary
    candidate = go_bin_dir() / BINARY_NAME
    if candidate.is_file():
        return candidate
    raise SerpError(f"go install finished but no binary at {go_bin_dir()}")


def _run(cmd):
    from evo_cli.console import CommandError, run_command

    try:
        run_command(cmd)
    except CommandError as exc:
        raise SerpError(str(exc)) from exc


def pick_method(method):
    if method != "auto":
        return method
    if shutil.which("brew"):
        return "brew"
    return "binary"


def path_hint():
    """Warn only when the managed bin dir is invisible to the shell."""
    target = str(bin_dir())
    entries = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    if any(os.path.normpath(part) == os.path.normpath(target) for part in entries):
        return None
    return target
