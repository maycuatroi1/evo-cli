import base64
import io
import json
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from evo_cli import console, imaging
from evo_cli.cli import cli
from evo_cli.commands import image as image_cmd
from evo_cli.imaging import align, core, gemini, ncnn
from evo_cli.imaging.errors import ImagingError

PINNED_WINDOWS_ASSET = (
    "https://github.com/upscayl/upscayl-ncnn/releases/download/20251207-174704/upscayl-bin-20251207-174704-windows.zip"
)

GRID = 128


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("the test tried to reach the network")

    def no_binary(*args, **kwargs):
        pytest.fail("the test tried to spawn upscayl-bin")

    monkeypatch.setenv("EVO_UPSCAYL_DIR", str(tmp_path / "upscayl"))
    monkeypatch.setenv("EVO_IMAGE_CACHE", str(tmp_path / "image-cache"))
    monkeypatch.delenv("EVO_IMAGE_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ncnn.shutil, "which", lambda name: None)
    monkeypatch.setattr(console, "download_file", no_network)
    monkeypatch.setattr(ncnn, "_run", no_binary)
    monkeypatch.setattr(subprocess, "run", no_binary)
    monkeypatch.setattr(subprocess, "Popen", no_binary)
    monkeypatch.setattr(gemini.urllib.request, "urlopen", no_network)
    monkeypatch.setattr(gemini.time, "sleep", lambda seconds: None)
    return tmp_path / "upscayl"


@pytest.fixture
def installed(sandbox):
    binary = ncnn.binary_dir() / f"upscayl-bin-{ncnn.RELEASE}-windows" / ncnn.BINARY_NAME
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("binary", encoding="utf-8")
    models = ncnn.models_dir()
    models.mkdir(parents=True, exist_ok=True)
    for ext in ncnn.MODEL_EXTS:
        (models / f"{ncnn.DEFAULT_MODEL}.{ext}").write_text("weights", encoding="utf-8")
    return binary


def _write_release_zip(destination, member=None):
    member = member or f"upscayl-bin-{ncnn.RELEASE}-windows/{ncnn.BINARY_NAME}"
    with zipfile.ZipFile(destination, "w") as bundle:
        bundle.writestr(member, "binary")


def _zip_downloader(calls, member=None):
    def download(url, destination, description=""):
        calls.append(url)
        _write_release_zip(destination, member)

    return download


def _file_downloader(calls, body="weights"):
    def download(url, destination, description=""):
        calls.append(url)
        Path(destination).write_text(body, encoding="utf-8")

    return download


def _runner(calls, returncode=0, produce=True, stderr="upscayl-bin: something went wrong"):
    def run(cmd):
        calls.append(cmd)
        if produce:
            Path(cmd[cmd.index("-o") + 1]).write_text("png", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, "", stderr)

    return run


def test_sandbox_fails_a_test_that_reaches_the_network(tmp_path):
    with pytest.raises(pytest.fail.Exception):
        console.download_file(PINNED_WINDOWS_ASSET, str(tmp_path / "asset.zip"))
    with pytest.raises(pytest.fail.Exception):
        gemini.urllib.request.urlopen(gemini.API_URL.format(model=gemini.DEFAULT_MODEL))


def test_sandbox_fails_a_test_that_spawns_the_binary(installed, tmp_path):
    src = tmp_path / "in.png"
    src.write_text("png", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception):
        ncnn.upscale(src, tmp_path / "out.png")
    with pytest.raises(pytest.fail.Exception):
        ncnn._run([str(installed)])
    with pytest.raises(pytest.fail.Exception):
        subprocess.run([sys.executable, "-c", "pass"], check=False)


def test_ncnn_asset_url_pins_the_release():
    assert ncnn.asset_url("windows") == PINNED_WINDOWS_ASSET
    assert ncnn.RELEASE in ncnn.asset_url("linux")
    assert "latest" not in ncnn.asset_url("macos")


@pytest.mark.parametrize("system,slug", [("Windows", "windows"), ("Darwin", "macos"), ("Linux", "linux")])
def test_ncnn_asset_url_maps_the_platform_name(monkeypatch, system, slug):
    monkeypatch.setattr(ncnn.platform, "system", lambda: system)
    assert ncnn.asset_url().endswith(f"-{slug}.zip")


def test_ncnn_asset_url_rejects_an_unknown_platform(monkeypatch):
    monkeypatch.setattr(ncnn.platform, "system", lambda: "Haiku")
    with pytest.raises(ImagingError) as excinfo:
        ncnn.asset_url()
    assert "haiku" in str(excinfo.value).lower()


def test_ncnn_model_urls_pair_the_bin_and_the_param():
    urls = ncnn.model_urls("ultrasharp-4x")
    assert urls == (
        "https://raw.githubusercontent.com/upscayl/upscayl/main/resources/models/ultrasharp-4x.bin",
        "https://raw.githubusercontent.com/upscayl/upscayl/main/resources/models/ultrasharp-4x.param",
    )


def test_ncnn_find_binary_prefers_path(monkeypatch, tmp_path):
    on_path = tmp_path / "bin" / ncnn.BINARY_NAME
    on_path.parent.mkdir(parents=True)
    on_path.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(ncnn.shutil, "which", lambda name: str(on_path) if name == "upscayl-bin" else None)
    assert ncnn.find_binary() == on_path


def test_ncnn_find_binary_falls_back_to_the_cache_dir(installed):
    assert ncnn.find_binary() == installed


def test_ncnn_find_binary_is_none_when_nothing_is_installed():
    assert ncnn.find_binary() is None


def test_ncnn_ensure_binary_downloads_and_unpacks_the_pinned_zip():
    calls = []
    binary = ncnn.ensure_binary(_zip_downloader(calls))
    assert calls == [ncnn.asset_url()]
    assert binary.is_file()
    assert binary.name == ncnn.BINARY_NAME
    assert ncnn.binary_dir() in binary.parents


def test_ncnn_ensure_binary_reuses_what_is_already_unpacked(installed):
    def refuse(*args, **kwargs):
        pytest.fail("ensure_binary downloaded again instead of reusing the cache")

    assert ncnn.ensure_binary(refuse) == installed


def test_ncnn_ensure_binary_reports_a_zip_without_the_binary():
    calls = []
    with pytest.raises(ImagingError) as excinfo:
        ncnn.ensure_binary(_zip_downloader(calls, member="upscayl-bin-windows/README.md"))
    assert ncnn.BINARY_NAME in str(excinfo.value)


def test_ncnn_ensure_model_downloads_the_pair_once():
    calls = []
    models = ncnn.ensure_model("remacri-4x", _file_downloader(calls))
    assert calls == list(ncnn.model_urls("remacri-4x"))
    assert (models / "remacri-4x.bin").is_file()
    assert (models / "remacri-4x.param").is_file()

    ncnn.ensure_model("remacri-4x", _file_downloader(calls))
    assert len(calls) == 2


def test_ncnn_ensure_model_rejects_an_unknown_name():
    with pytest.raises(ImagingError) as excinfo:
        ncnn.ensure_model("not-a-model", _file_downloader([]))
    assert ncnn.DEFAULT_MODEL in str(excinfo.value)


def test_ncnn_command_passes_resize_without_scale(tmp_path):
    cmd = ncnn.build_command("upscayl-bin", tmp_path / "a.png", tmp_path / "b.png", "ultrasharp-4x", (1200, 800), "m")
    assert cmd[cmd.index("-r") + 1] == "1200x800"
    assert "-s" not in cmd
    assert cmd[cmd.index("-n") + 1] == "ultrasharp-4x"
    assert cmd[cmd.index("-f") + 1] == "png"


def test_ncnn_command_without_a_size_has_no_resize_flag(tmp_path):
    cmd = ncnn.build_command("upscayl-bin", tmp_path / "a.png", tmp_path / "b.png", "ultrasharp-4x", None, "m")
    assert "-r" not in cmd
    assert "-s" not in cmd


def test_ncnn_upscale_runs_the_binary_and_returns_the_output(installed, tmp_path):
    src = tmp_path / "in.png"
    src.write_text("png", encoding="utf-8")
    dst = tmp_path / "out" / "in.png"
    calls = []
    result = ncnn.upscale(src, dst, size=(64, 32), runner=_runner(calls))
    assert result == dst
    assert dst.is_file()
    assert calls[0][0] == str(installed)
    assert calls[0][calls[0].index("-r") + 1] == "64x32"
    assert calls[0][calls[0].index("-m") + 1] == str(ncnn.models_dir())


def test_ncnn_upscale_raises_when_the_binary_fails(installed, tmp_path):
    src = tmp_path / "in.png"
    src.write_text("png", encoding="utf-8")
    with pytest.raises(ImagingError) as excinfo:
        ncnn.upscale(src, tmp_path / "out.png", runner=_runner([], returncode=1, produce=False))
    assert "something went wrong" in str(excinfo.value)
    assert "exit 1" in str(excinfo.value)


def test_ncnn_upscale_names_the_crash_instead_of_the_last_progress_line(installed, tmp_path):
    src = tmp_path / "in.png"
    src.write_text("png", encoding="utf-8")
    crash = _runner([], returncode=3221225477, produce=False, stderr="0.00%\n87.40%")
    with pytest.raises(ImagingError) as excinfo:
        ncnn.upscale(src, tmp_path / "out.png", runner=crash)
    message = str(excinfo.value)
    assert "0xC0000005" in message
    assert message.index("3221225477") < message.index("87.40%")


def test_ncnn_upscale_names_a_signal_on_posix(installed, tmp_path):
    src = tmp_path / "in.png"
    src.write_text("png", encoding="utf-8")
    with pytest.raises(ImagingError) as excinfo:
        ncnn.upscale(src, tmp_path / "out.png", runner=_runner([], returncode=-11, produce=False))
    assert "killed by signal 11" in str(excinfo.value)


def test_ncnn_buffer_limit_matches_the_measured_crash_thresholds():
    assert ncnn.buffer_bytes((6400, 6400), 4) == 2621440000
    assert ncnn.buffer_bytes((6400, 6400), 3) == 1966080000
    assert not ncnn.fits_buffer((6400, 6400), 4)
    assert ncnn.fits_buffer((6400, 6400), 3)
    assert ncnn.fits_buffer((5792, 5792), 4) and not ncnn.fits_buffer((5793, 5793), 4)
    assert ncnn.fits_buffer((6688, 6688), 3) and not ncnn.fits_buffer((6689, 6689), 3)
    assert ncnn.fits_buffer((3750, 2500), 4)


def test_ncnn_safe_size_only_shrinks_what_cannot_fit():
    assert ncnn.safe_size((6400, 6400), 3) == (6400, 6400)
    assert ncnn.safe_size((3750, 2500), 4) == (3750, 2500)
    shrunk = ncnn.safe_size((6400, 6400), 4)
    assert shrunk == (5792, 5792)
    assert ncnn.fits_buffer(shrunk, 4)


def test_ncnn_safe_size_keeps_the_aspect_ratio_of_a_huge_frame():
    shrunk = ncnn.safe_size((40000, 20000), 3)
    assert ncnn.fits_buffer(shrunk, 3)
    assert shrunk[0] == pytest.approx(shrunk[1] * 2, rel=0.01)


def test_ncnn_safe_size_terminates_on_an_absurd_frame():
    assert ncnn.safe_size((10**6, 10**6), 4) == ncnn.safe_size((10**6, 10**6), 4)
    assert ncnn.fits_buffer(ncnn.safe_size((10**6, 10**6), 4), 4)


def test_ncnn_module_never_imports_pillow_or_numpy():
    source = Path(ncnn.__file__).read_text(encoding="utf-8")
    assert "PIL" not in source
    assert "numpy" not in source


def test_ncnn_siblings_hint_the_image_extra_when_pillow_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setitem(sys.modules, "numpy", None)
    for loader in (imaging.load_pillow, imaging.load_numpy):
        with pytest.raises(ImagingError) as excinfo:
            loader()
        assert "pip install evo_cli[image]" in str(excinfo.value)


def _mosaic(seed=7, size=GRID):
    numpy = imaging.load_numpy()
    Image = imaging.load_pillow()
    tile = numpy.random.default_rng(seed).integers(30, 220, (16, 16), dtype=numpy.uint8)
    return Image.fromarray(tile).resize((size, size), Image.NEAREST).convert("L")


def _blur(grey, radius=2):
    from PIL import ImageFilter

    return grey.filter(ImageFilter.GaussianBlur(radius))


def _opaque(grey, alpha=None):
    numpy = imaging.load_numpy()
    Image = imaging.load_pillow()
    plane = numpy.asarray(grey.convert("L"))
    band = numpy.full(plane.shape, 255, numpy.uint8) if alpha is None else alpha
    return Image.fromarray(numpy.dstack([plane, plane, plane, band]), "RGBA")


def _badge(body=100, rim=100, backdrop=0, rim_alpha=128, size=GRID):
    numpy = imaging.load_numpy()
    Image = imaging.load_pillow()
    data = numpy.zeros((size, size, 4), numpy.uint8)
    data[:, :, :3] = backdrop
    outer = slice(size // 4 - 3, size - size // 4 + 3)
    inner = slice(size // 4, size - size // 4)
    data[outer, outer, :3] = rim
    data[outer, outer, 3] = rim_alpha
    data[inner, inner, :3] = body
    data[inner, inner, 3] = 255
    return Image.fromarray(data, "RGBA")


def _flattened(image, backdrop=255):
    Image = imaging.load_pillow()
    ground = Image.new("RGBA", image.size, (backdrop, backdrop, backdrop, 255))
    return Image.alpha_composite(ground, image.convert("RGBA")).convert("RGB")


def _roll(image, dy, dx):
    numpy = imaging.load_numpy()
    Image = imaging.load_pillow()
    return Image.fromarray(numpy.roll(numpy.roll(numpy.asarray(image), dy, 0), dx, 1), image.mode)


def test_align_find_shift_is_zero_on_the_same_frame():
    reference = _opaque(_mosaic())
    dy, dx, peak = align.find_shift(reference, reference, grid=GRID)
    assert (dy, dx) == (0, 0)
    assert peak > 0.5


def test_align_find_shift_recovers_a_known_offset():
    reference = _opaque(_mosaic())
    moved = _opaque(_roll(_mosaic(), 7, -3))
    assert align.find_shift(reference, moved, grid=GRID)[:2] == (-7, 3)


def test_align_find_shift_survives_a_blurred_candidate():
    reference = _opaque(_mosaic())
    moved = _opaque(_roll(_blur(_mosaic()), 7, -3))
    assert align.find_shift(reference, moved, grid=GRID)[:2] == (-7, 3)


def test_align_find_shift_scales_the_offset_to_the_source_pixels():
    reference = _opaque(_mosaic(size=GRID))
    moved = _opaque(_roll(_mosaic(size=GRID), 8, 0))
    assert align.find_shift(reference, moved, grid=GRID // 2)[0] == -8


def test_align_fidelity_is_capped_on_an_identical_frame():
    reference = _opaque(_mosaic())
    assert align.fidelity(reference, reference, grid=GRID) == align.MAX_PSNR


def test_align_fidelity_normalises_exposure():
    numpy = imaging.load_numpy()
    Image = imaging.load_pillow()
    base = _mosaic()
    darker = Image.fromarray((numpy.asarray(base, numpy.float32) * 0.7 + 40).astype(numpy.uint8))
    assert align.fidelity(_opaque(base), _opaque(darker), grid=GRID) > 45


def test_align_fidelity_drops_on_a_blurred_candidate():
    reference = _opaque(_mosaic())
    blurred = _opaque(_blur(_mosaic()))
    score = align.fidelity(reference, blurred, grid=GRID)
    assert 10 < score < 30


def test_align_fidelity_only_looks_inside_the_opaque_mask():
    numpy = imaging.load_numpy()
    Image = imaging.load_pillow()
    base = numpy.asarray(_mosaic())
    band = numpy.zeros(base.shape, numpy.uint8)
    band[GRID // 4 : GRID - GRID // 4, GRID // 4 : GRID - GRID // 4] = 255
    reference = _opaque(_mosaic(), alpha=band)
    noise = numpy.random.default_rng(1).integers(0, 255, base.shape, dtype=numpy.uint8)
    outside = numpy.where(band == 255, base, noise).astype(numpy.uint8)
    assert align.fidelity(reference, _opaque(Image.fromarray(outside)), grid=GRID) == align.MAX_PSNR


def test_align_rim_lift_is_zero_when_the_rim_is_untouched():
    assert align.rim_lift(_badge(), _badge(), grid=GRID) == 0.0


def test_align_rim_lift_orders_the_merge_the_drift_and_the_control():
    source = _badge()
    merged = align.rim_lift(source, _badge(rim=115), grid=GRID)
    drifted = align.rim_lift(source, _badge(rim=127), grid=GRID)
    control = align.rim_lift(source, _badge(rim=92), grid=GRID)
    assert control < 0 < merged < drifted
    assert merged < 20 < drifted


def test_align_rim_lift_reads_the_rim_of_an_rgb_candidate():
    source = _badge()
    assert align.rim_lift(source, _badge(rim=127).convert("RGB"), grid=GRID) > 20


def test_align_rim_lift_clears_a_faithful_candidate_rendered_on_a_white_backdrop():
    source = _badge()
    assert align.rim_lift(source, _flattened(source), grid=GRID) == pytest.approx(0.0, abs=1.0)


def test_align_rim_lift_rejects_a_white_backdrop_that_swallowed_the_rim():
    source = _badge()
    assert align.rim_lift(source, _badge(rim=255, backdrop=255).convert("RGB"), grid=GRID) > 100


def test_align_rim_lift_reads_the_same_number_with_or_without_engine_alpha():
    source = _badge()
    engine = _badge(rim=115)
    carried = align.rim_lift(source, engine, grid=GRID)
    baked = align.rim_lift(source, _flattened(engine), grid=GRID)
    assert carried == pytest.approx(15.0, abs=0.5)
    assert baked == pytest.approx(carried, abs=1.0)


def test_align_rim_lift_follows_the_shift_back_onto_the_candidate_edge():
    source = _badge()
    drifted = _flattened(_roll(_badge(), 7, -3))
    shift = align.find_shift(source, drifted, grid=GRID)[:2]
    assert shift == (-7, 3)
    aligned = align.rim_lift(source, drifted, shift, grid=GRID)
    ignored = align.rim_lift(source, drifted, grid=GRID)
    assert aligned == pytest.approx(0.0, abs=1.0)
    assert ignored > aligned + 15


def test_align_rim_lift_is_zero_without_a_source_rim():
    assert align.rim_lift(_badge().convert("RGB"), _badge(rim=127), grid=GRID) == 0.0
    assert align.rim_lift(_opaque(_mosaic()), _badge(rim=127), grid=GRID) == 0.0


def test_align_restore_alpha_reattaches_the_shifted_source_alpha():
    numpy = imaging.load_numpy()
    source = _badge()
    drifted = _roll(_badge(rim=177, backdrop=255), 7, -3).convert("RGB")
    shift = align.find_shift(source, drifted, grid=GRID)[:2]
    assert shift == (-7, 3)
    merged = align.restore_alpha(source, drifted, shift)
    assert merged.mode == "RGBA"
    assert merged.size == source.size
    expected = numpy.roll(numpy.roll(numpy.asarray(source.split()[-1]), 7, 0), -3, 1)
    assert numpy.array_equal(numpy.asarray(merged.split()[-1]), expected)


def test_align_restore_alpha_puts_the_rim_back_over_the_drifted_edge():
    numpy = imaging.load_numpy()
    source = _badge()
    drifted = _roll(_badge(rim=177, backdrop=255), 7, -3).convert("RGB")
    shift = align.find_shift(source, drifted, grid=GRID)[:2]

    aligned = numpy.asarray(align.restore_alpha(source, drifted, shift))
    under_rim = aligned[aligned[:, :, 3] == 128][:, 0]
    assert set(numpy.unique(under_rim)) == {177}

    naive = numpy.asarray(align.restore_alpha(source, drifted))
    under_rim = naive[naive[:, :, 3] == 128][:, 0]
    assert 255 in set(numpy.unique(under_rim))


def test_align_carry_alpha_lifts_the_band_onto_a_bigger_candidate():
    Image = imaging.load_pillow()
    numpy = imaging.load_numpy()
    source = _badge()
    bigger = _badge(rim=177, backdrop=255).convert("RGB").resize((GRID * 2, GRID * 2), Image.LANCZOS)
    merged = align.carry_alpha(source.split()[-1], bigger)
    assert merged.mode == "RGBA"
    assert merged.size == (GRID * 2, GRID * 2)
    assert set(numpy.unique(numpy.asarray(merged.split()[-1]))) >= {0, 255}


def test_align_carry_alpha_keeps_a_matching_band_untouched():
    numpy = imaging.load_numpy()
    source = _badge()
    merged = align.carry_alpha(source.split()[-1], _badge(rim=177).convert("RGB"))
    assert numpy.array_equal(numpy.asarray(merged.split()[-1]), numpy.asarray(source.split()[-1]))


def test_align_restore_alpha_resizes_the_candidate_to_the_source():
    Image = imaging.load_pillow()
    source = _badge()
    bigger = _badge(rim=177, backdrop=255).convert("RGB").resize((GRID * 2, GRID * 2), Image.LANCZOS)
    assert align.restore_alpha(source, bigger).size == source.size


def _png(size=(8, 8), mode="RGBA"):
    Image = imaging.load_pillow()
    buffer = io.BytesIO()
    Image.new(mode, size, 128).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_response(png_bytes, key="inlineData"):
    part = {key: {"mimeType": "image/png", "data": base64.b64encode(png_bytes).decode()}}
    return {"candidates": [{"content": {"parts": [{"text": "here you go"}, part]}}]}


def _failing(status, calls):
    def request(url, headers, payload, timeout):
        calls.append(url)
        failure = ImagingError(f"POST {url} -> HTTP {status}")
        failure.status = status
        raise failure

    return request


def test_gemini_endpoint_matches_the_documented_image_route():
    assert gemini.API_URL.format(model=gemini.DEFAULT_MODEL) == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image:generateContent"
    )
    assert gemini.SIZES == ("1K", "2K", "4K")


def test_gemini_payload_asks_for_an_image_at_the_requested_size():
    payload = gemini.build_payload(b"png-bytes", size="2K")
    parts = payload["contents"][0]["parts"]
    config = payload["generationConfig"]
    assert config["responseModalities"] == ["IMAGE"]
    assert config["imageConfig"]["imageSize"] == "2K"
    assert parts[0]["text"] == gemini.PROMPT
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == b"png-bytes"


def test_gemini_payload_rejects_a_size_off_the_ladder():
    with pytest.raises(ImagingError) as excinfo:
        gemini.build_payload(b"png-bytes", size="8K")
    assert "1K, 2K, 4K" in str(excinfo.value)


def test_gemini_upload_is_downscaled_to_the_long_edge():
    Image = imaging.load_pillow()
    sent = Image.open(io.BytesIO(gemini.prepare_upload(Image.new("L", (3000, 1500)))))
    assert sent.size == (gemini.UPLOAD_EDGE, gemini.UPLOAD_EDGE // 2)


def test_gemini_upload_leaves_a_small_image_and_its_alpha_alone():
    Image = imaging.load_pillow()
    source = Image.new("RGBA", (64, 32), (10, 20, 30, 40))
    sent = Image.open(io.BytesIO(gemini.prepare_upload(source)))
    assert sent.size == (64, 32)
    assert "A" in sent.getbands()


def test_gemini_post_sends_the_key_in_a_header_and_keeps_it_out_of_the_url(monkeypatch):
    seen = {}

    def request(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return _image_response(b"png")

    monkeypatch.setattr(gemini, "_request_json", request)
    gemini._post({"contents": []}, gemini.DEFAULT_MODEL, gemini.REQUEST_TIMEOUT)
    assert seen["headers"]["x-goog-api-key"] == "test-key"
    assert "test-key" not in seen["url"]
    assert seen["timeout"] == gemini.REQUEST_TIMEOUT


def test_gemini_retries_a_throttled_status_then_succeeds(monkeypatch):
    calls = []

    def request(url, headers, payload, timeout):
        calls.append(url)
        if len(calls) < 3:
            failure = ImagingError("throttled")
            failure.status = 429
            raise failure
        return _image_response(b"png")

    monkeypatch.setattr(gemini, "_request_json", request)
    assert gemini._post({"contents": []}, gemini.DEFAULT_MODEL, 10)
    assert len(calls) == 3


@pytest.mark.parametrize("status", [429, 500, 503])
def test_gemini_gives_up_after_the_retry_budget(monkeypatch, status):
    calls = []
    monkeypatch.setattr(gemini, "_request_json", _failing(status, calls))
    with pytest.raises(ImagingError) as excinfo:
        gemini._post({"contents": []}, gemini.DEFAULT_MODEL, 10)
    assert len(calls) == gemini.RETRIES
    assert str(status) in str(excinfo.value)


@pytest.mark.parametrize("status", [400, 401, 404])
def test_gemini_does_not_retry_a_client_error(monkeypatch, status):
    calls = []
    monkeypatch.setattr(gemini, "_request_json", _failing(status, calls))
    with pytest.raises(ImagingError):
        gemini._post({"contents": []}, gemini.DEFAULT_MODEL, 10)
    assert len(calls) == 1


@pytest.mark.parametrize("key", ["inlineData", "inline_data"])
def test_gemini_extracts_the_image_part_under_either_spelling(key):
    assert gemini._extract_image(_image_response(b"png-bytes", key=key)) == b"png-bytes"


def test_gemini_reports_a_blocked_prompt():
    with pytest.raises(ImagingError) as excinfo:
        gemini._extract_image({"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})
    assert "SAFETY" in str(excinfo.value)


def test_gemini_reports_a_response_without_an_image():
    body = {"candidates": [{"content": {"parts": [{"text": "sorry"}]}, "finishReason": "RECITATION"}]}
    with pytest.raises(ImagingError) as excinfo:
        gemini._extract_image(body)
    assert "RECITATION" in str(excinfo.value)


def test_gemini_upscale_returns_the_decoded_image(monkeypatch):
    Image = imaging.load_pillow()
    rendered = _png((16, 16), "RGB")
    seen = {}

    def poster(payload, model, timeout):
        seen.update(payload=payload, model=model)
        return _image_response(rendered)

    data = gemini.upscale(Image.new("RGBA", (64, 64)), poster=poster)
    assert data == rendered
    assert Image.open(io.BytesIO(data)).size == (16, 16)
    assert seen["model"] == gemini.DEFAULT_MODEL
    assert seen["payload"]["generationConfig"]["imageConfig"]["imageSize"] == gemini.DEFAULT_SIZE


def test_gemini_upscale_surfaces_a_transport_failure():
    def poster(payload, model, timeout):
        raise ImagingError("POST -> HTTP 503")

    with pytest.raises(ImagingError) as excinfo:
        gemini.upscale(imaging.load_pillow().new("RGB", (32, 32)), poster=poster)
    assert "503" in str(excinfo.value)


SOURCE_EDGE = 200


def _encode(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _source(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def _poster(calls, image=None, failure=None):
    def post(payload, model, timeout):
        Image = imaging.load_pillow()
        calls.append(model)
        if failure is not None:
            raise failure
        if image is not None:
            return _image_response(_encode(image))
        sent = base64.b64decode(payload["contents"][0]["parts"][1]["inline_data"]["data"])
        with Image.open(io.BytesIO(sent)) as handle:
            return _image_response(_encode(_flattened(handle)))

    return post


def _series(values):
    remaining = list(values)

    def measured(*args, **kwargs):
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return measured


def _image_runner(calls):
    def run(cmd):
        Image = imaging.load_pillow()
        calls.append(cmd)
        with Image.open(cmd[cmd.index("-i") + 1]) as handle:
            frame = handle.convert("RGBA")
        if "-r" in cmd:
            width, height = cmd[cmd.index("-r") + 1].split("x")
            frame = frame.resize((int(width), int(height)), Image.LANCZOS)
        frame.save(cmd[cmd.index("-o") + 1])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


def _fed_runner(seen):
    inner = _image_runner([])

    def run(cmd):
        Image = imaging.load_pillow()
        with Image.open(cmd[cmd.index("-i") + 1]) as handle:
            seen.append((handle.mode, handle.size))
        return inner(cmd)

    return run


def _picky_runner(bad):
    inner = _image_runner([])

    def run(cmd):
        if bad in Path(cmd[cmd.index("-i") + 1]).name:
            return subprocess.CompletedProcess(cmd, 3221225477, "", "0.00%\n87.40%")
        return inner(cmd)

    return run


@pytest.fixture
def delivery(tmp_path):
    _source(tmp_path / "in" / "theme" / "node_primary_64x64.png", _badge(size=SOURCE_EDGE))
    return tmp_path / "in"


def _process(root, out_dir, calls=None, runs=None, image=None, failure=None, settings=None, runner=None, **kwargs):
    return core.process_many(
        core.find_sources(root),
        out_dir,
        root=root,
        settings=settings or core.preset(provider="gemini"),
        poster=_poster(calls if calls is not None else [], image=image, failure=failure),
        runner=runner or _image_runner(runs if runs is not None else []),
        **kwargs,
    )


def _opened(path):
    Image = imaging.load_pillow()
    with Image.open(path) as handle:
        return handle.mode, handle.size


def test_preset_asset_carries_the_measured_defaults():
    settings = core.preset("asset")
    assert settings["outputs"] == ["master", "ui"]
    assert settings["rim_lift_max"] == 20.0
    assert settings["declared_ratio"] == 3.125
    assert settings["master_scale"] == 1.0
    assert settings["provider"] == "auto"


def test_preset_rim_lift_max_sits_above_both_engines_and_below_a_backdrop_drift():
    evidence = core.RIM_LIFT_EVIDENCE
    passing = max(evidence["gemini_white_backdrop"], evidence["ncnn_control"])
    assert passing < core.RIM_LIFT_MAX < evidence["gemini_backdrop_drift"]
    assert core.preset("asset")["rim_lift_max"] == core.RIM_LIFT_MAX
    assert "rim_lift_max" in core.PRESETS["asset"]


def test_preset_rim_lift_min_mirrors_the_max_and_brackets_the_crushed_rim():
    evidence = core.RIM_LIFT_EVIDENCE
    assert evidence["gemini_crushed_rim"] < core.RIM_LIFT_MIN < evidence["gemini_clean_node"]
    assert core.RIM_LIFT_MIN == -core.RIM_LIFT_MAX
    assert core.RIM_LIFT_MIN - evidence["gemini_crushed_rim"] == pytest.approx(100.0)
    assert evidence["gemini_clean_node"] - core.RIM_LIFT_MIN == pytest.approx(21.8)
    assert core.preset("asset")["rim_lift_min"] == core.RIM_LIFT_MIN
    assert "rim_lift_min" in core.PRESETS["asset"]


def test_preset_fidelity_floor_sits_midway_between_the_repaints_and_the_faithful_renders():
    evidence = core.FIDELITY_EVIDENCE
    repainted = max(evidence["gemini_repainted_panel"], evidence["gemini_crushed_rim"])
    faithful = min(
        evidence["gemini_ornate_frame"],
        evidence["gemini_clean_node"],
        evidence["ncnn_connector"],
        evidence["ncnn_card_frame"],
    )
    assert repainted < core.FIDELITY_DB_MIN < faithful
    assert core.FIDELITY_DB_MIN - repainted == pytest.approx(9.72)
    assert faithful - core.FIDELITY_DB_MIN == pytest.approx(9.75)
    assert core.preset("asset")["fidelity_db_min"] == core.FIDELITY_DB_MIN
    assert "fidelity_db_min" in core.PRESETS["asset"]


def test_preset_shift_peak_floor_sits_between_the_spurious_offsets_and_the_trusted_registrations():
    evidence = core.SHIFT_PEAK_EVIDENCE
    assert evidence["gemini_connector_spurious"] < evidence["highest_measured_shift"]
    assert evidence["highest_measured_shift"] < core.SHIFT_PEAK_MIN < evidence["lowest_shipped_zero_shift"]
    assert core.SHIFT_PEAK_MIN - evidence["highest_measured_shift"] == pytest.approx(0.17)
    assert evidence["lowest_shipped_zero_shift"] - core.SHIFT_PEAK_MIN == pytest.approx(0.175)
    assert evidence["lowest_shipped_zero_shift"] < evidence["run_median"] < evidence["run_max"]


def test_preset_keeps_the_shift_peak_floor_out_of_the_hashed_settings():
    assert "shift_peak_min" not in core.preset("asset")
    assert set(core.PRESETS["asset"]) == {
        "provider",
        "model",
        "gemini_model",
        "image_size",
        "master_scale",
        "declared_ratio",
        "rim_lift_max",
        "rim_lift_min",
        "fidelity_db_min",
        "outputs",
    }


def test_preset_rejects_an_unknown_name():
    with pytest.raises(ImagingError) as excinfo:
        core.preset("hero")
    assert "asset" in str(excinfo.value)


def test_preset_overrides_replace_only_what_is_given():
    settings = core.preset("asset", provider="ncnn", model=None, rim_lift_max=5.0)
    assert settings["provider"] == "ncnn"
    assert settings["rim_lift_max"] == 5.0
    assert settings["model"] == core.PRESETS["asset"]["model"]


def test_core_resolve_provider_prefers_the_hosted_engine(monkeypatch):
    monkeypatch.setattr(core, "has_gemini_credentials", lambda: True)
    assert core.resolve_provider("auto") == "gemini"
    monkeypatch.setattr(core, "has_gemini_credentials", lambda: False)
    assert core.resolve_provider(None) == "ncnn"
    assert core.resolve_provider("gemini") == "gemini"


def test_core_resolve_provider_honours_the_environment_override(monkeypatch):
    monkeypatch.setattr(core, "has_gemini_credentials", lambda: True)
    monkeypatch.setenv("EVO_IMAGE_PROVIDER", "ncnn")
    assert core.resolve_provider("auto") == "ncnn"
    monkeypatch.setenv("EVO_IMAGE_PROVIDER", "topaz")
    with pytest.raises(ImagingError):
        core.resolve_provider("auto")


def test_core_resolve_provider_rejects_an_unknown_name():
    with pytest.raises(ImagingError) as excinfo:
        core.resolve_provider("topaz")
    assert "topaz" in str(excinfo.value)


@pytest.mark.parametrize(
    "name,size,expected",
    [
        ("background_panel_2048x2048.png", (6400, 6400), (2048, 2048)),
        ("card_frame_left_1200x800.png", (3750, 2500), (1200, 800)),
        ("node_primary_256x256.png", (800, 800), (256, 256)),
    ],
)
def test_core_declared_size_comes_from_the_filename(name, size, expected):
    assert core.declared_size(name, size) == expected


def test_core_declared_size_falls_back_to_the_delivery_ratio():
    assert core.declared_size("logo.png", (800, 800)) == (256, 256)
    assert core.declared_size("logo_v2.png", (3750, 2500)) == (1200, 800)


def test_core_find_sources_walks_the_tree_and_skips_the_noise(tmp_path):
    root = tmp_path / "in"
    _source(root / "1. Theme" / "node_primary_256x256.png", _badge(size=32))
    _source(root / "2. Theme" / "card_frame_left_1200x800.png", _badge(size=32))
    _source(root / "__MACOSX" / "._node_primary_256x256.png", _badge(size=32))
    (root / ".DS_Store").write_text("noise", encoding="utf-8")
    (root / "readme.txt").write_text("noise", encoding="utf-8")
    found = core.find_sources(root)
    assert [path.name for path in found] == ["node_primary_256x256.png", "card_frame_left_1200x800.png"]
    assert core.find_sources(found[0]) == [found[0]]


def test_core_find_sources_reports_an_empty_tree(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ImagingError) as excinfo:
        core.find_sources(tmp_path / "empty")
    assert "no images" in str(excinfo.value)


def test_core_writes_both_sizes_from_one_call(tmp_path, delivery):
    calls = []
    record = _process(delivery, tmp_path / "out", calls=calls)[0]
    assert len(calls) == 1
    assert record["engine"] == "gemini"
    assert record["file"] == "theme/node_primary_64x64.png"
    assert record["master_size"] == "200x200"
    assert record["ui_size"] == "64x64"
    assert _opened(tmp_path / "out" / "master" / "theme" / "node_primary_64x64.png") == ("RGBA", (200, 200))
    assert _opened(tmp_path / "out" / "ui" / "theme" / "node_primary_64x64.png") == ("RGBA", (64, 64))


def test_core_keeps_a_source_without_alpha_in_rgb(tmp_path):
    root = tmp_path / "in"
    _source(root / "background_panel_50x50.png", _badge(size=SOURCE_EDGE).convert("RGB"))
    record = _process(root, tmp_path / "out")[0]
    assert record["alpha"] is False
    assert record["rim_lift"] == 0.0
    assert _opened(tmp_path / "out" / "ui" / "background_panel_50x50.png") == ("RGB", (50, 50))


def test_core_falls_back_when_the_rim_lift_crosses_the_threshold(tmp_path, delivery, installed):
    calls = []
    runs = []
    drifted = _badge(size=SOURCE_EDGE, rim=255, backdrop=255).convert("RGB")
    record = _process(delivery, tmp_path / "out", calls=calls, runs=runs, image=drifted)[0]
    assert record["engine"] == "ncnn"
    assert float(record["fallback"].split()[2]) > 20
    assert len(calls) == 1
    assert len(runs) == 1
    assert _opened(tmp_path / "out" / "master" / "theme" / "node_primary_64x64.png") == ("RGBA", (200, 200))


def test_core_threshold_decides_which_engine_ships(tmp_path, delivery, installed):
    strict = _process(delivery, tmp_path / "strict", settings=core.preset(provider="gemini", rim_lift_max=-1.0))[0]
    loose = _process(delivery, tmp_path / "loose", settings=core.preset(provider="gemini", rim_lift_max=999.0))[0]
    assert strict["engine"] == "ncnn"
    assert loose["engine"] == "gemini"


@pytest.mark.parametrize(
    "measured,limit,engine,fell_back",
    [
        (0.0, 20.0, "gemini", False),
        (19.9, 20.0, "gemini", False),
        (20.0, 20.0, "gemini", False),
        (20.1, 20.0, "ncnn", True),
        (-8.0, -1.0, "gemini", False),
        (0.0, -1.0, "ncnn", True),
        (120.0, 999.0, "gemini", False),
    ],
    ids=["clean", "just-under", "on-the-line", "just-over", "below-a-strict-limit", "strict", "loose"],
)
def test_fallback_table_follows_the_rim_lift_against_the_limit(
    monkeypatch, tmp_path, delivery, installed, measured, limit, engine, fell_back
):
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: 42.0)
    monkeypatch.setattr(core, "rim_lift", lambda *args, **kwargs: measured)
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini", rim_lift_max=limit))[0]
    assert record["engine"] == engine
    assert record["rim_lift"] == measured
    assert record["fidelity_db"] == 42.0
    assert ("fallback" in record) is fell_back
    if fell_back:
        assert record["fallback"] == f"rim lift {measured} > {limit}"


@pytest.mark.parametrize(
    "measured,floor,engine,fell_back",
    [
        (-19.9, -20.0, "gemini", False),
        (-20.0, -20.0, "gemini", False),
        (-20.1, -20.0, "ncnn", True),
        (-120.0, -20.0, "ncnn", True),
        (-8.0, -5.0, "ncnn", True),
    ],
    ids=["just-inside", "on-the-line", "just-under", "crushed", "below-a-strict-floor"],
)
def test_fallback_table_catches_a_rim_crushed_darker_than_the_source(
    monkeypatch, tmp_path, delivery, installed, measured, floor, engine, fell_back
):
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: 42.0)
    monkeypatch.setattr(core, "rim_lift", lambda *args, **kwargs: measured)
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini", rim_lift_min=floor))[0]
    assert record["engine"] == engine
    assert ("fallback" in record) is fell_back
    if fell_back:
        assert record["fallback"] == f"rim lift {measured} < {floor}"


def test_fallback_now_reads_the_fidelity_number_the_rim_bound_cannot_see(monkeypatch, tmp_path, delivery, installed):
    monkeypatch.setattr(core, "rim_lift", lambda *args, **kwargs: 5.9)
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: 6.19)
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini"))[0]
    assert record["engine"] == "ncnn"
    assert record["fallback"] == "fidelity 6.19 dB < 20.0 dB"


def test_fallback_keeps_a_candidate_the_rim_and_the_floor_both_clear(monkeypatch, tmp_path, delivery, installed):
    monkeypatch.setattr(core, "rim_lift", lambda *args, **kwargs: 4.1)
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: 29.75)
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini"))[0]
    assert record["engine"] == "gemini"
    assert "fallback" not in record
    assert "rejected" not in record


@pytest.mark.parametrize(
    "name,measured,db,engine",
    [
        ("background_panel", 5.9, 6.19, "ncnn"),
        ("spine_tile_vertical", -120.0, 10.28, "ncnn"),
        ("card_frame_left", 4.1, 29.75, "gemini"),
        ("node_primary", 1.8, 37.53, "gemini"),
        ("card_frame_right", 32.0, 44.31, "ncnn"),
        ("connector_straight", 75.3, 41.72, "ncnn"),
    ],
)
def test_gate_replays_the_paid_run_over_the_first_theme(
    monkeypatch, tmp_path, delivery, installed, name, measured, db, engine
):
    monkeypatch.setattr(core, "rim_lift", lambda *args, **kwargs: measured)
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: db)
    record = _process(delivery, tmp_path / name, settings=core.preset(provider="gemini"))[0]
    assert record["engine"] == engine


def test_gate_records_the_shift_without_judging_it(monkeypatch, tmp_path, delivery, installed):
    monkeypatch.setattr(core, "find_shift", lambda *args, **kwargs: (6, 9, 0.033))
    monkeypatch.setattr(core, "rim_lift", lambda *args, **kwargs: 1.8)
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: 37.53)
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini"))[0]
    assert record["shift"] == [6, 9]
    assert record["engine"] == "gemini"
    assert "fallback" not in record


def _shift_probe(seen, value=1.8):
    def measured(reference, candidate, shift=(0, 0), **kwargs):
        seen.append(tuple(shift))
        return value

    return measured


def test_gate_discards_a_shift_the_correlation_peak_cannot_support(monkeypatch, tmp_path, delivery, installed):
    seen = []
    monkeypatch.setattr(core, "find_shift", lambda *args, **kwargs: (-8, 6, 0.029))
    monkeypatch.setattr(core, "rim_lift", _shift_probe(seen))
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: 37.53)
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini"))[0]
    assert record["engine"] == "gemini"
    assert record["shift"] == [-8, 6]
    assert record["peak"] == 0.029
    assert record["shift_discarded"] == "peak 0.029 < 0.25"
    assert seen == [(0, 0)]
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report[0]["shift"] == [-8, 6]
    assert report[0]["shift_discarded"] == "peak 0.029 < 0.25"


def test_gate_keeps_following_a_shift_the_peak_supports(monkeypatch, tmp_path, delivery, installed):
    seen = []
    monkeypatch.setattr(core, "find_shift", lambda *args, **kwargs: (-8, 6, 0.425))
    monkeypatch.setattr(core, "rim_lift", _shift_probe(seen))
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: 37.53)
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini"))[0]
    assert record["shift"] == [-8, 6]
    assert "shift_discarded" not in record
    assert seen == [(-8, 6)]


def test_gate_leaves_a_zero_shift_unremarked_however_low_the_peak(monkeypatch, tmp_path, delivery, installed):
    monkeypatch.setattr(core, "find_shift", lambda *args, **kwargs: (0, 0, 0.010))
    monkeypatch.setattr(core, "rim_lift", lambda *args, **kwargs: 1.8)
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: 37.53)
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini"))[0]
    assert record["shift"] == [0, 0]
    assert "shift_discarded" not in record


def test_report_row_separates_the_rejected_candidate_from_the_shipped_file(monkeypatch, tmp_path, delivery, installed):
    monkeypatch.setattr(core, "rim_lift", _series([5.9, 5.8]))
    monkeypatch.setattr(core, "fidelity", _series([6.19, 44.31]))
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini"))[0]
    rejected = record["rejected"]
    assert (record["engine"], record["fidelity_db"], record["rim_lift"]) == ("ncnn", 44.31, 5.8)
    assert (rejected["engine"], rejected["fidelity_db"], rejected["rim_lift"]) == ("gemini", 6.19, 5.9)
    assert set(rejected) == {"engine", "shift", "peak", "fidelity_db", "rim_lift"}
    assert record["fallback"] == "fidelity 6.19 dB < 20.0 dB"
    assert {"file", "engine", "fidelity_db", "shift", "rim_lift"} <= set(record)
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report[0]["rejected"] == rejected
    assert report[0]["fidelity_db"] == 44.31


def test_core_falls_back_when_the_upstream_call_fails(tmp_path, delivery, installed):
    runs = []
    record = _process(delivery, tmp_path / "out", runs=runs, failure=ImagingError("POST -> HTTP 500"))[0]
    assert record["engine"] == "ncnn"
    assert "500" in record["fallback"]
    assert record["fidelity_db"] > 0
    assert len(runs) == 1


def test_core_lifts_a_target_larger_than_what_came_back(monkeypatch, tmp_path, delivery, installed):
    monkeypatch.setattr(core, "find_shift", lambda *args, **kwargs: (0, 0, 1.0))
    runs = []
    small = _flattened(_badge(size=100))
    record = _process(delivery, tmp_path / "out", runs=runs, image=small)[0]
    assert record["engine"] == "gemini"
    assert record["engine_size"] == "100x100"
    assert record["master_via"] == "gemini+ncnn"
    assert "ui_via" not in record
    assert runs[0][runs[0].index("-r") + 1] == "200x200"


def _alpha_of(path):
    Image = imaging.load_pillow()
    numpy = imaging.load_numpy()
    with Image.open(path) as handle:
        return numpy.asarray(handle.convert("RGBA").split()[-1])


def test_core_leaves_the_alpha_where_the_source_put_it_when_the_shift_is_untrustworthy(
    monkeypatch, tmp_path, delivery, installed
):
    numpy = imaging.load_numpy()
    monkeypatch.setattr(core, "find_shift", lambda *args, **kwargs: (-8, 6, 0.029))
    monkeypatch.setattr(core, "rim_lift", lambda *args, **kwargs: 1.8)
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: 37.53)
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini"))[0]
    source = _alpha_of(delivery / "theme" / "node_primary_64x64.png")
    shipped = _alpha_of(tmp_path / "out" / "master" / "theme" / "node_primary_64x64.png")
    assert record["engine"] == "gemini"
    assert numpy.array_equal(shipped, source)
    assert not numpy.array_equal(shipped, numpy.roll(numpy.roll(source, 8, 0), -6, 1))


