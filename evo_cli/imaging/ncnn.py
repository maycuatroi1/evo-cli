import os
import platform
import shutil
import subprocess
import tempfile
import urllib.error
import zipfile
from pathlib import Path

from evo_cli.imaging.errors import ImagingError

RELEASE = "20251207-174704"
ASSET_URL = "https://github.com/upscayl/upscayl-ncnn/releases/download/{release}/upscayl-bin-{release}-{slug}.zip"
MODEL_URL = "https://raw.githubusercontent.com/upscayl/upscayl/main/resources/models/{name}.{ext}"

MODELS = (
    "digital-art-4x",
    "high-fidelity-4x",
    "remacri-4x",
    "ultramix-balanced-4x",
    "ultrasharp-4x",
    "upscayl-lite-4x",
    "upscayl-standard-4x",
)
DEFAULT_MODEL = "ultrasharp-4x"
MODEL_EXTS = ("bin", "param")

BINARY_NAME = "upscayl-bin.exe" if os.name == "nt" else "upscayl-bin"

MODEL_SCALE = 4
BUFFER_LIMIT = 2**31
SHRINK_STEP = 0.99


def cache_dir():
    custom = os.environ.get("EVO_UPSCAYL_DIR")
    return Path(custom) if custom else Path.home() / ".evo" / "upscayl"


def binary_dir():
    return cache_dir() / RELEASE


def models_dir():
    return cache_dir() / "models"


def platform_slug():
    system = platform.system().lower()
    slug = {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(system)
    if not slug:
        raise ImagingError(f"no prebuilt upscayl-bin for {system}; put one on PATH as {BINARY_NAME}")
    return slug


def asset_url(slug=None):
    return ASSET_URL.format(release=RELEASE, slug=slug or platform_slug())


def model_urls(model):
    return tuple(MODEL_URL.format(name=model, ext=ext) for ext in MODEL_EXTS)


def _managed_binary():
    root = binary_dir()
    if not root.is_dir():
        return None
    for candidate in sorted(root.rglob(BINARY_NAME)):
        if candidate.is_file():
            return candidate
    return None


def find_binary():
    found = shutil.which("upscayl-bin")
    if found:
        return Path(found)
    return _managed_binary()


def install_binary(downloader=None):
    url = asset_url()
    root = binary_dir()
    root.mkdir(parents=True, exist_ok=True)

    if downloader is None:
        from evo_cli.console import download_file as downloader

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / Path(url).name
        try:
            downloader(url, str(archive), f"upscayl-bin {RELEASE}")
        except (urllib.error.URLError, OSError) as exc:
            raise ImagingError(f"download failed: {url} ({exc})")
        try:
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(root)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ImagingError(f"could not unpack {archive.name}: {exc}")

    binary = _managed_binary()
    if not binary:
        raise ImagingError(f"no {BINARY_NAME} inside {url}")
    try:
        os.chmod(binary, 0o755)
    except OSError:
        pass
    return binary


def ensure_binary(downloader=None):
    return find_binary() or install_binary(downloader)


def ensure_model(model=DEFAULT_MODEL, downloader=None):
    if model not in MODELS:
        raise ImagingError(f"unknown model: {model}\nAvailable: {', '.join(MODELS)}")
    target = models_dir()
    target.mkdir(parents=True, exist_ok=True)

    if downloader is None:
        from evo_cli.console import download_file as downloader

    for url in model_urls(model):
        path = target / Path(url).name
        if path.is_file() and path.stat().st_size:
            continue
        try:
            downloader(url, str(path), path.name)
        except (urllib.error.URLError, OSError) as exc:
            path.unlink(missing_ok=True)
            raise ImagingError(f"model download failed: {url} ({exc})")
    return target


def buffer_bytes(size, channels):
    return int(size[0]) * int(size[1]) * MODEL_SCALE * MODEL_SCALE * int(channels)


def fits_buffer(size, channels):
    return buffer_bytes(size, channels) < BUFFER_LIMIT


def safe_size(size, channels):
    width, height = int(size[0]), int(size[1])
    while (width > 1 or height > 1) and not fits_buffer((width, height), channels):
        factor = min(SHRINK_STEP, (BUFFER_LIMIT / buffer_bytes((width, height), channels)) ** 0.5)
        width = max(1, int(width * factor))
        height = max(1, int(height * factor))
    return width, height


def build_command(binary, src, dst, model, size, models_root):
    cmd = [str(binary), "-i", str(src), "-o", str(dst), "-m", str(models_root), "-n", model, "-f", "png"]
    if size:
        cmd += ["-r", f"{int(size[0])}x{int(size[1])}"]
    return cmd


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def _exit_label(code):
    if code < 0:
        return f"killed by signal {-code}"
    if code < 256:
        return f"exit {code}"
    return f"exit {code} (0x{code:08X})"


def _tail(result):
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    lines = [line for line in output.splitlines() if line.strip()]
    return f"{_exit_label(result.returncode)}, last output: {lines[-1] if lines else 'no output'}"


def upscale(src, dst, model=DEFAULT_MODEL, size=None, runner=None):
    binary = ensure_binary()
    models_root = ensure_model(model)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_command(binary, src, dst, model, size, models_root)
    result = (runner or _run)(cmd)
    if result.returncode != 0 or not dst.is_file():
        raise ImagingError(f"upscayl-bin failed on {Path(src).name}: {_tail(result)}")
    return dst
