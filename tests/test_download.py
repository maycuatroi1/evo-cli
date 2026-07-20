from pathlib import Path

import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import download as dl

YOUTUBE = "https://www.youtube.com/watch?v=8c6r6RqtkkQ"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("the test tried to run a real command")

    monkeypatch.setattr(dl, "run_command", blocked)


@pytest.fixture
def opts(tmp_path):
    return dl.collect_opts(
        outdir=str(tmp_path),
        quality="best",
        container="mp4",
        audio_only=False,
        audio_format="mp3",
        fmt=None,
        playlist=False,
        subs=None,
        thumbnail=False,
        sponsorblock=False,
        cookies=None,
        archive=False,
        section=None,
        ascii_names=False,
        concurrent=4,
        limit_rate=None,
        name=None,
        dry_run=False,
    )


def test_command_is_registered():
    assert "download" in cli.commands
    for name in ("get", "audio", "formats", "install", "check", "sites"):
        assert name in cli.commands["download"].commands


def test_help_runs():
    for args in (["download", "-h"], ["download", "get", "-h"], ["download", "audio", "-h"]):
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0


def test_format_selector_best_prefers_mp4():
    selector = dl.format_selector("best", "mp4", has_ffmpeg=True)
    assert selector.startswith("bv*[ext=mp4]+ba[ext=m4a]")


def test_format_selector_caps_height():
    assert "[height<=1080]" in dl.format_selector("1080p", "mkv", has_ffmpeg=True)


def test_format_selector_without_ffmpeg_uses_single_file():
    assert dl.format_selector("720p", "mp4", has_ffmpeg=False) == "b[height<=720]/b"


def test_format_selector_worst():
    assert dl.format_selector("worst", "mp4", has_ffmpeg=True) == "wv*+wa/w"


def test_is_direct_file():
    assert dl.is_direct_file("https://example.com/tool.zip")
    assert dl.is_direct_file("https://example.com/a/b/paper.pdf?x=1")
    assert not dl.is_direct_file(YOUTUBE)
    assert not dl.is_direct_file("https://example.com/clip.mp4")


def test_direct_filename_unquotes():
    assert dl.direct_filename("https://example.com/my%20file.zip") == "my file.zip"


def test_output_template_playlist(tmp_path):
    template = dl.output_template(tmp_path, playlist=True, name=None)
    assert "%(playlist_index)03d" in template
    assert template.startswith(str(tmp_path))


def test_output_template_respects_name(tmp_path):
    assert dl.output_template(tmp_path, False, "%(id)s.%(ext)s").endswith("%(id)s.%(ext)s")


def test_build_args_default(opts):
    args = dl.build_ytdlp_args(opts, has_ffmpeg=True)
    assert "--no-playlist" in args
    assert "--merge-output-format" in args
    assert args[args.index("--merge-output-format") + 1] == "mp4"
    assert "--embed-metadata" in args


def test_build_args_audio_only(opts):
    opts["audio_only"] = True
    opts["audio_format"] = "flac"
    args = dl.build_ytdlp_args(opts, has_ffmpeg=True)
    assert "-x" in args
    assert args[args.index("--audio-format") + 1] == "flac"
    assert "--merge-output-format" not in args


def test_build_args_raw_format_wins(opts):
    opts["fmt"] = "137+140"
    opts["audio_only"] = True
    args = dl.build_ytdlp_args(opts, has_ffmpeg=True)
    assert args[args.index("-f") + 1] == "137+140"
    assert "-x" not in args


def test_build_args_extras(opts):
    opts.update(
        {
            "subs": "en,vi",
            "thumbnail": True,
            "sponsorblock": True,
            "cookies": "chrome",
            "section": "10:00-15:00",
            "ascii_names": True,
            "archive": True,
            "limit_rate": "2M",
        }
    )
    args = dl.build_ytdlp_args(opts, has_ffmpeg=True)
    assert args[args.index("--sub-langs") + 1] == "en,vi"
    assert "--embed-subs" in args
    assert "--embed-thumbnail" in args
    assert args[args.index("--sponsorblock-remove") + 1] == "default"
    assert args[args.index("--cookies-from-browser") + 1] == "chrome"
    assert args[args.index("--download-sections") + 1] == "*10:00-15:00"
    assert "--restrict-filenames" in args
    assert "--download-archive" in args
    assert args[args.index("-r") + 1] == "2M"