def test_core_rolls_the_alpha_onto_a_candidate_the_peak_vouches_for(monkeypatch, tmp_path, delivery, installed):
    numpy = imaging.load_numpy()
    monkeypatch.setattr(core, "find_shift", lambda *args, **kwargs: (-8, 6, 0.425))
    monkeypatch.setattr(core, "rim_lift", lambda *args, **kwargs: 1.8)
    monkeypatch.setattr(core, "fidelity", lambda *args, **kwargs: 37.53)
    _process(delivery, tmp_path / "out", settings=core.preset(provider="gemini"))
    source = _alpha_of(delivery / "theme" / "node_primary_64x64.png")
    shipped = _alpha_of(tmp_path / "out" / "master" / "theme" / "node_primary_64x64.png")
    assert numpy.array_equal(shipped, numpy.roll(numpy.roll(source, 8, 0), -6, 1))


def test_core_feeds_the_local_engine_the_source_file_when_it_fits(tmp_path, delivery, installed):
    seen = []
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="ncnn"), runner=_fed_runner(seen))[0]
    assert seen == [("RGBA", (SOURCE_EDGE, SOURCE_EDGE))]
    assert "ncnn_input" not in record


def test_core_feeds_three_channels_when_the_fourth_would_overflow(monkeypatch, tmp_path, delivery, installed):
    monkeypatch.setattr(ncnn, "BUFFER_LIMIT", 2_000_000)
    seen = []
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="ncnn"), runner=_fed_runner(seen))[0]
    assert seen == [("RGB", (SOURCE_EDGE, SOURCE_EDGE))]
    assert record["ncnn_input"] == f"rgb {SOURCE_EDGE}x{SOURCE_EDGE}"
    assert record["master_size"] == f"{SOURCE_EDGE}x{SOURCE_EDGE}"

    numpy = imaging.load_numpy()
    master = tmp_path / "out" / "master" / "theme" / "node_primary_64x64.png"
    assert _opened(master) == ("RGBA", (SOURCE_EDGE, SOURCE_EDGE))
    assert numpy.array_equal(_alpha_of(master), _alpha_of(delivery / "theme" / "node_primary_64x64.png"))


