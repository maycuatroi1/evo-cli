import datetime
import subprocess
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


@pytest.mark.parametrize("playlist", [False, True])
def test_output_template_caps_the_title(tmp_path, playlist):
    # A Facebook reel titles itself with its whole caption; uncapped, the file
    # name overruns the 255-byte limit and yt-dlp dies with "File name too long".
    template = dl.output_template(tmp_path, playlist, name=None)
    assert f"%(title).{dl.TITLE_BYTE_LIMIT}B" in template
    assert "%(title)s" not in template


def test_output_template_caps_the_playlist_folder(tmp_path):
    template = dl.output_template(tmp_path, playlist=True, name=None)
    assert f"%(playlist_title).{dl.TITLE_BYTE_LIMIT}B" in template


def test_title_cap_leaves_room_for_the_suffixes_ytdlp_appends():
    # yt-dlp downloads to `<name>.f<format_id>.<ext>.part` and the sanitizer
    # swaps `:` `|` `?` for 3-byte fullwidth twins before any of that.
    assert dl.TITLE_BYTE_LIMIT + len(".f1049104804294604v.mp4.part") < 255


def test_build_args_records_what_it_downloaded(opts, tmp_path):
    args = dl.build_ytdlp_args(opts, has_ffmpeg=True, manifest=tmp_path / "files.txt")
    assert args[args.index("--print-to-file") + 1] == "after_move:filepath"
    assert args[args.index("--print-to-file") + 2] == str(tmp_path / "files.txt")


def test_build_args_without_manifest_stays_quiet(opts):
    assert "--print-to-file" not in dl.build_ytdlp_args(opts, has_ffmpeg=True)


def test_downloaded_paths_drops_blanks_and_repeats(tmp_path):
    manifest = tmp_path / "files.txt"
    manifest.write_text("/a.mp4\n\n/b.mp4\n/a.mp4\n", encoding="utf-8")
    assert dl.downloaded_paths(manifest) == ["/a.mp4", "/b.mp4"]


def test_downloaded_paths_survives_a_missing_manifest(tmp_path):
    assert dl.downloaded_paths(tmp_path / "nope.txt") == []


def test_mp4_downloads_get_the_h264_pass(opts):
    assert dl.wants_playable_output(opts, has_ffmpeg=True) is True


@pytest.mark.parametrize(
    "key, value",
    [("keep_codec", True), ("audio_only", True), ("list_formats", True), ("container", "webm")],
)
def test_h264_pass_is_skipped_when_it_does_not_apply(opts, key, value):
    opts[key] = value
    assert dl.wants_playable_output(opts, has_ffmpeg=True) is False


def test_h264_pass_needs_ffmpeg(opts):
    assert dl.wants_playable_output(opts, has_ffmpeg=False) is False


def test_transcode_keeps_a_playable_audio_track(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "ffmpeg")
    args = dl.transcode_args(tmp_path / "in.mp4", tmp_path / "out.mp4", "libx264", {"acodec": "aac"})
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-c:a") + 1] == "copy"
    assert "-crf" in args


def test_transcode_re_encodes_an_unplayable_audio_track(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "ffmpeg")
    args = dl.transcode_args(tmp_path / "in.mp4", tmp_path / "out.mp4", "libx264", {"acodec": "opus"})
    assert args[args.index("-c:a") + 1] == "aac"


def test_hardware_encoder_gets_a_bitrate_instead_of_a_crf(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "ffmpeg")
    media = {"acodec": "aac", "vbitrate": 2_000_000}
    args = dl.transcode_args(tmp_path / "in.mp4", tmp_path / "out.mp4", "h264_videotoolbox", media)
    # H.264 needs more bits than VP9 for the same picture.
    assert args[args.index("-b:v") + 1] == str(int(2_000_000 * dl.H264_BITRATE_HEADROOM))
    assert "-crf" not in args


def test_hardware_encoder_falls_back_to_a_floor_bitrate(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "ffmpeg")
    args = dl.transcode_args(tmp_path / "in.mp4", tmp_path / "out.mp4", "h264_videotoolbox", {"acodec": "aac"})
    assert args[args.index("-b:v") + 1] == str(dl.MIN_H264_BITRATE)


