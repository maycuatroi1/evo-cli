import ctypes.util
import importlib.metadata
import os
import sys
from pathlib import Path

import rich_click as click
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from evo_cli.console import console, info, step, success, warning
from evo_cli.imaging import core, load_numpy, load_pillow, ncnn
from evo_cli.imaging.creds import has_gemini_credentials
from evo_cli.imaging.errors import ImagingError

OUTPUT_CHOICES = ("master", "ui", "both")
PROVIDER_CHOICES = ("auto", "gemini", "ncnn")
DEFAULT_OUT_DIR = "image-out"
IMAGE_EXTRA_FIX = "run `pip install evo_cli[image]`"
BINARY_FIX = "run `evo image install`"
MODEL_FIX = f"`evo image install` fetches {ncnn.DEFAULT_MODEL}"
VULKAN_FIX = "install the GPU driver / Vulkan runtime the local engine needs"
GEMINI_FIX = "run `evo cred add gemini_api_key --from-stdin`"

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo image upscale hero.png[/cyan]                    one file into ./image-out\n"
    "  [cyan]evo image upscale ./in -o ./out[/cyan]               master + ui + report.json\n"
    "  [cyan]evo image upscale ./in -o ./out --dry-run[/cyan]     count what is cached\n"
    "  [cyan]evo image upscale ./in --provider ncnn[/cyan]        force the local engine\n"
    "  [cyan]evo image upscale ./in --only ui --scale 0.5[/cyan]  half-size UI set\n"
    "  [cyan]evo image upscale ./in --force[/cyan]                ignore the cache\n"
    "  [cyan]evo image check[/cyan]                               is every moving part there?\n"
    "  [cyan]evo image install[/cyan]                             fetch upscayl-bin + model\n\n"
    "[dim]Gemini renders when its key is stored, upscayl-ncnn otherwise.\n"
    "Every render is cached by content hash, so a second run is free.[/dim]"
)

UPSCALE_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo image upscale ./in -o ./out[/cyan]         master + ui + report.json\n"
    "  [cyan]evo image upscale ./in -o ./out --dry-run[/cyan]  prints `cached: <n>`\n"
    "  [cyan]evo image upscale ./in --model remacri-4x[/cyan]  pick the local model\n"
    "  [cyan]evo image upscale card.png --only master[/cyan]   skip the UI set\n\n"
    "[dim]`master` keeps the rendered resolution, `ui` lands on the size the\n"
    "filename declares (`name_256x256.png`), or the preset ratio otherwise.[/dim]"
)


def pillow_version():
    try:
        load_pillow()
    except ImagingError:
        return None
    try:
        return importlib.metadata.version("pillow")
    except importlib.metadata.PackageNotFoundError:
        return "installed"


def numpy_version():
    try:
        numpy = load_numpy()
    except ImagingError:
        return None
    return getattr(numpy, "__version__", "installed")


def vulkan_loader():
    if os.name == "nt":
        names = ("vulkan-1",)
    elif sys.platform == "darwin":
        names = ("vulkan", "MoltenVK")
    else:
        names = ("vulkan",)
    for name in names:
        found = ctypes.util.find_library(name)
        if found:
            return found
    return None


def installed_models():
    root = ncnn.models_dir()
    if not root.is_dir():
        return []
    return [name for name in ncnn.MODELS if all((root / f"{name}.{ext}").is_file() for ext in ncnn.MODEL_EXTS)]


def environment_parts():
    binary = ncnn.find_binary()
    models = installed_models()
    return [
        ("Pillow", pillow_version(), IMAGE_EXTRA_FIX),
        ("numpy", numpy_version(), IMAGE_EXTRA_FIX),
        ("upscayl-bin", str(binary) if binary else None, BINARY_FIX),
        ("Models", ", ".join(models) if ncnn.DEFAULT_MODEL in models else None, MODEL_FIX),
        ("Vulkan", vulkan_loader(), VULKAN_FIX),
        ("Gemini key", "gemini_api_key" if has_gemini_credentials() else None, GEMINI_FIX),
    ]


def report_environment():
    console.print()
    table = Table(show_header=True, header_style="accent", expand=False)
    table.add_column("Component", style="info", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail", style="dim")
    missing = []
    for name, detail, fix in environment_parts():
        if detail:
            table.add_row(name, "[success]ok[/success]", escape(str(detail)))
        else:
            missing.append(name)
            table.add_row(name, "[error]missing[/error]", fix)
    table.add_row("Cache", "[info]-[/info]", escape(str(core.cache_root())))
    console.print(table)
    return missing


def build_settings(preset_name, provider, model, scale, only):
    return core.preset(
        preset_name,
        provider=core.resolve_provider(provider),
        model=model,
        master_scale=scale,
        outputs=list(core.OUTPUT_SETS) if only == "both" else [only],
    )


@click.group(
    "image",
    epilog=EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "Upscale images with **Gemini** or **upscayl-ncnn**.\n\n"
        "`upscale` walks a file or a whole tree and writes two PNG sets - `master` at the "
        "rendered resolution and `ui` at the size each filename declares - plus a "
        "`report.json` recording which engine shipped every image. Renders are cached by "
        "content hash, so re-running over the same tree is free.\n\n"
        "`check` says which moving part is missing, `install` fetches the local engine."
    ),
)
def image_group():
    pass