def test_core_shrinks_the_local_input_but_still_delivers_the_source_size(monkeypatch, tmp_path, delivery, installed):
    monkeypatch.setattr(ncnn, "BUFFER_LIMIT", 1_000_000)
    seen = []
    record = _process(delivery, tmp_path / "out", settings=core.preset(provider="ncnn"), runner=_fed_runner(seen))[0]
    mode, fed = seen[0]
    assert mode == "RGB"
    assert fed[0] < SOURCE_EDGE and ncnn.fits_buffer(fed, 3)
    assert record["ncnn_input"] == f"rgb {fed[0]}x{fed[1]}"
    assert record["master_size"] == f"{SOURCE_EDGE}x{SOURCE_EDGE}"
    assert _opened(tmp_path / "out" / "master" / "theme" / "node_primary_64x64.png") == (
        "RGBA",
        (SOURCE_EDGE, SOURCE_EDGE),
    )


def test_core_guards_the_lift_path_too(monkeypatch, tmp_path, delivery, installed):
    monkeypatch.setattr(ncnn, "BUFFER_LIMIT", 2_000_000)
    monkeypatch.setattr(core, "find_shift", lambda *args, **kwargs: (0, 0, 1.0))
    seen = []
    record = _process(delivery, tmp_path / "out", image=_flattened(_badge(size=100)), runner=_fed_runner(seen))[0]
    assert record["engine"] == "gemini"
    assert record["master_via"] == "gemini+ncnn"
    assert seen == [("RGB", (SOURCE_EDGE, SOURCE_EDGE))]
    assert record["ncnn_input"] == f"rgb {SOURCE_EDGE}x{SOURCE_EDGE}"
    assert _opened(tmp_path / "out" / "master" / "theme" / "node_primary_64x64.png") == (
        "RGBA",
        (SOURCE_EDGE, SOURCE_EDGE),
    )