def test_build_args_list_formats_is_minimal(opts):
    opts["list_formats"] = True
    args = dl.build_ytdlp_args(opts, has_ffmpeg=True)
    assert "-F" in args
    assert "-o" not in args


def test_build_args_adds_js_runtime(opts, monkeypatch):
    monkeypatch.setattr(dl, "find_js_runtime", lambda: ("node", "/usr/bin/node"))
    args = dl.build_ytdlp_args(opts, has_ffmpeg=True)
    assert args[args.index("--js-runtimes") + 1] == "node"


def test_build_args_skips_default_deno(opts, monkeypatch):
    monkeypatch.setattr(dl, "find_js_runtime", lambda: ("deno", "/usr/bin/deno"))
    assert "--js-runtimes" not in dl.build_ytdlp_args(opts, has_ffmpeg=True)


def test_default_output_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("EVO_DOWNLOAD_DIR", str(tmp_path))
    assert dl.default_output_dir() == tmp_path


def test_bare_url_routes_to_get(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(dl, "do_get", lambda urls, opts, assume_yes: captured.update(urls=urls, opts=opts))
    result = CliRunner().invoke(cli, ["download", YOUTUBE, "-o", str(tmp_path), "-q", "720p"])
    assert result.exit_code == 0
    assert captured["urls"] == [YOUTUBE]
    assert captured["opts"]["quality"] == "720p"


def test_bare_url_with_leading_option_routes_to_get(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(dl, "do_get", lambda urls, opts, assume_yes: captured.update(opts=opts))
    result = CliRunner().invoke(cli, ["download", "-a", "-o", str(tmp_path), YOUTUBE])
    assert result.exit_code == 0
    assert captured["opts"]["audio_only"] is True


def test_dry_run_builds_command(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "ensure_ytdlp", lambda assume_yes=False: ["yt-dlp"])
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    result = CliRunner().invoke(cli, ["download", YOUTUBE, "-o", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0
    assert "yt-dlp" in result.output
    assert YOUTUBE in result.output


def test_dry_run_echo_keeps_brackets(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "ensure_ytdlp", lambda assume_yes=False: ["yt-dlp"])
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    result = CliRunner().invoke(cli, ["download", YOUTUBE, "-q", "720p", "-o", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0
    assert "[height<=720]" in result.output.replace("\n", "")


def test_audio_only_without_ffmpeg_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "ensure_ytdlp", lambda assume_yes=False: ["yt-dlp"])
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: None)
    result = CliRunner().invoke(cli, ["download", "-a", YOUTUBE, "-o", str(tmp_path)])
    assert result.exit_code != 0
    assert "ffmpeg is required" in result.output


def test_direct_file_bypasses_ytdlp(monkeypatch, tmp_path):
    calls = {}

    def fake_download(url, destination, description="Downloading"):
        Path(destination).write_text("payload", encoding="utf-8")
        calls["url"] = url

    monkeypatch.setattr(dl, "download_file", fake_download)
    monkeypatch.setattr(dl, "ensure_ytdlp", lambda assume_yes=False: pytest.fail("should not use yt-dlp"))
    result = CliRunner().invoke(cli, ["download", "https://example.com/tool.zip", "-o", str(tmp_path)])
    assert result.exit_code == 0
    assert calls["url"] == "https://example.com/tool.zip"
    assert (tmp_path / "tool.zip").read_text(encoding="utf-8") == "payload"


def test_sites_lists_common_sources():
    result = CliRunner().invoke(cli, ["download", "sites"])
    assert result.exit_code == 0
    assert "YouTube" in result.output


def test_check_fails_without_ytdlp(monkeypatch):
    monkeypatch.setattr(dl, "ytdlp_version", lambda: None)
    result = CliRunner().invoke(cli, ["download", "check"])
    assert result.exit_code != 0