@image_group.command(
    "upscale",
    epilog=UPSCALE_EPILOG,
    help=(
        "Upscale `SOURCE` - one image or a directory tree.\n\n"
        "The output tree mirrors the input under `<out>/master` and `<out>/ui`, keeping "
        "alpha where the source had it, and `<out>/report.json` lists the engine, the "
        "sizes and the timings per image."
    ),
)
@click.argument("source", type=click.Path(exists=True))
@click.option(
    "-o",
    "--output",
    "out_dir",
    type=click.Path(file_okay=False),
    default=DEFAULT_OUT_DIR,
    show_default=True,
    help="Where `master/`, `ui/` and `report.json` land.",
)
@click.option(
    "--preset",
    "preset_name",
    type=click.Choice(sorted(core.PRESETS)),
    default=core.DEFAULT_PRESET,
    show_default=True,
    help="Measured defaults to start from.",
)
@click.option(
    "--provider",
    type=click.Choice(PROVIDER_CHOICES),
    default="auto",
    show_default=True,
    help="`auto` picks Gemini when its key is stored, else the local engine.",
)
@click.option(
    "--model",
    type=click.Choice(ncnn.MODELS),
    default=None,
    help=f"Local upscayl model. Default: `{ncnn.DEFAULT_MODEL}`.",
)
@click.option("--scale", type=float, default=None, help="Master size relative to the source. Default: `1.0`.")
@click.option(
    "--only",
    type=click.Choice(OUTPUT_CHOICES),
    default="both",
    show_default=True,
    help="Write just one of the two output sets.",
)
@click.option("-j", "--jobs", type=int, default=4, show_default=True, help="Images in flight at once.")
@click.option("--dry-run", is_flag=True, help="Report what the cache already covers and write nothing.")
@click.option("--force", is_flag=True, help="Render again even when the cache has the image.")
def upscale_cmd(source, out_dir, preset_name, provider, model, scale, only, jobs, dry_run, force):
    step("evo image upscale")
    try:
        settings = build_settings(preset_name, provider, model, scale, only)
        sources = core.find_sources(source)
    except ImagingError as exc:
        raise click.ClickException(str(exc)) from exc

    info(
        f"{len(sources)} image(s), engine [accent]{settings['provider']}[/accent], "
        f"sets [accent]{', '.join(settings['outputs'])}[/accent]"
    )

    if dry_run:
        results = core.process_many(
            sources, out_dir, root=source, settings=settings, jobs=jobs, force=force, dry_run=True
        )
        ready = sum(1 for item in results if item.get("cached"))
        info(f"Dry run: {len(results)} images, cached: {ready}, to render: {len(results) - ready}")
        return

    done = []

    def on_item(entry):
        done.append(entry)
        state = entry.get("error") or ("cached" if entry.get("cached") else entry.get("engine") or "done")
        info(f"{len(done)}/{len(sources)} {escape(str(entry.get('file')))} - {escape(str(state))}")

    results = core.process_many(
        sources, out_dir, root=source, settings=settings, jobs=jobs, force=force, on_item=on_item
    )
    failed = [item for item in results if item and item.get("error")]
    cached = sum(1 for item in results if item and item.get("cached"))
    for item in failed:
        warning(f"{escape(str(item.get('file')))}: {escape(str(item['error']))}")
    info(f"cached: {cached}, rendered: {len(results) - cached - len(failed)}, failed: {len(failed)}")
    if failed:
        raise click.ClickException(f"{len(failed)} of {len(results)} images failed.")
    success(
        f"Wrote {', '.join(settings['outputs'])} + {core.REPORT_NAME} "
        f"under [accent]{escape(str(Path(out_dir)))}[/accent]"
    )


@image_group.command(
    "check",
    help=(
        "Verify every moving part: Pillow, numpy, the upscayl binary, the model files, "
        "a Vulkan device and the Gemini key. Exits non-zero when one is missing."
    ),
)
def check_cmd():
    step("evo image check")
    missing = report_environment()
    if missing:
        raise click.ClickException(
            f"missing: {', '.join(missing)}. "
            "Run `evo image install` for the local engine, "
            "`evo cred add gemini_api_key --from-stdin` for the hosted one."
        )
    success("Ready to upscale.")


@image_group.command(
    "install",
    help=(
        "Fetch **upscayl-bin** and the default model ahead of time.\n\n"
        "Everything lands under `~/.evo/upscayl` (or `$EVO_UPSCAYL_DIR`), so the first "
        "`evo image upscale` does not stop to download."
    ),
)
@click.option(
    "-m",
    "--model",
    "models",
    type=click.Choice(ncnn.MODELS),
    multiple=True,
    help=f"Model to fetch; repeatable. Default: `{ncnn.DEFAULT_MODEL}`.",
)
@click.option("--all-models", is_flag=True, help="Fetch every model upscayl ships.")
def install_cmd(models, all_models):
    step("evo image install")
    wanted = list(ncnn.MODELS) if all_models else list(models) or [ncnn.DEFAULT_MODEL]
    try:
        binary = ncnn.ensure_binary()
        info(f"upscayl-bin at [accent]{escape(str(binary))}[/accent]")
        for name in wanted:
            ncnn.ensure_model(name)
            info(f"model [accent]{name}[/accent] ready")
    except ImagingError as exc:
        raise click.ClickException(str(exc)) from exc
    report_environment()
    success("Local engine ready.")
