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

from evo_cli import console, imaging
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


def _runner(calls, returncode=0, produce=True):
    def run(cmd):
        calls.append(cmd)
        if produce:
            Path(cmd[cmd.index("-o") + 1]).write_text("png", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, "", "upscayl-bin: something went wrong")

    return run


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
            return _image_response(_encode(handle.convert("RGB")))

    return post


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


@pytest.fixture
def delivery(tmp_path):
    _source(tmp_path / "in" / "theme" / "node_primary_64x64.png", _badge(size=SOURCE_EDGE))
    return tmp_path / "in"


def _process(root, out_dir, calls=None, runs=None, image=None, failure=None, settings=None, **kwargs):
    return core.process_many(
        core.find_sources(root),
        out_dir,
        root=root,
        settings=settings or core.preset(provider="gemini"),
        poster=_poster(calls if calls is not None else [], image=image, failure=failure),
        runner=_image_runner(runs if runs is not None else []),
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
    drifted = _badge(size=SOURCE_EDGE, rim=127, backdrop=255).convert("RGB")
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


def test_core_falls_back_when_the_upstream_call_fails(tmp_path, delivery, installed):
    runs = []
    record = _process(delivery, tmp_path / "out", runs=runs, failure=ImagingError("POST -> HTTP 500"))[0]
    assert record["engine"] == "ncnn"
    assert "500" in record["fallback"]
    assert record["fidelity_db"] > 0
    assert len(runs) == 1


def test_core_lifts_a_target_larger_than_what_came_back(tmp_path, delivery, installed):
    runs = []
    small = _badge(size=100).convert("RGB")
    record = _process(delivery, tmp_path / "out", runs=runs, image=small)[0]
    assert record["engine"] == "gemini"
    assert record["engine_size"] == "100x100"
    assert record["master_via"] == "gemini+ncnn"
    assert "ui_via" not in record
    assert runs[0][runs[0].index("-r") + 1] == "200x200"


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