def test_core_report_names_a_file_the_engine_could_not_render(tmp_path, installed):
    root = tmp_path / "in"
    _source(root / "1. Theme" / "node_primary_64x64.png", _badge(size=SOURCE_EDGE))
    _source(root / "2. Theme" / "background_panel_64x64.png", _badge(size=SOURCE_EDGE, body=140))
    records = _process(root, tmp_path / "out", settings=core.preset(provider="ncnn"), runner=_picky_runner("panel"))
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    need = {"file", "engine", "fidelity_db", "shift", "rim_lift"}
    assert len(report) == 2
    assert all(need <= set(item) for item in report)
    assert all({"master_size", "ui_size", "total_s", "cached"} <= set(item) for item in report)
    failed = [item for item in report if item.get("error")]
    assert [item["file"] for item in failed] == ["2. Theme/background_panel_64x64.png"]
    assert failed[0]["engine"] is None
    assert failed[0]["fidelity_db"] is None
    assert failed[0]["cached"] is False
    assert "0xC0000005" in failed[0]["error"] and "87.40%" in failed[0]["error"]
    assert records[1]["error"] == failed[0]["error"]


def test_core_report_carries_the_keys_a_review_needs(tmp_path):
    root = tmp_path / "in"
    _source(root / "1. Theme" / "node_primary_64x64.png", _badge(size=SOURCE_EDGE))
    _source(root / "2. Theme" / "card_frame_left_40x20.png", _badge(size=SOURCE_EDGE, body=140))
    _process(root, tmp_path / "out")
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    need = {"file", "engine", "fidelity_db", "shift", "rim_lift"}
    assert len(report) == 2
    assert all(need <= set(item) for item in report)
    assert all({"master_size", "ui_size", "total_s", "gemini_s", "ncnn_s"} <= set(item) for item in report)
    assert [item["file"] for item in report] == [
        "1. Theme/node_primary_64x64.png",
        "2. Theme/card_frame_left_40x20.png",
    ]
    assert [item["cached"] for item in report] == [False, False]


