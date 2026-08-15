import hashlib
import io
import json
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

from evo_cli.imaging import gemini, load_pillow, ncnn
from evo_cli.imaging.align import carry_alpha, fidelity, find_shift, restore_alpha, rim_lift
from evo_cli.imaging.creds import has_gemini_credentials
from evo_cli.imaging.errors import ImagingError

PROVIDERS = ("gemini", "ncnn")
OUTPUT_SETS = ("master", "ui")
DEFAULT_PRESET = "asset"
DECLARED_RATIO = 3.125
RIM_LIFT_EVIDENCE = {
    "gemini_white_backdrop": 15.6,
    "gemini_backdrop_drift": 155.0,
    "ncnn_control": 5.8,
}
RIM_LIFT_MAX = 20.0
REPORT_NAME = "report.json"
FAILED_FIELDS = (
    "engine",
    "engine_size",
    "shift",
    "peak",
    "fidelity_db",
    "rim_lift",
    "source_size",
    "master_size",
    "ui_size",
    "total_s",
)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
SKIP_DIRS = ("__MACOSX",)
SIZE_RE = re.compile(r"_(\d+)x(\d+)$")
GPU_LOCK = Lock()

PRESETS = {
    "asset": {
        "provider": "auto",
        "model": ncnn.DEFAULT_MODEL,
        "gemini_model": gemini.DEFAULT_MODEL,
        "image_size": gemini.DEFAULT_SIZE,
        "master_scale": 1.0,
        "declared_ratio": DECLARED_RATIO,
        "rim_lift_max": RIM_LIFT_MAX,
        "outputs": list(OUTPUT_SETS),
    }
}


def preset(name=DEFAULT_PRESET, **overrides):
    base = PRESETS.get(name or DEFAULT_PRESET)
    if base is None:
        raise ImagingError(f"unknown preset '{name}' (expected one of: {', '.join(sorted(PRESETS))})")
    settings = dict(base)
    settings.update({key: value for key, value in overrides.items() if value is not None})
    return settings


def resolve_provider(provider):
    if provider in PROVIDERS:
        return provider
    if provider in (None, "", "auto"):
        preferred = (os.environ.get("EVO_IMAGE_PROVIDER") or "").strip().lower()
        if preferred in PROVIDERS:
            return preferred
        if preferred and preferred != "auto":
            raise ImagingError(
                f"EVO_IMAGE_PROVIDER is set to '{preferred}', which is not one of: {', '.join(PROVIDERS)}"
            )
        return "gemini" if has_gemini_credentials() else "ncnn"
    raise ImagingError(f"unknown provider '{provider}' (expected one of: {', '.join(PROVIDERS)}, auto)")


def cache_root():
    custom = os.environ.get("EVO_IMAGE_CACHE")
    return Path(custom) if custom else Path.home() / ".evo" / "image-cache"


