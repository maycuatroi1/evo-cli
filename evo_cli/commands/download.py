import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import rich_click as click
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from evo_cli.console import (
    console,
    download_file,
    info,
    run_command,
    step,
    success,
    warning,
)

QUALITIES = ["best", "2160p", "1440p", "1080p", "720p", "480p", "360p", "worst"]
AUDIO_FORMATS = ["mp3", "m4a", "opus", "flac", "wav", "aac", "vorbis", "best"]
CONTAINERS = ["mp4", "mkv", "webm", "auto"]
BROWSERS = ["chrome", "edge", "firefox", "brave", "chromium", "opera", "vivaldi", "safari"]
JS_RUNTIMES = ["deno", "bun", "node"]

DIRECT_EXTS = {
    ".7z",
    ".apk",
    ".appimage",
    ".bin",
    ".bz2",
    ".csv",
    ".deb",
    ".dmg",
    ".doc",
    ".docx",
    ".epub",
    ".exe",
    ".gz",
    ".img",
    ".iso",
    ".jar",
    ".json",
    ".msi",
    ".pdf",
    ".ppt",
    ".pptx",
    ".rar",
    ".rpm",
    ".tar",
    ".tgz",
    ".txt",
    ".whl",
    ".xls",
    ".xlsx",
    ".xz",
    ".zip",
    ".zst",
}

SITES = [
    ("YouTube", "video, playlist, live, shorts, music"),
    ("TikTok / Douyin", "video, no watermark"),
    ("Facebook", "video, reels, watch"),
    ("Instagram", "post, reels, stories (cần --cookies)"),
    ("X / Twitter", "video, spaces"),
    ("Twitch", "VOD, clip, live"),
    ("Vimeo", "video (cần --cookies nếu private)"),
    ("SoundCloud", "track, playlist"),
    ("Bilibili", "video, bangumi"),
    ("Reddit", "video + audio"),
    ("Dailymotion", "video"),
    ("Direct URL", ".zip .pdf .exe .iso ... tải thẳng"),
]

FFMPEG_PACKAGES = {
    "apt-get": "ffmpeg",
    "dnf": "ffmpeg",
    "yum": "ffmpeg",
    "pacman": "ffmpeg",
    "zypper": "ffmpeg",
    "brew": "ffmpeg",
    "winget": "Gyan.FFmpeg",
    "choco": "ffmpeg",
}

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo download <url>[/cyan]                          best quality, merged to MP4\n"
    "  [cyan]evo download <url> -q 1080p[/cyan]                 cap the resolution\n"
    "  [cyan]evo download <url> -a[/cyan]                       audio only, MP3\n"
    "  [cyan]evo download <url> -a --audio-format flac[/cyan]   lossless audio\n"
    "  [cyan]evo download <url> -o D:\\Videos[/cyan]             pick the output folder\n"
    "  [cyan]evo download <url> -s en,vi --thumbnail[/cyan]     embed subtitles + cover art\n"
    "  [cyan]evo download <url> --section 10:00-15:00[/cyan]    cut a clip out of a long video\n"
    "  [cyan]evo download <url> -p --archive[/cyan]             whole playlist, skip what you have\n"
    "  [cyan]evo download <url> --cookies chrome[/cyan]         private / age-gated / member-only\n"
    "  [cyan]evo download formats <url>[/cyan]                  list every available stream\n"
    "  [cyan]evo download check[/cyan]                          verify yt-dlp + ffmpeg\n"
    "  [cyan]evo download sites[/cyan]                          common supported sources\n\n"
    "[dim]Powered by yt-dlp (1800+ sites) with ffmpeg for merging. Plain file URLs\n"
    "(.zip, .pdf, .iso, ...) are fetched directly instead.[/dim]"
)


def default_output_dir():
    env = os.environ.get("EVO_DOWNLOAD_DIR")
    if env:
        return Path(env)
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        return downloads
    return Path.cwd()


def ytdlp_command():
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return None


def ytdlp_version():
    cmd = ytdlp_command()
    if not cmd:
        return None
    try:
        result = subprocess.run(cmd + ["--version"], capture_output=True, text=True)
    except OSError:
        return None
    return (result.stdout or "").strip() or None


def find_ffmpeg():
    return shutil.which("ffmpeg")