def test_core_keeps_the_local_engine_serial_across_jobs(tmp_path, installed):
    root = tmp_path / "in"
    sources = [_source(root / f"tile_{index}_32x32.png", _badge(size=96, body=100 + index)) for index in range(3)]
    guard = threading.Lock()
    state = {"active": 0, "peak": 0}
    inner = _image_runner([])

    def runner(cmd):
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        result = inner(cmd)
        with guard:
            state["active"] -= 1
        return result

    records = core.process_many(
        sources,
        tmp_path / "out",
        root=root,
        settings=core.preset(provider="ncnn"),
        jobs=3,
        runner=runner,
    )
    assert [record["engine"] for record in records] == ["ncnn"] * 3
    assert state["peak"] == 1


def test_cache_makes_the_second_run_free(tmp_path, delivery):
    calls = []
    first = _process(delivery, tmp_path / "out", calls=calls)[0]
    second = _process(delivery, tmp_path / "out", calls=calls)[0]
    assert len(calls) == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["fidelity_db"] == first["fidelity_db"]


def test_cache_misses_when_a_setting_changes(tmp_path, delivery):
    calls = []
    _process(delivery, tmp_path / "out", calls=calls)
    _process(delivery, tmp_path / "out", calls=calls, settings=core.preset(provider="gemini", image_size="2K"))
    assert len(calls) == 2