def cache_key(source, settings):
    digest = hashlib.sha256()
    with open(source, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    digest.update(json.dumps(settings, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest()


def declared_size(name, size, ratio=DECLARED_RATIO):
    match = SIZE_RE.search(Path(name).stem)
    if match:
        return int(match.group(1)), int(match.group(2))
    return max(1, round(size[0] / ratio)), max(1, round(size[1] / ratio))


def find_sources(root):
    root = Path(root)
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise ImagingError(f"no such file or directory: {root}")
    found = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in parts):
            continue
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            found.append(path)
    if not found:
        raise ImagingError(f"no images under {root} (looked for {', '.join(IMAGE_EXTS)})")
    return found


def _relative(source, root):
    source = Path(source)
    if root and Path(root).is_dir():
        try:
            return source.relative_to(Path(root))
        except ValueError:
            pass
    return Path(source.name)


def _output_path(out_dir, label, relative):
    return Path(out_dir) / label / relative.with_suffix(".png")


def _cache_paths(cache_dir, key):
    root = Path(cache_dir) if cache_dir else cache_root()
    return root / f"{key}.json", root / f"{key}.png"


def _label_size(size):
    return f"{int(size[0])}x{int(size[1])}"


def _scaled(size, scale):
    return max(1, round(size[0] * scale)), max(1, round(size[1] * scale))


def _parse_size(text):
    if not isinstance(text, str) or "x" not in text:
        return None
    width, _, height = text.partition("x")
    if not (width.isdigit() and height.isdigit()):
        return None
    return int(width), int(height)


def _read_record(path):
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_cache(record_path, record, image_path=None, image=None):
    record_path.parent.mkdir(parents=True, exist_ok=True)
    if image is not None and image_path is not None:
        image.save(image_path)
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _call_gemini(frame, settings, poster):
    data = gemini.upscale(
        frame,
        size=settings["image_size"],
        model=settings.get("gemini_model"),
        poster=poster,
    )
    Image = load_pillow()
    with Image.open(io.BytesIO(data)) as handle:
        return handle.convert("RGB")


def _stage(source, staged):
    Image = load_pillow()
    with Image.open(source) as handle:
        alpha = "A" in handle.getbands() or "transparency" in handle.info
        if ncnn.fits_buffer(handle.size, 4 if alpha else 3):
            return Path(source), None, None
        native = ncnn.safe_size(handle.size, 3)
        frame = handle.convert("RGBA")
    band = frame.split()[-1] if alpha else None
    body = frame.convert("RGB")
    if body.size != native:
        body = body.resize(native, Image.LANCZOS)
    body.save(staged)
    return staged, band, native


def _call_ncnn(source, size, settings, runner, record=None):
    Image = load_pillow()
    with tempfile.TemporaryDirectory() as tmp:
        fed, band, native = _stage(source, Path(tmp) / "staged.png")
        if native is not None and record is not None:
            record["ncnn_input"] = f"rgb {_label_size(native)}"
        target = Path(tmp) / "upscaled.png"
        with GPU_LOCK:
            ncnn.upscale(fed, target, model=settings["model"], size=size, runner=runner)
        with Image.open(target) as handle:
            result = handle.convert("RGBA")
    return carry_alpha(band, result) if band is not None else result


def _lift(image, size, settings, runner, record=None):
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "staged.png"
        image.save(staged)
        return _call_ncnn(staged, size, settings, runner, record)


def _measure(frame, candidate, merge=True):
    dy, dx, peak = find_shift(frame, candidate)
    merged = restore_alpha(frame, candidate, (dy, dx)) if merge else candidate.convert("RGBA")
    metrics = {
        "shift": [dy, dx],
        "peak": round(peak, 3),
        "fidelity_db": round(fidelity(frame, candidate), 2),
        "rim_lift": round(rim_lift(frame, candidate, (dy, dx)), 1),
    }
    return merged, metrics


def _render(source, frame, settings, record, poster, runner):
    if settings["provider"] == "gemini":
        started = time.time()
        try:
            candidate = _call_gemini(frame, settings, poster)
        except ImagingError as exc:
            record["gemini_s"] = round(time.time() - started, 1)
            record["fallback"] = str(exc)
        else:
            record["gemini_s"] = round(time.time() - started, 1)
            merged, metrics = _measure(frame, candidate)
            record.update(metrics)
            limit = float(settings["rim_lift_max"])
            if metrics["rim_lift"] <= limit:
                record["engine"] = "gemini"
                record["ncnn_s"] = 0.0
                return merged, candidate.size
            record["fallback"] = f"rim lift {metrics['rim_lift']} > {limit}"

    started = time.time()
    result = _call_ncnn(source, frame.size, settings, runner, record)
    record["ncnn_s"] = round(time.time() - started, 1)
    record["engine"] = "ncnn"
    merged, metrics = _measure(frame, result, merge=False)
    record.update(metrics)
    return merged, frame.size


def process_one(
    source,
    out_dir,
    root=None,
    settings=None,
    force=False,
    cache_dir=None,
    poster=None,
    runner=None,
    dry_run=False,
):
    settings = dict(settings or preset())
    settings["provider"] = resolve_provider(settings.get("provider"))
    source = Path(source)
    out_dir = Path(out_dir)
    relative = _relative(source, root)
    record_path, image_path = _cache_paths(cache_dir, cache_key(source, settings))
    stored = None if force else _read_record(record_path)
    ready = bool(stored) and all(_output_path(out_dir, label, relative).is_file() for label in settings["outputs"])

    if dry_run:
        return {"file": relative.as_posix(), "engine": (stored or {}).get("engine"), "cached": ready}
    if ready:
        return dict(stored, file=relative.as_posix(), cached=True)

    started = time.time()
    Image = load_pillow()
    with Image.open(source) as handle:
        has_alpha = handle.mode in ("RGBA", "LA") or "transparency" in handle.info
        frame = handle.convert("RGBA")
    targets = {
        "master": _scaled(frame.size, float(settings["master_scale"])),
        "ui": declared_size(source.name, frame.size, float(settings["declared_ratio"])),
    }
    record = {"file": relative.as_posix(), "alpha": has_alpha, "source_size": _label_size(frame.size)}

    if stored and image_path.is_file():
        with Image.open(image_path) as handle:
            base = handle.convert("RGBA")
        record = dict(stored, file=relative.as_posix())
        native = _parse_size(record.get("engine_size")) or base.size
        cached = True
    else:
        base, native = _render(source, frame, settings, record, poster, runner)
        record["engine_size"] = _label_size(native)
        _write_cache(record_path, record, image_path, base)
        cached = False

    for label in settings["outputs"]:
        target = targets[label]
        picture = base
        if native[0] < target[0] or native[1] < target[1]:
            lifted = time.time()
            picture = _lift(base, target, settings, runner, record)
            record["ncnn_s"] = round(float(record.get("ncnn_s") or 0.0) + time.time() - lifted, 1)
            record[f"{label}_via"] = f"{record['engine']}+ncnn"
        if picture.size != target:
            picture = picture.resize(target, Image.LANCZOS)
        if not has_alpha:
            picture = picture.convert("RGB")
        path = _output_path(out_dir, label, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        picture.save(path)
        record[f"{label}_size"] = _label_size(target)

    record["total_s"] = round(time.time() - started, 1)
    _write_cache(record_path, record)
    return dict(record, cached=cached)


def failed_record(source, root, error):
    record = {"file": _relative(source, root).as_posix(), "error": str(error)}
    record.update({key: None for key in FAILED_FIELDS})
    record["cached"] = False
    return record


def write_report(out_dir, records):
    path = Path(out_dir) / REPORT_NAME
    kept = []
    previous = None
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    if isinstance(previous, list):
        fresh = {record.get("file") for record in records}
        kept = [item for item in previous if isinstance(item, dict) and item.get("file") not in fresh]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept + list(records), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def process_many(
    sources,
    out_dir,
    root=None,
    settings=None,
    jobs=4,
    force=False,
    cache_dir=None,
    poster=None,
    runner=None,
    dry_run=False,
    on_item=None,
):
    settings = dict(settings or preset())
    settings["provider"] = resolve_provider(settings.get("provider"))
    sources = [Path(item) for item in sources]
    results = [None] * len(sources)

    def work(index):
        return process_one(
            sources[index],
            out_dir,
            root=root,
            settings=settings,
            force=force,
            cache_dir=cache_dir,
            poster=poster,
            runner=runner,
            dry_run=dry_run,
        )

    def keep(index, entry):
        results[index] = entry
        if on_item:
            on_item(entry)

    def attempt(index):
        try:
            keep(index, work(index))
        except (ImagingError, OSError) as exc:
            keep(index, failed_record(sources[index], root, exc))

    if dry_run or jobs <= 1 or len(sources) <= 1:
        for index in range(len(sources)):
            attempt(index)
    else:
        with ThreadPoolExecutor(max_workers=min(jobs, len(sources))) as pool:
            futures = {pool.submit(attempt, index): index for index in range(len(sources))}
            for future in as_completed(futures):
                future.result()

    if not dry_run:
        write_report(out_dir, results)
    return results