def test_playable_codec_is_left_alone(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    monkeypatch.setattr(dl, "probe_media", lambda path: {"vcodec": "h264", "acodec": "aac", "vbitrate": 1})
    assert dl.make_playable(video) is False
    assert video.read_bytes() == b"data"


def test_vp9_is_converted_in_place(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"vp9 data")
    monkeypatch.setattr(dl, "probe_media", lambda path: {"vcodec": "vp9", "acodec": "aac", "vbitrate": 1})
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(dl, "h264_encoder", lambda: "libx264")

    def fake_ffmpeg(cmd, check=True):
        Path(cmd[-1]).write_bytes(b"h264 data")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(dl, "run_command", fake_ffmpeg)
    assert dl.make_playable(video) is True
    assert video.read_bytes() == b"h264 data"
    assert list(tmp_path.iterdir()) == [video]


def test_a_failed_conversion_keeps_the_download(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"vp9 data")
    monkeypatch.setattr(dl, "probe_media", lambda path: {"vcodec": "vp9", "acodec": "aac", "vbitrate": 1})
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(dl, "h264_encoder", lambda: "libx264")
    monkeypatch.setattr(dl, "run_command", lambda cmd, check=True: subprocess.CompletedProcess(cmd, 1))
    assert dl.make_playable(video) is False
    assert video.read_bytes() == b"vp9 data"
    assert list(tmp_path.iterdir()) == [video]


def test_hardware_encoder_failure_retries_in_software(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"vp9 data")
    monkeypatch.setattr(dl, "probe_media", lambda path: {"vcodec": "vp9", "acodec": "aac", "vbitrate": 1})
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(dl, "h264_encoder", lambda: "h264_videotoolbox")
    attempts = []

    def flaky_ffmpeg(cmd, check=True):
        encoder = cmd[cmd.index("-c:v") + 1]
        attempts.append(encoder)
        if encoder == "h264_videotoolbox":
            return subprocess.CompletedProcess(cmd, 1)
        Path(cmd[-1]).write_bytes(b"h264 data")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(dl, "run_command", flaky_ffmpeg)
    assert dl.make_playable(video) is True
    assert attempts == ["h264_videotoolbox", "libx264"]


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


def version_released_days_ago(days):
    released = datetime.date.today() - datetime.timedelta(days=days)
    return f"{released.year}.{released.month:02d}.{released.day:02d}"


def test_recent_ytdlp_is_not_stale():
    assert dl.ytdlp_stale_age(version_released_days_ago(3)) is None


def test_old_ytdlp_reports_its_age():
    assert dl.ytdlp_stale_age(version_released_days_ago(200)) == 200


def test_nightly_ytdlp_build_still_parses():
    assert dl.ytdlp_stale_age(version_released_days_ago(90) + ".232919") == 90


@pytest.mark.parametrize("version", [None, "", "2026", "2026.13.99", "unknown"])
def test_unparsable_ytdlp_version_is_not_stale(version):
    assert dl.ytdlp_stale_age(version) is None


def test_check_warns_when_ytdlp_is_stale(monkeypatch):
    monkeypatch.setattr(dl, "ytdlp_version", lambda: version_released_days_ago(200))
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(dl, "find_js_runtime", lambda: ("node", "/usr/bin/node"))
    result = CliRunner().invoke(cli, ["download", "check"])
    assert result.exit_code == 0
    assert "200 days old" in result.output.replace("\n", "")


def test_failure_on_stale_ytdlp_points_at_the_update(monkeypatch, tmp_path):
    class Failed:
        returncode = 1

    monkeypatch.setattr(dl, "ensure_ytdlp", lambda assume_yes=False: ["yt-dlp"])
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(dl, "ytdlp_version", lambda: version_released_days_ago(200))
    monkeypatch.setattr(dl, "run_command", lambda *a, **kw: Failed())
    result = CliRunner().invoke(cli, ["download", YOUTUBE, "-o", str(tmp_path)])
    assert result.exit_code != 0
    assert "evo download install" in result.output.replace("\n", "")


def test_failure_on_fresh_ytdlp_keeps_the_formats_hint(monkeypatch, tmp_path):
    class Failed:
        returncode = 1

    monkeypatch.setattr(dl, "ensure_ytdlp", lambda assume_yes=False: ["yt-dlp"])
    monkeypatch.setattr(dl, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(dl, "ytdlp_version", lambda: version_released_days_ago(3))
    monkeypatch.setattr(dl, "run_command", lambda *a, **kw: Failed())
    result = CliRunner().invoke(cli, ["download", YOUTUBE, "-o", str(tmp_path)])
    assert result.exit_code != 0
    assert "evo download formats" in result.output.replace("\n", "")