def ffmpeg_version():
    exe = find_ffmpeg()
    if not exe:
        return None
    try:
        result = subprocess.run([exe, "-version"], capture_output=True, text=True)
    except OSError:
        return None
    first = ((result.stdout or "") + (result.stderr or "")).strip().splitlines()
    return first[0] if first else None


def find_js_runtime():
    for name in JS_RUNTIMES:
        path = shutil.which(name)
        if path:
            return name, path
    return None, None


def install_ytdlp():
    run_command([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"], status="Installing yt-dlp")


def detect_package_manager():
    for manager in ("apt-get", "dnf", "yum", "pacman", "zypper", "brew", "winget", "choco"):
        if shutil.which(manager):
            return manager
    return None


def install_ffmpeg(assume_yes):
    manager = detect_package_manager()
    if not manager:
        warning("No supported package manager found. Install ffmpeg manually.")
        return False
    package = FFMPEG_PACKAGES[manager]
    if manager == "winget":
        cmd = ["winget", "install", "-e", "--id", package]
    elif manager == "brew":
        cmd = ["brew", "install", package]
    elif manager == "choco":
        cmd = ["choco", "install", package] + (["-y"] if assume_yes else [])
    elif manager == "pacman":
        cmd = ["pacman", "-S", "--noconfirm" if assume_yes else "--needed", package]
    else:
        cmd = [manager, "install"] + (["-y"] if assume_yes else []) + [package]
    if os.name != "nt" and manager not in ("brew",) and os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    result = run_command(cmd, check=False)
    return result.returncode == 0


def ensure_ytdlp(assume_yes=False):
    cmd = ytdlp_command()
    if cmd:
        return cmd
    warning("yt-dlp is not installed.")
    if not assume_yes and not click.confirm("Install yt-dlp now with pip?", default=True):
        raise click.ClickException("yt-dlp is required. Install it with: pip install -U yt-dlp")
    install_ytdlp()
    cmd = ytdlp_command()
    if not cmd:
        raise click.ClickException("yt-dlp was installed but could not be found. Check your PATH.")
    return cmd


def format_selector(quality, container, has_ffmpeg):
    if quality == "worst":
        return "wv*+wa/w" if has_ffmpeg else "w"
    height = None if quality == "best" else quality.rstrip("p")
    limit = f"[height<={height}]" if height else ""
    if not has_ffmpeg:
        return f"b{limit}/b"
    if container == "mp4":
        return f"bv*{limit}[ext=mp4]+ba[ext=m4a]/bv*{limit}+ba/b{limit}/b"
    return f"bv*{limit}+ba/b{limit}/b"


def output_template(outdir, playlist, name):
    if name:
        template = name
    elif playlist:
        template = "%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s"
    else:
        template = "%(title)s.%(ext)s"
    return str(Path(outdir) / template)


def is_direct_file(url):
    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    return suffix in DIRECT_EXTS


def direct_filename(url):
    name = Path(unquote(urlparse(url).path)).name
    return name or "download.bin"


def build_ytdlp_args(opts, has_ffmpeg):
    args = []
    runtime, _ = find_js_runtime()
    if runtime and runtime != "deno":
        args += ["--js-runtimes", runtime]

    if opts["list_formats"]:
        args += ["-F", "--no-playlist"]
        return args

    args += ["-o", output_template(opts["outdir"], opts["playlist"], opts["name"])]
    args += ["--no-playlist"] if not opts["playlist"] else ["--yes-playlist"]
    args += ["-N", str(opts["concurrent"])]

    if opts["fmt"]:
        args += ["-f", opts["fmt"]]
    elif opts["audio_only"]:
        args += ["-f", "ba/b", "-x", "--audio-format", opts["audio_format"], "--audio-quality", "0"]
    else:
        args += ["-f", format_selector(opts["quality"], opts["container"], has_ffmpeg)]
        if has_ffmpeg and opts["container"] != "auto":
            args += ["--merge-output-format", opts["container"]]

    if has_ffmpeg:
        args += ["--embed-metadata", "--embed-chapters"]
    if opts["thumbnail"]:
        args += ["--embed-thumbnail"]
    if opts["subs"]:
        args += ["--sub-langs", opts["subs"], "--write-subs", "--write-auto-subs"]
        if has_ffmpeg:
            args += ["--embed-subs"]
    if opts["sponsorblock"]:
        args += ["--sponsorblock-remove", "default"]
    if opts["cookies"]:
        args += ["--cookies-from-browser", opts["cookies"]]
    if opts["archive"]:
        args += ["--download-archive", str(Path(opts["outdir"]) / ".evo-download-archive.txt")]
    if opts["section"]:
        args += ["--download-sections", f"*{opts['section']}", "--force-keyframes-at-cuts"]
    if opts["ascii_names"]:
        args += ["--restrict-filenames"]
    if opts["limit_rate"]:
        args += ["-r", opts["limit_rate"]]
    return args


def report_environment():
    version = ytdlp_version()
    ffmpeg = ffmpeg_version()
    runtime, runtime_path = find_js_runtime()
    console.print()
    table = Table(show_header=True, header_style="accent", expand=False)
    table.add_column("Component", style="info", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail", style="dim")
    table.add_row(
        "yt-dlp",
        "[success]ok[/success]" if version else "[error]missing[/error]",
        version or "run `evo download install`",
    )
    table.add_row(
        "ffmpeg",
        "[success]ok[/success]" if ffmpeg else "[error]missing[/error]",
        ffmpeg or "needed to merge video + audio and convert audio",
    )
    table.add_row(
        "JS runtime",
        "[success]ok[/success]" if runtime else "[warning]missing[/warning]",
        f"{runtime} ({runtime_path})" if runtime else "optional - some sites need it to extract streams",
    )
    table.add_row("Output dir", "[info]-[/info]", str(default_output_dir()))
    console.print(table)


def do_direct(url, outdir):
    target = Path(outdir) / direct_filename(url)
    Path(outdir).mkdir(parents=True, exist_ok=True)
    info(f"Direct download: [accent]{url}[/accent]")
    try:
        download_file(url, str(target), target.name)
    except OSError as exc:
        raise click.ClickException(f"Download failed: {exc}")
    success(f"Saved to [accent]{target}[/accent]")


def do_get(urls, opts, assume_yes):
    if not urls:
        raise click.ClickException("Give me at least one URL.")

    outdir = Path(opts["outdir"])
    outdir.mkdir(parents=True, exist_ok=True)

    direct = [u for u in urls if is_direct_file(u)]
    media = [u for u in urls if u not in direct]

    for url in direct:
        do_direct(url, outdir)

    if not media:
        return

    exe = ensure_ytdlp(assume_yes)
    has_ffmpeg = bool(find_ffmpeg())
    if not has_ffmpeg:
        if opts["audio_only"]:
            raise click.ClickException("ffmpeg is required to extract audio. Run `evo download install --with-deps`.")
        warning("ffmpeg not found: falling back to single-file streams (lower quality).")
        info("Install it with `evo download install --with-deps` for the best quality.")

    cmd = exe + build_ytdlp_args(opts, has_ffmpeg) + list(media)
    if opts["dry_run"]:
        console.print(f"[cmd]$ {escape(' '.join(cmd))}[/cmd]")
        return

    result = run_command(cmd, check=False)
    if result.returncode != 0:
        raise click.ClickException("yt-dlp failed. Try `evo download formats <url>` to inspect the source.")
    if not opts["list_formats"]:
        success(f"Saved to [accent]{outdir}[/accent]")


class DownloadGroup(click.RichGroup):
    def parse_args(self, ctx, args):
        if args and args[0] not in self.commands and args[0] not in ("-h", "--help"):
            args = ["get", *args]
        return super().parse_args(ctx, args)


@click.group(
    "download",
    cls=DownloadGroup,
    epilog=EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "Download video / audio from **common sources**.\n\n"
        "Wraps `yt-dlp` (YouTube, TikTok, Facebook, Instagram, X, Twitch, Vimeo, "
        "SoundCloud, Bilibili, Reddit, ... 1800+ sites) and uses `ffmpeg` to merge "
        "video + audio into a single file. Plain file URLs are fetched directly.\n\n"
        "`evo download <url>` is a shortcut for `evo download get <url>`."
    ),
)
def download():
    pass


def get_options(func):
    options = [
        click.option(
            "-o",
            "--output",
            "outdir",
            type=click.Path(file_okay=False),
            default=None,
            help="Output folder. Default: `~/Downloads` (or `$EVO_DOWNLOAD_DIR`).",
        ),
        click.option(
            "-q",
            "--quality",
            type=click.Choice(QUALITIES),
            default="best",
            show_default=True,
            help="Cap the video resolution.",
        ),
        click.option(
            "-c",
            "--container",
            type=click.Choice(CONTAINERS),
            default="mp4",
            show_default=True,
            help="Container to merge into. `auto` keeps whatever the site serves.",
        ),
        click.option("-a", "--audio-only", is_flag=True, help="Grab the audio track only."),
        click.option(
            "--audio-format",
            type=click.Choice(AUDIO_FORMATS),
            default="mp3",
            show_default=True,
            help="Audio codec to convert to with `-a`.",
        ),
        click.option("-f", "--format", "fmt", default=None, help="Raw yt-dlp format selector, overrides `-q` / `-a`."),
        click.option(
            "-p", "--playlist", is_flag=True, help="Download the whole playlist / channel, not just the one video."
        ),
        click.option(
            "-s", "--subs", default=None, metavar="LANGS", help="Download and embed subtitles, e.g. `en,vi` or `all`."
        ),
        click.option("--thumbnail", is_flag=True, help="Embed the thumbnail as cover art."),
        click.option("--sponsorblock", is_flag=True, help="Cut sponsor segments out (YouTube)."),
        click.option(
            "--cookies",
            type=click.Choice(BROWSERS),
            default=None,
            help="Load cookies from a browser for private / age-gated / member-only media.",
        ),
        click.option("--archive", is_flag=True, help="Record what was downloaded and skip it next time."),
        click.option(
            "--section", default=None, metavar="RANGE", help="Download only a time range, e.g. `10:00-15:00`."
        ),
        click.option(
            "--ascii", "ascii_names", is_flag=True, help="Restrict filenames to ASCII (no spaces or diacritics)."
        ),
        click.option(
            "-N", "--concurrent", type=int, default=4, show_default=True, help="Fragments to download in parallel."
        ),
        click.option("-r", "--limit-rate", default=None, metavar="RATE", help="Cap the download speed, e.g. `2M`."),
        click.option("--name", default=None, metavar="TEMPLATE", help="yt-dlp output template, e.g. `%(id)s.%(ext)s`."),
        click.option("-y", "--yes", "assume_yes", is_flag=True, help="Assume yes for install prompts."),
        click.option("--dry-run", is_flag=True, help="Print the yt-dlp command instead of running it."),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def collect_opts(
    outdir,
    quality,
    container,
    audio_only,
    audio_format,
    fmt,
    playlist,
    subs,
    thumbnail,
    sponsorblock,
    cookies,
    archive,
    section,
    ascii_names,
    concurrent,
    limit_rate,
    name,
    dry_run,
    list_formats=False,
):
    return {
        "outdir": Path(outdir) if outdir else default_output_dir(),
        "quality": quality,
        "container": container,
        "audio_only": audio_only,
        "audio_format": audio_format,
        "fmt": fmt,
        "playlist": playlist,
        "subs": subs,
        "thumbnail": thumbnail,
        "sponsorblock": sponsorblock,
        "cookies": cookies,
        "archive": archive,
        "section": section,
        "ascii_names": ascii_names,
        "concurrent": concurrent,
        "limit_rate": limit_rate,
        "name": name,
        "dry_run": dry_run,
        "list_formats": list_formats,
    }


@download.command(
    "get",
    epilog=EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "Download one or more `URLS`.\n\n"
        "Picks the best video + audio and merges them into a single MP4 next to your "
        "other downloads. Use `-q` to cap the resolution, `-a` for audio only, and "
        "`--cookies <browser>` when the media needs a login."
    ),
)
@click.argument("urls", nargs=-1, required=True, metavar="URLS...")
@get_options
def get_cmd(
    urls,
    outdir,
    quality,
    container,
    audio_only,
    audio_format,
    fmt,
    playlist,
    subs,
    thumbnail,
    sponsorblock,
    cookies,
    archive,
    section,
    ascii_names,
    concurrent,
    limit_rate,
    name,
    assume_yes,
    dry_run,
):
    step("evo download")
    opts = collect_opts(
        outdir,
        quality,
        container,
        audio_only,
        audio_format,
        fmt,
        playlist,
        subs,
        thumbnail,
        sponsorblock,
        cookies,
        archive,
        section,
        ascii_names,
        concurrent,
        limit_rate,
        name,
        dry_run,
    )
    do_get(list(urls), opts, assume_yes)


@download.command(
    "audio",
    epilog=EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Download `URLS` as audio only. Shortcut for `evo download get -a`.",
)
@click.argument("urls", nargs=-1, required=True, metavar="URLS...")
@click.option(
    "-o",
    "--output",
    "outdir",
    type=click.Path(file_okay=False),
    default=None,
    help="Output folder. Default: `~/Downloads`.",
)
@click.option(
    "--audio-format",
    type=click.Choice(AUDIO_FORMATS),
    default="mp3",
    show_default=True,
    help="Audio codec to convert to.",
)
@click.option("-p", "--playlist", is_flag=True, help="Download the whole playlist.")
@click.option("--thumbnail", is_flag=True, help="Embed the thumbnail as cover art.")
@click.option("--cookies", type=click.Choice(BROWSERS), default=None, help="Load cookies from a browser.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Assume yes for install prompts.")
def audio_cmd(urls, outdir, audio_format, playlist, thumbnail, cookies, assume_yes):
    step("evo download audio")
    opts = collect_opts(
        outdir,
        "best",
        "auto",
        True,
        audio_format,
        None,
        playlist,
        None,
        thumbnail,
        False,
        cookies,
        False,
        None,
        False,
        4,
        None,
        None,
        False,
    )
    do_get(list(urls), opts, assume_yes)


@download.command(
    "formats",
    context_settings={"help_option_names": ["-h", "--help"]},
    help="List every stream a `URL` offers, so you can pick one with `get -f <id>`.",
)
@click.argument("url")
@click.option("--cookies", type=click.Choice(BROWSERS), default=None, help="Load cookies from a browser.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Assume yes for install prompts.")
def formats_cmd(url, cookies, assume_yes):
    step("evo download formats")
    opts = collect_opts(
        None,
        "best",
        "auto",
        False,
        "mp3",
        None,
        False,
        None,
        False,
        False,
        cookies,
        False,
        None,
        False,
        4,
        None,
        None,
        False,
        list_formats=True,
    )
    do_get([url], opts, assume_yes)


@download.command(
    "install",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "Install or update **yt-dlp** (via pip).\n\n"
        "With `--with-deps` it also installs **ffmpeg** using whichever package manager "
        "it detects (apt, dnf, pacman, brew, winget, choco)."
    ),
)
@click.option("--with-deps", is_flag=True, help="Also install ffmpeg.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Assume yes for package-manager prompts.")
def install_cmd(with_deps, assume_yes):
    step("evo download install")
    install_ytdlp()
    if with_deps:
        if find_ffmpeg():
            info("ffmpeg already present.")
        else:
            info("Installing [accent]ffmpeg[/accent]")
            install_ffmpeg(assume_yes)
    report_environment()
    if ytdlp_version():
        success("Ready to download.")


@download.command(
    "check",
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Verify that yt-dlp, ffmpeg and a JS runtime are available.",
)
def check_cmd():
    step("evo download check")
    report_environment()
    if not ytdlp_version():
        raise click.ClickException("yt-dlp is missing. Run `evo download install`.")
    if not find_ffmpeg():
        warning("ffmpeg missing: merging and audio conversion will not work.")
        info("Fix it with `evo download install --with-deps`.")
        return
    if not find_js_runtime()[0]:
        warning("No JS runtime (deno / node / bun): a few sites may hand back fewer formats.")
    success("Ready to download.")


@download.command(
    "sites",
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Show the common sources this command handles.",
)
@click.option("--all", "show_all", is_flag=True, help="List every extractor yt-dlp supports.")
def sites_cmd(show_all):
    step("evo download sites")
    if show_all:
        exe = ensure_ytdlp(True)
        run_command(exe + ["--list-extractors"], check=False)
        return
    table = Table(show_header=True, header_style="accent", expand=False)
    table.add_column("Source", style="info", no_wrap=True)
    table.add_column("Notes", style="dim")
    for name, note in SITES:
        table.add_row(name, note)
    console.print(table)
    console.print()
    info("yt-dlp supports 1800+ sites. Full list: [accent]evo download sites --all[/accent]")