def test_cache_misses_when_the_source_changes(tmp_path, delivery):
    calls = []
    _process(delivery, tmp_path / "out", calls=calls)
    _source(delivery / "theme" / "node_primary_64x64.png", _badge(size=SOURCE_EDGE, body=160))
    _process(delivery, tmp_path / "out", calls=calls)
    assert len(calls) == 2


def test_cache_rebuilds_a_missing_output_without_a_new_call(tmp_path, delivery):
    calls = []
    _process(delivery, tmp_path / "out", calls=calls)
    lost = tmp_path / "out" / "ui" / "theme" / "node_primary_64x64.png"
    lost.unlink()
    record = _process(delivery, tmp_path / "out", calls=calls)[0]
    assert len(calls) == 1
    assert record["cached"] is True
    assert _opened(lost) == ("RGBA", (64, 64))


def test_cache_force_ignores_the_stored_result(tmp_path, delivery):
    calls = []
    _process(delivery, tmp_path / "out", calls=calls)
    record = _process(delivery, tmp_path / "out", calls=calls, force=True)[0]
    assert len(calls) == 2
    assert record["cached"] is False


def test_cache_dry_run_counts_what_is_already_there(tmp_path, delivery):
    calls = []
    assert _process(delivery, tmp_path / "out", calls=calls, dry_run=True) == [
        {"file": "theme/node_primary_64x64.png", "engine": None, "cached": False}
    ]
    assert calls == []
    assert not (tmp_path / "out" / "report.json").exists()
    _process(delivery, tmp_path / "out", calls=calls)
    assert _process(delivery, tmp_path / "out", calls=calls, dry_run=True) == [
        {"file": "theme/node_primary_64x64.png", "engine": "gemini", "cached": True}
    ]
    assert len(calls) == 1


def test_registered_image_group_sits_on_the_cli():
    assert "image" in cli.commands
    for name in ("upscale", "check", "install"):
        assert name in cli.commands["image"].commands


def test_registered_image_help_runs_for_every_subcommand():
    for args in (["image", "-h"], ["image", "upscale", "-h"], ["image", "check", "-h"], ["image", "install", "-h"]):
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0
    listing = CliRunner().invoke(cli, ["image", "-h"]).output
    assert "Examples" in listing
    assert "evo image upscale" in listing


def test_registered_image_upscale_dry_run_counts_what_is_ready(tmp_path, delivery):
    out = tmp_path / "out"
    args = ["image", "upscale", str(delivery), "-o", str(out), "--provider", "gemini", "--dry-run"]
    first = CliRunner().invoke(cli, args)
    assert first.exit_code == 0
    assert "cached: 0" in first.output
    assert not out.exists()

    _process(delivery, out)
    second = CliRunner().invoke(cli, args)
    assert second.exit_code == 0
    assert "cached: 1" in second.output


def test_registered_image_upscale_hands_the_options_to_the_engine(monkeypatch, tmp_path, delivery):
    seen = {}

    def fake(sources, out_dir, **kwargs):
        seen["sources"] = [Path(item).name for item in sources]
        seen["out_dir"] = out_dir
        seen.update(kwargs)
        return [{"file": "theme/node_primary_64x64.png", "engine": "ncnn", "cached": False}]

    monkeypatch.setattr(core, "process_many", fake)
    result = CliRunner().invoke(
        cli,
        [
            "image",
            "upscale",
            str(delivery),
            "-o",
            str(tmp_path / "out"),
            "--provider",
            "ncnn",
            "--model",
            "remacri-4x",
            "--scale",
            "0.5",
            "--only",
            "ui",
            "-j",
            "2",
            "--force",
        ],
    )
    assert result.exit_code == 0
    assert seen["sources"] == ["node_primary_64x64.png"]
    assert seen["jobs"] == 2
    assert seen["force"] is True
    assert seen["settings"]["provider"] == "ncnn"
    assert seen["settings"]["model"] == "remacri-4x"
    assert seen["settings"]["master_scale"] == 0.5
    assert seen["settings"]["outputs"] == ["ui"]


def test_registered_image_upscale_reports_a_failed_item(monkeypatch, tmp_path, delivery):
    monkeypatch.setattr(core, "process_many", lambda *args, **kwargs: [{"file": "a.png", "error": "boom"}])
    result = CliRunner().invoke(cli, ["image", "upscale", str(delivery), "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "boom" in result.output


def test_registered_image_check_exits_non_zero_when_a_part_is_missing(monkeypatch):
    monkeypatch.setattr(image_cmd, "vulkan_loader", lambda: "vulkan-1")
    result = CliRunner().invoke(cli, ["image", "check"])
    assert result.exit_code == 1
    assert "upscayl-bin" in result.output
    assert "missing" in result.output


def test_registered_image_check_is_happy_on_a_complete_machine(monkeypatch, installed):
    monkeypatch.setattr(image_cmd, "vulkan_loader", lambda: "vulkan-1")
    result = CliRunner().invoke(cli, ["image", "check"])
    assert result.exit_code == 0
    assert "missing" not in result.output


def test_registered_image_install_reuses_what_is_already_there(monkeypatch, installed):
    monkeypatch.setattr(image_cmd, "vulkan_loader", lambda: "vulkan-1")
    result = CliRunner().invoke(cli, ["image", "install"])
    assert result.exit_code == 0
    assert ncnn.DEFAULT_MODEL in result.output
